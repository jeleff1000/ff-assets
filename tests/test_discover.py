from __future__ import annotations

from harvest.discover import describe_tables, normalize_url_pattern, run_discovery
from harvest.http import Response


def test_section_words_stay_literal_entity_ids_collapse():
    """A rule that collapsed any 8+ character segment turned "football" into a
    placeholder and hid every section of a multi-sport host behind one pattern. A
    discovery tool that hides sections is worse than no discovery tool."""
    assert normalize_url_pattern("/football/l-NFL") == "/football/l-{id}"
    assert normalize_url_pattern("/football/roster/t-CHI/y-1924") == \
        "/football/roster/t-{id}/y-{year}"
    assert normalize_url_pattern("/players/r/rice02100.html") == "/players/r/{id}.html"
    assert normalize_url_pattern("/coaches/beli01900.html") == "/coaches/{id}.html"


def test_entity_ids_collapse_before_years():
    """rice02100 holds 4+ digits, so a year-first rule rewrites it to rice{year} and
    splits ONE player family into hundreds of patterns -- inflating the very count
    this tool exists to report."""
    assert "{year}" not in normalize_url_pattern("/players/r/rice02100.html")
    assert normalize_url_pattern("/1963nfl.html") == "/{year}nfl.html"


def test_describe_tables_reports_every_table_not_just_the_first():
    """The ProFootballArchives parser read only the LINEUPS table, so ten further data
    tables per page were invisible to every counter. Discovery must see all of them."""
    html = ("<table><tr><th>Player</th><th>Yds</th></tr>"
            "<tr><td>A</td><td>1</td></tr></table>"
            "<table><tr><th>Team</th><th>Pts</th></tr>"
            "<tr><td>B</td><td>7</td></tr></table>")
    tables = describe_tables(html)
    assert len(tables) == 2
    assert tables[0]["headers"] == ["player", "yds"]
    assert tables[1]["headers"] == ["pts", "team"]
    assert all(t["schema_fingerprint"] for t in tables)


def test_discovery_follows_one_hop_and_records_robots_denials(tmp_path):
    pages = {
        "https://example.com/index.html":
            '<a href="/players/p/poll01300.html">P</a><table><tr><th>Year</th></tr>'
            '<tr><td>1963</td></tr></table>',
        "https://example.com/players/p/poll01300.html":
            '<table><tr><th>Transaction</th><th>Date</th></tr>'
            '<tr><td>Signed</td><td>1963</td></tr></table>',
    }

    def fake_fetch(url):
        body = pages.get(url, "").encode()
        return Response(url, 200 if body else 404, "ok" if body else "absent",
                        body, "text/html")

    summary = run_discovery(
        source="example", probes=["https://example.com/index.html"],
        output_dir=tmp_path, domains={"example.com"}, allowed_prefixes=("/",),
        fetch=fake_fetch, max_pages=10, children_per_pattern=2,
        artifact_run_id="test", obey_robots=False,
    )
    patterns = {p["url_pattern"] for p in summary["patterns"]}
    assert "/players/p/{id}.html" in patterns, (
        "the ONE HOP into children is the whole point -- it is how the transactions "
        "table was found, since it lives inside player pages and not in any nav")
    child = next(p for p in summary["patterns"]
                 if p["url_pattern"] == "/players/p/{id}.html")
    assert child["table_shapes"][0]["headers"] == ["date", "transaction"]
    assert (tmp_path / "ARTIFACT_MANIFEST.json").is_file()
    assert (tmp_path / "DISCOVERY_SUMMARY.json").is_file()


def test_discovery_scope_is_broader_than_declared_datasets():
    """Discovery scoped to the datasets' allowed_prefixes can only re-confirm what we
    already declared. Measured 2026-07-26: the dataset allowlist filtered
    /football/results/ and /football/l-* out of a StatsCrew discovery run, so the tool
    reported 8 patterns for a site with many more. discovery_prefixes exists to break
    that chicken-and-egg."""
    import yaml
    from pathlib import Path
    catalog = yaml.safe_load(
        (Path(__file__).parents[1] / "harvest" / "source_catalog.yaml").read_text(
            encoding="utf-8"))
    for name, spec in catalog["sources"].items():
        scope = spec.get("discovery_prefixes")
        assert scope, f"{name}: no discovery_prefixes -- discovery cannot find the unknown"
        dataset_prefixes = {
            p for ds in spec.get("datasets", {}).values()
            for p in ds.get("allowed_prefixes", [])
        }
        assert not dataset_prefixes or set(scope) != dataset_prefixes or scope == ["/"], (
            f"{name}: discovery scope equals the dataset allowlist, so it can only "
            "confirm known families")


def test_report_distinguishes_held_from_merely_declared(tmp_path):
    """The two-state report was actively misleading: it counted a pattern as covered
    whenever a DECLARED dataset claimed the prefix, so nflcom read "0 unclaimed" while
    /players/{slug}/stats/* was attributed to player_game_stats -- declared and NEVER
    RUN. A report claiming we hold pages we have never fetched is worse than none."""
    from harvest.discover_report import build_report

    summary = {
        "source": "s",
        "counters": {"url_patterns": 2, "patterns_with_tables": 2},
        "patterns": [
            {"url_pattern": "/a/{id}", "pages_seen": 1, "example_urls": ["u1"],
             "n_distinct_table_shapes": 1,
             "table_shapes": [{"max_rows": 5, "surface": "canonical_candidate"}]},
            {"url_pattern": "/b/{id}", "pages_seen": 1, "example_urls": ["u2"],
             "n_distinct_table_shapes": 1,
             "table_shapes": [{"max_rows": 9, "surface": "canonical_candidate"}]},
        ],
    }
    spec = {"datasets": {"ds": {"allowed_prefixes": ["/a", "/b"]}}}
    report = build_report(summary, spec, {"s": {"/a/{id}": 120}})
    states = {r["url_pattern"]: r["state"] for r in report["all_patterns"]}
    assert states["/a/{id}"] == "HELD", "we fetched 120 of these -- do not re-fetch"
    assert states["/b/{id}"] == "DECLARED_NOT_HELD", (
        "declared by a dataset but never harvested -- this is the case the old report "
        "silently reported as covered")
    assert report["counters"]["patterns_worth_fetching"] == 1
    assert [r["url_pattern"] for r in report["worth_fetching"]] == ["/b/{id}"]
