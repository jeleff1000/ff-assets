from __future__ import annotations

import gzip
import json
from pathlib import Path

from harvest.http import classify_status
from harvest.runner import run_fixture_harvest

FIXTURES = Path(__file__).parent / "fixtures"


def test_http_status_classification() -> None:
    assert classify_status(200) == "ok"
    assert classify_status(404) == "absent"
    assert classify_status(429) == "throttled"
    assert classify_status(503) == "retryable"
    assert classify_status(403) == "blocked"


def test_fixture_harvest_writes_raw_records_ledger_and_manifest(tmp_path: Path) -> None:
    work = {
        "key": "1921|dayton-triangles",
        "season": 1921,
        "team": "dayton-triangles",
        "url": "https://www.nfl.com/sitemap/html/rosters/1921/dayton-triangles",
    }
    html = (FIXTURES / "nflcom_roster.html").read_text(encoding="utf-8")
    result = run_fixture_harvest(
        source="nflcom",
        dataset="team_season_roster",
        work_items=[work],
        pages={work["url"]: html},
        output_dir=tmp_path,
        shard_id=0,
        shard_count=1,
        artifact_run_id="fixture",
    )
    assert result["record_count"] == 1
    assert (tmp_path / "ARTIFACT_MANIFEST.json").is_file()
    ledger = [json.loads(line) for line in (tmp_path / "REQUEST_LEDGER.jsonl").read_text().splitlines()]
    assert ledger[0]["status"] == "ok"
    raw_file = next((tmp_path / "raw").glob("*.html.gz"))
    assert "Herb Sies" in gzip.decompress(raw_file.read_bytes()).decode()


def test_fixture_harvest_records_absent_page(tmp_path: Path) -> None:
    work = {"key": "missing", "season": 1921, "team": "x", "url": "https://www.nfl.com/missing"}
    result = run_fixture_harvest(
        source="nflcom",
        dataset="team_season_roster",
        work_items=[work],
        pages={},
        output_dir=tmp_path,
        shard_id=0,
        shard_count=1,
        artifact_run_id="fixture",
    )
    assert result["record_count"] == 0
    ledger = json.loads((tmp_path / "REQUEST_LEDGER.jsonl").read_text().strip())
    assert ledger["status"] == "absent"


def test_prepartitioned_harvest_does_not_reshard_census_work(tmp_path: Path) -> None:
    work = {
        "key": "1921|dayton-triangles",
        "season": 1921,
        "team": "dayton-triangles",
        "url": "https://www.nfl.com/sitemap/html/rosters/1921/dayton-triangles",
    }
    html = (FIXTURES / "nflcom_roster.html").read_text(encoding="utf-8")
    result = run_fixture_harvest(
        source="nflcom",
        dataset="team_season_roster",
        work_items=[work],
        pages={work["url"]: html},
        output_dir=tmp_path,
        shard_id=1,
        shard_count=2,
        artifact_run_id="fixture",
        work_items_prepartitioned=True,
    )
    assert result["record_count"] == 1
