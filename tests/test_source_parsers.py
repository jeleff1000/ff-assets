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
