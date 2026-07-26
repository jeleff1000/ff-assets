"""Turn a discovery run into the human-readable question: what did we find, and do we
ALREADY HOLD IT?

Discovery output is a machine inventory. This renders the part a person has to decide
on -- and it must distinguish three states, not two:

  HELD                we have actually fetched these pages (HOLDINGS.json, generated
                      from the real capture artifacts). Do NOT re-fetch them.
  DECLARED_NOT_HELD   a dataset in the catalog claims this prefix, but nothing was ever
                      harvested for it.
  UNCLAIMED           nobody declared it at all -- the unknown unknowns.

The two-state version of this report was actively misleading. It counted a pattern as
covered whenever a DECLARED dataset claimed the prefix, so nflcom reported "0 unclaimed"
while /players/{slug}/stats/* was attributed to player_game_stats -- a dataset that is
declared and has never run. A report that says we have something we have never fetched
is worse than no report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harvest.catalog import load_catalog


def load_holdings(path: Path) -> dict:
    """What we ACTUALLY hold, per source -> url pattern -> pages fetched."""
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    held: dict[str, dict[str, int]] = {}
    for source, datasets in raw.get("sources", {}).items():
        for dataset, spec in datasets.items():
            for pattern, count in (spec.get("url_patterns") or {}).items():
                bucket = held.setdefault(source, {})
                bucket[pattern] = bucket.get(pattern, 0) + int(count or 0)
    return held


def build_report(summary: dict, catalog_spec: dict, holdings: dict | None = None) -> dict:
    claims: list[tuple[str, str]] = []
    for name, dataset in (catalog_spec.get("datasets") or {}).items():
        for prefix in dataset.get("allowed_prefixes", []):
            claims.append((prefix, name))
    claims.sort(key=lambda c: -len(c[0]))

    held = (holdings or {}).get(summary["source"], {})
    rows = []
    for pattern in summary["patterns"]:
        claimed_by = next(
            (name for prefix, name in claims
             if prefix != "/" and pattern["url_pattern"].startswith(prefix)),
            None,
        )
        pages_held = held.get(pattern["url_pattern"], 0)
        state = ("HELD" if pages_held else
                 "DECLARED_NOT_HELD" if claimed_by else "UNCLAIMED")
        rows.append({
            "state": state,
            "pages_already_held": pages_held,
            "url_pattern": pattern["url_pattern"],
            "pages_seen": pattern["pages_seen"],
            "table_shapes": pattern["n_distinct_table_shapes"],
            "max_rows": max((s["max_rows"] for s in pattern["table_shapes"]), default=0),
            "surfaces": sorted({s["surface"] for s in pattern["table_shapes"]}),
            "claimed_by_dataset": claimed_by,
            "example_url": pattern["example_urls"][0] if pattern["example_urls"] else None,
        })
    with_tables = [r for r in rows if r["table_shapes"]]
    # WORTH FETCHING = carries tables and we do NOT already hold it. Re-fetching pages
    # we hold wastes the politeness budget at small independent archives for nothing.
    worth_fetching = [r for r in with_tables if r["state"] != "HELD"]
    unclaimed = [r for r in with_tables if r["state"] == "UNCLAIMED"]
    return {
        "source": summary["source"],
        "counters": {
            **summary["counters"],
            "patterns_unclaimed_with_tables": len(unclaimed),
            "patterns_held": sum(1 for r in with_tables if r["state"] == "HELD"),
            "patterns_declared_not_held": sum(
                1 for r in with_tables if r["state"] == "DECLARED_NOT_HELD"),
            "patterns_worth_fetching": len(worth_fetching),
        },
        "worth_fetching": sorted(worth_fetching, key=lambda r: -r["max_rows"]),
        "unclaimed_patterns_with_tables": sorted(
            unclaimed, key=lambda r: -r["max_rows"]
        ),
        "all_patterns": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("harvest/source_catalog.yaml"))
    parser.add_argument("--holdings", type=Path, default=Path("harvest/HOLDINGS.json"))
    args = parser.parse_args()
    summary = json.loads(
        (args.discovery / "DISCOVERY_SUMMARY.json").read_text(encoding="utf-8")
    )
    spec = load_catalog(args.catalog).source(summary["source"])
    report = build_report(summary, spec, load_holdings(args.holdings))
    (args.discovery / "DISCOVERY_REPORT.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    c = report["counters"]
    print(f"{report['source']}: patterns={c['url_patterns']} "
          f"with_tables={c['patterns_with_tables']} HELD={c['patterns_held']} "
          f"declared_not_held={c['patterns_declared_not_held']} "
          f"unclaimed={c['patterns_unclaimed_with_tables']} "
          f"WORTH_FETCHING={c['patterns_worth_fetching']}")
    for row in report["worth_fetching"][:25]:
        print(f"   -> {row['state']:18s} {row['max_rows']:>5} rows  "
              f"{row['table_shapes']} shape(s)  {row['url_pattern'][:46]}")


if __name__ == "__main__":
    main()
