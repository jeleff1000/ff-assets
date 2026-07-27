"""StatsCrew multi-table capture: the tags, the columns, and the cells nobody counted.

Every assertion here is anchored to a shape MEASURED on retained bytes (the discovery
artifacts of run 30224337692, pages `/football/stats/t-CLE/y-1963`,
`/football/results/t-CLE/y-1963`, `/football/stats/p-brownjim001`), not to a shape
imagined for the test.
"""

from __future__ import annotations

import pytest

from harvest.sources import get_adapter
from harvest.sources.common import parse_table_views, unique_keys

STATS_PAGE = """
<html><body>
<h2>Passing:</h2>
<div class="stat-table"><table class="sortable">
<thead><tr>
<th class="dt-left">Player</th>
<th><a title="Attempts">Att</a></th>
<th><a title="Completions">Comp</a></th>
<th><a title="Completion Percentage">Comp %</a></th>
<th><a title="PassingTouchdowns">TDs</a></th>
<th><a title="Touchdown Percentage">TD %</a></th>
</tr></thead>
<tbody>
<td class="dt-left"><a href="https://www.statscrew.com/football/stats/p-ryanfra001">Frank Ryan</a></td>
<td>256</td><td>135</td><td>52.7</td><td>25</td><td>9.8</td>
<td class="dt-left">Totals</td><td>322</td><td>164</td><td>50.9</td><td>27</td><td>8.4</td>
</tbody></table></div>

<h2>Rushing:</h2>
<div class="stat-table"><table class="sortable">
<thead><tr>
<th class="dt-left">Player</th>
<th><a title="Attempts">No</a></th>
<th><a title="Yards">Yds</a></th>
<th><a title="Average">Avg</a></th>
<th><a title="Long Run">Long</a></th>
<th><a title="RushingTouchdowns">TDs</a></th>
</tr></thead>
<tbody>
<td class="dt-left"><a href="https://www.statscrew.com/football/stats/p-brownjim001">Jim Brown</a></td>
<td>291</td><td>1863</td><td>6.4</td><td>80</td><td>12</td>
</tbody></table></div>

<h2>Receiving:</h2>
<div class="stat-table"><table class="sortable">
<thead><tr>
<th class="dt-left">Player</th>
<th><a title="Receptions">No</a></th>
<th><a title="Yards">Yds</a></th>
<th><a title="Average">Avg</a></th>
<th><a title="Long Reception">Long</a></th>
<th><a title="ReceivingTouchdowns">TDs</a></th>
</tr></thead>
<tbody>
<td class="dt-left"><a href="https://www.statscrew.com/football/stats/p-colligar001">Gary Collins</a></td>
<td>43</td><td>674</td><td>15.7</td><td>62</td><td>13</td>
</tbody></table></div>
</body></html>
"""

RESULTS_PAGE = """
<html><body>
<h2>1963 Cleveland Browns Game-by-Game Results</h2>
<div class="stat-table"><table class="sortable">
<thead><tr>
<th>Date</th><th>Game</th><th>Res</th><th>Home</th><th>Road</th><th>Record</th><th></th><th></th>
</tr></thead>
<tbody>
<tr class = reg_season>
<td>September 15, 1963</td>
<td><a href="https://www.statscrew.com/football/results/t-WAS/y-1963">Washington Redskins</a> 14 at
    <a href="https://www.statscrew.com/football/results/t-CLE/y-1963">Cleveland Browns</a> 37</td>
<td>W</td><td>1-0-0</td><td>0-0-0</td><td>1-0-0</td><td>boxscore</td><td>recap</td>
</tr>
<tr class = post_season>
<td>December 29, 1963</td>
<td><a href="https://www.statscrew.com/football/results/t-CLE/y-1963">Cleveland Browns</a> 10 at
    <a href="https://www.statscrew.com/football/results/t-GNB/y-1963">Green Bay Packers</a> 40</td>
<td>L</td><td>1-0-0</td><td>0-1-0</td><td>1-1-0</td><td>boxscore</td><td>recap</td>
</tr>
</tbody></table></div>
</body></html>
"""

STATS_WORK = {
    "key": "1963|CLE",
    "season": 1963,
    "team": "CLE",
    "url": "https://www.statscrew.com/football/stats/t-CLE/y-1963",
}
RESULTS_WORK = {
    "key": "1963|CLE",
    "season": 1963,
    "team": "CLE",
    "url": "https://www.statscrew.com/football/results/t-CLE/y-1963",
}


def _statscrew():
    return get_adapter("statscrew")


