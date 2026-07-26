from __future__ import annotations

from pathlib import Path

import pytest

from harvest.sources import get_adapter

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("source", "dataset", "fixture", "work", "expected"),
    [
        (
            "nflcom",
            "team_season_roster",
            "nflcom_roster.html",
            {"season": 1921, "team": "dayton-triangles", "url": "https://www.nfl.com/sitemap/html/rosters/1921/dayton-triangles"},
            ("Herb Sies", "herb-sies"),
        ),
        (
            "profootballarchives",
            "player_game_participation",
            "pfa_boxscore.html",
            {"season": 1921, "team": "DAY", "game_id": "1921apfa035", "url": "https://www.profootballarchives.com/nflboxscores1/1921apfa035.html"},
            ("Herb Sies", "SiesHe20"),
        ),
        (
            "statscrew",
            "team_season_roster",
            "statscrew_roster.html",
            {"season": 1922, "team": "AKR", "url": "https://www.statscrew.com/football/roster/t-AKR/y-1922"},
            ("Jim Flower", "flowejim001"),
        ),
    ],
)
def test_source_parser_emits_normalized_identity(
    source: str, dataset: str, fixture: str, work: dict, expected: tuple[str, str]
) -> None:
    adapter = get_adapter(source)
    rows = adapter.parse(dataset, (FIXTURES / fixture).read_text(encoding="utf-8"), work)
    assert len(rows) == 1
    assert (rows[0]["player"], rows[0]["source_player_id"]) == expected
    assert rows[0]["source"] == source
    assert rows[0]["dataset"] == dataset
    assert rows[0]["season"] == work["season"]
    assert rows[0]["source_url"] == work["url"]


def test_parser_rejects_empty_surface() -> None:
    adapter = get_adapter("nflcom")
    with pytest.raises(ValueError, match="no usable roster rows"):
        adapter.parse(
            "team_season_roster",
            "<html><body>blocked</body></html>",
            {"season": 1921, "team": "dayton", "url": "https://www.nfl.com/x"},
        )


def test_pfa_parses_colspan_lineup_table() -> None:
    html = """
    <table><tr><th colspan="3">LINEUPS</th></tr>
    <tr><th colspan="3">Dayton Triangles</th></tr>
    <tr><td>23</td><td>ROG/RDG</td><td><a href="/players/s/sies00200.html">Herb Sies</a></td></tr>
    <tr><th colspan="3">Canton Bulldogs</th></tr>
    <tr><td>2</td><td>ROG/RDG</td><td><a href="/players/o/osbo00400.html">Duke Osborn</a></td></tr>
    </table>
    """
    rows = get_adapter("profootballarchives").parse(
        "player_game_participation",
        html,
        {"season": 1921, "game_id": "1921apfa035", "url": "https://www.profootballarchives.com/x"},
    )
    assert [(row["team"], row["player"], row["source_player_id"]) for row in rows] == [
        ("Dayton Triangles", "Herb Sies", "sies00200"),
        ("Canton Bulldogs", "Duke Osborn", "osbo00400"),
    ]


def test_rows_without_tr_wrappers_are_recovered():
    """StatsCrew serves team-season stat tables as "<tbody><td>...</td><td>...</td>"
    with NO <tr> wrappers. A parser that only opens a row on <tr> silently discards
    every player row and keeps the one well-formed Totals line -- a 1,084-page harvest
    would have captured totals and nothing else, with no error anywhere. Browsers infer
    the rows from the header width; so do we.
    """
    from harvest.sources.common import parse_tables

    html = (
        "<table><thead><tr><th>Player</th><th>Yds</th></tr></thead>"
        "<tbody>"
        "<td><a href='/football/stats/p-ryanfra001'>Frank Ryan</a></td><td>2026</td>"
        "<td><a href='/football/stats/p-brownjim001'>Jim Brown</a></td><td>996</td>"
        "</tbody></table>"
    )
    tables = parse_tables(html)
    assert len(tables) == 1
    rows = tables[0]
    assert len(rows) == 2, "both unwrapped rows must be recovered"
    assert rows[0]["player"]["text"] == "Frank Ryan"
    assert rows[0]["player"]["href"].endswith("p-ryanfra001")
    assert rows[1]["yds"]["text"] == "996"


def test_trailing_partial_row_is_dropped_not_padded():
    """A partial run at the end must be dropped. Padding it would invent cells, which
    is worse than reporting one fewer row."""
    from harvest.sources.common import parse_tables

    html = ("<table><thead><tr><th>Player</th><th>Yds</th></tr></thead>"
            "<tbody><td>A</td><td>1</td><td>B</td></tbody></table>")
    rows = parse_tables(html)[0]
    assert len(rows) == 1 and rows[0]["player"]["text"] == "A"


def test_well_formed_rows_still_parse_unchanged():
    from harvest.sources.common import parse_tables

    html = ("<table><tr><th>Player</th><th>Yds</th></tr>"
            "<tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></table>")
    rows = parse_tables(html)[0]
    assert [r["player"]["text"] for r in rows] == ["A", "B"]
