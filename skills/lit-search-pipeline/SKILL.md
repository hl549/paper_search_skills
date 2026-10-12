---
name: lit-search-pipeline
description: Seed papers + a topic description → automatically find related literature → export an Excel paper list. OpenAlex public API + any OpenAI-compatible LLM. Use when a user asks to find papers citing X, literature related to X, or a literature search or review.
---

# Literature Search Pipeline (citation graph + topic search → LLM screening → Excel)

## When to use

The user supplies one or more seed papers and a topic description (optional; derive it from the seed papers if omitted), and asks for related literature organized in a table.

## Dependencies

- Python ≥ 3.9; the only third-party package is `openpyxl` (`pip install -r requirements.txt`).
- Public API: OpenAlex; these scripts make requests without an API key. Provider access limits still apply.
- Semantic screening requires an **OpenAI-compatible** LLM, either local (vLLM, LM Studio, or Ollama) or cloud-hosted. Configure it through the environment variables below.

## LLM configuration (Step 4 only; other steps do not need it)

```bash
export PAPER_LLM_BASE="http://127.0.0.1:1234/v1"   # Or https://api.openai.com/v1, https://api.deepseek.com/v1, etc.
export PAPER_LLM_MODEL="qwen3.8-27b"              # Any model name supported by the endpoint
export PAPER_LLM_KEY=""                          # Leave empty locally; use sk-... for cloud APIs
```

The compatibility layer omits Authorization when no key is set, retries cloud HTTP 400 responses without `temperature` (for example, when a GPT-family endpoint rejects that parameter), and strips surrounding JSON code fences from responses.

**Current-script compatibility:** `filter_papers.py` reads `PAPER_LLM_API_KEY`, not `PAPER_LLM_KEY`. Retain the configuration above and pass the key through explicitly:

```bash
export PAPER_LLM_API_KEY="$PAPER_LLM_KEY"
```

`LMSTUDIO_BASE` and `LMSTUDIO_MODEL` remain fallback environment variables when `PAPER_LLM_BASE` or `PAPER_LLM_MODEL` is unset. Screening uses the OpenAI-compatible `/chat/completions` interface directly through `urllib`; there is no SDK dependency. The model must return a JSON array. Reasons and tags intentionally remain in Chinese.

## Six-step pipeline

Run from the repository root (called `.` below); keep intermediate files in `data/`. The original command examples are retained in translation below. Where those examples differ from the scripts currently in this repository, an explicit compatibility note and a supported command follow them. Use the supported commands for execution.

Before running, prepare the configuration files where the scripts actually read them:

```bash
mkdir -p data
cp templates/config.json tools/config.json
cp templates/profiles.example.json tools/profiles.json
export PAPER_DIGEST_DIR="$PWD/data"
```

Edit `tools/profiles.json` using the profile guidance below, then copy the edited profile for retrieval:

```bash
cp tools/profiles.json data/profiles.json
```

`PAPER_DIGEST_DIR` controls citation-cache and retrieval configuration/state paths. It does not change screening paths: `filter_papers.py` reads `config.json` and `profiles.json` beside its own script in `tools/`. Explicit output arguments keep results in `data/`.

### Step 1 — Build the citation graph

Original command examples:

```bash
python3 tools/cite_graph.py --seed-arxiv <arXivId>          # From an arXiv ID
# Or
python3 tools/cite_graph.py --seed-doi <DOI> --seed-title "..." --seed-authors "..." --seed-year 2024
```

The original workflow describes `data/cite_graph.json` containing citing and referenced papers, with a default depth of two.

**Current-script compatibility:** the script accepts repeatable `--seed` arguments containing a DOI, arXiv ID, or OpenAlex W-ID; it does not implement the separate seed flags above. Set `--out` explicitly. It writes both the graph and a flat candidate list; the latter is the input for merging and screening. Citation traversal defaults to two levels, while reference expansion defaults to one (`--refs-max-depth 1`).

```bash
python3 tools/cite_graph.py --seed "<DOI-or-arXiv-ID>" --depth 2 --directions cites,refs --since-year 2010 --cites-per-seed 25 --refs-to-resolve 18 --out data/cite_candidates.json
```

Outputs: `data/cite_candidates.json` and `data/cite_candidates_flat.json`. Detail records are cached in `data/cite_cache/` with the setup above. Without an override, the cache is beside the script. The earlier workflow reported roughly 400–600 candidates in about four minutes for a typical depth-two run; actual time and count depend on the seeds and API responses.

### Step 2 — Search by topic description

Original command examples:

```bash
cp templates/profiles.example.json templates/profiles.json   # First run
# Edit templates/profiles.json: set the slug, criteria.rubric_hint (contrasting score bands work best), and prefilter keywords
python3 tools/fetch_papers.py --profile <slug> --query "<topic description>"
```

