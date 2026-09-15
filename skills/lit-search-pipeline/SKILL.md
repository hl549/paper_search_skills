---
name: lit-search-pipeline
description: 种子论文 + 一段主题描述 → 自动找相关文献 → 输出 Excel 文章表。OpenAlex 免费 API + 任意 OpenAI 兼容 LLM。当用户需要"找引用了 X 的论文/与 X 相关的文献/文献调研/literature search"时使用。
---

# Literature Search Pipeline（引用图 + 描述检索 → LLM 精筛 → Excel）

## 何时使用
用户给出：一篇（或多篇）种子论文 + 一段主题描述（可选，缺省从种子论文抽取），要求找相关文献并整理成表。

## 依赖
- Python ≥ 3.9，唯一第三方包：`openpyxl`（`pip install -r requirements.txt`）
- 免费公开 API：OpenAlex（无需 key）
- 精筛步骤需要一个 **OpenAI 兼容** 的 LLM（本地 vLLM/LM Studio/Ollama 或云端任意），通过环境变量配置，见下。

## LLM 配置（Step 4 用；其他步骤不需要）
```bash
export PAPER_LLM_BASE="http://127.0.0.1:1234/v1"   # 或 https://api.openai.com/v1、api.deepseek.com/v1 等
export PAPER_LLM_MODEL="qwen3.8-27b"              # 任意模型名
export PAPER_LLM_KEY=""                          # 本地留空；云端填 sk-...
```
兼容层已处理：无 key 不加 Authorization；云端 400（如 gpt 系拒绝 temperature）自动降级重试；响应带 ```json 围栏自动剥除。

## 六步流水线
工作目录 = 仓库根（下称 `.`）；中间产物统一放 `data/`。

### Step 1 — 建引用图
```bash
python3 tools/cite_graph.py --seed-arxiv <arXivId>          # 从 arXiv id
# 或
python3 tools/cite_graph.py --seed-doi <DOI> --seed-title "..." --seed-authors "..." --seed-year 2024
```
产出 `data/cite_graph.json`（引用者 + 被引者，默认 2 层）。

### Step 2 — 按主题描述检索
```bash
cp templates/profiles.example.json templates/profiles.json   # 首次
# 编辑 templates/profiles.json：填 slug、criteria.rubric_hint（对立分档最稳）、prefilter 关键词
python3 tools/fetch_papers.py --profile <slug> --query "<主题描述>"
```
产出 `data/<slug>_search.json`。期刊论文走 OpenAlex，arXiv 走 arXiv API，自动合并去重。

### Step 3 — 合并去重
```bash
python3 tools/merge_candidates.py --cite-graph data/cite_graph.json --search data/<slug>_search.json
```
产出 `data/candidates.json`。DOI 优先、标题归一化去重。

### Step 4 — LLM 语义精筛
```bash
python3 tools/filter_papers.py --profile <slug>
```
产出 `data/<slug>_filtered.json`。按 `templates/config.json` 的 `keep_threshold`（默认 ≥6/10 保留）筛选；每批 15 篇，断点续跑（已评的跳过）。

### Step 5 — 出 Excel
```bash
python3 tools/papers_to_excel.py --profile <slug>
```
产出 `data/<slug>_papers.xlsx`，列：标题/作者/年份/期刊/DOI/链接/一句话结论。

### Step 6 — （可选）补 ISSN/补全
`tools/resolve_issns.py` 用于把期刊名解析成 ISSN（给需要 ISSN 的下游表格）。

## 质量要点
- **rubric_hint 决定精筛质量**：写成"9-10 分 = 直接研究 X 的 Y 机制；≤4 分 = 仅在背景提及 X"的对立分档，比一段笼统描述稳定得多。
- prefilter 关键词宁少勿多，避免误杀；真正的过滤交给 LLM。
- 候选池 >500 时先跑 `merge_candidates.py` 的 `--max` 截断，省 token。
- 结果交付前人工抽检 5-10 条，确认阈值没系统性偏向。

## 坑
- 别把 OpenAlex 的 `doi` 字段当 URL 前缀——完整 DOI 含 `10.xxxx/` 前缀。
- 云端 API 对 `temperature` 可能 400：兼容层已自动降级，不要手动删参数。
- LLM 输出偶尔多包一层对象：解析器已处理"取外层数组 / 单对象包数组"两种情况。
- 断网/限流：OpenAlex 免费层约 100k 请求/天，正常调研远不会触发。
