"""Validation and sequential assembly of C2D-XML documents."""

from .assembler import C2DAssembler, C2DAssemblyError

from .validator import (
    ValidationIssue,
    ValidationResult,
    validate_document,
    validate_update,
)

__all__ = [
    "C2DAssembler",
    "C2DAssemblyError",
    "ValidationIssue",
    "ValidationResult",
    "validate_document",
    "validate_update",
]
