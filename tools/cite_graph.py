#!/usr/bin/env python3
"""Citation-graph search: BFS over OpenAlex citing/references edges from seed papers.

Finds related target literature through citation relationships:
  - cites direction : "who cited this paper" (forward snowball, recent work)
  - refs  direction : "what this paper references" (backward snowball),
                      and recursively depth>1 = "papers citing/being-cited-by
                      the chain" e.g. 引用引用文献的文献

Output is in the SAME candidate schema as fetch_papers.py (+via path fields),
so it plugs straight into filter_papers.py / make_digest.py:

  python3 cite_graph.py --seed 10.1017/jfm.2026.11893 --depth 2 \
      --directions cites,refs --since-year 2021 --out cite_candidates.json
  python3 filter_papers.py --candidates cite_candidates.json \
      --out cite_filtered.json --profile <topic>

Verified API facts (2026-08):
  - detail /works/{WID}         -> full record incl. abstract_inverted_index,
                                   referenced_works (list of bare URLs), cited_by_count
  - citing /works?filter=cites:{WID}&sort=publication_date:desc&per-page=N
                                  -> bulk records with abstracts
  - batch id resolution (/works?ids=, filter=id:a|b) is UNRELIABLE on this API
    version (400/empty) -> resolve references one detail call at a time;
    results cached in cite_cache/ so re-runs are free.
"""
import argparse, json, os, re, sys, time, urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 paper-digest/1.0"
BASE_DIR = os.environ.get("PAPER_DIGEST_DIR", os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "cite_cache")

def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def reconstruct_abstract(inv):
    if not inv:
        return ""
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    return " ".join(w for _, w in sorted(pos)) if pos else ""


def get_json(url, tries=3):
    last = None
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (t + 1))
    raise RuntimeError(f"GET failed after {tries} tries: {last}\nurl={url}")


