from __future__ import annotations

import re

from .common import TableView, normalize_header, parse_table_views, roster_rows

SOURCE = "statscrew"

# Datasets whose pages carry MANY tables. Their rows are meaningless without a table
# tag: `avg long no player tds yds` is the header of rushing, receiving, punt returns,
# kick returns AND interceptions on the same page, and the table's position moves
# between pages, so neither the header signature nor the index identifies the table.
MULTI_TABLE_DATASETS = frozenset({"team_season_stats", "player_season_stats"})

# NOTHING is skipped by caption. "Playing Career" looked like page furniture and is in
# fact year / league / team / GP / GS -- a tenure witness carrying the LEAGUE label that
# the defunct-league question turns on. Dropping tables because they look uninteresting
# is the exact failure this lane exists to undo.

_PLAYER_ID = re.compile(r"/football/stats/p-([a-z0-9]+)", re.I)
_TEAM_SEASON = re.compile(r"/football/(roster|stats|results)/t-([^/?#]+)/y-(\d{4})", re.I)

# Reserved row keys. A source column that normalises onto one of these would overwrite
# our provenance, so `_reserve` renames the SOURCE column rather than losing either.
_RESERVED = frozenset(
    {
        "source",
        "dataset",
        "season",
        "team",
        "game_id",
        "player",
        "source_player_id",
        "source_url",
        "table_tag",
        "table_caption",
        "table_index",
        "row_class",
        "is_total_row",
        "game_team_ids",
    }
)


def _id(href: str) -> str | None:
    match = _PLAYER_ID.search(href or "")
    return match.group(1) if match else None


def _table_tag(view: TableView) -> str | None:
    """The table's OWN caption, normalised. Never the index."""
    if not view.caption:
        return None
    return normalize_header(view.caption) or None


def _reserve(key: str) -> str:
    return f"stat_{key}" if key in _RESERVED else key


def _base_record(dataset: str, view: TableView, work: dict, row_class: str | None) -> dict:
    return {
        "source": SOURCE,
        "dataset": dataset,
        "season": int(work["season"]) if work.get("season") is not None else None,
        "team": work.get("team"),
        "game_id": work.get("game_id"),
        "player": None,
        "source_player_id": work.get("source_player_id"),
        "source_url": work["url"],
        "table_tag": None,
        "table_caption": view.caption,
        "table_index": view.table_index,
        "row_class": row_class,
        "is_total_row": False,
        "game_team_ids": None,
    }


def _row_classes(view: TableView) -> list[str | None]:
    padded = list(view.row_classes)
    padded += [None] * (len(view.rows) - len(padded))
    return padded


def _stat_rows(dataset: str, views: list[TableView], work: dict) -> list[dict]:
    """One TABLE-TAGGED row per data row of every captioned stat table.

    Values keep the source's own text. Typing them here would be inference at capture
    time, and capture parsers are exactly where this program has been burned by
    inference; downstream lanes can type from `table_tag` plus the published column
    title, both of which this capture preserves.
    """
    output: list[dict] = []
    for view in views:
        tag = _table_tag(view)
        if tag is None or not view.rows:
            continue
        for row, row_class in zip(view.rows, _row_classes(view)):
            record = _base_record(dataset, view, work, row_class)
            record["table_tag"] = tag
            player_cell = row.get("player")
            if player_cell:
                record["player"] = player_cell["text"] or None
                record["source_player_id"] = _id(player_cell["href"]) or record["source_player_id"]
            else:
                record["player"] = work.get("player")
            for key, cell in row.items():
                if key == "player":
                    continue
                record[_reserve(key)] = cell["text"] or None
            # On a player page the season and team are ROW attributes; the work item
            # only knows the PAGE's identity, so the row's own values win where present.
            year_cell = row.get("year")
            if year_cell and re.fullmatch(r"\d{4}", (year_cell["text"] or "").strip()):
                record["season"] = int(year_cell["text"].strip())
            team_cell = row.get("team")
            if team_cell and team_cell["text"]:
                record["team"] = team_cell["text"]
            label = (player_cell or row.get(view.headers[0]) or {}).get("text", "")
            record["is_total_row"] = label.strip().lower() in {"totals", "total"}
            output.append(record)
    return output


