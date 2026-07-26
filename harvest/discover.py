"""Discovery: enumerate what a source PUBLISHES before committing to a harvest.

The failure this closes (measured 2026-07-26 against our own captures):

* `source_catalog.yaml` declared six datasets; the workflow matrix ran three. Nothing
  compared the two, so three declared datasets sat unharvested indefinitely.
* Our hand-authored idea of each site's table-of-contents was wrong in BOTH directions.
  Against ProFootballArchives' own nav it missed Coaches, Awards, Leaderboards and
  Seasons, and invented a "transactions" section that is not in the nav at all --
  transactions turned out to be real, but living INSIDE player pages. Absence from a
  nav is not absence from a site.
* The ProFootballArchives boxscore parser reads only the LINEUPS table, so ten further
  data tables per page (RUSHING, PASSING, RECEIVING, INTERCEPTIONS, PUNTING, PUNT
  RETURNS, KICKOFFS, KICKOFF RETURNS, SACKS, scoring plays) were never seen by any
  counter.

So discovery must be a MEASUREMENT, never an assertion. This module walks a source's
own link graph from declared probe pages, follows ONE HOP into a sample of children per
newly-seen URL pattern (the hop that hid transactions), and records for every page:

  * every TABLE it carries -- normalized headers, row count, schema fingerprint, and
    the `classify_surface` verdict
  * every internal LINK PATTERN it exposes

Output is an inventory of `url_pattern -> observed table shapes`, which is the input a
human (or the pilot stage) needs to decide what to capture. It fetches no more than
`--max-pages` and obeys robots and the catalog allowlist exactly as the census does.

Run:
    python -m harvest.discover --source profootballarchives --out out/discovery
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import urllib.robotparser
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from harvest.catalog import load_catalog
from harvest.classify import classify_surface, schema_fingerprint
from harvest.core import build_manifest
from harvest.census import url_allowed
from harvest.http import HttpClient
from harvest.sources.common import normalize_header, parse_tables

_LINK = re.compile(r'<a[^>]+href=["\']([^"\']+)["\']', re.I)


def normalize_url_pattern(path: str) -> str:
    """Collapse a URL path to its FAMILY pattern.

    Entity slugs collapse; SECTION WORDS STAY LITERAL. A rule that collapsed any
    segment of 8+ characters turned "football" into a placeholder and hid every
    section of a multi-sport host behind one pattern -- a discovery tool that hides
    sections is worse than none.
    """
    u = path.split("#")[0].split("?")[0]
    # Entity-id paths FIRST: a player file like /players/r/rice02100.html contains 4+
    # digits, so a bare year rule would rewrite it to rice{year} and split ONE family
    # into hundreds of "patterns" -- inflating the very count this tool reports.
    u = re.sub(r"/([a-z])/[a-z]+\d+[a-z]?\.html$", r"/\1/{id}.html", u)
    u = re.sub(r"/(coaches|players|teams)/[a-z]+\d+[a-z]?\.html$", r"/\1/{id}.html", u)
    u = re.sub(r"\d{4,}", "{year}", u)
    u = re.sub(r"/\d+", "/{n}", u)
    u = re.sub(r"/(?=[a-z0-9]*-)[a-z0-9-]{6,}/", "/{slug}/", u)
    u = re.sub(r"/[a-z0-9]{14,}/", "/{slug}/", u)
    u = re.sub(r"\b(p|t|l|in|pro)-[A-Za-z0-9-]{3,}", r"\1-{id}", u)
    return u or "/"


def describe_tables(html: str) -> list[dict]:
    """Every table on the page, with the shape a capture contract needs."""
    out = []
    for index, rows in enumerate(parse_tables(html)):
        if not rows:
            continue
        headers = sorted({key for row in rows for key in row})
        ids = {c for c in ("player", "team", "season", "year") if c in headers}
        out.append(
            {
                "table_index": index,
                "headers": headers,
                "n_headers": len(headers),
                "n_rows": len(rows),
                "schema_fingerprint": schema_fingerprint(rows),
                "surface": classify_surface(
                    [{k: v.get("text", "") for k, v in row.items()} for row in rows],
                    identifiers=ids,
                ),
            }
        )
    return out


def run_discovery(
    *,
    source: str,
    probes: list[str],
    output_dir: Path,
    domains: set[str],
    allowed_prefixes: tuple[str, ...],
    fetch,
    max_pages: int,
    children_per_pattern: int,
    artifact_run_id: str,
    obey_robots: bool = True,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    pending: list[tuple[str, int]] = [(u, 0) for u in sorted(set(probes))]
    seen: set[str] = set()
    pages: list[dict] = []
    ledger: list[dict] = []
    per_pattern_fetched: dict[str, int] = defaultdict(int)
    robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    while pending and len(seen) < max_pages:
        url, depth = pending.pop(0)
        if url in seen:
            continue
        parsed = urlparse(url)
        is_probe = depth == 0
        if not is_probe and not url_allowed(
            url, domains=domains, allowed_prefixes=allowed_prefixes
        ):
            continue
        if obey_robots:
            robot = robots.get(parsed.netloc)
            if robot is None:
                robot = urllib.robotparser.RobotFileParser()
                robot.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
                try:
                    robot.read()
                except Exception:  # unreachable robots must not silently allow
                    robot = None
                robots[parsed.netloc] = robot
            if robot is not None and not robot.can_fetch("*", url):
                ledger.append({"url": url, "status": "robots_denied"})
                continue

        seen.add(url)
        response = fetch(url)
        ledger.append(
            {
                "url": url,
                "status": response.status,
                "status_code": response.status_code,
                "error": response.error,
                "depth": depth,
            }
        )
        if response.status != "ok" or not response.body:
            continue

        html = response.body.decode("utf-8", "replace")
        pattern = normalize_url_pattern(parsed.path)
        per_pattern_fetched[pattern] += 1

        digest = f"{abs(hash(url)):016x}"
        (raw_dir / f"{digest}.html.gz").write_bytes(gzip.compress(response.body))
        pages.append(
            {
                "url": url,
                "url_pattern": pattern,
                "depth": depth,
                "raw_path": f"raw/{digest}.html.gz",
                "tables": describe_tables(html),
            }
        )

        # ONE HOP: sample a few children per NEWLY-seen pattern. This is the hop that
        # a nav-only enumeration skips, and the reason it under-counts a site.
        if depth >= 1:
            continue
        for href in _LINK.findall(html):
            if href.startswith(("mailto:", "javascript:", "#", "tel:")):
                continue
            child = urljoin(url, href)
            if child in seen:
                continue
            child_pattern = normalize_url_pattern(urlparse(child).path)
            if per_pattern_fetched[child_pattern] >= children_per_pattern:
                continue
            if not url_allowed(child, domains=domains, allowed_prefixes=allowed_prefixes):
                continue
            per_pattern_fetched[child_pattern] += 1
            pending.append((child, depth + 1))

    by_pattern: dict[str, dict] = {}
    for page in pages:
        entry = by_pattern.setdefault(
            page["url_pattern"],
            {"url_pattern": page["url_pattern"], "pages_seen": 0,
             "example_urls": [], "table_shapes": {}},
        )
        entry["pages_seen"] += 1
        if len(entry["example_urls"]) < 3:
            entry["example_urls"].append(page["url"])
        for table in page["tables"]:
            shape = entry["table_shapes"].setdefault(
                table["schema_fingerprint"],
                {"schema_fingerprint": table["schema_fingerprint"],
                 "headers": table["headers"], "surface": table["surface"],
                 "seen_on_pages": 0, "max_rows": 0},
            )
            shape["seen_on_pages"] += 1
            shape["max_rows"] = max(shape["max_rows"], table["n_rows"])
    for entry in by_pattern.values():
        entry["table_shapes"] = sorted(
            entry["table_shapes"].values(), key=lambda s: -s["seen_on_pages"]
        )
        entry["n_distinct_table_shapes"] = len(entry["table_shapes"])

    (output_dir / "DISCOVERY_PAGES.jsonl").write_text(
        "".join(json.dumps(p, separators=(",", ":")) + "\n" for p in pages),
        encoding="utf-8",
    )
    (output_dir / "REQUEST_LEDGER.jsonl").write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in ledger),
        encoding="utf-8",
    )
    summary = {
        "source": source,
        "artifact_run_id": artifact_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "probes": sorted(set(probes)),
        "counters": {
            "pages_fetched": len(pages),
            "urls_attempted": len(seen),
            "url_patterns": len(by_pattern),
            "patterns_with_tables": sum(
                1 for e in by_pattern.values() if e["n_distinct_table_shapes"]
            ),
            "distinct_table_shapes": sum(
                e["n_distinct_table_shapes"] for e in by_pattern.values()
            ),
            "robots_denied": sum(1 for r in ledger if r["status"] == "robots_denied"),
            "failed": sum(
                1 for r in ledger if r["status"] not in {"ok", "robots_denied"}
            ),
        },
        "patterns": sorted(by_pattern.values(), key=lambda e: -e["pages_seen"]),
    }
    (output_dir / "DISCOVERY_SUMMARY.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    manifest = build_manifest(
        output_dir,
        source=source,
        dataset="_discovery",
        shard_id=0,
        shard_count=1,
        artifact_run_id=artifact_run_id,
    )
    (output_dir / "ARTIFACT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--probe", action="append", default=[])
    parser.add_argument("--max-pages", type=int, default=400)
    parser.add_argument("--children-per-pattern", type=int, default=3)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--catalog", type=Path, default=Path("harvest/source_catalog.yaml"))
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    spec = catalog.source(args.source)
    probes = args.probe or list(spec.get("discovery_probes", []))
    if not probes:
        raise SystemExit(
            f"{args.source}: no discovery probes -- declare discovery_probes in the "
            "catalog or pass --probe"
        )
    # Discovery scope is DELIBERATELY broader than the datasets' allowed_prefixes.
    # Scoping discovery to what the datasets already claim means it can only ever
    # re-confirm families we declared -- measured 2026-07-26, when the dataset allowlist
    # silently filtered /football/results/ and /football/l-* out of a StatsCrew run and
    # left discovery reporting 8 patterns for a site with far more.
    prefixes = tuple(spec.get("discovery_prefixes") or ["/"])
    client = HttpClient(delay_seconds=float(spec.get("delay_seconds", 1.0)))
    summary = run_discovery(
        source=args.source,
        probes=probes,
        output_dir=args.out,
        domains=set(spec["domains"]),
        allowed_prefixes=prefixes,
        fetch=client.fetch,
        max_pages=args.max_pages,
        children_per_pattern=args.children_per_pattern,
        artifact_run_id=args.run_id,
    )
    counters = summary["counters"]
    print(
        f"{args.source}: pages={counters['pages_fetched']} "
        f"patterns={counters['url_patterns']} "
        f"table_shapes={counters['distinct_table_shapes']} "
        f"robots_denied={counters['robots_denied']} failed={counters['failed']}"
    )


if __name__ == "__main__":
    main()