def work_detail(wid):
    """Full record for a W-id, disk-cached."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    f = os.path.join(CACHE_DIR, wid + ".json")
    if os.path.exists(f):
        with open(f) as fh:
            return json.load(fh)
    d = get_json(f"https://api.openalex.org/works/{wid}")
    try:
        with open(f, "w") as fh:
            json.dump(d, fh)
    except OSError:
        pass
    return d


def resolve_seed(s):
    """Accept DOI / arXiv id / W-id / openalex URL -> (W-id, title)."""
    s = s.strip()
    if re.fullmatch(r"W\d+", s):
        d = work_detail(s)
        return s, clean(d.get("display_name")) or "(untitled)"
    if s.lower().startswith(("http://", "https://")):
        m = re.search(r"/(W\d+)$", s.rstrip("/"))
        if m:
            d = work_detail(m.group(1))
            return m.group(1), clean(d.get("display_name")) or "(untitled)"
    # DOI (with or without scheme) or arXiv id -> resolve via doi filter / search
    cand = s.split("/")[-1] if s.count("/") >= 2 and not s.startswith("http") else s
    try:
        d = get_json("https://api.openalex.org/works?filter=doi:" +
                     urllib.parse.quote(cand, safe="") + "&per-page=1")
        if d["results"]:
            r0 = d["results"][0]
            return r0["id"].split("/")[-1], clean(r0.get("display_name")) or "(untitled)"
    except Exception:  # noqa: BLE001 - not a DOI, fall through to arXiv search
        pass
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", cand):
        q = ("https://api.openalex.org/works?filter=locations.landing_page_url:"
             + urllib.parse.quote(f"https://arxiv.org/abs/{cand.split('v')[0]}", safe="")
             + "&per-page=1")
        d = get_json(q)
        if d["results"]:
            r0 = d["results"][0]
            return r0["id"].split("/")[-1], clean(r0.get("display_name")) or "(untitled)"
    raise SystemExit(f"cannot resolve seed to an OpenAlex work: {s!r}")


import urllib.parse  # noqa: E402 (used in resolve_seed)


def work_to_candidate(w, via, depth):
    locs = w.get("locations") or []
    src_name = ""
    for l in locs:
        s = (l.get("source") or {})
        if s.get("display_name"):
            src_name = s["display_name"]
            break
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    return {
        "source": f"cite-graph:{src_name}" if src_name else "cite-graph",
        "journal": src_name,
        "title": clean(w.get("display_name")),
        "abstract": clean(reconstruct_abstract(w.get("abstract_inverted_index"))),
        "authors": [a["author"]["display_name"] for a in (w.get("authorships") or [])[:8]],
        "date": (w.get("publication_date") or "")[:10],
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else "",
        "id": doi or w["id"],
        "cited_by_count": w.get("cited_by_count", 0),
        "depth": depth,
        "via": via,          # list of hops: ["W-seed", "cites", "W-mid", "refs"]
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="append", required=True,
                    help="DOI / arXiv id / W-id; repeatable")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--directions", default="cites,refs",
                    help="comma list: cites and/or refs (default both)")
    ap.add_argument("--since-year", type=int, default=None,
                    help="only keep hops published this year or later "
                         "(default: current-3 for cites, none for refs)")
    ap.add_argument("--cites-per-seed", type=int, default=25,
                    help="max citing works fetched per seed (most recent first)")
    ap.add_argument("--refs-to-resolve", type=int, default=18,
                    help="max reference ids resolved per hop (detail calls)")
    ap.add_argument("--refs-max-depth", type=int, default=1,
                    help="refs direction expands only up to this depth "
                         "(default 1; each extra level = many detail API calls)")
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "cite_candidates.json"))
    args = ap.parse_args()

    dirs = [d.strip() for d in args.directions.split(",") if d.strip() in ("cites", "refs")]
    import datetime as _dt
    cur_year = _dt.date.today().year
    cites_since = args.since_year or (cur_year - 3)
    refs_since = args.since_year

    # frontier: list of (wid, via_list); seeds first
    seen = set()
    found = {}          # wid -> candidate
    frontier = []
    for s in args.seed:
        wid, title = resolve_seed(s)
        print(f"SEED  {wid}  {title[:70]}")
        w = work_detail(wid)
        seen.add(wid)
        found[wid] = work_to_candidate(w, ["seed"], 0)
    # rebuild frontier properly (seeds, depth 0 -> expand to depth 1)
    seeds = list(found.keys())
    level_results = {0: list(seeds)}
    next_frontier = [(wid, ["seed"]) for wid in seeds]

    for depth in range(1, args.depth + 1):
        print(f"\n=== LEVEL {depth} (expanding {len(next_frontier)} frontier nodes) ===")
        new_frontier = []
        for parent_wid, parent_via in next_frontier:
            if "cites" in dirs:
                # bulk citing query, most recent first
                url = ("https://api.openalex.org/works?filter=cites:" + parent_wid
                       + f"&sort=publication_date:desc&per-page={args.cites_per_seed}")
                try:
                    d = get_json(url)
                except Exception as e:  # noqa: BLE001
                    print(f"  cites query failed for {parent_wid}: {e}", file=sys.stderr)
                    continue
                n = 0
                for x in d["results"]:
                    yr = (x.get("publication_date") or "")[:4]
                    if yr and int(yr) < cites_since:
                        break                      # sorted desc -> can stop early
                    w2 = x["id"].split("/")[-1]
                    via = parent_via + ["cites", w2]
                    if w2 not in seen:
                        seen.add(w2)
                        found[w2] = work_to_candidate(x, [parent_wid, "cites"] if depth == 1 else via, depth)
                        new_frontier.append((w2, via))
                        n += 1
                print(f"  cites<- {parent_wid}: fetched, +{n} new (since {cites_since})")
            if "refs" in dirs and depth <= args.refs_max_depth:
                # detail gives referenced_works as bare URLs; resolve individually
                try:
                    w = work_detail(parent_wid)
                except Exception as e:  # noqa: BLE001
                    print(f"  detail failed for {parent_wid}: {e}", file=sys.stderr)
                    continue
                refs = (w.get("referenced_works") or [])[: args.refs_to_resolve]
                n = 0
                for ru in refs:
                    rwid = ru.split("/")[-1] if isinstance(ru, str) else None
                    if not rwid or rwid in seen:
                        continue
                    try:
                        rx = work_detail(rwid)
                    except Exception as e:  # noqa: BLE001
                        print(f"    ref resolve failed {rwid}: {e}", file=sys.stderr)
                        continue
                    yr = (rx.get("publication_date") or "")[:4]
                    if refs_since and yr and int(yr) < refs_since:
                        continue
                    seen.add(rwid)
                    via = parent_via + ["refs", rwid]
                    found[rwid] = work_to_candidate(rx, [parent_wid, "refs"] if depth == 1 else via, depth)
                    new_frontier.append((rwid, via))
                    n += 1
                print(f"  refs-> {parent_wid}: resolved, +{n} new")

        level_results[depth] = [c["id"] for c in found.values() if c["depth"] == depth]
        next_frontier = new_frontier
        if not next_frontier:
            print("frontier empty -> stop")
            break

    # drop seeds from output? keep them flagged with depth 0 (useful context) but mark
    out = [c for c in found.values() if c["depth"] > 0]
    payload = {
        "seeds": [found[w] for w in seeds],
        "directions": dirs,
        "levels": level_results,
        "candidates": sorted(out, key=lambda c: (c["date"], -c.get("cited_by_count", 0)), reverse=True),
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    # filter_papers.py expects a flat list -> also write the flat file
    flat_path = args.out.replace(".json", "_flat.json") if ".json" in args.out else args.out + "_flat"
    with open(flat_path, "w") as fh:
        json.dump(payload["candidates"], fh, ensure_ascii=False, indent=1)

    print(f"\nDONE: {len(out)} unique papers found via citation graph")
    for lv in sorted(k for k in level_results if k > 0):
        print(f"  depth {lv}: {len(level_results[lv])}")
    print(f"flat candidates -> {flat_path}   (feed to filter_papers.py --candidates)")


if __name__ == "__main__":
    main()
