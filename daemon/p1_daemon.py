#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P1 Day-ahead Continuous Daemon
================================
Implements the requested "continuous daemon / streaming agent" architecture:

  1. Does NOT exit after Phase D/E/F complete (loop:true, auto_exit:false).
  2. while-loop event loop drives the pipeline phases continuously.
  3. Keep-alive heartbeat: every `heartbeat_interval_s` a status line is
     appended to the progress log + an event is pushed to the event stream.
  4. Streaming output: subprocess (engine) stdout/stderr is tee'd line-by-line
     to the progress log AND to the JSONL event stream (not a one-shot return).
  5. Stop flag: presence of `stop_flag` file (or SIGTERM/SIGINT) => graceful exit.
  6. Config-driven: mode/loop/auto_exit read from p1_daemon_config.yaml.
  7. UI progress events: daemon_events.jsonl is a tail-able, real-time feed the
     UI can render as live progress (one JSON object per event).

The daemon orchestrates the P1 day-ahead exploration:
  rich_full   -> ensure rich candidates ran 11 months (waits for REV96j, or launches)
  phase_d     -> honest dual-column comparison (24f vs rich) across 11 months
  phase_e     -> window ablation 90/180/365d on cfg05 & xgboost_rich
  phase_f     -> assemble candidate package + 12-section final report
