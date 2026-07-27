"""GREEN MUST MEAN CAPTURED.

Run 30226751762 is the whole reason this file exists. Fifteen jobs, every one reporting
success:

  * ten of them (`team_season_stats`, `team_season_results`) burned ~10 machine-hours
    and wrote a 0-byte ledger and no records, because the datasets were declared in the
    catalog with no parser branch behind them;
  * one of them (`team_season_roster` shard 1) was robots-denied on all 477 URLs of a
    real partition in 0.65 seconds and reported an empty census. 178 team-seasons from
    that partition are still uncaptured.

Nothing in the pipeline distinguished any of that from success. These tests do.
"""

from __future__ import annotations

from harvest.census import census_failure_reason
from harvest.runner import harvest_failure_reason


def test_zero_records_from_real_work_is_a_failure() -> None:
    """The team_season_stats shape: work items existed, the parser emitted nothing."""
    reason = harvest_failure_reason(
        {"record_count": 0, "request_count": 217, "work_item_count": 217},
        source="statscrew",
        dataset="team_season_stats",
        shard=0,
    )
    assert reason and "ZERO records" in reason and "parser branch" in reason


def test_an_empty_work_list_is_a_census_failure_not_an_empty_harvest() -> None:
    reason = harvest_failure_reason(
        {"record_count": 0, "request_count": 0, "work_item_count": 0},
        source="statscrew",
        dataset="team_season_roster",
        shard=1,
    )
    assert reason and "census failure" in reason


def test_a_real_capture_passes() -> None:
    assert (
        harvest_failure_reason(
            {"record_count": 17132, "request_count": 502, "work_item_count": 502},
            source="statscrew",
            dataset="team_season_roster",
            shard=0,
        )
        is None
    )


def test_total_robots_denial_is_reported_as_a_block_not_an_empty_census() -> None:
    """Shard 1's exact signature. Reporting this as 'nothing to do' hid a live gap."""
    reason = census_failure_reason(
        {"requests": 477, "work_items": 0, "request_status": {"robots_denied": 477}},
        source="statscrew",
        dataset="team_season_roster",
        shard=1,
    )
    assert reason and "block" in reason


def test_requests_without_work_items_fail_whatever_the_status_mix() -> None:
    reason = census_failure_reason(
        {"requests": 300, "work_items": 0, "request_status": {"ok": 300}},
        source="statscrew",
        dataset="team_season_stats",
        shard=2,
    )
    assert reason and "ZERO work items" in reason


def test_a_healthy_census_passes() -> None:
    assert (
        census_failure_reason(
            {"requests": 502, "work_items": 502, "request_status": {"ok": 502}},
            source="statscrew",
            dataset="team_season_roster",
            shard=0,
        )
        is None
    )
