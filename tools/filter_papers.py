#!/usr/bin/env python3
"""Semantic relevance filter: local LLM reads TITLE + ABSTRACT (not tags).

Batch mode: ~15 papers per call, model returns JSON scores.
Endpoint: LM Studio OpenAI-compatible API (default http://127.0.0.1:1234/v1).
Output: filtered.json — kept papers sorted by score desc + rejected list (for audit).
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
        return {"criteria": {"topic": f"{dc['field']} — 通用每日监控",
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
    subfields = "\n".join(f"- {s}" for s in criteria.get("subfields", [])) or "- (未指定子方向，以主题描述为准)"
    papers_txt = []
    for i, p in enumerate(batch, 1):
        abs_ = (p.get("abstract") or "").strip()
        if len(abs_) > 2500:
            abs_ = abs_[:2500] + " …[截断]"
        papers_txt.append(
            f"[{i}] 期刊/来源: {p['journal']}\n标题: {p['title']}"
            f"\n摘要: {abs_ if len(abs_) >= 100 else '(无摘要,仅凭标题判断)'}")
    topic = criteria.get("topic", "")
    notes = criteria.get("notes", "")
    rubric = criteria.get("rubric_hint", "")
    return (
        "你是该领域的资深审稿人。请对下面每篇论文的【标题+摘要】做语义相关性分析，"
        "判断其与用户研究主题(见下)的相关程度。不要依赖期刊名或分类标签做主要依据。\n\n"
        f"=== 用户研究主题 ===\n{topic}\n关注的子方向:\n{subfields}\n"
        f"{notes}\n"
        f"{rubric}\n\n"
        "=== 待评估论文 ===\n" + "\n\n".join(papers_txt) + "\n\n"
        "对每篇输出: score(0-10整数, >=6为相关), reason(一句话中文说明判断依据,"
        "指出标题或摘要中的关键内容), tags(1-3个中文子方向标签)。"
        '只返回 JSON 数组, 形如 [{"id": <序号>, "score": N, "reason": "...", "tags": ["..."]}, ...]'
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
                p["reason"] = "(模型未返回该条目)"
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
