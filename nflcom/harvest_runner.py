"""
nflcom/harvest_runner.py -- self-contained NFL.com player-stats harvester for GitHub Actions shards.

One-off bulk crawl distributed across a workflow matrix: each job owns the players whose slug hashes
into its shard and fetches their stat views (logs/career/splits/situational, per active year for the
per-year views). Parsed rows go straight to parquet (no HTML cache on runners); the parser is copied
verbatim from the local harvester (yahoo_oauth scripts/sota_recon/nflcom_harvest.py) so the output
merges cleanly into the local corpus.

    python nflcom/harvest_runner.py --shard 0 --num-shards 60 --out out/
"""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
POLITE_SECONDS = 0.4  # per-request pause within one shard job


def http_get(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                html = r.read().decode("utf-8", errors="replace")
            time.sleep(POLITE_SECONDS)
            return html
        except urllib.error.HTTPError as e:
            time.sleep(POLITE_SECONDS)
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(30.0 * (attempt + 1))
                continue
            if e.code >= 500 and attempt < 2:
                time.sleep(3.0)
                continue
            return None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(3.0)
            if attempt == 2:
                return None
    return None


# ---- parser: copied verbatim from the local harvester ----------------------------------------
def _clean(raw: str) -> str:
    raw = re.sub(r"<svg.*?</svg>", " ", raw, flags=re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return _html.unescape(re.sub(r"\s+", " ", raw)).strip()


def _dedup(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for h in headers:
        key = re.sub(r"[^a-z0-9]+", "_", h.lower()).strip("_") or "col"
        seen[key] = seen.get(key, 0) + 1
        out.append(key if seen[key] == 1 else f"{key}_{seen[key]}")
    return out


def parse_all_tables(html: str) -> list[dict]:
    if not html:
        return []
    out = []
    for m in re.finditer(r"<table.*?</table>", html, re.S):
        seg = m.group(0)
        pre = html[max(0, m.start() - 500):m.start()]
        pre = re.sub(r"<svg.*?</svg>", " ", pre, flags=re.S)
        heads = re.findall(r"<(?:h[1-6]|caption)[^>]*>(.*?)</(?:h[1-6]|caption)>", pre, re.S)
        caption = _clean(heads[-1]) if heads else ""
        thead = re.search(r"<thead.*?</thead>", seg, re.S)
        if thead:
            hdr_cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", thead.group(0), re.S)
        else:
            first_tr = re.search(r"<tr.*?</tr>", seg, re.S)
            hdr_cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", first_tr.group(0), re.S) if first_tr else []
        headers = [_clean(c) for c in hdr_cells]
        headers = [h for h in headers if h != ""]
        if len(headers) < 2:
            continue
        keys = _dedup(headers)
        tbody = re.search(r"<tbody.*?</tbody>", seg, re.S)
        body = tbody.group(0) if tbody else seg
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", body, re.S):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if not tds:
                continue
            cells = [_clean(c) for c in tds]
            if len([c for c in cells if c]) == 0:
                continue
            row = dict(zip(keys, cells[:len(keys)]))
            ps = re.search(r"/players/([a-z0-9\-]+)/", tr)
            ts = re.search(r"/teams/([a-z0-9\-]+)/", tr)
            if ps:
                row["_player_slug"] = ps.group(1)
            if ts:
                row["_team_slug"] = ts.group(1)
            rows.append(row)
        if rows:
            out.append({"caption": caption, "headers": headers, "keys": keys, "rows": rows})
    return out
# ----------------------------------------------------------------------------------------------


def crawl_player_view(slug: str, view: str, yrs: list[int]) -> list[dict]:
    rows = []
    if view == "career":
        html = http_get(f"https://www.nfl.com/players/{slug}/stats/")
        for t in parse_all_tables(html or ""):
            for r in t["rows"]:
                r["_view"] = "career"; r["_table"] = t["caption"]; r["nflcom_slug"] = slug
                rows.append(r)
    else:
        for year in yrs:
            html = http_get(f"https://www.nfl.com/players/{slug}/stats/{view}/{year}/")
            for t in parse_all_tables(html or ""):
                for r in t["rows"]:
                    r["_view"] = view; r["_table"] = t["caption"]
                    r["nflcom_slug"] = slug; r["season"] = year
                    if view == "logs" and "fum" in t["keys"] and "lost" in t["keys"]:
                        r["fumbles"] = r.get("fum"); r["fumbles_lost"] = r.get("lost")
                    rows.append(r)
    return rows


def shard_of(slug: str, n: int) -> int:
    return int(hashlib.sha1(slug.encode()).hexdigest(), 16) % n


def main() -> None:
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--num-shards", type=int, required=True)
    ap.add_argument("--views", default="logs,career,splits,situational")
    ap.add_argument("--universe", default="nflcom/player_universe.json")
    ap.add_argument("--done", default="nflcom/done_views.json")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()

    uni = json.loads(Path(a.universe).read_text())
    done = json.loads(Path(a.done).read_text()) if Path(a.done).exists() else {}
    views = a.views.split(",")
    mine = sorted(s for s in uni if shard_of(s, a.num_shards) == a.shard)
    print(f"shard {a.shard}/{a.num_shards}: {len(mine)} players")

    buckets: dict[str, list[dict]] = {v: [] for v in views}
    done_now: dict[str, list[str]] = {v: [] for v in views}
    t0 = time.time()
    for i, slug in enumerate(mine, 1):
        yrs = sorted(set(uni[slug]))
        for v in views:
            if slug in set(done.get(v, [])):
                continue
            buckets[v].extend(crawl_player_view(slug, v, yrs))
            done_now[v].append(slug)
        if i % 25 == 0:
            print(f"  [{i}/{len(mine)}] {time.time()-t0:,.0f}s elapsed", flush=True)

    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    for v in views:
        if buckets[v]:
            pd.DataFrame(buckets[v]).astype(str).to_parquet(
                outdir / f"player_{v}_shard{a.shard:03d}.parquet", index=False)
    (outdir / f"done_shard{a.shard:03d}.json").write_text(json.dumps(done_now))
    print("wrote:", {v: len(buckets[v]) for v in views})


if __name__ == "__main__":
    main()
