# AGENTS.md — Literature Search Pipeline

> 本仓库是一个**可被任意 coding agent（GPT/Claude Code/Codex/Hermes 等）直接加载的文献调研工具集**。
> 你（agent）进入此目录后：读 `skills/lit-search-pipeline/SKILL.md` 获取完整流程与坑位，然后按用户给的种子论文 + 主题描述执行。

## 快速开始（agent 视角）
1. 确认依赖：`pip install -r requirements.txt`（只需 openpyxl）。
2. 向用户确认 LLM 配置（Step 4 精筛需要 OpenAI 兼容端点）：
   ```bash
   export PAPER_LLM_BASE=... PAPER_LLM_MODEL=... PAPER_LLM_KEY=...
   ```
3. 按 `skills/lit-search-pipeline/SKILL.md` 的六步执行；中间产物放 `data/`。
4. 交付 `data/<slug>_papers.xlsx`。

## 目录
```
skills/lit-search-pipeline/SKILL.md   # 完整流程（canonical，agent 先读这个）
tools/                                # 5 个脚本，纯 stdlib + openpyxl
templates/profiles.example.json       # 检索 profile 模板（含评分 rubric 写法）
templates/config.json                 # 精筛阈值配置
data/                                 # 中间产物与最终 Excel（gitignore 之外的示例除外）
requirements.txt
```

## 设计原则
- 只有 Step 4（LLM 精筛）依赖外部模型，走 OpenAI 兼容协议 → **换任何 LLM 只改 3 个环境变量**。
- 其余步骤零 key、零配额限制（OpenAlex 免费公开 API）。
- 无硬编码用户路径；`PAPER_LLM_*` / `PAPER_DIGEST_DIR` 环境变量可覆盖默认。

## 对 agent 的约束
- 不要自行发明评分逻辑——精筛 prompt 已固定，改 rubric 只改 `profiles.json` 的 `rubric_hint`。
- 不要 `git add`  `data/` 下的大文件（>10MB 的 json/Excel 走 git-lfs 或单独发）。
- 交付前人工/agent 抽检 5-10 条结果的 `keep` 判定是否合理。
