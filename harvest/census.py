from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd

from harvest.catalog import load_catalog
from harvest.classify import classify_surface, schema_fingerprint
from harvest.core import build_manifest, shard_for
from harvest.http import HttpClient
from harvest.sources import get_adapter
from harvest.sources.common import parse_tables


def expand_seed_spec(
    dataset_spec: dict,
    *,
    year_start: int | None,
    year_end: int | None,
    catalog_dir: Path,
) -> list[str]:
    """Expand catalog-owned all-year seeds without hard-coding them in workflows."""
    first = max(int(dataset_spec["first_year"]), year_start or int(dataset_spec["first_year"]))
    last = min(int(dataset_spec["last_year"]), year_end or int(dataset_spec["last_year"]))
    if first > last:
        raise ValueError("requested census year range is empty")
    seeds = list(dataset_spec.get("census_seeds", []))
    for spec in dataset_spec.get("census_seed_ranges", []):
        lo = max(first, int(spec["first_year"]))
        hi = min(last, int(spec["last_year"]))
        seeds.extend(spec["template"].format(year=year) for year in range(lo, hi + 1))
    inventory_name = dataset_spec.get("seed_inventory")
    template = dataset_spec.get("census_seed_template")
    if inventory_name and template:
        with (catalog_dir / inventory_name).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                year = int(row["year"])
                if first <= year <= last:
                    seeds.append(template.format(**row))
    return sorted(set(seeds))


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(value)