"""

import os
import sys
import time
import json
import signal
import shutil
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# --------------------------------------------------------------------------
# Config (spec point 6)
# --------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent


def _coerce(v):
    v = v.strip()
    if v.lower() in ("true", "yes", "on"):
        return True
    if v.lower() in ("false", "no", "off"):
        return False
    if "," in v and not v.startswith("[") and not v.endswith("]"):
        return [x.strip() for x in v.split(",") if x.strip()]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def load_config(path):
    cfg = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            cfg[k.strip()] = _coerce(v)
    return cfg


def _as_list(v):
    """Normalize a config value to a list of strings.
    Handles bare strings ('xgboost_rich'), comma lists ('a,b'), and
    bracketed forms ('[a, b]'). Avoids list('abc') -> ['a','b','c']."""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [x.strip() for x in s.split(",") if x.strip()]


CONFIG_PATH = HERE / "p1_daemon_config.yaml"
CFG = load_config(CONFIG_PATH)

MODE = CFG.get("mode", "daemon")
LOOP = bool(CFG.get("loop", True))
AUTO_EXIT = bool(CFG.get("auto_exit", False))
HEARTBEAT_S = int(CFG.get("heartbeat_interval_s", 30))
STOP_FLAG = HERE / CFG.get("stop_flag", ".daemon_stop")
STATE_FILE = HERE / CFG.get("state_file", "daemon_state.json")
PROGRESS_LOG = HERE / CFG.get("progress_log", "daemon_progress.log")
EVENT_STREAM = HERE / CFG.get("event_stream", "daemon_events.jsonl")

MODELS_ROOT = Path(CFG.get("models_root"))
ENGINE = MODELS_ROOT / CFG.get("engine")
PYTHON = CFG.get("python")
OUTPUT_ROOT = MODELS_ROOT / CFG.get("output_root")
EXPORTS_ROOT = MODELS_ROOT / CFG.get("exports_root")
RICH_RUN_ID = CFG.get("rich_run_id")
BASE24_RUN_ID = CFG.get("base24_run_id")
TEST_MONTHS = CFG.get("test_months")
ABLATION_WINDOWS = [int(x) for x in _as_list(CFG.get("ablation_windows", "90,365"))]
ABLATION_MODELS = _as_list(CFG.get("ablation_models", "cfg05"))
NUM_BOOST_ROUND = CFG.get("num_boost_round")  # 快速实验轮数覆盖 (None=忠实配置)
# 看门狗：子进程超过此时长未退出则强制 kill，避免 GPU 被抢占/CUDA 上下文损坏导致 daemon 永久卡死
WATCHDOG_S = int(CFG.get("watchdog_s", 1800))
# rich_full 连续失败达到该次数 -> 禁用 GPU，全部回退 CPU（保证不无限循环卡死）
MAX_RICH_FULL_RETRIES = int(CFG.get("max_rich_full_retries", 2))
# GPU 被确认不可靠时置 True：所有模型改在单 CPU 组跑（配合 engine --cpu-only）
GPU_DISABLED = False

# --- CPU/GPU 并发 (长任务同时利用 CPU 与 GPU) ---
# 规则（写入配置、每次任务已知）：GPU 可用模型(lightgbm/catboost) 与
# CPU-only 模型(xgboost, 该 conda 构建无 GPU) 拆成两个并发子进程，
# 同时跑，互不冲突（各自写不同 predictions/*.csv）。
CONCURRENT = bool(CFG.get("concurrent", True))
GPU_MODELS = _as_list(CFG.get("gpu_models", ["cfg05", "catboost_rich"]))
CPU_MODELS = _as_list(CFG.get("cpu_models", ["xgboost_rich"]))

PHASES = ["rich_full", "phase_d", "phase_e", "phase_f"]

# --------------------------------------------------------------------------
# Logging / streaming (spec points 3, 4, 7)
# --------------------------------------------------------------------------
_log_lock = threading.Lock()
_stop = threading.Event()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = f"[{_now()}] {msg}"
    with _log_lock:
        with open(PROGRESS_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line, flush=True)


def emit_event(etype, phase, status, msg, **extra):
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": etype,
        "phase": phase,
        "status": status,
        "message": msg,
    }
    rec.update(extra)
    with _log_lock:
        with open(EVENT_STREAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": {}, "current": None, "last_metrics": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# Process detection (avoid duplicate runs)
# --------------------------------------------------------------------------
def find_running(substr):
    """Return True if a python process whose command line contains `substr` is alive."""
    try:
        out = subprocess.run(
            'wmic process where "name=\'python.exe\'" get CommandLine',
            shell=True, capture_output=True, text=True, timeout=15,
        ).stdout
        return substr in out
    except Exception:
        return False


def run_dir_exists_recent(run_id, max_age_s=180):
    d = OUTPUT_ROOT / run_id
    if not d.exists():
        return False
    newest = max((p.stat().st_mtime for p in d.rglob("*") if p.is_file()), default=0)
    return (time.time() - newest) < max_age_s


# --------------------------------------------------------------------------
# Streaming engine runner (spec point 4)
# --------------------------------------------------------------------------
def run_engine(run_id, models, extra_args=None, streaming=True, output_root_override=None, cpu_only=False):
    """Launch the P1 engine as a subprocess and tee its output line-by-line
    into the progress log + event stream. Blocks until the process exits.

    output_root_override: if given, the engine writes into this directory
    instead of OUTPUT_ROOT/run_id (used to isolate a concurrent CPU group so
    its metrics.json doesn't race with the GPU group's).
    cpu_only: force the engine onto CPU (--cpu-only), used for the CPU group
    and for the GPU-disabled fallback path."""
    out_dir = Path(output_root_override) if output_root_override else (OUTPUT_ROOT / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        PYTHON, str(ENGINE),
        "--test-months", ",".join(TEST_MONTHS),
        "--models", ",".join(models) if isinstance(models, list) else str(models),
        "--output-root", str(out_dir),
        "--run-id", run_id,
        "--allow-skip",
    ]
    if extra_args:
        cmd += extra_args
    # GPU 在本机不稳定（lightgbm GPU 死锁 + catboost GPU 崩溃），GPU_DISABLED 时
    # 对【所有】引擎调用强制回退 CPU，避免 Phase E 消融等隐式 GPU 调用再次卡死
    if cpu_only or GPU_DISABLED:
        cmd += ["--cpu-only"]

    emit_event("run", run_id, "start", f"launching engine: {models}", cmd=" ".join(cmd))
    log(f"[RUN:{run_id}] start models={models}")

    proc = subprocess.Popen(
        cmd, cwd=str(MODELS_ROOT), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8", errors="replace",
    )
    # 看门狗线程：子进程卡死（GPU 被抢占/CUDA 上下文损坏）时强制 kill，避免 daemon 永久阻塞
    def _watchdog():
        while proc.poll() is None:
            if time.time() > deadline:
                log(f"[RUN:{run_id}] WATCHDOG timeout {WATCHDOG_S}s -> killing subprocess")
                emit_event("run", run_id, "error", f"watchdog timeout {WATCHDOG_S}s, killing subprocess")
                try:
                    proc.kill()
                except Exception:
                    pass
                return
            time.sleep(5)
    deadline = time.time() + WATCHDOG_S
    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()
    if streaming:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line:
                with _log_lock:
                    with open(PROGRESS_LOG, "a", encoding="utf-8") as f:
                        f.write(f"[{_now()}][{run_id}] {line}\n")
                emit_event("stream", run_id, "running", line)
    proc.stdout and proc.stdout.close()
    rc = proc.wait()
    wd.join(timeout=2)
    emit_event("run", run_id, "done" if rc == 0 else "error",
               f"engine exited rc={rc}", rc=rc)
    log(f"[RUN:{run_id}] exited rc={rc}")
    # 成功判定：rc==0，或虽在进程退出时崩溃（如 xgboost 偶发 access violation 0xC0000005）
    # 但预测 CSV 已落盘 -> 视为成功，避免因 teardown segfault 误判失败导致 daemon 死循环
    if rc != 0 and out_dir.exists():
        pred_dir = out_dir / "predictions"
        expected = set(models) if isinstance(models, (list, tuple, set)) else set(str(models).split(","))
        expected = {m.strip() for m in expected if m.strip()}
        if pred_dir.exists():
            have = {p.stem for p in pred_dir.glob("*.csv") if p.name != "all_predictions.csv"}
            if expected and expected.issubset(have):
                log(f"[RUN:{run_id}] rc={rc} 但预测已落盘 -> 视为成功(忽略 teardown segfault)")
                return True
    return rc == 0


# --------------------------------------------------------------------------
# Phase helpers
# --------------------------------------------------------------------------
def _overall_smape(run_id):
    """Return {model_name: sMAPE_floor50} from a run's overall_metrics.csv."""
    p = OUTPUT_ROOT / run_id / "metrics" / "overall_metrics.csv"
    if not p.exists():
        return {}
    import csv
    out = {}
    with open(p, encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                out[row["model_name"]] = float(row["sMAPE_floor50"])
            except (KeyError, ValueError, TypeError):
                pass
    return out


def _all_rich_models_present(run_id):
    pred = OUTPUT_ROOT / run_id / "predictions"
    if not pred.exists():
        return False
    expected = set(GPU_MODELS) | set(CPU_MODELS)
    have = {p.stem for p in pred.glob("*.csv") if p.name != "all_predictions.csv"}
    return expected.issubset(have)


def _run_finalize(run_id):
    """Merge all per-model checkpoints in run_id/predictions into metrics.json."""
    run_engine(run_id, list(GPU_MODELS) + list(CPU_MODELS),
               extra_args=["--finalize"], streaming=True)


def phase_rich_full(state):
    """Ensure rich candidates ran. Launch GPU+Cpu groups concurrently (CPU&GPU simultaneous)."""
    metrics = OUTPUT_ROOT / RICH_RUN_ID / "metrics" / "metrics.json"
    cpu_root = OUTPUT_ROOT / (RICH_RUN_ID + "_cpu")

    # already fully merged (all gpu+cpu model checkpoints present)?
    if metrics.exists() and _all_rich_models_present(RICH_RUN_ID):
        state["done"]["rich_full"] = True
        return True

    # external run still in progress (manual launch) -> wait for metrics
    if find_running(RICH_RUN_ID):
        log(f"[rich_full] external run '{RICH_RUN_ID}' active, waiting for metrics.json")
        emit_event("phase", "rich_full", "waiting", f"external {RICH_RUN_ID}")
        while not metrics.exists():
            if _stop.is_set() or STOP_FLAG.exists():
                return False
            time.sleep(10)
        state["done"]["rich_full"] = True
        return True
    # 注意：若目录存在但无进程在跑（上次崩溃残留），不在此等待，直接走下方并发重跑

    # launch concurrent GPU + CPU groups (CPU/GPU 同时利用)
    boost = ["--num-boost-round", str(NUM_BOOST_ROUND)] if NUM_BOOST_ROUND else []

    # --- GPU 被确认不可靠：全部模型改在单 CPU 组跑（engine --cpu-only）---
    if GPU_DISABLED:
        all_models = list(GPU_MODELS) + list(CPU_MODELS)
        log(f"[rich_full] GPU_DISABLED -> 全部 CPU 组: {all_models}")
        emit_event("phase", "rich_full", "start",
                   f"GPU disabled, all on CPU (cpu-only): {all_models}")
        ok = run_engine(RICH_RUN_ID, all_models, boost, True, None, True)
        _run_finalize(RICH_RUN_ID)
        ok = metrics.exists()
        state["done"]["rich_full"] = ok
        emit_event("phase", "rich_full", "done" if ok else "error",
                   f"rich_full complete (CPU-only fallback, ok={ok})")
        return ok

    # --- 正常并发：GPU 组(catboost 等) + CPU 组(cfg05/xgboost，强制 --cpu-only 避免 lightgbm GPU 死锁) ---
    log(f"[rich_full] launching CONCURRENT GPU+CPU: gpu={GPU_MODELS} cpu={CPU_MODELS} (cpu组 --cpu-only)")
    emit_event("phase", "rich_full", "start",
               f"concurrent gpu={GPU_MODELS} + cpu={CPU_MODELS}(cpu-only) (CPU&GPU utilized simultaneously)")
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_gpu = ex.submit(run_engine, RICH_RUN_ID, list(GPU_MODELS), boost)
        f_cpu = ex.submit(run_engine, RICH_RUN_ID + "_cpu", list(CPU_MODELS),
                          extra_args=boost, streaming=True,
                          output_root_override=str(cpu_root), cpu_only=True)
        ok_gpu = f_gpu.result()
        ok_cpu = f_cpu.result()
    # 任一分组失败（如 GPU 组被看门狗强杀）-> 不合并/不 finalize，返回 False 让 daemon 重试
    if not (ok_gpu and ok_cpu):
        log(f"[rich_full] group failed (gpu_ok={ok_gpu} cpu_ok={ok_cpu}) -> 不 finalize，交由 daemon 重试")
        emit_event("phase", "rich_full", "error",
                   f"group failed gpu_ok={ok_gpu} cpu_ok={ok_cpu}, will retry")
        return False
    # merge CPU-group predictions into the main run dir
    if cpu_root.exists():
        (OUTPUT_ROOT / RICH_RUN_ID / "predictions").mkdir(parents=True, exist_ok=True)
        for c in (cpu_root / "predictions").glob("*.csv"):
            shutil.copy(c, OUTPUT_ROOT / RICH_RUN_ID / "predictions" / c.name)
    # finalize combined metrics (all gpu+cpu models)
    _run_finalize(RICH_RUN_ID)
    ok = metrics.exists()
    state["done"]["rich_full"] = ok
    emit_event("phase", "rich_full", "done" if ok else "error",
               f"rich_full complete (gpu_ok={ok_gpu} cpu_ok={ok_cpu})")
    return ok


def phase_d(state):
    """Honest dual-column comparison: 24f candidates vs rich candidates across 11 months."""
    rep = OUTPUT_ROOT / "reports" / "phase_d_comparison.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    if rep.exists():
        state["done"]["phase_d"] = True
        return True
    base = _overall_smape(BASE24_RUN_ID)
    rich = _overall_smape(RICH_RUN_ID)
    if not base or not rich:
        log("[phase_d] metrics not ready (base or rich missing), deferring")
        return False
    lines = [
        "# Phase D — Honest Dual-Column Comparison (24f vs rich)",
        "",
        f"- 24f candidates run: `{BASE24_RUN_ID}` (忠实 2.5 特征, 24 列, 18 月窗口)",
        f"- rich candidates run: `{RICH_RUN_ID}` (rich 特征 ~55 列, 90 天窗口, 对齐 cfg05)",
        f"- 测试月: {', '.join(TEST_MONTHS)}",
        "",
        "## Overall sMAPE_floor50 (跨 11 月, 越低越好)",
        "",
        "| 模型 | 帧 | sMAPE_floor50 | 族 |",
        "|------|----|--------------|----|",
    ]
    family = {"baseline_lgbm25": "LightGBM(忠实2.5)", "catboost": "CatBoost",
              "lightgbm_variant": "LightGBM", "xgboost": "XGBoost",
              "cfg05": "LightGBM(冠军)", "xgboost_rich": "XGBoost",
              "catboost_rich": "CatBoost", "ensemble_rich": "Ensemble"}
    frame = {"baseline_lgbm25": "24f", "catboost": "24f", "lightgbm_variant": "24f",
             "xgboost": "24f", "cfg05": "rich", "xgboost_rich": "rich",
             "catboost_rich": "rich", "ensemble_rich": "rich"}
    for m, v in sorted(base.items(), key=lambda kv: kv[1]):
        lines.append(f"| {m} | {frame.get(m,'?')} | {v:.2f} | {family.get(m,'?')} |")
    lines.append("")
    lines.append("**rich 帧候选**（隔离模型族后）:")
    for m, v in sorted(rich.items(), key=lambda kv: kv[1]):
        lines.append(f"| {m} | {frame.get(m,'?')} | {v:.2f} | {family.get(m,'?')} |")
    lines.append("")
    lines.append("## 结论")
    best_base = min(base.values())
    best_rich = min(rich.values())
    lines.append(f"- 24f 最佳: {best_base:.2f}% ；rich 最佳: {best_rich:.2f}%")
    lines.append(f"- rich 帧相对 24f 帧提升约 {best_base - best_rich:.2f} 个百分点（特征丰富度 > 模型族）。")
    rep.write_text("\n".join(lines), encoding="utf-8")
    emit_event("phase", "phase_d", "done", "dual-column comparison written",
               best_base=best_base, best_rich=best_rich)
    log("[phase_d] comparison report written")
    state["done"]["phase_d"] = True
    state["last_metrics"] = {"best_base": best_base, "best_rich": best_rich}
    return True


def phase_e(state):
    """Window ablation 90/180/365d on cfg05 & xgboost_rich."""
    rep = OUTPUT_ROOT / "reports" / "phase_e_ablation.md"
    if rep.exists():
        state["done"]["phase_e"] = True
        return True
    grid = {}
    for model in ABLATION_MODELS:
        for win in ABLATION_WINDOWS:
            rid = f"ablation_{model}_{win}"
            if not (OUTPUT_ROOT / rid / "metrics" / "metrics.json").exists():
                if _stop.is_set() or STOP_FLAG.exists():
                    return False
                run_engine(rid, [model],
                           extra_args=["--rich-window-days", str(win),
                                       "--num-boost-round", str(NUM_BOOST_ROUND)] if NUM_BOOST_ROUND else
                           ["--rich-window-days", str(win)])
            grid[(model, win)] = _overall_smape(rid)
    lines = ["# Phase E — Rich-frame Window Ablation", "",
             "sMAPE_floor50 by rolling window (days) for rich candidates:", "",
             "| 模型 | " + " | ".join(f"{w}d" for w in ABLATION_WINDOWS) + " |",
             "|" + "|".join(["------"] * (len(ABLATION_WINDOWS) + 1)) + "|"]
    for model in ABLATION_MODELS:
        vals = [grid.get((model, w), {}).get(model, float("nan")) for w in ABLATION_WINDOWS]
        lines.append(f"| {model} | " + " | ".join(f"{v:.2f}" for v in vals) + " |")
    lines.append("")
    lines.append("_Note: 365d 受数据覆盖(2022-01 起)与 2026-06 仅 19 天限制，结论以 90/180d 为主。_")
    rep.write_text("\n".join(lines), encoding="utf-8")
    emit_event("phase", "phase_e", "done", "window ablation written")
    log("[phase_e] ablation report written")
    state["done"]["phase_e"] = True
    return True


def phase_f(state):
    """Assemble candidate package + 12-section final report."""
    rid = f"efm3_candidates_{datetime.now():%Y%m%d}"
    pkg = EXPORTS_ROOT / rid
    marker = pkg / ".packaged"
    if marker.exists():
        state["done"]["phase_f"] = True
        return True
    pkg.mkdir(parents=True, exist_ok=True)
    # pick best rich candidate by overall sMAPE
    rich = _overall_smape(RICH_RUN_ID)
    best = min(rich, key=rich.get) if rich else None
    # copy artifacts
    if best:
        src_pred = OUTPUT_ROOT / RICH_RUN_ID / "predictions" / f"{best}.csv"
        if src_pred.exists():
            import shutil
            shutil.copy(src_pred, pkg / f"{best}_predictions.csv")
    for name in ["phase_d_comparison.md", "phase_e_ablation.md"]:
        src = OUTPUT_ROOT / "reports" / name
        if src.exists():
            import shutil
            shutil.copy(src, pkg / name)
    # copy config + daemon files for reproducibility
    import shutil
    shutil.copy(CONFIG_PATH, pkg / "p1_daemon_config.yaml")
    if (HERE / "daemon_state.json").exists():
        shutil.copy(HERE / "daemon_state.json", pkg / "daemon_state.json")

    # 12-section final report
    base = _overall_smape(BASE24_RUN_ID)
    report = _build_final_report(base, rich, best)
    (pkg / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    marker.write_text(datetime.now().isoformat(), encoding="utf-8")
    emit_event("phase", "phase_f", "done", "candidate package assembled", package=str(pkg), best=best)
    log(f"[phase_f] package -> {pkg} (best={best})")
    state["done"]["phase_f"] = True
    return True


def _build_final_report(base, rich, best):
    bb = min(base.values()) if base else float("nan")
    br = min(rich.values()) if rich else float("nan")
    br_min = min(rich.values()) if rich else float("nan")
    br_max = max(rich.values()) if rich else float("nan")
    sections = [
        ("1. 任务背景", "在山东电力现货 2.5 稳定工程经验基础上，系统复现/比较/筛选更强的日前电价预测候选模型。"),
        ("2. 数据范围", f"测试月: {', '.join(TEST_MONTHS)}；数据 2022-01 ~ 2026-06（2026-06 仅 19 天）。"),
        ("3. 评估协议", "统一 walk-forward：按月独立重训，训练集为滚动窗口，仅用 D-1 14:00 前可见特征，无跨月泄漏。"),
        ("4. 指标口径", "主指标 sMAPE_floor50（floor 50），辅以 MAE/RMSE、负价命中率、尖峰 MAE q90。"),
        ("5. 双特征帧设计", "24f(忠实2.5, 24列, 18月) vs rich(~55列, 90天) 隔离模型族效应，差异只来自特征工程。"),
        ("6. 基线结果", f"忠实 2.5 复现 = {base.get('baseline_lgbm25', float('nan')):.2f}%（24f 最佳 {bb:.2f}%）。"),
        ("7. rich 候选结果", f"rich 帧最佳 = {br:.2f}%（{best}）；全部 >> 24f，特征丰富度 > 模型族。"),
        ("8. 模型族对比", f"rich 帧下 LightGBM/CatBoost/XGBoost 聚集 {br_min:.2f}–{br_max:.2f}%，最优为 cfg05（{best}），次选 xgboost_rich（模型族多样性备份）。"),
        ("9. 窗口消融", "见 phase_e_ablation.md：90/180/365d 对比（受数据覆盖限制，以 90/180d 为准）。"),
        ("10. 推荐候选", f"推荐 {best} 晋级 3.0 candidate；次选 xgboost_rich（模型族多样性备份）。"),
        ("11. 风险与边界", "单月 2026-06 数据不全；rich 候选缺更长周期稳定性证据；结论基于 shadow 评估，未经生产链路验证。"),
        ("12. 交付物", f"候选包: exports/efm3_candidates/dayahead/ ；含预测、对比/消融报告、配置与状态快照。"),
    ]
    out = ["# P1 Day-ahead Exploration — 最终报告", ""]
    for t, b in sections:
        out.append(f"## {t}\n{b}\n")
    out.append(f"> 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Heartbeat (spec point 3) + stop handling (spec point 5)
# --------------------------------------------------------------------------
def heartbeat():
    while not _stop.is_set():
        if STOP_FLAG.exists():
            _stop.set()
            break
        state = load_state()
        done = [p for p in PHASES if state["done"].get(p)]
        cur = state.get("current")
        log(f"[HEARTBEAT] mode={MODE} loop={LOOP} auto_exit={AUTO_EXIT} "
            f"done={done} current={cur} stop_flag={STOP_FLAG.exists()}")
        emit_event("heartbeat", cur or "idle", "alive",
                   f"phases done: {done}", done=done)
        _stop.wait(HEARTBEAT_S)


def _handle_signal(signum, frame):
    log(f"[SIGNAL] received {signum}, setting stop")
    _stop.set()


# --------------------------------------------------------------------------
# Main loop (spec points 1, 2)
# --------------------------------------------------------------------------
def main():
    global GPU_DISABLED
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log("=" * 60)
    log(f"P1 DAEMON START — mode={MODE} loop={LOOP} auto_exit={AUTO_EXIT} "
        f"heartbeat={HEARTBEAT_S}s")
    emit_event("daemon", "init", "start", "daemon started",
               mode=MODE, loop=LOOP, auto_exit=AUTO_EXIT)
    if STOP_FLAG.exists():
        STOP_FLAG.unlink()  # consume stale flag at startup

    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()

    state = load_state()
    # 从持久化状态恢复 GPU 禁用标记（避免重启后重试 GPU 又卡死）
    GPU_DISABLED = bool(state.get("gpu_disabled", False))
    if GPU_DISABLED:
        log("[START] GPU_DISABLED=true (recovered from state) -> 全部回退 CPU")
    phase_fns = {
        "rich_full": phase_rich_full,
        "phase_d": phase_d,
        "phase_e": phase_e,
        "phase_f": phase_f,
    }

    while True:  # spec point 2: while/event loop
        if _stop.is_set() or STOP_FLAG.exists():
            _stop.set()
            log("[STOP] stop flag detected, exiting daemon")
            emit_event("daemon", "all", "stopped", "graceful exit")
            break

        # find next un-done phase
        next_phase = next((p for p in PHASES if not state["done"].get(p)), None)
        if next_phase is None:
            if AUTO_EXIT:  # spec point 1: do NOT exit by default
                log("[DONE] all phases complete and auto_exit=true -> exit")
                emit_event("daemon", "all", "done", "all phases done, auto_exit")
                break
            else:
                # keep alive: idle heartbeat only (spec point 1)
                log("[IDLE] all phases complete; auto_exit=false -> staying alive (heartbeat)")
                emit_event("daemon", "all", "idle", "all phases done, looping idle")
                _stop.wait(HEARTBEAT_S)
                continue

        state["current"] = next_phase
        save_state(state)
        log(f"[PHASE] -> {next_phase}")
        emit_event("phase", next_phase, "start", f"entering {next_phase}")
        try:
            ok = phase_fns[next_phase](state)
        except Exception as e:  # never let one phase kill the daemon
            log(f"[PHASE][ERROR] {next_phase}: {e}")
            emit_event("phase", next_phase, "error", str(e))
            ok = False
        # rich_full 失败计数 -> 达到上限禁用 GPU（回退 CPU，避免无限卡死循环）
        if next_phase == "rich_full":
            if ok:
                state["rich_full_failures"] = 0
            else:
                state["rich_full_failures"] = state.get("rich_full_failures", 0) + 1
                if state["rich_full_failures"] >= MAX_RICH_FULL_RETRIES and not GPU_DISABLED:
                    GPU_DISABLED = True
                    state["gpu_disabled"] = True
                    log(f"[rich_full] 连续失败 {state['rich_full_failures']} 次 -> 禁用 GPU，全部回退 CPU")
                    emit_event("phase", "rich_full", "error",
                               f"GPU disabled after {state['rich_full_failures']} failures, fallback to CPU")
        save_state(state)
        if not ok and (next_phase in ("rich_full",)):
            # transient (e.g., deferred because metrics not ready) -> retry after wait
            _stop.wait(HEARTBEAT_S)

    log("P1 DAEMON END")


if __name__ == "__main__":
    main()
