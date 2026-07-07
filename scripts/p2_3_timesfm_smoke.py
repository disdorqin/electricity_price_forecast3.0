"""P2.3 TimesFM smoke test — 3 representative months only.

Runs timesfm on 2025-03, 2025-09, 2026-05, records timing + completeness.
No timemixer, no rt916, no full backfill.
"""
from __future__ import annotations
import os, sys, time as time_module, json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

EFM3 = Path("D:/作业/大创_挑战杯_互联网/大学生创新创业计划/大创实现/其他资料/efm3.0")
sys.path.insert(0, str(EFM3))

os.environ.setdefault("OPTIM_NUM_WORKERS", "0")
os.environ.setdefault("OPTIM_PIN_MEMORY", "0")
os.environ["PROJECT_ROOT"] = str(EFM3)
os.environ.pop("HF_ENDPOINT", None)

from pipelines.ledger_predict import _predict_model

PYTHON = "D:/computer_download/environment/conda/epf-2/python.exe"
SMOKE_MONTHS = ["2025-03", "2025-09", "2026-05"]
PRED_OUT = EFM3 / "outputs" / "runs"

results = {}

for month in SMOKE_MONTHS:
    y, m = month.split("-")
    d0 = pd.Timestamp(year=int(y), month=int(m), day=1)
    d1 = d0 + pd.offsets.MonthEnd(1)
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range(d0, d1, freq="D")]
    print(f"\n[timesfm-smoke] === {month} ({len(days)} days) ===", flush=True)

    month_ok = 0
    month_total_s = 0
    month_skip_cached = 0

    for day in days:
        out_dir = PRED_OUT / day / "realtime" / "prediction"
        out_csv = out_dir / "timesfm_predictions.csv"
        if out_csv.exists():
            month_skip_cached += 1
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        pd_cutoff = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        rt_cutoff = f"{pd_cutoff} 14:00:00"

        t0 = time_module.time()
        try:
            _predict_model(
                model_name="timesfm",
                task="realtime",
                target_date=day,
                data_path=str(EFM3 / "data" / "shandong_pmos_hourly.xlsx"),
                epf_root=None,
                allow_v2_fallback=False,
                epf_v1_mode="exact",
                cutoff_date=rt_cutoff,
                realtime_cutoff_hour=14,
                training_months=12,
                val_ratio=0.2,
                timemixer_epochs=80,
                timemixer_patience=15,
                timemixer_batch_size=16,
                timemixer_full_refit=True,
                timemixer_seeds=42,
                seed=42,
                deterministic=False,
                output_path=str(out_csv),
            )
            elapsed = time_module.time() - t0
            month_ok += 1
            month_total_s += elapsed
            print(f"  {day}: ok({elapsed:.0f}s)", flush=True)
        except Exception as e:
            elapsed = time_module.time() - t0
            print(f"  {day}: ERROR after {elapsed:.0f}s: {e}", flush=True)

    month_avg = round(month_total_s / month_ok, 1) if month_ok else None
    print(f"[timesfm-smoke] {month}: ok={month_ok}/{len(days)} avg={month_avg}s cached={month_skip_cached}", flush=True)
    results[month] = {
        "days_total": len(days),
        "days_ok": month_ok,
        "days_cached": month_skip_cached,
        "avg_sec_per_day": month_avg,
        "total_sec": round(month_total_s, 1),
    }

# Summary
print("\n[timesfm-smoke] === SUMMARY ===", flush=True)
all_ok = sum(r["days_ok"] for r in results.values())
all_total = sum(r["days_total"] for r in results.values())
all_sec = sum(r["total_sec"] for r in results.values())
print(f"Total: {all_ok}/{all_total} days completed in {round(all_sec,1)}s", flush=True)
print(f"Avg sec/day across all runs: {round(all_sec/all_ok,1) if all_ok else 'N/A'}", flush=True)

# Write results
EFM3.joinpath("outputs", "runs", "p2_3_timesfm_smoke.json").write_text(
    json.dumps({"results": results, "total_ok": all_ok, "total_days": all_total, "total_sec": all_sec,
                "timestamp": datetime.now(timezone.utc).isoformat()}, indent=2),
    encoding="utf-8",
)
print(f"[timesfm-smoke] Results saved to outputs/runs/p2_3_timesfm_smoke.json", flush=True)
print("[timesfm-smoke] DONE", flush=True)
