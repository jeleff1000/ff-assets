"""Turn a discovery run into the human-readable question: what did we find, and is it
already claimed by a dataset we capture?

Discovery output is a machine inventory. This renders the part a person has to decide
on: which observed URL patterns carry tables, which are already covered by a declared
dataset's allowed_prefixes, and which are UNCLAIMED -- the unknowns.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harvest.catalog import load_catalog


def build_report(summary: dict, catalog_spec: dict) -> dict:
    claims: list[tuple[str, str]] = []
    for name, dataset in (catalog_spec.get("datasets") or {}).items():
        for prefix in dataset.get("allowed_prefixes", []):
            claims.append((prefix, name))
    claims.sort(key=lambda c: -len(c[0]))

    rows = []
    for pattern in summary["patterns"]:
        claimed_by = next(
            (name for prefix, name in claims
             if prefix != "/" and pattern["url_pattern"].startswith(prefix)),
            None,
        )
        rows.append({
            "url_pattern": pattern["url_pattern"],
            "pages_seen": pattern["pages_seen"],
            "table_shapes": pattern["n_distinct_table_shapes"],
            "max_rows": max((s["max_rows"] for s in pattern["table_shapes"]), default=0),
            "surfaces": sorted({s["surface"] for s in pattern["table_shapes"]}),
            "claimed_by_dataset": claimed_by,
            "example_url": pattern["example_urls"][0] if pattern["example_urls"] else None,
        })
    with_tables = [r for r in rows if r["table_shapes"]]
    unclaimed = [r for r in with_tables if not r["claimed_by_dataset"]]
    return {
        "source": summary["source"],
        "counters": {
            **summary["counters"],
            "patterns_unclaimed_with_tables": len(unclaimed),
        },
        "unclaimed_patterns_with_tables": sorted(
            unclaimed, key=lambda r: -r["max_rows"]
        ),
        "all_patterns": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("harvest/source_catalog.yaml"))
    args = parser.parse_args()
    summary = json.loads(
        (args.discovery / "DISCOVERY_SUMMARY.json").read_text(encoding="utf-8")
    )
    spec = load_catalog(args.catalog).source(summary["source"])
    report = build_report(summary, spec)
    (args.discovery / "DISCOVERY_REPORT.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    c = report["counters"]
    print(f"{report['source']}: patterns={c['url_patterns']} "
          f"with_tables={c['patterns_with_tables']} "
          f"UNCLAIMED_WITH_TABLES={c['patterns_unclaimed_with_tables']}")
    for row in report["unclaimed_patterns_with_tables"][:25]:
        print(f"   !! {row['max_rows']:>5} rows  {row['table_shapes']} shape(s)  "
              f"{row['url_pattern'][:52]}")


if __name__ == "__main__":
    main()
