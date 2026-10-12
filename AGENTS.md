# AGENTS.md — Literature Search Pipeline

> This repository is a **literature search toolkit that any coding agent (GPT, Claude Code, Codex, Hermes, etc.) can load directly**.
> On entering this directory, read `skills/lit-search-pipeline/SKILL.md` for the full workflow and pitfalls, then work from the user's seed papers and topic description.

## Agent quickstart

1. Install dependencies: `pip install -r requirements.txt` (only openpyxl).
2. Confirm the LLM configuration with the user if it has not already been supplied. Semantic screening requires an OpenAI-compatible endpoint:
   ```bash
   export PAPER_LLM_BASE=... PAPER_LLM_MODEL=... PAPER_LLM_API_KEY=...
   ```
3. Follow `skills/lit-search-pipeline/SKILL.md`; keep intermediate files in `data/`.
4. Deliver `data/<slug>_papers.xlsx`.

## Layout

```
skills/lit-search-pipeline/SKILL.md   # Canonical workflow; agents read this first
tools/                              # Six scripts; standard library + openpyxl
templates/profiles.example.json      # Search profile template and rubric guidance
templates/config.json               # Screening configuration
data/                               # Intermediate files and final Excel (gitignored except .gitkeep)
requirements.txt
```

## Design principles

- Semantic screening is the only implemented model call. It uses the OpenAI-compatible protocol; **switch providers with the three LLM environment variables above**.
- Retrieval uses OpenAlex and arXiv without API keys in these scripts; provider limits still apply.
- There are no hardcoded user paths. `PAPER_LLM_*` configures the model; `PAPER_DIGEST_DIR` overrides citation-search and journal-fetch paths. Screening reads configuration beside its script.

## Agent constraints

- Do not invent scoring logic. The screening prompt is fixed; customize the rubric only through `criteria.rubric_hint` in `profiles.json`.
- Do not `git add` large files from `data/`. Use Git LFS or deliver JSON/Excel files larger than 10 MB separately.
- Before delivering results, manually or with an agent spot-check 5-10 papers to confirm that the keep/reject decisions are reasonable.
