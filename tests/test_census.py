from __future__ import annotations

import json
from pathlib import Path

from harvest.census import crawl_fixture, expand_seed_spec, run_census
from harvest.classify import classify_surface, schema_fingerprint
from harvest.http import Response

FIXTURES = Path(__file__).parent / "fixtures"


def test_census_stays_in_approved_namespace_and_deduplicates() -> None:
    pages = {
        "https://www.nfl.com/sitemap/html/rosters/1921/dayton-triangles": """
            <a href='/sitemap/html/rosters/1921/dayton-triangles'>self</a>
            <a href='/sitemap/html/rosters/1921/chicago-staleys'>next</a>
            <a href='/news/unrelated'>news</a>
            <a href='https://example.com/escape'>escape</a>
        """,
        "https://www.nfl.com/sitemap/html/rosters/1921/chicago-staleys": "<p>done</p>",
    }
    result = crawl_fixture(
        "https://www.nfl.com/sitemap/html/rosters/1921/dayton-triangles",
        pages,
        domains={"www.nfl.com"},
        allowed_prefixes=("/sitemap/html/rosters/",),
    )
    assert [row["url"] for row in result] == sorted(pages)


def test_surface_classifier_distinguishes_witness_utility() -> None:
    roster = [{"player": "Herb Sies", "team": "Dayton Triangles", "season": "1921"}]
    assert classify_surface(roster, identifiers={"player", "team", "season"}) == "canonical_candidate"
    assert classify_surface([{"player": "Herb Sies", "season": "1921"}], identifiers={"player", "season"}) == "identity_only"
    assert classify_surface([{"story": "Sies scored"}], identifiers=set()) == "context_only"
    assert classify_surface([], identifiers=set()) == "unusable"
    assert schema_fingerprint(roster) == schema_fingerprint(list(reversed(roster)))


def test_census_uses_url_context_to_classify_roster_as_canonical(tmp_path) -> None:
    url = "https://www.nfl.com/sitemap/html/rosters/1921/dayton-triangles"
    html = """<table><tr><th>Player</th></tr><tr><td><a href='/players/herb-sies/'>Herb Sies</a></td></tr></table>"""

    def fetch(requested: str) -> Response:
        assert requested == url
        return Response(url, 200, "ok", html.encode(), "text/html")

    report = run_census(
        source="nflcom",
        dataset="team_season_roster",
        seeds=[url],
        output_dir=tmp_path,
        domains={"www.nfl.com"},
        allowed_prefixes=("/sitemap/html/rosters/",),
        fetch=fetch,
        max_pages=1,
        shard_id=0,
        shard_count=1,
        artifact_run_id="fixture",
        obey_robots=False,
    )
    assert report["usability"] == {"canonical_candidate": 1}


def test_hierarchical_census_assigns_every_discovery_branch_to_one_shard(tmp_path) -> None:
    seed = "https://www.nfl.com/sitemap/html/rosters/"
    year_pages = {
        f"{seed}1921/": ["dayton-triangles", "chicago-staleys"],
        f"{seed}1922/": ["akron-pros", "buffalo-all-americans"],
    }
    pages = {
        seed: "".join(f'<a href="{url}">{url}</a>' for url in year_pages),
    }
    for year_url, teams in year_pages.items():
        pages[year_url] = "".join(f'<a href="{team}">{team}</a>' for team in teams)
        for team in teams:
            pages[f"{year_url}{team}"] = "<table><tr><th>Player</th></tr><tr><td>Example Player</td></tr></table>"

    def fetch(url: str) -> Response:
        return Response(url, 200, "ok", pages[url].encode(), "text/html")

    found: list[dict] = []
    for shard in range(3):
        out = tmp_path / str(shard)
        run_census(
            source="nflcom",
            dataset="team_season_roster",
            seeds=[seed],
            output_dir=out,
            domains={"www.nfl.com"},
            allowed_prefixes=("/sitemap/html/rosters/",),
            fetch=fetch,
            max_pages=100,
            shard_id=shard,
            shard_count=3,
            artifact_run_id="fixture",
            obey_robots=False,
        )
        found.extend(json.loads(line) for line in (out / "WORK_ITEMS.jsonl").read_text().splitlines())

    expected_urls = {f"{year_url}{team}" for year_url, teams in year_pages.items() for team in teams}
    assert {item["url"] for item in found} == expected_urls
    assert len(found) == len(expected_urls)


def test_seed_spec_expands_year_ranges_and_inventory(tmp_path) -> None:
    inventory = tmp_path / "team_seasons.csv"
    inventory.write_text("year,team\n1921,DAY\n1922,AKR\n", encoding="utf-8")
    assert expand_seed_spec(
        {
            "first_year": 1920,
            "last_year": 2025,
            "census_seed_ranges": [
                {"first_year": 1920, "last_year": 1921, "template": "https://example.test/{year}/"}
            ],
        },
        year_start=1921,
        year_end=1922,
        catalog_dir=tmp_path,
    ) == ["https://example.test/1921/"]
    assert expand_seed_spec(
        {
            "first_year": 1920,
            "last_year": 2025,
            "seed_inventory": "team_seasons.csv",
            "census_seed_template": "https://example.test/{team}/{year}",
        },
        year_start=1921,
        year_end=1921,
        catalog_dir=tmp_path,
    ) == ["https://example.test/DAY/1921"]


def test_census_uses_source_adapter_to_classify_pfa_lineups(tmp_path) -> None:
    url = "https://www.profootballarchives.com/nflboxscores1/1921apfa035.html"
    html = (FIXTURES / "pfa_boxscore.html").read_bytes()

    report = run_census(
        source="profootballarchives",
        dataset="player_game_participation",
        seeds=[url],
        output_dir=tmp_path,
        domains={"www.profootballarchives.com"},
        allowed_prefixes=("/nflboxscores",),
        fetch=lambda requested: Response(requested, 200, "ok", html, "text/html"),
        max_pages=1,
        shard_id=0,
        shard_count=1,
        artifact_run_id="fixture",
        obey_robots=False,
    )
    assert report["usability"] == {"canonical_candidate": 1}


def test_dataset_page_is_a_crawl_leaf(tmp_path) -> None:
    url = "https://www.nfl.com/sitemap/html/rosters/1921/dayton-triangles"
    next_url = "https://www.nfl.com/sitemap/html/rosters/1922/dayton-triangles"
    html = f"<a href='{next_url}'>next</a><table><tr><th>Player</th></tr><tr><td><a href='/players/herb-sies/'>Herb Sies</a></td></tr></table>"
    requested: list[str] = []

    def fetch(requested_url: str) -> Response:
        requested.append(requested_url)
        return Response(requested_url, 200, "ok", html.encode(), "text/html")

    run_census(
        source="nflcom",
        dataset="team_season_roster",
        seeds=[url],
        output_dir=tmp_path,
        domains={"www.nfl.com"},
        allowed_prefixes=("/sitemap/html/rosters/",),
        fetch=fetch,
        max_pages=10,
        shard_id=0,
        shard_count=1,
        artifact_run_id="fixture",
        obey_robots=False,
    )
    assert requested == [url]
