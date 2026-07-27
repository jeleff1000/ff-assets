from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


@dataclass
class TableView:
    """One table, plus everything the SOURCE published ABOUT that table.

    The three fields beyond `rows` exist because dropping them causes measured data
    loss, each of a kind this program has already paid for once:

    `caption`       StatsCrew stat pages carry FIVE tables with byte-identical headers
                    (`avg long no player tds yds` is rushing AND receiving AND punt
                    returns AND kick returns AND interceptions). The header signature
                    cannot separate them and the table INDEX moves from page to page,
                    so the caption is the only stable tag. Tagging by position is the
                    same class of error as the nflcom column shift.
    `header_titles` StatsCrew publishes each column's full name in a title attribute
                    (`<th><a title="Yards per Attempt">Yds/Att</a>`). Resolving nflcom's
                    polysemous stored columns cost a whole slice; here the source hands
                    us the semantics and the old parser discarded them at parse time.
    `row_classes`   `<tr class=reg_season>` / `post_season` is the source's own
                    season-type declaration, and season type is an open program question.
    """

    caption: str | None
    headers: list[str]
    header_labels: list[str]
    header_titles: dict[str, str]
    rows: list[dict[str, dict[str, str]]]
    row_classes: list[str | None]
    column_groups: dict[str, str] = field(default_factory=dict)
    blank_headers: int = 0
    duplicate_headers: int = 0
    orphan_rows: int = 0
    dropped_partial_cells: int = 0
    header_rows: int = 1
    table_index: int = -1
    css_class: str = ""


def unique_keys(labels: list[str]) -> tuple[list[str], int, int]:
    """Assign a UNIQUE key per column, preserving order.

    A blank or repeated header must never collapse two columns onto one dict key: the
    later cell silently overwrites the earlier one and a whole column disappears with
    no error anywhere. StatsCrew results pages ship two trailing blank `<th>`s, so the
    naive mapping was losing a cell on every game row of every results page.
    """
    keys: list[str] = []
    seen: dict[str, int] = {}
    blanks = 0
    duplicates = 0
    for index, label in enumerate(labels):
        base = label
        if not base:
            base = f"col_{index}"
            blanks += 1
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count:
            duplicates += 1
            keys.append(f"{base}_{count + 1}")
        else:
            keys.append(base)
    return keys, blanks, duplicates


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[dict[str, str]]]] = []
        self.captions: list[str | None] = []
        self.classes: list[str] = []
        self.row_classes: list[list[str | None]] = []
        # Cells emitted OUTSIDE any <tr>, per table. StatsCrew serves
        # "<tbody><td>...</td><td>...</td>" with no row wrappers at all, so a parser
        # that only opens a row on <tr> silently discards every data row and keeps the
        # single well-formed Totals line. Browsers infer the rows; so must we.
        self.orphans: list[list[dict[str, str]]] = []
        self._table: list[list[dict[str, str]]] | None = None
        self._orphan: list[dict[str, str]] | None = None
        self._row: list[dict[str, str]] | None = None
        self._row_class: str | None = None
        self._table_row_classes: list[str | None] | None = None
        self._cell: dict[str, str] | None = None
        self._heading: str | None = None
        self._last_heading: str | None = None
        self._table_caption: str | None = None
        self._table_class: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag in {"h1", "h2", "h3"}:
            self._heading = ""
        elif tag == "table":
            self._table = []
            self._orphan = []
            self._table_row_classes = []
            self._table_caption = self._last_heading
            self._table_class = values.get("class", "")
        elif tag == "tr" and self._table is not None:
            self._row = []
            self._row_class = values.get("class") or None
        elif tag in {"th", "td"} and (self._row is not None or self._table is not None):
            span = values.get("colspan", "1").strip() or "1"
            self._cell = {
                "text": "",
                "href": "",
                "hrefs": "",
                "kind": tag,
                "title": "",
                "colspan": span if span.isdigit() and int(span) > 0 else "1",
            }
        elif tag == "a" and self._cell is not None:
            href = values.get("href", "")
            if href:
                # A cell can carry SEVERAL links -- a StatsCrew results row names both
                # teams inside one `game` cell. Keeping only one loses an identifier,
                # so `href` is the FIRST link (what a single-link cell means) and
                # `hrefs` keeps every link the cell published.
                if not self._cell["href"]:
                    self._cell["href"] = href
                self._cell["hrefs"] = f"{self._cell['hrefs']} {href}".strip()
            if values.get("title"):
                self._cell["title"] = values["title"]

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data
        elif self._heading is not None:
            self._heading += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3"} and self._heading is not None:
            text = " ".join(self._heading.split()).strip()
            self._last_heading = text.rstrip(":").strip() or None
            self._heading = None
        elif tag in {"th", "td"} and self._cell is not None:
            self._cell["text"] = " ".join(self._cell["text"].split())
            if self._row is not None:
                self._row.append(self._cell)
            elif self._orphan is not None:
                self._orphan.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
                if self._table_row_classes is not None:
                    self._table_row_classes.append(self._row_class)
            self._row = None
            self._row_class = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
                self.orphans.append(self._orphan or [])
                self.captions.append(self._table_caption)
                self.classes.append(self._table_class)
                self.row_classes.append(self._table_row_classes or [])
            self._table = None
            self._orphan = None
            self._table_row_classes = None


def _span(cell: dict[str, str]) -> int:
    return int(cell.get("colspan", "1") or "1")