def test_identical_headers_stay_separable_by_caption() -> None:
    """Rushing and receiving publish the SAME header set. Without the caption their rows
    are indistinguishable after capture, and five such tables share one shape on a real
    page -- so a header signature cannot tag them and the table index moves per page."""
    rows = _statscrew().parse("team_season_stats", STATS_PAGE, STATS_WORK)
    rushing = [r for r in rows if r["table_tag"] == "rushing"]
    receiving = [r for r in rows if r["table_tag"] == "receiving"]
    assert {"passing", "rushing", "receiving"} == {r["table_tag"] for r in rows}
    assert rushing[0]["player"] == "Jim Brown"
    assert receiving[0]["player"] == "Gary Collins"
    # same stored column, different meaning -- which is the whole reason for the tag
    assert rushing[0]["yds"] == "1863"
    assert receiving[0]["yds"] == "674"


def test_every_row_carries_its_native_player_id() -> None:
    rows = _statscrew().parse("team_season_stats", STATS_PAGE, STATS_WORK)
    ryan = next(r for r in rows if r["player"] == "Frank Ryan")
    assert ryan["source_player_id"] == "ryanfra001"
    assert ryan["season"] == 1963 and ryan["team"] == "CLE"


def test_totals_row_is_flagged_not_silently_mixed_with_players() -> None:
    """The Totals line is a real published row, but summing it with the player rows
    double-counts a whole team-season. Keep it, label it."""
    rows = _statscrew().parse("team_season_stats", STATS_PAGE, STATS_WORK)
    totals = [r for r in rows if r["is_total_row"]]
    assert len(totals) == 1
    assert totals[0]["table_tag"] == "passing" and totals[0]["att"] == "322"
    assert all(r["source_player_id"] for r in rows if not r["is_total_row"])


def test_short_labels_are_a_trap_and_the_titles_are_captured() -> None:
    """`tds` is PassingTouchdowns and `td` is Touchdown PERCENTAGE; `comp` and `comp_2`
    are Completions and Completion Percentage. Storing the numbers without the source's
    own titles stores values whose meaning is a guess -- the nflcom polysemy, re-created
    on a new source."""
    entries = _statscrew().column_dictionary("team_season_stats", STATS_PAGE, STATS_WORK)
    passing = {e["column_key"]: e["source_title"] for e in entries if e["table_tag"] == "passing"}
    assert passing["tds"] == "PassingTouchdowns"
    assert passing["td"] == "Touchdown Percentage"
    assert passing["comp"] == "Completions"
    assert passing["comp_2"] == "Completion Percentage"
    # and the same short label means different things under different captions
    by_tag = {(e["table_tag"], e["column_key"]): e["source_title"] for e in entries}
    assert by_tag[("rushing", "no")] == "Attempts"
    assert by_tag[("receiving", "no")] == "Receptions"


def test_duplicate_and_blank_headers_never_lose_a_cell() -> None:
    """Two columns normalising to one key silently overwrote each other: the results
    page ships two trailing blank <th>s and `Comp` / `Comp %` both reduce to `comp`."""
    keys, blanks, duplicates = unique_keys(["date", "", "comp", "comp", ""])
    assert keys == ["date", "col_1", "comp", "comp_2", "col_4"]
    assert (blanks, duplicates) == (2, 1)
    view = parse_table_views(RESULTS_PAGE)[0]
    assert view.blank_headers == 2
    assert len(view.headers) == 8 == len(set(view.headers))
    assert all(len(row) == 8 for row in view.rows)


def test_results_capture_the_season_type_the_source_declares() -> None:
    rows = _statscrew().parse("team_season_results", RESULTS_PAGE, RESULTS_WORK)
    assert [r["row_class"] for r in rows] == ["reg_season", "post_season"]
    assert rows[0]["res"] == "W" and rows[1]["res"] == "L"
    assert rows[0]["record"] == "1-0-0"


def test_both_teams_survive_a_single_game_cell() -> None:
    """One `game` cell links BOTH teams. Keeping a single href drops an opponent
    identifier on every game row of every results page."""
    rows = _statscrew().parse("team_season_results", RESULTS_PAGE, RESULTS_WORK)
    assert rows[0]["game_team_ids"] == "CLE WAS"
    assert rows[1]["game_team_ids"] == "CLE GNB"


def test_player_page_rows_take_their_own_season_not_the_page_s() -> None:
    page = """
    <html><body><h2>Rushing:</h2><table class="sortable">
    <thead><tr><th>Year</th><th>Team</th><th><a title="Attempts">No</a></th>
    <th><a title="Yards">Yds</a></th></tr></thead>
    <tbody><tr><td>1957</td><td>Cleveland Browns</td><td>202</td><td>942</td></tr>
    <tr><td>1958</td><td>Cleveland Browns</td><td>257</td><td>1527</td></tr></tbody>
    </table></body></html>
    """
    work = get_adapter("statscrew").work_item_from_url(
        "player_season_stats", "https://www.statscrew.com/football/stats/p-brownjim001"
    )
    assert work["season"] is None, "a career page has no single season to invent"
    rows = get_adapter("statscrew").parse("player_season_stats", page, work)
    assert [r["season"] for r in rows] == [1957, 1958]
    assert all(r["source_player_id"] == "brownjim001" for r in rows)
    assert all(r["table_tag"] == "rushing" for r in rows)
    # the source's own Team column is preserved under a non-colliding key
    assert rows[0]["stat_team"] == "Cleveland Browns"


