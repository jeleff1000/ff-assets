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


def test_all_source_census_is_manual_not_push_triggered() -> None:
    """A full re-census must be a DECISION, never a side effect of editing code.

    This ran on every push touching harvest/**, so three unrelated commits in one
    afternoon launched three 60-job runs that re-crawled datasets we already hold --
    thousands of redundant requests at small independent archives.
    """
    text = (WORKFLOWS / "witness-census-all.yml").read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert "workflow_dispatch" in data["on"]
    assert "push" not in data["on"], (
        "re-crawling held pages must not be triggered by a code edit")
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


def test_discovery_workflow_is_per_host_and_bounded() -> None:
    """Discovery must stay ONE request stream per host and must bound its page budget.

    Sharding discovery across many runners would multiply the request rate against
    small independent archives for no gain -- discovery is a few hundred pages. The
    per-host limit is the politeness contract, not an optimisation.
    """
    path = WORKFLOWS / "witness-discover-all.yml"
    text = path.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert "workflow_dispatch" in data["on"]
    assert "pytest" in text
    assert "upload-artifact@v4" in text
    assert "retention-days: 14" in text
    assert "timeout-minutes:" in text
    assert "max-parallel: 3" in text, "one job per source == one stream per host"
    assert "--max-pages" in text and "--children-per-pattern" in text
    for source in ("profootballarchives", "statscrew", "nflcom"):
        assert source in text


def test_every_source_declares_discovery_probes() -> None:
    """A source with no probes cannot be discovered, and would silently be skipped."""
    catalog = yaml.safe_load(
        (Path(__file__).parents[1] / "harvest" / "source_catalog.yaml").read_text(
            encoding="utf-8"
        )
    )
    for name, spec in catalog["sources"].items():
        assert spec.get("discovery_probes"), f"{name}: no discovery_probes declared"


def test_pilot_workflow_samples_and_never_registers() -> None:
    """A pilot proposes; it must not edit the catalog. Bulk authorisation is a human
    decision made against the proposal, not something a workflow grants itself."""
    text = (WORKFLOWS / "witness-pilot.yml").read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert "workflow_dispatch" in data["on"]
    assert "pytest" in text
    assert "--sample" in text and "--family" in text
    assert "upload-artifact@v4" in text and "retention-days: 14" in text
    assert "source_catalog" not in text, "a pilot must never write the catalog"


def test_gap_harvest_is_manual_bounded_and_polite_PER_HOST() -> None:
    """Stage 3 harvests only measured GAPS, and its parallelism is bounded PER HOST.

    The original assertion here pinned `max-parallel: 15` and justified it as parallelism
    "spread across hosts" -- but every job in the matrix targets statscrew.com, so the
    real setting was 15 concurrent streams at one small independent archive. One runner
    was then refused robots.txt, logged 477 denials in 0.65s and still reported green;
    178 team-seasons from its partition remain uncaptured. The cap must be derived from
    the matrix, not asserted independently of it.
    """
    path = WORKFLOWS / "witness-harvest-gap.yml"
    text = path.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert "workflow_dispatch" in data["on"]
    assert "push" not in data["on"], "a bulk harvest must never fire from a code edit"
    assert "pytest" in text
    assert "timeout-minutes:" in text
    assert "--max-pages" in text and "--num-shards" in text
    assert "upload-artifact@v4" in text and "retention-days: 14" in text

    strategy = data["jobs"]["harvest"]["strategy"]
    cap = int(strategy["max-parallel"])
    hosts = {target["source"] for target in strategy["matrix"]["target"]}
    catalog = yaml.safe_load(
        (Path(__file__).parents[1] / "harvest" / "source_catalog.yaml").read_text(encoding="utf-8")
    )
    slowest = min(float(catalog["sources"][host]["delay_seconds"]) for host in hosts)
    # Worst case every running job sits on the same host, because the matrix may be
    # filtered to one source at dispatch time.
    assert cap / slowest <= 6.0, (
        f"max-parallel {cap} over {sorted(hosts)} at {slowest}s delay is "
        f"{cap / slowest:.0f} req/s against a single host"
    )
