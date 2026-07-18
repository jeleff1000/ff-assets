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
    for name in ("witness-census.yml", "witness-harvest.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "max-parallel:" in text
        assert "timeout-minutes:" in text
        assert "num_shards" in text
        assert "max_pages" in text

