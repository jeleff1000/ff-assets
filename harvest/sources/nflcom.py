from __future__ import annotations

import re

from .common import roster_rows

SOURCE = "nflcom"


def _id(href: str) -> str | None:
    match = re.search(r"/players/([a-z0-9-]+)/?", href)
    return match.group(1) if match else None


def parse(dataset: str, html: str, work: dict) -> list[dict]:
    if dataset != "team_season_roster":
        raise ValueError(f"unsupported nflcom parser dataset: {dataset}")
    rows = roster_rows(SOURCE, dataset, html, work, id_from_href=_id)
    if not rows:
        raise ValueError("no usable roster rows")
    return rows


def work_item_from_url(dataset: str, url: str) -> dict | None:
    if dataset != "team_season_roster":
        return None
    match = re.search(r"/sitemap/html/rosters/(\d{4})/([^/?#]+)", url)
    if not match:
        return None
    season, team = match.groups()
    return {"key": f"{season}|{team}", "season": int(season), "team": team, "url": url}
