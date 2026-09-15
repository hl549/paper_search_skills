# Literature Search Pipeline（引用图 + 描述检索 → LLM 精筛 → Excel）

种子论文 + 一段主题描述 → 自动找相关文献 → 输出 Excel 文章表。
流水线：OpenAlex 引用图扩展 → 期刊/arXiv 描述检索 → 合并去重 → **任意 LLM 语义精筛** → Excel。

## 能否用于其他 LLM？
**可以。** 全流程只有 Step 4（精筛）调用 LLM，走的是**标准 OpenAI 兼容
chat/completions 协议**（urllib 直连，无 SDK）。任何满足以下条件的模型都行：

- OpenAI-compatible API：OpenAI / DeepSeek / Moonshot / Kimi / Grok / xAI /
  Gemini (OpenAI 兼容端点) / 本地 vLLM / LM Studio / Ollama
- 能按指令**输出 JSON 数组**（评分 prompt 见 `filter_papers.py: build_prompt`）

实测配置（任选其一，只需 3 个环境变量）：

```bash
# 本地 vLLM / LM Studio（无需 key）
export PAPER_LLM_BASE=http://127.0.0.1:8000/v1
export PAPER_LLM_MODEL=<你的模型名>

# 云端（如 DeepSeek）
export PAPER_LLM_BASE=https://api.deepseek.com/v1
export PAPER_LLM_MODEL=deepseek-chat
export PAPER_LLM_API_KEY=sk-xxxx
```

兼容细节已内置：部分云端 API 不接受 `temperature` 参数 → 自动去掉重试；
响应里带 ````json 围栏 → 自动剥掉取最外层 JSON 数组。
评分质量取决于模型语义理解能力——7B 级小模型可用但偏粗糙，
建议 ≥14B 或云端主力模型。

## 依赖
- Python 3.8+，仅需 `openpyxl`（Step 5 出 Excel）：`pip install openpyxl`
- 网络访问 OpenAlex（免费、无需 API key）
- 无硬编码路径：所有文件相对脚本目录解析，`PAPER_DIGEST_DIR` 可覆盖

## 用法（6 步）

```bash
cp templates/profiles.example.json profiles.json   # 填 journals/criteria（见下）
cp templates/config.json config.json

# Step 0: 按你的主题填 profiles.json 里的 profile：
#   journals = 领域核心期刊 ISSN（先查 ISSN！见下）
#   criteria.rubric_hint 写成对立分档："9-10分：…；≤4分：…"（判别力最好）
#   日更大刊（Nature/Science）务必加 prefilter.keywords 防候选爆炸

# Step 1: 引用图扩展（cites + refs，depth-2 典型 ~400-600 候选 / ~4min）
python3 cite_graph.py --seed <DOI或arXivID> --depth 2 --directions cites,refs \
    --since-year 2010 --cites-per-seed 25 --refs-to-resolve 18 \
    --out my_cite_candidates.json
#   → 同时产出 my_cite_candidates_flat.json（可直接喂 filter）
#   cite_cache/ 磁盘缓存，重跑免费

# Step 2: 描述检索（期刊 + arXiv，一次性综述用大 --days）
python3 fetch_papers.py --profile my_topic --days 3650 --out my_journal_candidates.json

# Step 3: 合并去重（保留摘要更长的版本，source_tags 记录来源）
python3 merge_candidates.py --in my_cite_candidates_flat.json my_journal_candidates.json \
    --out my_combined.json --tags cite,journal

# Step 4: LLM 语义精筛（~4s/篇；设好上面的 PAPER_LLM_* 环境变量）
python3 filter_papers.py --candidates my_combined.json --profile my_topic --out my_filtered.json
#   → kept[] + rejected_count/rejected_sample（可审计）

# Step 5: Excel（标准 Articles sheet：No./Title/Journal/Year/Web Link）
python3 papers_to_excel.py --filtered my_filtered.json --out my_articles.xlsx
```

## 关键坑（实测踩过）
- **ISSN 必须核实**：凭记忆写 ISSN 曾导致他领域文章混入。
  `python3 resolve_issns.py`（改 NAMES 列表）或
  `curl -s 'https://api.openalex.org/sources?search=<刊名>'`（ISSN 字段名 `issn`，印刷+电子都收）。
- OpenAlex 期刊过滤字段是 `primary_location.source.issn`（旧写法 `issn:` 返回 400）。
- 多期刊合并查询会丢周更期刊——脚本已按刊独立查询 + cursor 翻页，勿重构掉。
- OpenAlex 批量 id 解析（`?ids=` / `filter=id:a|b`）不可靠（400/空），脚本用单篇详情。
- Science/Nature 页面 Cloudflare 拦截（403）：摘要缺口可接受，或走 doi.org redirect / Unpaywall。
- 长命令（>4min）用后台跑 + 断点续传：cite_cache/ 与 jsonl checkpoint 都支持重跑同命令恢复。
- Excel 的 Year 从 ISO date 字段取前 4 位（数据里没有独立 year 字段，曾因此整列空过）。

## 文件
```
cite_graph.py         Step 1 引用图扩展（OpenAlex，纯 stdlib）
fetch_papers.py       Step 2 期刊/arXiv 描述检索（纯 stdlib）
merge_candidates.py   Step 3 合并去重（纯 stdlib）
filter_papers.py      Step 4 LLM 精筛（OpenAI 兼容 API，纯 stdlib）
papers_to_excel.py    Step 5 Excel 输出（需 openpyxl）
resolve_issns.py      ISSN 解析辅助
templates/            profiles/config 模板
```