The original workflow describes `data/<slug>_search.json`, with journal papers retrieved through OpenAlex and preprints through the arXiv API, then merged and deduplicated automatically.

**Current-script compatibility:** retrieval scans the profile's journal ISSNs and arXiv categories; there is no `--query` option. Put the topic description in `criteria.topic` for Step 4 and coarse keywords in `prefilter.keywords`. Use `--days` to set the search window (a large value for a one-time review) and `--out` to choose the output. Retrieval reads `data/profiles.json` when `PAPER_DIGEST_DIR` is set as above, rather than `templates/profiles.json`.

```bash
python3 tools/fetch_papers.py --profile my_topic --days 3650 --out data/my_topic_search.json
```

Output: `data/my_topic_search.json`; duplicate DOI/arXiv IDs and IDs already recorded in the profile's `pushed_ids.json` are excluded. Retrieval state is stored under `data/state_my_topic/` with this setup.

### Step 3 — Merge and deduplicate

Original command example:

```bash
python3 tools/merge_candidates.py --cite-graph data/cite_graph.json --search data/<slug>_search.json
```

The original workflow describes `data/candidates.json` and DOI-first, normalized-title deduplication.

**Current-script compatibility:** the implemented interface is `--in` plus `--out`. Pass the flat citation list, not the graph object. Deduplication uses the existing paper `id`, falling back to a normalized DOI or URL when no ID is present; it does not normalize titles. `source_tags` preserve input provenance. For a duplicate, metadata is replaced only if the existing abstract is missing or shorter than 200 characters and the incoming abstract has at least 200 characters.

```bash
python3 tools/merge_candidates.py --in data/cite_candidates_flat.json data/my_topic_search.json --out data/candidates.json --tags cite,journal
```

Output: `data/candidates.json`.

### Step 4 — LLM semantic screening

Original command example:

```bash
python3 tools/filter_papers.py --profile <slug>
```

The original workflow describes `data/<slug>_filtered.json`, filtered using `keep_threshold` from `templates/config.json` (default: keep scores ≥6/10), in batches of 15 papers, with resume support that skips papers already scored.

**Current-script compatibility:** copy the template to `tools/config.json`, where the script reads `filter.keep_threshold` and `filter.batch_size`. Supply explicit candidate and output paths; their defaults are beside the script. This implementation does not write a checkpoint or skip previously scored papers, so rerunning screens the candidates again. Failed batches are retried up to three times and then skipped; review stderr before accepting the output.

```bash
python3 tools/filter_papers.py --candidates data/candidates.json --profile my_topic --out data/my_topic_filtered.json
```

Output: `data/my_topic_filtered.json`, containing `kept`, `rejected_count`, and a `rejected_sample` of up to 30 papers for auditing. Kept papers are sorted by descending score. The fixed prompt judges titles and abstracts; customize scoring only through `criteria.rubric_hint`. It requests Chinese reasons and 1–3 Chinese subfield tags. The earlier workflow estimated about four seconds per paper; timing depends on the model and endpoint.

### Step 5 — Export Excel

Original command example:

```bash
python3 tools/papers_to_excel.py --profile <slug>
```

The original workflow describes `data/<slug>_papers.xlsx` with title, authors, year, journal, DOI, link, and a one-sentence conclusion.

**Current-script compatibility:** the exporter accepts `--filtered` and `--out`, not `--profile`. Its actual `Articles` sheet has five columns: `No.`, `Title`, `Journal`, `Year`, and `Web Link (DOI)`. Authors, screening reasons, and tags remain in the JSON rather than becoming Excel columns. `--title` changes the sheet name; `--top N` limits the report to the highest-scoring papers.

```bash
python3 tools/papers_to_excel.py --filtered data/my_topic_filtered.json --out data/my_topic_papers.xlsx
```

Output: `data/my_topic_papers.xlsx`.

### Step 6 — Optional ISSN resolution

`tools/resolve_issns.py` resolves journal names to ISSNs for downstream tables that require them. Edit its `NAMES` list for the journals of interest, then capture the OpenAlex source lookup output:

```bash
python3 tools/resolve_issns.py > data/issns.txt
```

This helper prints lookup results; it does not update the profile or Excel file. Inspect the source record's `issn` array to confirm both print and electronic ISSNs: the helper currently prints `issn_l` and `issn_e`, which may not include every identifier.

## Profile shape and rubric template

The complete example template is:

