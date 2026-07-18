from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Callable


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[dict[str, str]]]] = []
        self._table: list[list[dict[str, str]]] | None = None
        self._row: list[dict[str, str]] | None = None
        self._cell: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = {"text": "", "href": "", "kind": tag}
        elif tag == "a" and self._cell is not None:
            self._cell["href"] = next((value or "" for name, value in attrs if name.lower() == "href"), "")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._cell["text"] = " ".join(self._cell["text"].split())
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def parse_tables(html: str) -> list[list[dict[str, dict[str, str]]]]:
    parser = _TableParser()
    parser.feed(html)
    output = []
    for table in parser.tables:
        header_index = next((i for i, row in enumerate(table) if any(cell["kind"] == "th" for cell in row)), None)
        if header_index is None:
            continue
        headers = [normalize_header(cell["text"]) for cell in table[header_index]]
        rows = []
        for cells in table[header_index + 1 :]:
            if len(cells) < len(headers):
                continue
            rows.append({headers[i]: cells[i] for i in range(len(headers))})
        if rows:
            output.append(rows)
    return output


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

