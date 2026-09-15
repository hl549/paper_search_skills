#!/usr/bin/env python3
"""Paper fetcher: arXiv (preprint) + OpenAlex (journals by ISSN).

Outputs unified candidate list (title+abstract+meta), deduped, excluding
already-pushed IDs. No API keys required.
Usage: python3 fetch_papers.py [--days 3] [--out candidates.json]
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.parse
from datetime import date, timedelta

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 paper-digest/1.0"
BASE_DIR = os.environ.get("PAPER_DIGEST_DIR", os.path.dirname(os.path.abspath(__file__)))
PROFILES_FILE = os.path.join(BASE_DIR, "profiles.json")

# Journal -> ISSN list (print + electronic). OpenAlex matches any of them.
JOURNALS = {
    "Journal of Fluid Mechanics":      ["0022-1120", "1469-468X"],
    "Physics of Fluids":               ["1070-6631", "1089-7666"],
    "Physical Review Fluids":          ["2469-990X", "2469-9918"],
    "Nature":                          ["0028-0836", "1476-4687"],
    "Science":                         ["0036-8075", "1095-9203"],
    "Nature Communications":           ["2041-1723"],
}

ARXIV_CATS = [  # fluid dynamics + optional ML-for-science crossover; extend freely
    "physics.flu-dyn",
]


def load_profile(name):
    """Load a named profile from profiles.json (cumulative, user-editable).

    A profile may override: journals {name:[issn...]}, arxiv_cats [...],
    prefilter {keywords:[...]} — coarse keyword gate applied to high-volume
    journals only; semantic judgment is always left to the LLM filter.
    """
    if not name:
        return {"journals": JOURNALS, "arxiv_cats": ARXIV_CATS, "prefilter": None}
    with open(PROFILES_FILE) as f:
        profiles = json.load(f)["profiles"]
    if name not in profiles:
        raise SystemExit(
            f"profile '{name}' not found; available: {', '.join(profiles)}")
    p = profiles[name]
    return {"journals": p.get("journals", JOURNALS),
            "arxiv_cats": p.get("arxiv_cats", ARXIV_CATS),
            "prefilter": p.get("prefilter")}


def apply_prefilter(cands, prefilter):
    """Coarse keyword gate: drops clearly off-topic items BEFORE the LLM.

    Only applied to sources named in 'apply_to' (default: all non-arXiv).
    arXiv category picks are already narrow and small — never gated."""
    if not prefilter or not prefilter.get("keywords"):
        return cands, 0
    kws = [k.lower() for k in prefilter["keywords"]]
    apply_to = set(prefilter.get("apply_to", ["journals"]))
    kept, dropped = [], 0
    for c in cands:
        is_jrnl = "OpenAlex" in (c.get("source") or "")
        if (is_jrnl and "journals" in apply_to) or \
           ((not is_jrnl) and "arxiv" in apply_to):
            text = ((c.get("title", "") + " " + c.get("abstract", ""))).lower()
            if not any(k in text for k in kws):
                dropped += 1
                continue
        kept.append(c)
    return kept, dropped


def state_file_for(profile_name):
    name = profile_name or "default"
    d = os.path.join(BASE_DIR, f"state_{name}")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "pushed_ids.json")


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def reconstruct_abstract(inv_idx):
    """OpenAlex stores abstracts as inverted index; rebuild the text."""
    if not inv_idx:
        return ""
    pos = {}
    for word, idxs in inv_idx.items():
        for i in idxs:
            pos[i] = word
    if not pos:
        return ""
    return " ".join(pos[i] for i in range(max(pos) + 1))


def fetch_arxiv(cats, since, max_results=150):
    q = "+OR+".join(f"cat:{c}" for c in cats)
    url = ("https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(q)
           + f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}")
    xml = http_get(url, timeout=40)
    out = []
    for m in re.finditer(r"<entry>(.*?)</entry>", xml, re.S):
        e = m.group(1)
        def tag(t):
            mm = re.search(f"<{t}[^>]*>(.*?)</{t}>", e, re.S)
            return (mm.group(1).strip() if mm else "")
        aid = tag("id").rsplit("/", 1)[-1]
        pub = tag("published")[:10]
        if pub < since:
            continue
        authors = [re.sub(r"<[^>]+>", "", a) for a in re.findall(
            r"<name>(.*?)</name>", e, re.S)]
        out.append({
            "source": "arXiv:preprint",
            "journal": "arXiv preprint (" + ",".join(cats) + ")",
            "title": re.sub(r"\s+", " ", tag("title")),
            "abstract": re.sub(r"\s+", " ", tag("summary")),
            "authors": authors[:8],
            "date": pub,
            "doi": "",
            "url": f"https://arxiv.org/abs/{aid}",
            "id": aid,
        })
    return out


def fetch_openalex(journals, since, per_page=200, max_pages=4):
    """Query each journal separately (weekly journals get drowned out in a
    combined date-sorted feed) and paginate with the cursor."""
    works = {}
    for jname, issns in journals.items():
        filt = ("primary_location.source.issn:" + "|".join(issns)
                + f",from_publication_date:{since}")
        url = ("https://api.openalex.org/works?filter=" + urllib.parse.quote(filt)
               + "&sort=publication_date:desc&per-page=%d" % per_page
               + "&select=id,doi,title,display_name,publication_date,"
                 "abstract_inverted_index,locations,authorships")
        n = 0
        for page in range(max_pages):
            try:
                data = json.loads(http_get(url, timeout=40))
            except Exception as ex:
                print(f"[OpenAlex] {jname} p{page}: {ex}", file=sys.stderr)
                break
            batch = data.get("results") or []
            if not batch:
                break
            for w in batch:
                works[w["id"]] = (w, jname)
                n += 1
            oldest = min((w.get("publication_date", "") for w in batch), default="")
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor or len(batch) < per_page or oldest[:4] == "0":
                break
            url = url + f"&cursor={urllib.parse.quote(cursor)}"
        print(f"[OpenAlex] {jname}: {n}", file=sys.stderr)
    return [(w, jname) for w, jname in works.values()]


def clean(s):
    """Strip MathML/HTML fragments some sources embed in titles."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def work_to_candidate(w, fallback_journal):
    locs = w.get("locations") or []
    src_name = ""
    for l in locs:
        s = (l.get("source") or {})
        if s.get("display_name"):
            src_name = s["display_name"]
            break
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    return {
        "source": f"OpenAlex:{fallback_journal}",
        "journal": fallback_journal,
        "title": clean(w.get("display_name") or ""),
        "abstract": clean(reconstruct_abstract(
            w.get("abstract_inverted_index"))),
        "authors": [a["author"]["display_name"] for a in
                    (w.get("authorships") or [])[:8]],
        "date": (w.get("publication_date") or "")[:10],
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else "",
        "id": doi or w["id"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "candidates.json"))
    ap.add_argument("--profile", default=None,
                    help="named profile in profiles.json (journals/arxiv_cats/prefilter)")
    args = ap.parse_args()

    prof = load_profile(args.profile)
    journals, arxiv_cats, prefilter = prof["journals"], prof["arxiv_cats"], prof["prefilter"]

    since = (date.today() - timedelta(days=args.days)).isoformat()
    sf = state_file_for(args.profile)
    pushed = set(json.load(open(sf))) if os.path.exists(sf) else set()

    cands, errors = [], []
    try:
        a = fetch_arxiv(arxiv_cats, since)
        print(f"[arXiv] {len(a)} papers (cats={arxiv_cats})", file=sys.stderr)
        cands += a
    except Exception as ex:
        errors.append(f"arXiv: {ex}")
    try:
        o = [work_to_candidate(w, j) for w, j in fetch_openalex(journals, since)]
        cands += o
    except Exception as ex:
        errors.append(f"OpenAlex: {ex}")

    # coarse keyword gate (high-volume journals only) before the semantic filter
    if prefilter:
        cands, dropped = apply_prefilter(cands, prefilter)
        print(f"[prefilter] dropped {dropped} off-keyword items", file=sys.stderr)

    # dedupe by DOI and arXiv id; drop already-pushed
    seen, fresh = set(), []
    for c in cands:
        key = (c["doi"] or "").lower() or c["id"].lower()
        if key in seen or key in pushed:
            continue
        # cross-source dedupe: same DOI as an arXiv entry already kept
        if c.get("doi") and any(c["doi"].lower() == s for s in seen):
            continue
        seen.add(key)
        fresh.append(c)

    json.dump(fresh, open(args.out, "w"), ensure_ascii=False, indent=1)
    print(f"TOTAL new candidates: {len(fresh)} (pushed-skipped excluded)", file=sys.stderr)
    if errors:
        print("ERRORS: " + "; ".join(errors), file=sys.stderr)
    for c in fresh[:6]:
        print(f"  [{c['journal']}] {c['title'][:80]} ({c['date']}) abstract={len(c['abstract'])}ch")


if __name__ == "__main__":
    main()