def url_allowed(url: str, *, domains: set[str], allowed_prefixes: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc in domains and any(
        parsed.path.startswith(prefix) for prefix in allowed_prefixes
    )


def crawl_fixture(
    seed: str,
    pages: dict[str, str],
    *,
    domains: set[str],
    allowed_prefixes: tuple[str, ...],
) -> list[dict]:
    pending = [seed]
    seen: set[str] = set()
    rows: list[dict] = []
    while pending:
        url = pending.pop(0)
        if url in seen or not url_allowed(url, domains=domains, allowed_prefixes=allowed_prefixes):
            continue
        seen.add(url)
        html = pages.get(url)
        if html is None:
            rows.append({"url": url, "status": "missing_fixture"})
            continue
        rows.append({"url": url, "status": "ok"})
        parser = _Links()
        parser.feed(html)
        candidates = sorted({urljoin(url, link) for link in parser.links})
        pending.extend(candidate for candidate in candidates if candidate not in seen)
    return sorted(rows, key=lambda row: row["url"])


def read_robots(robots_url: str, fetch) -> urllib.robotparser.RobotFileParser:
    """Fetch robots.txt through OUR client, not through urllib's own opener.

    `RobotFileParser.read()` opens the URL itself: no User-Agent, no rate pacing, no
    retry. And urllib's contract is that a 401/403 on robots.txt means DISALLOW
    EVERYTHING -- so one throttled response turns a whole shard into 452 "robots_denied"
    entries in under a second, which is exactly what happened to shard 1 of ff-assets runs
    30226751762 and 30232968983. Five jobs starting together fetch five robots.txt within
    the same instant, which is the one burst our per-host pacing never covered, because
    the pacing lives in the client this call bypassed.

    Going through `fetch` gives it the real User-Agent, the catalog delay and the client's
    retry/backoff. A genuine, repeated 401/403 still disallows -- politeness is not
    negotiable -- but a transient one no longer silently cancels a shard.
    """
    parser = urllib.robotparser.RobotFileParser(robots_url)
    parser.parse([])
    try:
        response = fetch(robots_url)
    except Exception:  # a robots fetch must never take the run down with it
        parser.allow_all = True
        return parser
    if response.status == "ok":
        parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
    elif response.status_code in {401, 403}:
        parser.disallow_all = True
    else:
        # 404 / 5xx / network: urllib's own rule is that an absent robots.txt allows all.
        parser.allow_all = True
    return parser


def _discover_links(url: str, body: str) -> list[str]:
    if body.lstrip().startswith("<?xml") or "<urlset" in body or "<sitemapindex" in body:
        return sorted(set(re.findall(r"<loc>\s*(.*?)\s*</loc>", body, flags=re.I | re.S)))
    parser = _Links()
    parser.feed(body)
    return sorted({urljoin(url, link) for link in parser.links})


def run_census(
    *,
    source: str,
    dataset: str,
    seeds: list[str],
    output_dir: Path,
    domains: set[str],
    allowed_prefixes: tuple[str, ...],
    fetch,
    max_pages: int,
    shard_id: int,
    shard_count: int,
    artifact_run_id: str,
    obey_robots: bool = True,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw_samples"
    raw_dir.mkdir(exist_ok=True)
    adapter = get_adapter(source)
    # Seeds are shared discovery roots. Their immediate links are deterministically
    # assigned to a shard, and every descendant keeps that branch owner. Hashing
    # every descendant independently would discard pages that only the owning
    # branch ever discovers.
    unique_seeds = sorted(set(seeds))
    # A single root seed is shared so every shard can see its top-level branches.
    # A generated all-year seed set is already a collection of independent roots,
    # so assign each root once and avoid N-shard duplicate traffic.
    pending: list[tuple[str, int | None]] = [
        (url, None if len(unique_seeds) == 1 else shard_for(url, shard_count)) for url in unique_seeds
    ]
    seen: set[str] = set()
    surfaces: list[dict] = []
    ledger: list[dict] = []
    work_items: list[dict] = []
    robots: dict[str, urllib.robotparser.RobotFileParser] = {}
    while pending and len(seen) < max_pages:
        url, branch_owner = pending.pop(0)
        if url in seen:
            continue
        parsed = urlparse(url)
        is_seed = url in seeds
        if not is_seed and not url_allowed(url, domains=domains, allowed_prefixes=allowed_prefixes):
            continue
        # Every shard reads the small discovery seeds. A non-seed branch is read
        # only by its assigned owner, which then owns every page below it.
        if branch_owner is not None and branch_owner != shard_id:
            continue
        seen.add(url)
        if obey_robots:
            robot = robots.get(parsed.netloc)
            if robot is None:
                robot = read_robots(f"{parsed.scheme}://{parsed.netloc}/robots.txt", fetch)
                robots[parsed.netloc] = robot
            if not robot.can_fetch("ff-assets-historical-witness", url):
                ledger.append({"url": url, "status": "robots_denied"})
                continue
        response = fetch(url)
        ledger.append({"url": url, "status": response.status, "status_code": response.status_code, "error": response.error})
        if response.status != "ok":
            continue
        body = response.body.decode("utf-8", errors="replace")
        content_hash = hashlib.sha256(response.body).hexdigest()
        links = _discover_links(url, body)
        work_builder = getattr(adapter, "work_item_from_url", None)
        page_work_item = work_builder(dataset, url) if work_builder else None
        # Dataset pages are leaves. Following their navigation links causes a
        # generated season/team seed to leak into adjacent seasons and duplicate
        # work already owned by another branch.
        if page_work_item is None:
            for link in links:
                if link in seen:
                    continue
                owner = shard_for(link, shard_count) if branch_owner is None else branch_owner
                pending.append((link, owner))
        # A seed may itself be a data page. It is fetched by every shard for
        # discovery but emitted by exactly one shard.
        emit_page = branch_owner is not None or shard_for(url, shard_count) == shard_id
        if page_work_item and emit_page:
            work_items.append(page_work_item)
        tables = parse_tables(body)
        adapter_rows: list[dict] = []
        if page_work_item and emit_page:
            try:
                adapter_rows = adapter.parse(dataset, body, page_work_item)
            except ValueError:
                pass
        adapter_usability = (
            classify_surface(adapter_rows, identifiers={"player", "team", "season"}) if adapter_rows else None
        )
        for table_index, table in enumerate(tables if emit_page else []):
            flat_rows = [{key: cell["text"] for key, cell in row.items()} for row in table]
            if page_work_item:
                for row in flat_rows:
                    for context_key in ("team", "season", "game_id"):
                        if page_work_item.get(context_key) is not None:
                            row[context_key] = page_work_item[context_key]
            columns = sorted({key for row in flat_rows for key in row})
            identifiers = {key for key in columns if key in {"player", "name", "team", "season", "year", "game", "date"}}
            normalized_ids = {"player" if key == "name" else "season" if key == "year" else key for key in identifiers}
            usability = adapter_usability or classify_surface(flat_rows, identifiers=normalized_ids)
            surfaces.append(
                {
                    "source": source,
                    "dataset": dataset,
                    "url": url,
                    "content_sha256": content_hash,
                    "table_index": table_index,
                    "row_count": len(table),
                    "adapter_record_count": len(adapter_rows),
                    "columns_json": json.dumps(columns),
                    "schema_fingerprint": schema_fingerprint(flat_rows),
                    "usability": usability,
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
        if tables and not (raw_dir / f"{content_hash}.html.gz").exists():
            (raw_dir / f"{content_hash}.html.gz").write_bytes(gzip.compress(response.body))
    if surfaces:
        pd.DataFrame(surfaces).to_parquet(output_dir / "SOURCE_SURFACE_CATALOG.parquet", index=False)
    (output_dir / "SCHEMA_FINGERPRINTS.json").write_text(
        json.dumps(sorted({row["schema_fingerprint"] for row in surfaces}), indent=2), encoding="utf-8"
    )
    (output_dir / "REQUEST_LEDGER.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in ledger), encoding="utf-8"
    )
    (output_dir / "WORK_ITEMS.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in sorted(work_items, key=lambda row: row["key"])),
        encoding="utf-8",
    )
    status_counts: dict[str, int] = {}
    for row in ledger:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    report = {
        "source": source,
        "dataset": dataset,
        "pages_seen": len(seen),
        "requests": len(ledger),
        "surfaces": len(surfaces),
        "work_items": len(work_items),
        "request_status": status_counts,
        "usability": {kind: sum(row["usability"] == kind for row in surfaces) for kind in sorted({r["usability"] for r in surfaces})},
    }
    (output_dir / "WITNESS_USABILITY_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    manifest = build_manifest(
        output_dir,
        source=source,
        dataset=f"census:{dataset}",
        shard_id=shard_id,
        shard_count=shard_count,
        artifact_run_id=artifact_run_id,
    )
    (output_dir / "ARTIFACT_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return report


def census_failure_reason(report: dict, *, source: str, dataset: str, shard: int) -> str | None:
    """A CENSUS THAT EMITTED NO WORK MUST FAIL, NOT EXIT 0.

    In run 30226751762 the `team_season_roster` shard 1 job logged 477 requests, 0
    surfaces and 0 work items in 0.65 SECONDS, then reported green. Its 477 seeds were a
    real partition: 178 team-seasons in it are still uncaptured today. The only ledger
    path that records a request without calling `fetch()` is `robots_denied`, and the
    client paces every real fetch, so 477 entries in under a second is proof the host
    refused robots.txt to that runner while 15 jobs hit one site at once. A run that was
    BLOCKED must never be indistinguishable from a run that legitimately found nothing.
    """
    statuses = report.get("request_status", {})
    denied = statuses.get("robots_denied", 0)
    if denied and denied == report["requests"]:
        return (
            f"{source}/{dataset} shard {shard}: every one of {denied} URLs was robots-denied "
            "-- treat this as a block, not as an empty census"
        )
    if report["requests"] and not report["work_items"]:
        return (
            f"{source}/{dataset} shard {shard}: {report['requests']} requests produced ZERO "
            f"work items (statuses: {statuses}) -- a shard with seeds must emit work"
        )
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--seed", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=10000)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument(
        "--budget-minutes", type=float, default=90.0,
        help="wall-clock budget for this shard; it FAILS when exceeded instead of "
             "burning the job timeout and being cancelled")
    parser.add_argument("--catalog", type=Path, default=Path("harvest/source_catalog.yaml"))
    parser.add_argument("--year-start", type=int)
    parser.add_argument("--year-end", type=int)
    args = parser.parse_args()
    catalog = load_catalog(args.catalog)
    source_spec = catalog.source(args.source)
    dataset_spec = catalog.dataset(args.source, args.dataset)
    seeds = args.seed or expand_seed_spec(
        dataset_spec,
        year_start=args.year_start,
        year_end=args.year_end,
        catalog_dir=args.catalog.parent,
    )
    if not seeds:
        raise SystemExit("at least one --seed or catalog census_seed is required")
    client = HttpClient(delay_seconds=float(source_spec["delay_seconds"]),
                        budget_seconds=args.budget_minutes * 60.0)
    result = run_census(
        source=args.source,
        dataset=args.dataset,
        seeds=seeds,
        output_dir=args.out,
        domains=set(source_spec["domains"]),
        allowed_prefixes=tuple(dataset_spec["allowed_prefixes"]),
        fetch=client.fetch,
        max_pages=args.max_pages,
        shard_id=args.shard,
        shard_count=args.num_shards,
        artifact_run_id=args.run_id,
    )
    print(json.dumps(result, indent=2))
    reason = census_failure_reason(
        result, source=args.source, dataset=args.dataset, shard=args.shard
    )
    if reason:
        raise SystemExit(reason)


if __name__ == "__main__":
    main()