def resolve_header(
    header_rows: list[list[dict[str, str]]]
) -> tuple[list[dict[str, str]], list[str], int]:
    """Pick the row that names the COLUMNS, and expand any group row above it.

    StatsCrew's `Total Scoring` table stacks two header rows: a GROUP row
    (`<th></th><th colspan=8>Touchdowns</th><th colspan=5>Other</th><th>Total</th>`) over
    the 15 real column names. Taking the first row containing a `<th>` gave a width of 4
    and then segmented 15-cell data rows into rows of four -- 39 fabricated rows out of
    11 players, with every value under the wrong name. Same class as the nflcom column
    shift, caught before the harvest instead of 7.78M rows later.

    The rule is EVIDENCE, not preference: a group stack is only accepted when the last
    header row is wider than the first AND the first row's colspans expand to exactly
    the last row's width. That is measurable on the page. When it does not hold -- PFA's
    boxscores open with `<th colspan=3>LINEUPS</th>` over a same-width team-name row --
    the first row stays the header and nothing changes.

    Returns (detail cells, per-column group label, number of header rows consumed).
    """
    if len(header_rows) >= 2:
        first, last = header_rows[0], header_rows[-1]
        expanded: list[str] = []
        for cell in first:
            expanded.extend([cell["text"]] * _span(cell))
        if len(last) > len(first) and len(expanded) == len(last):
            return last, expanded, len(header_rows)
    return header_rows[0], [""] * len(header_rows[0]), 1


def parse_table_views(html: str) -> list[TableView]:
    """Parse every table, keeping the caption, the header title attributes and the
    per-row class alongside the cells.

    HEADER-DRIVEN throughout: a column is located by its own header, never by an index
    into a shape we assumed. Every quantity we cannot represent (blank headers,
    duplicate headers, cells dropped from a partial trailing run) is COUNTED on the
    view rather than silently absorbed.
    """
    parser = _TableParser()
    parser.feed(html)
    views: list[TableView] = []
    for table_index, table in enumerate(parser.tables):
        orphan_cells = parser.orphans[table_index] if table_index < len(parser.orphans) else []
        header_index = next(
            (i for i, row in enumerate(table) if any(cell["kind"] == "th" for cell in row)), None
        )
        if header_index is None:
            continue
        stacked = []
        for row in table[header_index:]:
            if not any(cell["kind"] == "th" for cell in row):
                break
            stacked.append(row)
        header_cells, group_labels, header_row_count = resolve_header(stacked)
        header_index += header_row_count - 1
        labels = [normalize_header(cell["text"]) for cell in header_cells]
        raw_labels = [cell["text"] for cell in header_cells]
        keys, blanks, duplicates = unique_keys(labels)
        column_groups = {
            keys[i]: group_labels[i]
            for i in range(min(len(keys), len(group_labels)))
            if group_labels[i]
        }
        # Titles come from the RESOLVED header cells, never from a running index over
        # every <th> in the table: a two-row header would otherwise offset every title
        # by the width of the group row and label each column with its neighbour's name.
        header_titles = {
            keys[i]: header_cells[i]["title"] for i in range(len(keys)) if header_cells[i]["title"]
        }
        classes_for_rows = (
            parser.row_classes[table_index] if table_index < len(parser.row_classes) else []
        )
        rows: list[dict[str, dict[str, str]]] = []
        row_classes: list[str | None] = []
        for offset, cells in enumerate(table[header_index + 1 :], start=header_index + 1):
            if len(cells) < len(keys):
                continue
            rows.append({keys[i]: cells[i] for i in range(len(keys))})
            row_classes.append(classes_for_rows[offset] if offset < len(classes_for_rows) else None)
        # Recover rows the source never wrapped in <tr>: a flat run of cells segments
        # into rows of header width, exactly as a browser lays them out. Only whole
        # rows are emitted; a trailing partial run is DROPPED rather than padded, since
        # inventing cells would be worse than reporting fewer rows -- but the dropped
        # count is carried on the view, so a width mismatch surfaces as a NUMBER.
        orphan_rows = 0
        dropped = 0
        if orphan_cells and keys:
            width = len(keys)
            whole = (len(orphan_cells) // width) * width
            dropped = len(orphan_cells) - whole
            for start in range(0, whole, width):
                chunk = orphan_cells[start : start + width]
                rows.append({keys[i]: chunk[i] for i in range(width)})
                row_classes.append(None)
                orphan_rows += 1
        views.append(
            TableView(
                caption=parser.captions[table_index] if table_index < len(parser.captions) else None,
                headers=keys,
                header_labels=raw_labels,
                header_titles=header_titles,
                rows=rows,
                row_classes=row_classes,
                column_groups=column_groups,
                blank_headers=blanks,
                duplicate_headers=duplicates,
                orphan_rows=orphan_rows,
                dropped_partial_cells=dropped,
                header_rows=header_row_count,
                table_index=table_index,
                css_class=parser.classes[table_index] if table_index < len(parser.classes) else "",
            )
        )
    return views


def parse_tables(html: str) -> list[list[dict[str, dict[str, str]]]]:
    """Back-compatible view over parse_table_views: the tables that carried data rows."""
    return [view.rows for view in parse_table_views(html) if view.rows]


def roster_rows(
    source: str,
    dataset: str,
    html: str,
    work: dict,
    *,
    id_from_href: Callable[[str], str | None],
) -> list[dict]:
    output = []
    for table in parse_tables(html):
        for row in table:
            player_cell = row.get("player") or row.get("name")
            if not player_cell or not player_cell["text"]:
                continue
            source_id = id_from_href(player_cell["href"])
            if not source_id:
                continue
            output.append(
                {
                    "source": source,
                    "dataset": dataset,
                    "season": int(work["season"]),
                    "team": work.get("team"),
                    "game_id": work.get("game_id"),
                    "player": player_cell["text"],
                    "source_player_id": source_id,
                    "position": (row.get("pos") or row.get("pos_") or {}).get("text"),
                    "source_url": work["url"],
                }
            )
    return output
