from __future__ import annotations

import hashlib
import json
from typing import Iterable


def schema_fingerprint(rows: list[dict]) -> str:
    columns = sorted({key for row in rows for key in row})
    return hashlib.sha256(json.dumps(columns, separators=(",", ":")).encode()).hexdigest()


def classify_surface(rows: list[dict], *, identifiers: Iterable[str]) -> str:
    if not rows:
        return "unusable"
    ids = set(identifiers)
    columns = {key for row in rows for key in row}
    if {"player", "team", "season"}.issubset(ids) and ids.issubset(columns):
        return "canonical_candidate"
    if "player" in ids and "season" in ids and ids.issubset(columns):
        return "identity_only"
    if columns & {"story", "description", "note", "headline"}:
        return "context_only"
    if ids and ids.issubset(columns):
        return "corroborating_witness"
    return "unusable"