def _results_rows(dataset: str, views: list[TableView], work: dict) -> list[dict]:
    """The results page is a single UN-captioned table: a team's game calendar with a
    running record, and `<tr class=reg_season|post_season>` carrying the source's own
    season-type declaration -- which no other StatsCrew family publishes."""
    output: list[dict] = []
    for view in views:
        if not {"date", "game"} <= set(view.headers) or not view.rows:
            continue
        for row, row_class in zip(view.rows, _row_classes(view)):
            record = _base_record(dataset, view, work, row_class)
            record["table_tag"] = "results"
            game_cell = row.get("game") or {}
            # Both teams are linked inside the one `game` cell; keeping a single href
            # would drop an opponent identifier on every row.
            teams = {m.group(2) for m in _TEAM_SEASON.finditer(game_cell.get("hrefs", ""))}
            record["game_team_ids"] = " ".join(sorted(teams)) or None
            for key, cell in row.items():
                record[_reserve(key)] = cell["text"] or None
            output.append(record)
    return output


def column_dictionary(dataset: str, html: str, work: dict) -> list[dict]:
    """What the SOURCE PAGE published about each column, captured alongside the values.

    This is the denominator finding 8 said exists nowhere: Law B counts tables and Law C
    counts the columns we STORED, so a column the parser dropped -- or mislabelled --
    appears in no ledger anywhere. StatsCrew hands us the semantics directly
    (`<th><a title="Yards Lost on Sacks">Yds Lost</a>`), and short labels alone are a
    measured trap: in the passing table `tds` is "PassingTouchdowns" while `td` is
    "Touchdown Percentage", and `comp` / `comp_2` are Completions and Completion
    Percentage. Storing the values without this is storing numbers whose meaning is a
    guess -- the nflcom polysemy, re-created on a new source.
    """
    entries: list[dict] = []
    for view in parse_table_views(html):
        tag = _table_tag(view) or ("results" if {"date", "game"} <= set(view.headers) else None)
        if tag is None or not view.rows:
            continue
        for position, (key, label) in enumerate(zip(view.headers, view.header_labels)):
            entries.append(
                {
                    "source": SOURCE,
                    "dataset": dataset,
                    "table_tag": tag,
                    "table_caption": view.caption,
                    "column_key": _reserve(key) if key != "player" else key,
                    "column_position": position,
                    "source_label": label or None,
                    "source_title": view.header_titles.get(key),
                    "column_group": view.column_groups.get(key),
                    "header_rows": view.header_rows,
                    "source_url": work["url"],
                    "blank_headers": view.blank_headers,
                    "duplicate_headers": view.duplicate_headers,
                    "dropped_partial_cells": view.dropped_partial_cells,
                }
            )
    return entries


def parse(dataset: str, html: str, work: dict) -> list[dict]:
    if dataset == "team_season_roster":
        rows = roster_rows(SOURCE, dataset, html, work, id_from_href=_id)
        if not rows:
            raise ValueError("no usable roster rows")
        return rows
    views = parse_table_views(html)
    if dataset in MULTI_TABLE_DATASETS:
        rows = _stat_rows(dataset, views, work)
        if not rows:
            raise ValueError("no usable captioned stat rows")
        return rows
    if dataset == "team_season_results":
        rows = _results_rows(dataset, views, work)
        if not rows:
            raise ValueError("no usable results rows")
        return rows
    raise ValueError(f"unsupported statscrew parser dataset: {dataset}")


def work_item_from_url(dataset: str, url: str) -> dict | None:
    if dataset == "player_season_stats":
        match = _PLAYER_ID.search(url)
        if not match:
            return None
        player_id = match.group(1)
        # A player page spans a whole career, so its rows carry their own year and the
        # work item must not invent a single season for the page.
        return {
            "key": f"p|{player_id}",
            "season": None,
            "source_player_id": player_id,
            "url": url,
        }
    section = {
        "team_season_roster": "roster",
        "team_season_stats": "stats",
        "team_season_results": "results",
    }.get(dataset)
    if section is None:
        return None
    match = _TEAM_SEASON.search(url)
    if not match or match.group(1).lower() != section:
        return None
    _, team, season = match.groups()
    return {"key": f"{season}|{team}", "season": int(season), "team": team, "url": url}
