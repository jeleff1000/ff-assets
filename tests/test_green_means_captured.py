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


def test_robots_is_fetched_through_the_paced_client_not_urllibs_own_opener() -> None:
    """`RobotFileParser.read()` opens the URL itself -- no User-Agent, no pacing, no
    retry -- and urllib treats a 403 on robots.txt as DISALLOW ALL. Five jobs starting
    together fetched five robots.txt in the same instant, which is the one burst the
    per-host pacing never covered, because the pacing lives in the client that call
    bypassed. It cancelled a whole shard twice, in runs 30226751762 and 30232968983.
    """
    from harvest.census import read_robots
    from harvest.http import Response

    calls: list[str] = []

    def ok(url: str) -> Response:
        calls.append(url)
        return Response(url, 200, "ok", b"User-agent: *\nDisallow: /private\n", "text/plain")

    parser = read_robots("https://example.com/robots.txt", ok)
    assert calls == ["https://example.com/robots.txt"], "must go through OUR client"
    assert parser.can_fetch("ff-assets-historical-witness", "https://example.com/football/x")
    assert not parser.can_fetch("ff-assets-historical-witness", "https://example.com/private/x")


def test_a_transient_robots_failure_does_not_cancel_a_shard() -> None:
    """A 500 or a network error is not permission to crawl being withdrawn."""
    from harvest.census import read_robots
    from harvest.http import Response

    for status_code, status in ((500, "retryable"), (404, "absent"), (0, "network_error")):
        parser = read_robots(
            "https://example.com/robots.txt",
            lambda url, c=status_code, s=status: Response(url, c, s, b"", ""),
        )
        assert parser.can_fetch("ff-assets-historical-witness", "https://example.com/x"), (
            f"status {status_code} must not disallow everything")


def test_a_real_403_still_disallows_because_politeness_is_not_negotiable() -> None:
    from harvest.census import read_robots
    from harvest.http import Response

    parser = read_robots(
        "https://example.com/robots.txt",
        lambda url: Response(url, 403, "blocked", b"", ""),
    )
    assert not parser.can_fetch("ff-assets-historical-witness", "https://example.com/x")


def test_a_wedged_shard_fails_fast_instead_of_burning_the_job_timeout() -> None:
    """MEASURED 2026-07-27: team_season_results shard 0 ran 5h50m on a 504-seed partition
    and was CANCELLED at the job limit, while its siblings did 468-489 seeds in ~17
    MINUTES each. Three percent more work, twenty times the time -- so it was never a size
    problem and re-slicing would not have fixed it. Retries escalate (30/60/90s) and
    nothing bounded the total. Cancelled is not failed, so it did not even trip the
    zero-record gate cleanly.
    """
    import time

    from harvest.http import BudgetExhausted, HttpClient

    client = HttpClient(delay_seconds=0.0, budget_seconds=0.05)
    time.sleep(0.06)
    try:
        client.fetch("https://example.com/anything")
    except BudgetExhausted as exc:
        assert "budget exhausted" in str(exc)
    else:
        raise AssertionError("a shard past its budget must raise, not keep retrying")


def test_slice_size_and_request_rate_are_independent_knobs() -> None:
    """The fix for a wedged shard is a budget; the fix for its BLAST RADIUS is smaller
    slices. Conflating the two is how "15 runners" becomes 15 req/s at a small independent
    archive -- which is the thing that got us blocked twice."""
    from pathlib import Path

    from harvest.plan_matrix import max_parallel_for, plan

    catalog = Path(__file__).parents[1] / "harvest" / "source_catalog.yaml"
    fine = plan(only_dataset="team_season_results", shards="15")
    assert len(fine["include"]) == 15
    assert max_parallel_for(fine, catalog) == max_parallel_for(
        plan(only_dataset="team_season_results"), catalog
    ), "raising the shard count must not raise the per-host rate"
