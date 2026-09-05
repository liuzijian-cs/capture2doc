"""Sequential, transactional C2D tail replacement and bounded history selection."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from lxml import etree

from .validator import (
    NS,
    ValidationIssue,
    ValidationResult,
    _parse_and_validate,
    _validate_tree,
)

HISTORY_TOKEN_LIMIT = 1536
TAIL_ONLY_THRESHOLD = 768


class C2DAssemblyError(ValueError):
    """An assembly or context operation could not produce a valid result."""

    def __init__(self, issues: tuple[ValidationIssue, ...]):
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


class C2DAssembler:
    """Own one in-memory draft. Calls must be serial and updates applied once."""

    def __init__(self, *, lang: str | None = None):
        self._root = etree.Element(
            f"{{{NS}}}document", nsmap={None: NS}, attrib={"schema-version": "0.1"}
        )
        if lang is not None:
            if not isinstance(lang, str):
                raise ValueError("lang must be a string or None")
            self._root.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
        result = _validate_tree(self._root, "document")
        if not result.valid:
            raise C2DAssemblyError(result.issues)

    def apply_update(self, xml: str | bytes) -> ValidationResult:
        """Replace the tail and append new blocks, committing only on success."""
        update, result = _parse_and_validate(xml, "c2d-update")
        if not result.valid:
            return result
        assert update is not None
        candidate = deepcopy(self._root)
        if len(candidate):
            candidate.remove(candidate[-1])
        # Only the validated response's children enter the public document.
        candidate.extend(list(update))
        result = _validate_tree(candidate, "document")
        if result.valid:
            self._root = candidate
        return result

    def context_blocks(
        self,
        *,
        count_tokens: Callable[[str], int],
        token_budget: int = HISTORY_TOKEN_LIMIT,
        allow_large_tail: bool = False,
    ) -> tuple[bytes, ...]:
        """Count exactly the newline-joined XML returned to the caller.

        The injected counter must use the target model's tokenizer, be
        deterministic, and return a nonnegative integer. It must not add a
        separate chat template to each block. The caller checks the complete
        multimodal request budget separately.
        """
        if isinstance(token_budget, bool) or not isinstance(token_budget, int):
            raise ValueError("token_budget must be a nonnegative integer")
        if token_budget < 0:
            raise ValueError("token_budget must be a nonnegative integer")
        if not len(self._root):
            return ()
        budget = min(HISTORY_TOKEN_LIMIT, token_budget)
        blocks = tuple(
            etree.tostring(block, encoding="utf-8", with_tail=False, pretty_print=False)
            for block in list(self._root)[-3:]
        )

        def count(selected: tuple[bytes, ...]) -> int:
            tokens = count_tokens(b"\n".join(selected).decode("utf-8"))
            if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
                raise ValueError("count_tokens must return a nonnegative integer")
            return tokens

        tail_tokens = count(blocks[-1:])
        # Pipeline callers can retain an oversized tail in full when the actual
        # model budget permits it. The historical default remains unchanged.
        if allow_large_tail and tail_tokens > HISTORY_TOKEN_LIMIT:
            budget = token_budget
        if tail_tokens > budget:
            raise C2DAssemblyError(
                (
                    ValidationIssue(
                        "context",
                        "CONTEXT_BUDGET_EXCEEDED",
                        f"Last block uses {tail_tokens} tokens; history budget is {budget}",
                    ),
                )
            )
        if tail_tokens > TAIL_ONLY_THRESHOLD:
            return blocks[-1:]
        for size in range(len(blocks), 1, -1):
            selected = blocks[-size:]
            if count(selected) <= budget:
                return selected
        return blocks[-1:]

    def finalize(self) -> bytes:
        """Return UTF-8 XML without freezing the draft or writing any files."""
        if not len(self._root):
            raise C2DAssemblyError(
                (
                    ValidationIssue(
                        "assembly", "EMPTY_DOCUMENT", "No blocks have been assembled"
                    ),
                )
            )
        result = _validate_tree(self._root, "document")
        if not result.valid:
            raise C2DAssemblyError(result.issues)
        return etree.tostring(
            self._root, encoding="utf-8", xml_declaration=True, pretty_print=False
        )
