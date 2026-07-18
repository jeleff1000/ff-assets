from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest.catalog import CatalogError, load_catalog
from harvest.core import build_manifest, sha256_file, shard_for, verify_manifest


def test_shard_is_stable_and_bounded() -> None:
    assert shard_for("nflcom|team_season_roster|1921|dayton-triangles", 60) == shard_for(
        "nflcom|team_season_roster|1921|dayton-triangles", 60
    )
    assert 0 <= shard_for("same-key", 7) < 7
    with pytest.raises(ValueError):
        shard_for("same-key", 0)


def test_manifest_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    payload = tmp_path / "records.jsonl"
    payload.write_text('{"source":"nflcom"}\n', encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        source="nflcom",
        dataset="team_season_roster",
        shard_id=2,
        shard_count=8,
        artifact_run_id="fixture-run",
    )
    (tmp_path / "ARTIFACT_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert manifest["files"][0]["sha256"] == sha256_file(payload)
    assert verify_manifest(tmp_path) == manifest

    payload.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        verify_manifest(tmp_path)


def test_catalog_rejects_unsupported_pairs_and_bad_years(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(
        """
sources:
  nflcom:
    domains: [www.nfl.com]
    delay_seconds: 1.0
    datasets:
      team_season_roster:
        first_year: 1920
        last_year: 2025
        allowed_prefixes: [/sitemap/html/rosters/]
""",
        encoding="utf-8",
    )
    catalog = load_catalog(catalog_path)
    catalog.validate_request("nflcom", "team_season_roster", 1920, 2025)
    with pytest.raises(CatalogError, match="unsupported dataset"):
        catalog.validate_request("nflcom", "player_game_stats", 1920, 2025)
    with pytest.raises(CatalogError, match="outside supported range"):
        catalog.validate_request("nflcom", "team_season_roster", 1919, 1921)

