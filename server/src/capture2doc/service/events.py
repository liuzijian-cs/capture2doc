"""Pure ordered preview operations, shared by the service and test consumer."""
from __future__ import annotations

from copy import deepcopy


def patches(old: list[dict], new: list[dict], revision: int):
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    end_old, end_new = len(old), len(new)
    while end_old > prefix and end_new > prefix and old[end_old - 1] == new[end_new - 1]:
        end_old -= 1
        end_new -= 1
    removed, inserted = old[prefix:end_old], new[prefix:end_new]
    anchor = old[prefix - 1]["blockId"] if prefix else None
    # New independent blocks are individually visible; related replacements atomic.
    groups = [[b] for b in inserted] if not removed else [inserted]
    if removed and not inserted:
        groups = [[]]
    for group in groups:
        yield {
            "baseRevision": revision, "revision": revision + 1,
            "afterBlockId": anchor,
            "removeBlockIds": [b["blockId"] for b in removed],
            "blocks": deepcopy(group),
        }
        revision += 1
        if group:
            anchor = group[-1]["blockId"]
        removed = []


def apply_preview_patch(blocks: list[dict], revision: int, patch: dict):
    if patch["baseRevision"] != revision or patch["revision"] != revision + 1:
        raise ValueError("Preview revision mismatch; request a fresh snapshot")
    ids = [b["blockId"] for b in blocks]
    anchor = patch["afterBlockId"]
    index = 0 if anchor is None else ids.index(anchor) + 1
    removed = patch["removeBlockIds"]
    if ids[index:index + len(removed)] != removed:
        raise ValueError("Preview splice mismatch")
    result = deepcopy(blocks)
    old_versions = {b["blockId"]: b for b in blocks}
    for block in patch["blocks"]:
        old = old_versions.get(block["blockId"])
        if old and (block["version"] < old["version"] or
                    (block["version"] == old["version"] and block != old)):
            raise ValueError("Stale block version")
    result[index:index + len(removed)] = deepcopy(patch["blocks"])
    if len({b["blockId"] for b in result}) != len(result):
        raise ValueError("Duplicate block identity")
    return result, patch["revision"]
