"""Pilot: prove we can STRUCTURE a family before harvesting tens of thousands of pages.

STAGE 2 of three: DISCOVER -> PILOT -> HARVEST.

Discovery says "this URL family exists and carries tables shaped like X". That is not
enough to register a dataset. Before committing to a bulk crawl we have to answer:

  * Is the shape STABLE across pages, or did we look at one lucky example? A parser
    written against a single sample is how a 10-column roster became a 2-column capture
    and how a positional parser silently dropped every 7-column page.
  * What is the GRAIN and the KEY SPACE? A source-native id (p-brownjim001, smit36850,
    frank-abruzzino) is what makes a capture joinable later; a name alone is the twins
    hazard.
  * Are there MULTIPLE tables per page? If so every row must be table-tagged at capture
    time, or 82 logical tables collapse into 7 registry rows and 75 of them vanish.
  * Which columns would we DROP, and is that deliberate?

This module fetches a small sample of a family, parses every table on every page, and
emits a REGISTRATION PROPOSAL: per table shape, the observed headers, how many sampled
pages carried it, whether it is stable, the detected id columns, and the proposed
dataset name. That proposal is the artifact a human reviews before any bulk harvest.

It never writes to the catalog. Proposing is not registering.

Run:
    python -m harvest.pilot --source profootballarchives \\
        --family "/players/{letter}/{id}.html" --discovery out/profootballarchives \\
        --sample 25 --out out/pilot-pfa-players
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from harvest.catalog import load_catalog
from harvest.classify import classify_surface, schema_fingerprint
from harvest.core import build_manifest
from harvest.discover import normalize_url_pattern
from harvest.http import HttpClient
from harvest.sources.common import parse_tables

# columns that make a captured row JOINABLE later. A family whose pages expose none of
# these is context, not a witness -- worth knowing before harvesting 19,000 pages.
ID_HINTS = ("player", "team", "season", "year", "name", "date", "game", "opponent")
_HREF_ID = re.compile(r"/(?:p|t|l)-([A-Za-z0-9]+)|/([a-z']+\d+[a-z]?)\.html", re.I)


def _sample_urls(discovery_dir: Path, family: str, limit: int) -> list[str]:
    """Pull real example URLs for a family out of a discovery run."""
    pages_path = discovery_dir / "DISCOVERY_PAGES.jsonl"
    urls: list[str] = []
    if pages_path.is_file():
        for line in pages_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            page = json.loads(line)
            if page.get("url_pattern") == family and page["url"] not in urls:
                urls.append(page["url"])
    return urls[:limit]


def _detect_ids(headers: list[str], rows: list[dict]) -> dict:
    ids = [h for h in headers if any(h == hint or h.startswith(hint) for hint in ID_HINTS)]
    href_ids = 0
    for row in rows:
        for cell in row.values():
            if isinstance(cell, dict) and _HREF_ID.search(cell.get("href", "") or ""):
                href_ids += 1
                break
    return {
        "id_columns": ids,
        "rows_with_source_native_id_href": href_ids,
        "has_source_native_id": href_ids > 0,
    }


def _dataset_name(family: str) -> str:
    """Dataset name from the family's STABLE segments -- placeholders carry no meaning."""
    parts = [p for p in family.strip("/").split("/") if p and "{" not in p]
    parts = [re.sub(r"[^a-z0-9]+", "_", p.lower().replace(".html", "")).strip("_")
             for p in parts]
    return "_".join(p for p in parts if p) or "family"


