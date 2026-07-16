# PR #20 Cleanup Report (Gate G0)

**Branch**: `research/v3.1-model-upgrade`
**Date**: 2026-07-16 18:30+08
**State before**: PR #20 OPEN/DRAFT, HEAD `fcd483c`, contained 9MB parquet + derived CSVs in history.

## Actions taken

| # | Action | Status |
|---|--------|--------|
| A | Backup branch `backup/pr20-before-cleanup-20260716-1830` (local, no push) | ✅ |
| B | Remove large/derived files from history (filter-branch) | ✅ |
| C | Large artifacts moved to `<ARTIFACT_ROOT>/v31_r1/` (git retains hash + schema + scripts) | ✅ |
| D | `V31_PATCH_MANIFEST.md` updated — accurate include/exclude, A_q50 beats DD ~2pp on common mask, NO_SAFE_CANDIDATE | ✅ |
| E | `fusion/metrics.py` audit + migration: research metrics moved to `tools/research/metrics_contract.py`, `fusion/metrics.py` reverted to origin/main | ✅ |
| F | `.github/workflows/research-v31-contract.yml` added (research-only CI) | ✅ |
| G | Force-push with lease (research branch only) | ✅ |

## Files removed from history

- `data_audit/FULL_HISTORY_CANONICAL_PANEL.parquet` (9MB)
- `data_audit/FULL_HISTORY_CANONICAL_VERDICT.json` (contained user absolute path)
- `data_audit/FH_CALIB_COMMON_MASK_RANKING.csv`
- `data_audit/FH_CALIB_METRIC_AUDIT.csv`
- `data_audit/FH_CALIB_ORACLE_AUDIT.json`
- `data_audit/FH_NEW_COMMON_MASK_RANKING.csv`
- `data_audit/FH_NEW_METRIC_AUDIT.csv`
- `data_audit/FH_NEW_ORACLE_AUDIT.json`

All 8 files excised via `git filter-branch` (index-filter `git rm --cached`).

## Files added

- `tools/research/metrics_contract.py` — research metric wrapper (plain_smape + corrected smape_floor50)
- `docs/research/V31_PATCH_MANIFEST.md` — updated with accurate include/exclude + R1 verdict
- `.github/workflows/research-v31-contract.yml` — research-only CI (preflight security + contract tests)
- `PR20_CLEANUP_REPORT.md` — this file
- `PR20_ALLOWLIST.txt` — explicit allowlist
- `PR20_REMOTE_FILE_AUDIT.csv` — before/after file audit
- `PR20_CLEANUP_VERDICT.json` — cleanup verdict

## Files modified

- `tools/research/v31_lib.py`: import changed `fusion.metrics` → `metrics_contract`; docstring updated
- `tools/research/run_mini_replay.py`: import + docstring updated
- `tools/research/v31_lib.py`: PANEL path unchanged (panel file exists on disk but no longer tracked)
- `tests/research/test_v31_metrics_contract.py`: import changed + sys.path updated
- `fusion/metrics.py` — **reverted to origin/main** (no longer differs from production)

## Repository topology

- **origin/main SHA**: `680436b` (unchanged)
- **Production worktree**: `.../efm3.0` (branch `agent/production-circuit-gap-audit-db-redesign`, read-only, **NOT modified**)
- **Research worktree**: `.../electricity_forecast_model3.0-research` (branch `research/v3.1-model-upgrade`)
- **PR #20**: OPEN/DRAFT, `isDraft:true`, `promotion_allowed:false`
- **Production diff**: ZERO (fusion/metrics.py restored to origin/main; research tools in `tools/research/` not imported by production)

## Post-cleanup tests

- `pytest tests/research/test_v31_metrics_contract.py -v`: PASS (6 tests) — verifies metrics_contract.py is importable and correct
- `python tools/research/run_mini_replay.py`: PASS (14 checks) — verifies research engine still consistent
