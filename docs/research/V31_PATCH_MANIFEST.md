# EFM3 V3.1 Research Patch — Manifest

**Status**: RESEARCH ONLY / DRAFT. **`promotion_allowed = false`**. 生产主链（A05 / final.csv / 生产预测）**未被本补丁修改**。

## Verdict
`NO_SAFE_CANDIDATE_AFTER_FULL_HISTORY_EVALUATION` — 6 条新候选 Track（A–F）在合法全历史滚动回放下全部输给朴素 DA 代理基线（DD），未展现尾部风险改善。

## Included in this PR (allowlist)
- `docs/research/V31_RESEARCH_REPORT.md` — 技术报告 V2（含泄漏纠正 + 负向结论 + 全历史复盘）
- `docs/research/V31_RESEARCH_REPORT.docx` / `.pdf` — 报告二进制
- `docs/research/V31_PATCH_MANIFEST.md` — 本清单
- `tools/research/build_full_history_panel.py` — 全历史规范面板构建
- `tools/research/full_history_replay.py` — 校准基线回放（已修复 DD 泄漏）
- `tools/research/new_candidates_replay.py` — 6 条新 Track 回放
- `tools/research/historical_data_discovery.py` — 历史数据扫描
- `tools/research/md_to_doc.py` — 报告 md→docx/pdf 转换
- `research_memory/CURRENT_STATE.yaml` — 已加 `v3_1_research_patch` 段（含泄漏纠正 + 负向结论）

## Explicitly excluded (NOT pushed)
- `data_audit/*.csv` / `*.md`（派生结果，含 1.7MB 行级 Oracle；非源，报告已摘录关键数字）
- `data_audit/FULL_HISTORY_CANONICAL_PANEL.parquet`（大文件，可在研究仓本地重建）
- `research_runs/` 旧实验目录
- 任何 raw 数据 / 权重 / 密钥 / `.env*`

## Feature flag
研究脚本为独立工具（`tools/research/`），**不被生产主链 import**，等价于 feature flag OFF。生产代码路径零改动。

## How to reproduce
```
python tools/research/build_full_history_panel.py
python tools/research/full_history_replay.py        # 校准基线 (CPU)
python tools/research/new_candidates_replay.py      # 6 Track (CPU)
```
环境：conda `epf-2`，CPU-only（本机 GPU 路径 segfault，强制 CPU）。
