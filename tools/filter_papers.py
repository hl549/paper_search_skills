#!/usr/bin/env python3
"""Semantic relevance filter: an LLM reads TITLE + ABSTRACT (not tags).

Batch mode: ~15 papers per call, model returns JSON scores.
Endpoint: OpenAI-compatible API (default http://127.0.0.1:1234/v1).
Output: filtered.json — kept papers sorted by score desc + rejection count/sample.
"""
import argparse, json, os, re, sys, time
import urllib.request
import urllib.error

BASE = os.environ.get("PAPER_LLM_BASE") or os.environ.get("LMSTUDIO_BASE", "http://127.0.0.1:1234/v1")
MODEL = os.environ.get("PAPER_LLM_MODEL") or os.environ.get("LMSTUDIO_MODEL", "qwen3.8-27b")
API_KEY = os.environ.get("PAPER_LLM_API_KEY", "")   # OpenAI-compatible providers need a key; local vLLM/LM Studio can leave empty
_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.json")
PROFILES_FILE = os.path.join(_HERE, "profiles.json")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_profile(name):
    """Profile supplies the semantic criteria (user's own words + rubric).

    Falls back to config.json domain_context when no profile is given, so
    default behavior for existing callers is unchanged."""
    if not name:
        cfg = load_config()
        dc = cfg["domain_context"]
        return {"criteria": {"topic": f"{dc['field']} — general daily monitoring",
                             "subfields": dc.get("subfields", []),
                             "notes": dc.get("notes", "")}}
    with open(PROFILES_FILE) as f:
        profiles = json.load(f)["profiles"]
    if name not in profiles:
        raise SystemExit(
            f"profile '{name}' not found; available: {', '.join(profiles)}")
    p = profiles[name]
    return {"criteria": p.get("criteria", {}), "prefilter": p.get("prefilter")}


def _post(payload, headers, timeout):
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d["choices"][0]["message"]["content"]


def chat(messages, timeout=600):
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    base_payload = {"model": MODEL, "messages": messages, "max_tokens": 8000}
    try:
        return _post({**base_payload, "temperature": 0.1}, headers, timeout)
    except urllib.error.HTTPError as e:
        # Some OpenAI-compatible providers (e.g. certain GPT-class endpoints)
        # reject `temperature` with 400 -> retry without it.
        if e.code == 400:
            return _post(base_payload, headers, timeout)
        raise


def parse_json_block(text):
    """Extract first JSON array/object from possibly noisy LLM output."""
    m = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    for opener, closer in (("[", "]"), ("{", "}")):
        i = text.find(opener)
        j = text.rfind(closer)
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    raise ValueError("no JSON found in model output")


def build_prompt(criteria, batch):
    subfields = "\n".join(f"- {s}" for s in criteria.get("subfields", [])) or "- (No subfields specified; use the topic description.)"
    papers_txt = []
    for i, p in enumerate(batch, 1):
        abs_ = (p.get("abstract") or "").strip()
        if len(abs_) > 2500:
            abs_ = abs_[:2500] + " …[truncated]"
        papers_txt.append(
            f"[{i}] Journal/source: {p['journal']}\nTitle: {p['title']}"
            f"\nAbstract: {abs_ if len(abs_) >= 100 else '(No abstract; judge by the title alone.)'}")
    topic = criteria.get("topic", "")
    notes = criteria.get("notes", "")
    rubric = criteria.get("rubric_hint", "")
    return (
        "You are a senior reviewer in this field. Analyze the semantic relevance of "
        "each paper's TITLE + ABSTRACT to the user's research topic below. "
        "Do not rely primarily on journal names or category labels.\n\n"
        f"=== User's research topic ===\n{topic}\nSubfields of interest:\n{subfields}\n"
        f"{notes}\n"
        f"{rubric}\n\n"
        "=== Papers to evaluate ===\n" + "\n\n".join(papers_txt) + "\n\n"
        "For each paper, return: score (an integer from 0 to 10; >=6 means relevant), "
        "reason (one-sentence explanation of the judgment, in English, citing key content "
        "from the title or abstract), and tags (1-3 English subfield labels). "
        'Return only a JSON array, e.g. [{"id": <number>, "score": N, "reason": "...", "tags": ["..."]}, ...]'
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=os.path.join(_HERE, "candidates.json"))
    ap.add_argument("--out", default=os.path.join(_HERE, "filtered.json"))
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--profile", default=None,
                    help="named profile in profiles.json (criteria for scoring)")
    args = ap.parse_args()

    cfg = load_config()
    prof = load_profile(args.profile)
    criteria = prof["criteria"]
    bs = args.batch_size or cfg["filter"]["batch_size"]
    threshold = cfg["filter"]["keep_threshold"]
    cands = json.load(open(args.candidates))
    print(f"Filtering {len(cands)} papers, batch={bs}, keep>={threshold}, "
          f"profile={args.profile or '(default)'}", file=sys.stderr)

    results, rejected = [], []
    for start in range(0, len(cands), bs):
        batch = cands[start:start + bs]
        prompt = build_prompt(criteria, batch)
        last_err = None
        for attempt in range(3):
            try:
                raw = chat([{"role": "user", "content": prompt}])
                parsed = parse_json_block(raw)
                if isinstance(parsed, dict):
                    parsed = parsed.get("papers") or parsed.get("results")
                break
            except Exception as e:
                last_err = e
                time.sleep(5 * (attempt + 1))
        else:
            print(f"!! batch {start} failed after retries: {last_err}", file=sys.stderr)
            continue

        by_id = {}
        for item in parsed:
            try:
                idx = int(item.get("id")) - 1
            except Exception:
                continue
            if 0 <= idx < len(batch):
                p = dict(batch[idx])
                p["score"] = int(item.get("score", 0))
                p["reason"] = item.get("reason", "")
                p["tags"] = item.get("tags", [])
                by_id[idx] = p
        # papers the model skipped: mark low so they're auditable, not silently dropped
        for idx in range(len(batch)):
            if idx not in by_id:
                p = dict(batch[idx])
                p["score"] = 0
                p["reason"] = "(The model did not return this item.)"
                p["tags"] = []
                by_id[idx] = p
        for idx in range(len(batch)):
            (results if by_id[idx]["score"] >= threshold else rejected).append(by_id[idx])
        done = min(start + bs, len(cands))
        print(f"  batch {start // bs + 1}: scored {len(batch)} "
              f"(cumulative keep={sum(1 for r in results if True)})", file=sys.stderr)

    results.sort(key=lambda x: -x["score"])
    json.dump({"kept": results, "rejected_count": len(rejected),
               "rejected_sample": rejected[:30]}, open(args.out, "w"),
              ensure_ascii=False, indent=1)
    print(f"DONE kept={len(results)} rejected={len(rejected)} -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