def test_captioned_tables_are_never_dropped_for_looking_uninteresting() -> None:
    """"Playing Career" reads like page furniture and is year / league / team / GP / GS
    -- a tenure witness carrying the LEAGUE label the defunct-league question turns on."""
    page = """
    <html><body><h2>Playing Career:</h2><table class="sortable">
    <thead><tr><th>Year</th><th>League</th><th>Team</th><th>GP</th><th>GS</th></tr></thead>
    <tbody><tr><td>1957</td><td>NFL</td><td>Cleveland Browns</td><td>12</td><td>12</td></tr>
    </tbody></table></body></html>
    """
    work = get_adapter("statscrew").work_item_from_url(
        "player_season_stats", "https://www.statscrew.com/football/stats/p-brownjim001"
    )
    rows = get_adapter("statscrew").parse("player_season_stats", page, work)
    assert len(rows) == 1
    assert rows[0]["table_tag"] == "playing_career"
    assert rows[0]["league"] == "NFL" and rows[0]["gp"] == "12"


@pytest.mark.parametrize("dataset", ["team_season_stats", "team_season_results", "player_season_stats"])
def test_a_page_with_nothing_usable_raises_rather_than_returning_empty(dataset: str) -> None:
    """Silence is the failure mode this whole slice exists to remove: the runner turns a
    raise into a recorded parse_error, and returning [] would look like a clean page."""
    work = {"key": "k", "season": 1963, "team": "CLE", "url": "https://www.statscrew.com/x"}
    with pytest.raises(ValueError):
        get_adapter("statscrew").parse(dataset, "<html><body>blocked</body></html>", work)


GROUP_HEADER_PAGE = """
<html><body>
<h2>Total Scoring:</h2>
<table class="sortable">
<thead>
<tr><th></th><th colspan=3>Touchdowns</th><th colspan=2>Other</th><th>Total</th></tr>
<tr><th>Player</th>
<th><a title="Rushing Touchdowns">Rush</a></th>
<th><a title="Receiving Touchdowns">Rec</a></th>
<th><a title="Interception Return Touchdowns">Int</a></th>
<th><a title="Field Goals">FG</a></th>
<th><a title="Safeties">Saf</a></th>
<th><a title="Total Points">Points</a></th></tr>
</thead>
<tbody>
<td><a href="https://www.statscrew.com/football/stats/p-brownjim001">Jim Brown</a></td>
<td>12</td><td>3</td><td>0</td><td>0</td><td>0</td><td>90</td>
</tbody></table></body></html>
"""


def test_a_colspan_group_header_does_not_shred_the_data_rows() -> None:
    """MEASURED on /football/stats/t-CLE/y-1963. `Total Scoring` stacks a colspan group
    row over the real column names. Taking the first <th> row gave width 4, segmented
    15-cell rows into rows of four, and produced 39 fabricated rows from 11 players with
    every value under the wrong name -- the nflcom column shift, on a new source, caught
    before the harvest rather than 7.78M rows afterwards."""
    view = parse_table_views(GROUP_HEADER_PAGE)[0]
    assert view.header_rows == 2
    assert view.headers == ["player", "rush", "rec", "int", "fg", "saf", "points"]
    assert view.dropped_partial_cells == 0
    assert len(view.rows) == 1
    row = {k: c["text"] for k, c in view.rows[0].items()}
    assert row == {"player": "Jim Brown", "rush": "12", "rec": "3", "int": "0",
                   "fg": "0", "saf": "0", "points": "90"}


def test_the_group_label_is_kept_because_it_carries_meaning() -> None:
    """`Int` under Touchdowns is interception-return TOUCHDOWNS, not interceptions."""
    view = parse_table_views(GROUP_HEADER_PAGE)[0]
    assert view.column_groups["int"] == "Touchdowns"
    assert view.column_groups["fg"] == "Other"
    entries = _statscrew().column_dictionary("team_season_stats", GROUP_HEADER_PAGE, STATS_WORK)
    by_key = {e["column_key"]: e for e in entries}
    assert by_key["int"]["column_group"] == "Touchdowns"
    assert by_key["int"]["source_title"] == "Interception Return Touchdowns"


def test_a_same_width_header_stack_is_not_mistaken_for_a_group() -> None:
    """PFA boxscores open `<th colspan=3>LINEUPS</th>` over a same-width team-name row.
    Only a row whose colspans expand to exactly the NEXT row's width is a group header,
    so this shape keeps its original single-row reading."""
    html = ("<table><tr><th colspan='3'>LINEUPS</th></tr>"
            "<tr><th colspan='3'>Dayton Triangles</th></tr>"
            "<tr><td>23</td><td>ROG</td><td>Herb Sies</td></tr></table>")
    view = parse_table_views(html)[0]
    assert view.header_rows == 1
    assert view.headers == ["lineups"]
