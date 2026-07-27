"""THE GATE THAT WAS MISSING: a declared dataset must have a WORKING PARSER.

Run 30226751762 dispatched three StatsCrew datasets across 15 jobs. All 15 reported
green. Ten of them -- `team_season_stats` and `team_season_results`, roughly ten machine
hours -- captured NOTHING, because declaring a dataset in `source_catalog.yaml` was
necessary but not sufficient: `parse()` raised for those dataset names and
`work_item_from_url()` returned None, so the census emitted no work, the runner idled,
and the job exited 0.

The three-stage design already said a pilot proves a family is STRUCTURALLY parseable.
Nothing checked that the HARVESTER could parse it. That is this file.

`parser_probe` in the catalog is the contract: a representative URL the dataset's
`work_item_from_url` must recognise. A dataset that cannot name one has no business
being dispatched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harvest.sources import get_adapter

ROOT = Path(__file__).parents[1]
CATALOG = yaml.safe_load((ROOT / "harvest" / "source_catalog.yaml").read_text(encoding="utf-8"))

# Terminal, non-work dispositions. These are NOT "we have not got round to it" -- each
# one is a measured reason the dataset must never be dispatched, and a dataset carrying
# one is excluded from the parser gate but still has to say why in the catalog.
TERMINAL_DISPOSITIONS = {"held_elsewhere"}

# OPEN WORK, counted rather than excused. A dataset here is declared, has no parser, and
# must never be dispatched until it does. It is NOT a pass: the yahoo_oauth scoreboard
# carries `harvest_datasets_queued_without_parser` so the number is visible next to
# every other queue, and this file forbids it being dispatched meanwhile.
QUEUED_DISPOSITIONS = {"queued_no_parser"}


def _datasets() -> list[tuple[str, str, dict]]:
    return [
        (source, dataset, spec)
        for source, source_spec in CATALOG["sources"].items()
        for dataset, spec in source_spec["datasets"].items()
    ]


@pytest.mark.parametrize(("source", "dataset", "spec"), _datasets(), ids=lambda v: v if isinstance(v, str) else "")
def test_declared_dataset_is_dispatchable_or_explicitly_terminal(source, dataset, spec) -> None:
    disposition = spec.get("harvest_disposition")
    if disposition in TERMINAL_DISPOSITIONS | QUEUED_DISPOSITIONS:
        assert spec.get("disposition_reason"), (
            f"{source}/{dataset}: a non-dispatchable disposition must carry its evidence")
        # A queued dataset must be UNDISPATCHABLE, not merely unscheduled. If it could
        # be censused it would burn hours crawling for rows no parser can emit -- which
        # is precisely what ten jobs of run 30226751762 did.
        assert not spec.get("census_seed_template") and not spec.get("census_seed_ranges"), (
            f"{source}/{dataset}: has no parser but carries census seeds, so a dispatch "
            "would crawl for work items that can never be emitted")
        return
    assert disposition is None, (
        f"{source}/{dataset}: unknown harvest_disposition {disposition!r}")

    probe = spec.get("parser_probe")
    assert probe, (
        f"{source}/{dataset}: declares no parser_probe, so nothing proves the harvester "
        "can turn one of its URLs into work")

    adapter = get_adapter(source)
    work = adapter.work_item_from_url(dataset, probe)
    assert work is not None, (
        f"{source}/{dataset}: work_item_from_url returned None for its own probe {probe} -- "
        "this is exactly the shape that produced ten green jobs and zero records")
    assert work["url"] == probe
    assert "key" in work


@pytest.mark.parametrize(("source", "dataset", "spec"), _datasets(), ids=lambda v: v if isinstance(v, str) else "")
def test_declared_dataset_has_a_parse_branch(source, dataset, spec) -> None:
    """`parse()` must REACH a real branch for the dataset name.

    Empty HTML is expected to raise the branch's own "nothing usable here" ValueError.
    What must never happen is the routing error -- an unsupported-dataset raise -- which
    is the failure that went undetected for a whole harvest run.
    """
    if spec.get("harvest_disposition") in TERMINAL_DISPOSITIONS | QUEUED_DISPOSITIONS:
        return
    adapter = get_adapter(source)
    work = adapter.work_item_from_url(dataset, spec["parser_probe"])
    with pytest.raises(ValueError) as raised:
        adapter.parse(dataset, "<html><body>no tables here</body></html>", work)
    assert "unsupported" not in str(raised.value).lower(), (
        f"{source}/{dataset}: parse() has no branch for this dataset")


def test_multi_table_datasets_are_declared_as_such() -> None:
    """A multi-table page whose rows are not table-tagged collapses its tables into one
    undifferentiated heap, and the distinct tables become unrecoverable after capture.
    The catalog must say which datasets carry that risk."""
    for source, dataset, spec in _datasets():
        if spec.get("multi_table"):
            assert spec.get("parser_probe"), f"{source}/{dataset}: multi-table needs a probe"
