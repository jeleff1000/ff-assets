"""
harvest/plan_matrix.py -- decide WHICH harvest jobs exist, before any of them are created.

The gap workflow used to declare its full matrix in YAML and filter inside each job. That
filter worked -- a shard-1 retry made zero HTTP requests anywhere else -- but GitHub still
created all fifteen jobs, and each one checked out the repo, installed dependencies and ran
pytest before discovering it had nothing to do. Retrying ONE shard spun up fourteen runners
to do nothing, and in the run list it is indistinguishable from re-running the whole
harvest.

So the matrix is COMPUTED here and consumed via `fromJSON`, and the target list lives in
Python rather than YAML because that is where it can be tested: the per-host concurrency
rule is arithmetic over these targets and the catalog's delay, not a number asserted
separately from them.

Run:  python -m harvest.plan_matrix [--only-source S] [--only-dataset D] [--only-shard N]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml

# The gap harvest's targets. `shards` is the DEFAULT partition count; a dispatch may
# override it. It does NOT set the request rate -- `max_parallel_for` does -- so slices can
# be made smaller without touching politeness.
TARGETS = (
    {"source": "statscrew", "dataset": "team_season_roster", "shards": 5},
    {"source": "statscrew", "dataset": "team_season_stats", "shards": 5},
    {"source": "statscrew", "dataset": "team_season_results", "shards": 5},
)

# Requests per second we are willing to send to any ONE host. The catalog's delay_seconds
# is per PROCESS, so N concurrent jobs at one host send N/delay requests per second.
#
# Set to 4, not 6, on evidence rather than taste: statscrew.com refused robots.txt to one
# runner in BOTH bulk runs so far (30226751762 and 30232968983), each time cancelling a
# whole partition. It is a small independent archive and the only pre-1994 tackle source
# we have found, so the downside of being blocked is losing the capture entirely, while
# the upside of more concurrency is finishing 1,084 pages in 4.5 minutes instead of 3.
MAX_REQUESTS_PER_SECOND_PER_HOST = 4.0


def plan(
    *, only_source: str = "", only_dataset: str = "", only_shard: str = "",
    shards: str = "",
) -> dict[str, list]:
    """SHARD COUNT AND CONCURRENCY ARE INDEPENDENT.

    Raising the shard count makes each slice smaller, so a wedged shard costs 1/N of the
    partition instead of 1/5 and is cheap to re-dispatch. It does NOT raise the request
    rate at the host -- that is set by `max_parallel_for` -- and conflating the two is how
    a "15 runners" plan becomes 15 req/s at a small independent archive.
    """
    include = []
    for target in TARGETS:
        target = dict(target)
        if shards:
            target["shards"] = int(shards)
        if only_source and only_source != target["source"]:
            continue
        if only_dataset and only_dataset != target["dataset"]:
            continue
        for shard in range(int(target["shards"])):
            if only_shard != "" and int(only_shard) != shard:
                continue
            include.append(
                {
                    "source": target["source"],
                    "dataset": target["dataset"],
                    "shards": target["shards"],
                    "shard": shard,
                }
            )
    return {"include": include}


def max_parallel_for(matrix: dict[str, list], catalog_path: Path) -> int:
    """The politeness cap, DERIVED from the jobs that will actually run.

    Worst case every running job sits on the same host, because a dispatch can be filtered
    to one source -- so the cap is set by the fastest (smallest) delay among the hosts in
    the plan, never by an average.
    """
    hosts = {job["source"] for job in matrix["include"]}
    if not hosts:
        return 1
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    fastest = min(float(catalog["sources"][host]["delay_seconds"]) for host in hosts)
    cap = int(MAX_REQUESTS_PER_SECOND_PER_HOST * fastest)
    return max(1, min(cap, len(matrix["include"])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only-source", default="")
    parser.add_argument("--only-dataset", default="")
    parser.add_argument("--only-shard", default="")
    parser.add_argument("--shards", default="",
                        help="override the partition count; smaller slices "
                             "shrink the blast radius of a wedged shard and do "
                             "NOT raise the per-host request rate")
    parser.add_argument("--catalog", type=Path, default=Path("harvest/source_catalog.yaml"))
    args = parser.parse_args()
    matrix = plan(
        only_source=args.only_source,
        only_dataset=args.only_dataset,
        only_shard=args.only_shard,
        shards=args.shards,
    )
    if not matrix["include"]:
        raise SystemExit(
            "the requested filters select NO jobs -- check --only-source / --only-dataset "
            "/ --only-shard against harvest/plan_matrix.py TARGETS"
        )
    cap = max_parallel_for(matrix, args.catalog)
    print(f"planned {len(matrix['include'])} job(s), max-parallel {cap}", flush=True)
    for job in matrix["include"]:
        print(f"    {job['source']}/{job['dataset']} shard {job['shard']}", flush=True)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"matrix={json.dumps(matrix)}\n")
            handle.write(f"max_parallel={cap}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