```json
{
  "_description": "profiles: one slug per search task. criteria defines the LLM scoring criteria; prefilter is a coarse keyword filter.",
  "_rubric_guidance": "rubric_hint works best with contrasting score bands, e.g. '9-10 points: directly studies <core mechanism> of <core subject>; <=4 points: mentions <core subject> only in the background or a review.'",
  "profiles": {
    "my_topic": {
      "journals": [
        "0029-5493",
        "0141-0296"
      ],
      "arxiv_cats": ["physics.flu-dyn"],
      "prefilter": {
        "keywords": ["bubble", "electrolysis"],
        "apply_to": ["journals"]
      },
      "criteria": {
        "topic": "<Your research topic in one sentence>",
        "subfields": ["Subfield 1", "Subfield 2"],
        "notes": "Judge semantic relevance from the title and abstract; do not rely on journal or category labels.",
        "rubric_hint": "9-10 points: ...; <=4 points: ..."
      }
    }
  }
}
```

**Current-script compatibility:** the template's `journals` list must be changed in the working profile to a mapping from journal names to ISSN lists, as required by `fetch_openalex`. For example, the structure is `"journals": {"<verified journal name>": ["<verified print ISSN>", "<verified electronic ISSN>"]}`. Replace placeholders with verified values. Keep the same edited profile in `tools/profiles.json` and `data/profiles.json`.

Choose a slug for each search task (the executable examples use `my_topic`). Set `criteria.topic`, `subfields`, and `notes` from the user's research description. Customize only `criteria.rubric_hint` for the scoring rubric. `prefilter.apply_to` defaults to journals; add `"arxiv"` to apply the keyword gate to preprints too.

## Quality guidance

- **`rubric_hint` determines screening quality:** use contrasting score bands such as "9–10 points = directly studies mechanism Y of X; ≤4 points = mentions X only in the background." This is more reliable than a vague paragraph. Preserve the template form: `9-10 points: ...; <=4 points: ...`.
- Keep the prefilter keyword set small to avoid discarding relevant papers prematurely; leave semantic filtering to the LLM. Broad daily journals such as Nature and Science need useful `prefilter.keywords` to keep the candidate pool manageable.
- The original guidance recommends truncating candidate pools above 500 with `merge_candidates.py --max` to save tokens. **Current-script compatibility:** there is no `--max` option. If a cap is needed, make a separate, explicitly limited candidate JSON before screening and retain the full merged pool.
- Before delivery, manually spot-check 5–10 papers to confirm that keep/reject decisions are reasonable and the threshold does not introduce systematic bias.
- Screening quality depends on the model's semantic understanding. The earlier workflow suggested a model with at least 14B parameters or a capable cloud model; a 7B-class model may give coarser judgments. Verify quality with the spot-check rather than relying on model size alone.

## Pitfalls

- Do not treat OpenAlex's `doi` field as a URL prefix: preserve the complete DOI, including its `10.xxxx/` prefix, when removing `https://doi.org/`.
- Cloud APIs may reject `temperature` with HTTP 400. The compatibility layer already retries without it; do not remove the parameter manually.
- LLM output may occasionally wrap the array in an extra object. The parser extracts the outer JSON array or object and accepts an object containing an array under `papers` or `results`; it does not accept every arbitrary wrapper.
- Network outages and rate limits can interrupt retrieval. The original workflow cited roughly 100,000 requests per day for OpenAlex's free tier and noted that a normal review was unlikely to reach it. Treat that figure as historical guidance, not a guaranteed current limit; provider limits still apply.
- **Verify ISSNs rather than guessing.** Incorrect remembered ISSNs have previously pulled in papers from unrelated fields. Use `python3 tools/resolve_issns.py` after editing `NAMES`, or inspect `https://api.openalex.org/sources?search=<journal-name>` and its `issn` array for print and electronic identifiers.
- The OpenAlex journal filter field is `primary_location.source.issn`; the old `issn:` form returned HTTP 400.
- Combining multiple journals into one date-sorted request can bury weekly journals. Preserve the script's separate per-journal queries and cursor pagination.
- OpenAlex batch-ID resolution (`?ids=` or `filter=id:a|b`) has returned HTTP 400 or empty results. The citation script resolves references through individual detail requests and caches them.
- Science and Nature pages may return Cloudflare HTTP 403. Missing abstracts may be acceptable; DOI redirects or Unpaywall are possible external routes when additional retrieval is needed.
- Run long commands (over about four minutes) in the background when appropriate. Citation detail caching supports reruns, but fresh citing queries still make network requests. The earlier workflow mentioned a JSONL screening checkpoint; this repository does not implement one, so rerunning screening repeats the model calls.
- Excel years come from the first four characters of the ISO `date` field, with `year` as a fallback. Retrieval records generally do not have a separate `year`; relying only on it has previously produced an empty Year column.
