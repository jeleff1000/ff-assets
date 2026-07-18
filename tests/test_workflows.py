from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"


def test_new_workflows_are_manual_safe_and_artifact_backed() -> None:
    for name in ("witness-probe.yml", "witness-census.yml", "witness-harvest.yml"):
        path = WORKFLOWS / name
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert "workflow_dispatch" in data["on"]
        text = path.read_text(encoding="utf-8")
        assert "pytest" in text
        assert "upload-artifact@v4" in text
        assert "retention-days: 14" in text


def test_bulk_workflows_bound_parallelism_and_runtime() -> None:
    for name in ("witness-census.yml", "witness-census-all.yml", "witness-harvest.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "max-parallel:" in text
        assert "timeout-minutes:" in text
        assert "num_shards" in text or "--num-shards" in text
        assert "max_pages" in text or "--max-pages" in text


def test_all_source_census_runs_on_harvest_changes() -> None:
    text = (WORKFLOWS / "witness-census-all.yml").read_text(encoding="utf-8")
    assert "push:" in text
    assert "nflcom" in text
    assert "statscrew" in text
    assert "profootballarchives" in text
    assert "shard: [0, 1, 2" in text


def test_all_source_harvest_continues_only_after_successful_census() -> None:
    path = WORKFLOWS / "witness-harvest-all.yml"
    text = path.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert "workflow_run" in data["on"]
    assert "witness-census-all" in text
    assert "conclusion == 'success'" in text
    assert "run-id: ${{ github.event.workflow_run.id }}" in text
    assert "--work-items-prepartitioned" in text
    assert "upload-artifact@v4" in text
