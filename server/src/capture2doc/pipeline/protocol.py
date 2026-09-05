"""Constrained JSON transport; C2D XML inside each string is validated separately."""

from __future__ import annotations


def object_schema(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def response_schema(mode: str, attempt_id: str, versions: dict) -> dict:
    string = {"type": "string"}
    block = object_schema(
        {"xml": string, "text": string, "ocr_refs": {"type": "array", "items": string}}
    )
    fields = {"action": {"const": "submit"}}
    if mode == "generate":
        fields["tail"] = {"anyOf": [{"type": "null"}, block]}
    else:
        fields["attempt_id"] = {"const": attempt_id}
        fields["target_versions"] = object_schema(
            {key: {"const": value} for key, value in versions.items()}
        )
    fields["blocks"] = {"type": "array", "items": block}
    return {
        "anyOf": [
            object_schema(fields),
            object_schema(
                {
                    "action": {"const": "read_blocks"},
                    "block_ids": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                }
            ),
            object_schema({"action": {"const": "search_blocks"}, "query": string}),
        ]
    }
