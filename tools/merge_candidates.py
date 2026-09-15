#!/usr/bin/env python3
"""Merge candidate JSON files (cite_graph flat / fetch_papers outputs) by paper id.

Usage:
  python3 merge_candidates.py --in a.json b.json c.json --out combined.json [--tags tag1,tag2]
Each input is either a list of papers or {"papers": [...]}. Papers lacking 'id' get one from DOI/URL.
Tags are added to each paper's 'source_tags' for downstream audit (which list it came from).
"""
import argparse, json


def norm_id(p):
    pid = p.get("id")
    if not pid:
        doi = (p.get("doi") or "").strip().lower()
        url = (p.get("url") or "").strip()
        pid = ("doi:" + doi) if doi else ("url:" + url)
    return pid


def load_papers(path, tag):
    with open(path) as f:
        d = json.load(f)
    papers = d.get("papers", d) if isinstance(d, dict) else d
    out = []
    for p in papers:
        q = dict(p)
        q.setdefault("source_tags", [])
        st = q["source_tags"]
        if tag and tag not in st:
            st.append(tag)
        out.append(q)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inputs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tags", default="", help="comma list, one per input (default: filename stem)")
    args = ap.parse_args()

    tags = [t for t in args.tags.split(",") if t] or [x.rsplit(".", 1)[0].rsplit("/", 1)[-1] for x in args.inputs]
    assert len(tags) == len(args.inputs), "one tag per input file"

    seen, merged = {}, []
    dupes = 0
    for path, tag in zip(args.inputs, tags):
        for p in load_papers(path, tag):
            pid = norm_id(p)
            if pid in seen:
                prev = seen[pid]
                merged_tags = set(prev.get("source_tags") or []) | set(p.get("source_tags") or [])
                # prefer the entry with abstract + more metadata
                if not (prev.get("abstract") and len(str(prev["abstract"])) >= 200) and \
                   p.get("abstract") and len(str(p["abstract"])) >= 200:
                    prev.update({k: v for k, v in p.items() if v is not None})
                prev["source_tags"] = sorted(merged_tags)
                dupes += 1
            else:
                seen[pid] = p
                merged.append(p)

    with open(args.out, "w") as f:
        json.dump(merged, f, ensure_ascii=False)
    n_abs = sum(1 for p in merged if p.get("abstract") and len(str(p["abstract"])) >= 200)
    print(f"MERGED {len(merged)} unique papers ({dupes} duplicates folded); "
          f"{n_abs} with usable abstract -> {args.out}")


if __name__ == "__main__":
    main()
