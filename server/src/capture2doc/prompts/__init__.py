"""Versioned, packaged UTF-8 prompts; no interpolation of document content."""

from hashlib import sha256
from importlib.resources import files


def c2d_system_prompt() -> str:
    return files(__package__).joinpath("c2d_system.txt").read_text(encoding="utf-8")


def prompt_fingerprint(prompt: str) -> str:
    return sha256(prompt.encode("utf-8")).hexdigest()
