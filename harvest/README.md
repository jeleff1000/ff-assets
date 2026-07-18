# External historical witness harvest

This package inventories and harvests approved football-data namespaces into
GitHub Actions artifacts. It does not write to the production lake.

| Source | Dataset | Years | Intended witness use |
|---|---|---:|---|
| NFL.com | team-season roster | 1920-2025 | roster identity |
| Pro Football Archives | player game participation | 1919-2025 | team/game identity and participation |
| StatsCrew | team-season roster | 1920-2025 | independent roster identity |

The StatsCrew URL inventory in `team_seasons.csv` is generated from the local
schedule's year/team keys. It only enumerates URLs; harvested StatsCrew pages are
the witness evidence.

## Workflows

- `witness-probe.yml`: one explicitly supplied page for parser/access checks.
- `witness-census.yml`: catalog-driven, all-year namespace census. Review its
  surface catalog, schema fingerprints, request ledger, and usability report.
- `witness-harvest.yml`: reruns the reviewed census and emits normalized Parquet,
  gzipped raw pages, request ledger, and a checksummed manifest.

Both bulk workflows are manual, deterministically sharded, bounded to eight
parallel jobs, and retain artifacts for 14 days. They obey `robots.txt`, use a
source delay, retry transient responses, and never bypass blocks.

## Local validation

```bash
pip install -r requirements-harvest.txt
python -m pytest tests -q
python -m harvest.census --source nflcom --dataset team_season_roster \
  --year-start 1921 --year-end 1921 --shard 0 --num-shards 1 --out out/census
python -m harvest.runner --source nflcom --dataset team_season_roster \
  --work-items out/census/WORK_ITEMS.jsonl --work-items-prepartitioned \
  --shard 0 --num-shards 1 --out out/harvest
```

Treat `canonical_candidate` as structurally usable evidence, not automatic truth.
The local intake verifies every manifest and checksum before reconciliation.
