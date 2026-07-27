from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from harvest import PARSER_VERSION
from harvest.catalog import load_catalog
from harvest.core import build_manifest, shard_for
from harvest.http import HttpClient, Response
from harvest.sources import get_adapter


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def run_harvest(
    *,
    source: str,
    dataset: str,
    work_items: list[dict],
    fetch: Callable[[str], Response],
    output_dir: Path,
    shard_id: int,
    shard_count: int,
    artifact_run_id: str,
    work_items_prepartitioned: bool = False,
) -> dict:
    adapter = get_adapter(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    records: list[dict] = []
    ledger: list[dict] = []
    columns: dict[tuple, dict] = {}
    describe_columns = getattr(adapter, "column_dictionary", None)
    for work in sorted(work_items, key=lambda item: item["key"]):
        if not work_items_prepartitioned and shard_for(work["key"], shard_count) != shard_id:
            continue
        response = fetch(work["url"])
        ledger_row = {
            "key": work["key"],
            "url": work["url"],
            "status_code": response.status_code,
            "status": response.status,
            "error": response.error,
        }
        if response.status != "ok":
            ledger.append(ledger_row)
            continue
        content_hash = hashlib.sha256(response.body).hexdigest()
        raw_name = f"{content_hash}.html.gz"
        (raw_dir / raw_name).write_bytes(gzip.compress(response.body))
        try:
            parsed = adapter.parse(dataset, response.body.decode("utf-8", errors="replace"), work)
        except ValueError as exc:
            ledger_row["status"] = "parse_error"
            ledger_row["error"] = str(exc)
            ledger_row["content_sha256"] = content_hash
            ledger.append(ledger_row)
            continue
        if describe_columns is not None:
            for entry in describe_columns(
                dataset, response.body.decode("utf-8", errors="replace"), work
            ):
                key = (entry["table_tag"], entry["column_key"], entry.get("source_title"))
                seen = columns.get(key)
                if seen is None:
                    entry["pages_observed"] = 1
                    columns[key] = entry
                else:
                    seen["pages_observed"] += 1
        retrieved = datetime.now(timezone.utc).isoformat()
        for row in parsed:
            row.update(
                retrieved_at_utc=retrieved,
                content_sha256=content_hash,
                parser_version=PARSER_VERSION,
                artifact_run_id=artifact_run_id,
                shard_id=shard_id,
            )
        records.extend(parsed)
        ledger_row.update(content_sha256=content_hash, raw_path=f"raw/{raw_name}", record_count=len(parsed))
        ledger.append(ledger_row)
    if records:
        pd.DataFrame(records).to_parquet(output_dir / "records.parquet", index=False)
    _write_jsonl(output_dir / "REQUEST_LEDGER.jsonl", ledger)
    if columns:
        _write_jsonl(
            output_dir / "COLUMN_DICTIONARY.jsonl",
            sorted(columns.values(), key=lambda row: (row["table_tag"], row["column_position"])),
        )
    manifest = build_manifest(
        output_dir,
        source=source,
        dataset=dataset,
        shard_id=shard_id,
        shard_count=shard_count,
        artifact_run_id=artifact_run_id,
    )
    (output_dir / "ARTIFACT_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "record_count": len(records),
        "request_count": len(ledger),
        "work_item_count": len(work_items),
        "column_count": len(columns),
        "manifest": manifest,
    }


def harvest_failure_reason(result: dict, *, source: str, dataset: str, shard: int) -> str | None:
    """A HARVEST THAT CAPTURED NOTHING MUST FAIL, NOT EXIT 0.

    Run 30226751762 reported 15 green jobs. Ten of them -- `team_season_stats` and
    `team_season_results`, about ten machine-hours -- had written a 0-byte ledger and no
    records at all, because both datasets were declared in the catalog with no parser
    branch behind them. Green meant "the process did not raise", which is not the claim
    anyone reading a workflow badge makes. Exit status now carries the claim.
    """
    if not result["work_item_count"]:
        return (
            f"no work items for {source}/{dataset} shard {shard}: the census emitted an "
            "empty partition, which is a census failure, not an empty harvest"
        )
    if not result["record_count"]:
        return (
            f"{source}/{dataset} shard {shard}: {result['work_item_count']} work items "
            "produced ZERO records -- check that this dataset has a parser branch"
        )
    return None


def run_fixture_harvest(*, pages: dict[str, str], **kwargs) -> dict:
    def fetch(url: str) -> Response:
        if url not in pages:
            return Response(url, 404, "absent", b"", "text/html")
        return Response(url, 200, "ok", pages[url].encode(), "text/html")

    return run_harvest(fetch=fetch, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--work-items", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--num-shards", type=int, required=True)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument(
        "--work-items-prepartitioned",
        action="store_true",
        help="Process every supplied work item because census already assigned this branch to the shard",
    )
    parser.add_argument("--catalog", type=Path, default=Path("harvest/source_catalog.yaml"))
    args = parser.parse_args()
    catalog = load_catalog(args.catalog)
    source_spec = catalog.source(args.source)
    catalog.dataset(args.source, args.dataset)
    work_items = [json.loads(line) for line in args.work_items.read_text(encoding="utf-8").splitlines() if line]
    client = HttpClient(delay_seconds=float(source_spec["delay_seconds"]))
    result = run_harvest(
        source=args.source,
        dataset=args.dataset,
        work_items=work_items,
        fetch=client.fetch,
        output_dir=args.out,
        shard_id=args.shard,
        shard_count=args.num_shards,
        artifact_run_id=args.run_id,
        work_items_prepartitioned=args.work_items_prepartitioned,
    )
    print(json.dumps(result, indent=2))
    reason = harvest_failure_reason(
        result, source=args.source, dataset=args.dataset, shard=args.shard
    )
    if reason:
        raise SystemExit(reason)


if __name__ == "__main__":
    main()
