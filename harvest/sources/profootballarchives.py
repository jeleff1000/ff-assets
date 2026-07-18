from __future__ import annotations

import html as html_lib
import re

from .common import roster_rows

SOURCE = "profootballarchives"


def _id(href: str) -> str | None:
    match = re.search(r"/players/(?:[^/]+/)?([^/.]+)\.html", href)
    return match.group(1) if match else None


def _clean(fragment: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _lineup_rows(dataset: str, html: str, work: dict) -> list[dict]:
    table_match = re.search(r"<table[^>]*>.*?>\s*LINEUPS\s*</th>.*?</table>", html, flags=re.I | re.S)
    if not table_match:
        return []
    team: str | None = None
    output = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(0), flags=re.I | re.S):
        header = re.fullmatch(r"\s*<th[^>]*colspan=[\"']?3[\"']?[^>]*>(.*?)</th>\s*", row_html, flags=re.I | re.S)
        if header:
            value = _clean(header.group(1))
            if value.upper() != "LINEUPS":
                team = value
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
        if len(cells) != 3 or not team:
            continue
        link = re.search(r"href=[\"']([^\"']*/players/[^\"']+)[\"'][^>]*>(.*?)</a>", cells[2], flags=re.I | re.S)
        if not link:
            continue
        source_id = _id(link.group(1))
        if not source_id:
            continue
        output.append(
            {
                "source": SOURCE,
                "dataset": dataset,
                "season": int(work["season"]),
                "team": team,
                "game_id": work.get("game_id"),
                "player": _clean(link.group(2)),
                "source_player_id": source_id,
                "position": _clean(cells[1]),
                "source_url": work["url"],
            }
        )
    return output


def parse(dataset: str, html: str, work: dict) -> list[dict]:
    if dataset not in {"team_season_roster", "player_game_participation"}:
        raise ValueError(f"unsupported profootballarchives parser dataset: {dataset}")
    rows = _lineup_rows(dataset, html, work)
    if not rows:
        rows = roster_rows(SOURCE, dataset, html, work, id_from_href=_id)
    if not rows:
        raise ValueError("no usable roster rows")
    return rows


def work_item_from_url(dataset: str, url: str) -> dict | None:
    if dataset != "player_game_participation":
        return None
    match = re.search(r"/nflboxscores\d*/(\d{4}[a-z0-9]+)\.html", url)
    if not match:
        return None
    game_id = match.group(1)
    return {"key": game_id, "season": int(game_id[:4]), "game_id": game_id, "url": url}
