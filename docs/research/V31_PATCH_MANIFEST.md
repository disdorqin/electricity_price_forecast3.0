# EFM3 V3.1 Research Patch — Manifest (R1 Cleanup)

**Status**: RESEARCH ONLY / DRAFT. **`promotion_allowed = false`**. 生产主链（A05 / final.csv / 生产预测）**未被本补丁修改**。
生产 `fusion/metrics.py` 已恢复 origin/main 版本，研究专用 metric wrapper 移至 `tools/research/metrics_contract.py`。

## Verdict (R1 Corrected, 2026-07-16)
`V3_1_R1_CORRECTNESS_REPAIR_COMPLETE` — 8项缺陷修复后，A_q50（plain 41.96%）在泄漏无关 common mask 上优于合法 DD 代理（43.82%）约2pp。但仍为单弱模型全历史压力测试；生产 A05（~21-24%）远优，晋级门控仍 FAIL。结论：**NO_SAFE_CANDIDATE_AFTER_V3_1_R1_CORRECTED_REPLAY**。

R1 旧泄漏值 `[64.84, 41.39, 145.71]` (DD / plain-oracle / floor50-oracle) 已 **INVALIDATED**。

## Included in this PR (allowlist)
- `docs/research/V31_RESEARCH_REPORT.md` — 技术报告（含泄漏纠正 + R1修复 + 全历史复盘）
- `docs/research/V31_RESEARCH_REPORT.docx` / `.pdf` — 报告二进制
- `docs/research/V31_PATCH_MANIFEST.md` — 本清单（R1 cleanup 更新版）
- `tools/research/build_full_history_panel.py` — 全历史规范面板构建
- `tools/research/full_history_replay.py` — 校准基线回放
- `tools/research/new_candidates_replay.py` — 6 条新 Track 回放
- `tools/research/historical_data_discovery.py` — 历史数据扫描
- `tools/research/md_to_doc.py` — 报告 md→docx/pdf 转换
- `tools/research/v31_lib.py` — 共享 replay 引擎
- `tools/research/run_mini_replay.py` — 迷你 replay + 14 检查
- `tools/research/metrics_contract.py` — **[新增]** 研究专用 metric wrapper（替代 `fusion.metrics`）
- `tools/research/generate_v31_report.py` — R1 报告生成器
- `research_memory/CURRENT_STATE.yaml` — 已加 `v3_1_research_patch` 段（r1_invalidated_old_numbers, V3_1_R1_CORRECTNESS_REPAIR_COMPLETE）
- `tests/research/` — 8 合约测试文件 + conftest
- `.github/workflows/research-v31-contract.yml` — **[新增]** 研究专用 CI
- `PR20_CLEANUP_REPORT.md` / `PR20_ALLOWLIST.txt` / `PR20_REMOTE_FILE_AUDIT.csv` / `PR20_CLEANUP_VERDICT.json` — **[新增]** G0 清理证据

## NOT included in this PR (excluded after G0 cleanup)
- `data_audit/FULL_HISTORY_CANONICAL_PANEL.parquet` — 大文件，仓库外保存（`<ARTIFACT_ROOT>/v31_r1/`），hash 可复现
- `data_audit/FH_CALIB_*` / `FH_NEW_*` 派生 CSV/JSON — 派生结果，报告已摘录关键数字
- `data_audit/FULL_HISTORY_CANONICAL_VERDICT.json` — 含用户绝对路径，已移除
- `data_audit/V31_R1_RESULTS.json` — 小型汇总（保留）
- `research_runs/` — 旧实验目录
- 任何 raw 数据 / 权重 / 密钥 / `.env*` / 绝对路径

## Feature flag
研究脚本为独立工具（`tools/research/`），**不被生产主链 import**，等价于 feature flag OFF。生产代码路径零改动。

## How to reproduce
```
conda activate epf-2
cd <repo_root>
python tools/research/build_full_history_panel.py
python tools/research/full_history_replay.py        # 校准基线 (CPU)
python tools/research/new_candidates_replay.py      # 6 Track (CPU)
```
环境：conda `epf-2`，CPU-only（本机 GPU 路径 segfault，强制 CPU）。

## CI
`.github/workflows/research-v31-contract.yml` — 每次 push 到 `research/v3.1-*` 分支时运行：
- `tests/research/` 合约测试
- `run_mini_replay.py` | 14 检查
- import/compile 检查
- secret 扫描
- 大文件守卫
- 生产隔离守卫
