from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def shard_for(key: str, count: int) -> int:
    if count < 1:
        raise ValueError("shard count must be positive")
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(
    artifact_dir: Path,
    *,
    source: str,
    dataset: str,
    shard_id: int,
    shard_count: int,
    artifact_run_id: str,
) -> dict[str, Any]:
    files = []
    for path in sorted(p for p in artifact_dir.rglob("*") if p.is_file() and p.name != "ARTIFACT_MANIFEST.json"):
        files.append(
            {
                "path": path.relative_to(artifact_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "dataset": dataset,
        "shard_id": shard_id,
        "shard_count": shard_count,
        "artifact_run_id": artifact_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def verify_manifest(artifact_dir: Path) -> dict[str, Any]:
    manifest_path = artifact_dir / "ARTIFACT_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported manifest schema version")
    for entry in manifest.get("files", []):
        rel = Path(entry["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("unsafe manifest path")
        payload = artifact_dir / rel
        if not payload.is_file():
            raise ValueError(f"manifest file missing: {rel.as_posix()}")
        if sha256_file(payload) != entry["sha256"]:
            raise ValueError(f"checksum mismatch: {rel.as_posix()}")
    return manifest