def run_pilot(
    *,
    source: str,
    family: str,
    urls: list[str],
    output_dir: Path,
    fetch,
    artifact_run_id: str,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    shapes: dict[str, dict] = {}
    fetched, failed = 0, 0
    samples: list[dict] = []

    for url in urls:
        response = fetch(url)
        if response.status != "ok" or not response.body:
            failed += 1
            continue
        fetched += 1
        html = response.body.decode("utf-8", "replace")
        for index, rows in enumerate(parse_tables(html)):
            if not rows:
                continue
            headers = sorted({key for row in rows for key in row})
            fingerprint = schema_fingerprint(rows)
            flat = [{k: v.get("text", "") for k, v in row.items()} for row in rows]
            shape = shapes.setdefault(fingerprint, {
                "schema_fingerprint": fingerprint,
                "headers": headers,
                "n_headers": len(headers),
                "pages_carrying": 0,
                "total_rows": 0,
                "table_index_positions": [],
                "surface": classify_surface(
                    flat, identifiers={h for h in headers if h in ID_HINTS}),
                **_detect_ids(headers, rows),
            })
            shape["pages_carrying"] += 1
            shape["total_rows"] += len(rows)
            if index not in shape["table_index_positions"]:
                shape["table_index_positions"].append(index)
            if len(samples) < 12:
                samples.append({"url": url, "schema_fingerprint": fingerprint,
                                "sample_row": flat[0]})

    ordered = sorted(shapes.values(), key=lambda s: -s["pages_carrying"])
    for shape in ordered:
        share = shape["pages_carrying"] / fetched if fetched else 0.0
        shape["page_share"] = round(share, 3)
        # A shape on nearly every page is structural. A rare one is either a real
        # variant or a page-shape we have not understood -- both need a human, and
        # neither may be silently averaged away.
        shape["stability"] = ("structural" if share >= 0.9 else
                              "common" if share >= 0.5 else "variant")

    multi = len(ordered) > 1
    proposal = {
        "source": source,
        "family": family,
        "proposed_dataset": _dataset_name(family),
        "grain_note": (
            "MULTI-TABLE PAGE: every captured row MUST carry a table tag (the shape's "
            "fingerprint or a stable label). Without it these collapse into one dataset "
            "and the distinct tables become unrecoverable."
            if multi else "single-table page"),
        "tables_per_page": len(ordered),
        "requires_table_tagging": multi,
        "joinable": any(s["has_source_native_id"] or s["id_columns"] for s in ordered),
    }

    result = {
        "source": source,
        "family": family,
        "artifact_run_id": artifact_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "counters": {
            "urls_sampled": len(urls),
            "pages_fetched": fetched,
            "pages_failed": failed,
            "distinct_table_shapes": len(ordered),
            "structural_shapes": sum(1 for s in ordered if s["stability"] == "structural"),
            "variant_shapes": sum(1 for s in ordered if s["stability"] == "variant"),
        },
        "registration_proposal": proposal,
        "table_shapes": ordered,
        "sample_rows": samples,
    }
    (output_dir / "PILOT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    manifest = build_manifest(
        output_dir, source=source, dataset=proposal["proposed_dataset"],
        shard_id=0, shard_count=1, artifact_run_id=artifact_run_id,
    )
    (output_dir / "ARTIFACT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--family", required=True,
                        help="URL pattern from a discovery run, e.g. /players/{letter}/{id}.html")
    parser.add_argument("--discovery", type=Path,
                        help="discovery output dir to draw example URLs from")
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--sample", type=int, default=25)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--catalog", type=Path, default=Path("harvest/source_catalog.yaml"))
    args = parser.parse_args()

    family = args.family
    if not family.startswith("/"):
        # Git Bash/MSYS rewrites a leading "/" into a Windows path, so --family
        # "/players/..." can arrive as "C:/Program Files/Git/players/...". Fail loudly:
        # a silently mangled family produces a garbage dataset name and a pilot that
        # claims to describe a family it never looked at.
        raise SystemExit(
            f"--family must be a URL path starting with '/', got {family!r}. "
            "On Git Bash prefix the command with MSYS_NO_PATHCONV=1.")

    urls = list(args.url)
    if args.discovery:
        urls += [u for u in _sample_urls(args.discovery, family, args.sample)
                 if u not in urls]
    if not urls:
        raise SystemExit(
            f"{args.source} {family}: no sample URLs -- pass --url or point "
            "--discovery at a run that observed this family")

    spec = load_catalog(args.catalog).source(args.source)
    client = HttpClient(delay_seconds=float(spec.get("delay_seconds", 1.0)))
    result = run_pilot(
        source=args.source, family=family, urls=urls[:args.sample],
        output_dir=args.out, fetch=client.fetch, artifact_run_id=args.run_id,
    )
    counters = result["counters"]
    proposal = result["registration_proposal"]
    print(f"{args.source} {family}: pages={counters['pages_fetched']} "
          f"shapes={counters['distinct_table_shapes']} "
          f"structural={counters['structural_shapes']} "
          f"variant={counters['variant_shapes']}")
    print(f"  proposed dataset : {proposal['proposed_dataset']}")
    print(f"  table tagging    : {'REQUIRED' if proposal['requires_table_tagging'] else 'not needed'}")
    print(f"  joinable         : {proposal['joinable']}")
    for shape in result["table_shapes"][:12]:
        print(f"    [{shape['stability']:10s}] {shape['pages_carrying']:3d}p "
              f"{shape['total_rows']:5d}r  ids={shape['id_columns'][:3]}  "
              f"{shape['headers'][:8]}")


if __name__ == "__main__":
    main()
