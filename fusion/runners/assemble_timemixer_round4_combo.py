from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path("fusion_runs")
LEADERBOARD = Path("TimeMixer/outputs_v2/serial_keepdrop/leaderboard.csv")


MONTH_CANDIDATES = {
    "2026-02": [
        ("safe_fusion", ROOT / "timemixer_safe_fusion" / "2026-02_safe_rt9_16_fusion"),
        ("frozen", ROOT / "timemixer_frozen_batch" / "2026-02_historical_joint_timemixer_frozen_risk_peak_weighted_rt-rt_916_regime_affine"),
        ("multiseed", ROOT / "timemixer_multiseed_ensemble" / "2026-02_multiseed"),
    ],
    "2026-03": [
        ("safe_fusion", ROOT / "timemixer_safe_fusion" / "2026-03_safe_rt9_16_fusion"),
        ("direct24", ROOT / "timemixer_direct24_batch" / "2026-03_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine"),
        ("frozen", ROOT / "timemixer_frozen_batch" / "2026-03_historical_joint_timemixer_frozen_risk_peak_weighted_rt-rt_916_regime_affine"),
        ("multiseed", ROOT / "timemixer_multiseed_ensemble" / "2026-03_multiseed"),
        ("vmd", ROOT / "timemixer_vmd_rt916" / "2026-03_vmd_rt916"),
    ],
    "2026-04": [
        ("safe_fusion", ROOT / "timemixer_safe_fusion" / "2026-04_safe_rt9_16_fusion"),
        ("frozen", ROOT / "timemixer_frozen_batch" / "2026-04_historical_joint_timemixer_frozen_risk_peak_weighted_rt-rt_916_regime_affine"),
        ("multiseed", ROOT / "timemixer_multiseed_ensemble" / "2026-04_multiseed"),
    ],
    "2026-05": [
        ("safe_fusion", ROOT / "timemixer_safe_fusion" / "2026-05_safe_rt9_16_fusion"),
        ("direct24", ROOT / "timemixer_direct24_batch" / "2026-05_historical_joint_timemixer_risk_peak_weighted_rt-rt_916_regime_affine"),
        ("frozen", ROOT / "timemixer_frozen_batch" / "2026-05_historical_joint_timemixer_frozen_risk_peak_weighted_rt-rt_916_regime_affine"),
        ("multiseed", ROOT / "timemixer_multiseed_ensemble" / "2026-05_multiseed"),
        ("vmd", ROOT / "timemixer_vmd_rt916" / "2026-05_vmd_rt916"),
    ],
}


def load_metric(path: Path) -> tuple[float, float, float, float]:
    metrics = pd.read_csv(path / "metrics_by_period.csv")
    rt_overall = float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "overall"), "sMAPE"].iloc[0])
    da_overall = float(metrics.loc[(metrics["task"] == "da") & (metrics["period"] == "overall"), "sMAPE"].iloc[0])
    da_17 = float(metrics.loc[(metrics["task"] == "da") & (metrics["period"] == "17_24"), "sMAPE"].iloc[0])
    rt_17 = float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "17_24"), "sMAPE"].iloc[0])
    return da_overall, rt_overall, da_17, rt_17


def evaluate_metrics(pred_df: pd.DataFrame, task: str) -> pd.DataFrame:
    rows = []
    true_col = "day_ahead_clearing_price" if task == "da" else "realtime_price"
    for period in ["overall", "1_8", "9_16", "17_24"]:
        sub = pred_df if period == "overall" else pred_df[pred_df["period"] == period]
        pred = sub["y_pred"].to_numpy(float).copy()
        true = sub[true_col].to_numpy(float).copy()
        pred[pred < 50] = 50
        true[true < 50] = 50
        denom = (abs(pred) + abs(true)) / 2
        smape = float((abs(pred - true) / denom).mean() * 100)
        mae = float(abs(pred - true).mean())
        mse = float(((pred - true) ** 2).mean())
        rows.append({"task": task, "period": period, "n": len(sub), "MAE": mae, "MSE": mse, "RMSE": mse ** 0.5, "sMAPE": smape})
    return pd.DataFrame(rows)


def append_leaderboard(entries: list[dict[str, object]]) -> None:
    LEADERBOARD.parent.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(entries)
    if LEADERBOARD.exists():
        old = pd.read_csv(LEADERBOARD)
        merged = pd.concat([old, new_df], ignore_index=True)
    else:
        merged = new_df
    merged.to_csv(LEADERBOARD, index=False, encoding="utf-8-sig")


def main() -> None:
    out_root = ROOT / "timemixer_round4_combo"
    out_root.mkdir(parents=True, exist_ok=True)
    selected_rows = []
    pred_tables = []
    leaderboard_rows = []

    for month, candidates in MONTH_CANDIDATES.items():
        scored = []
        for name, path in candidates:
            da_overall, rt_overall, da_17, rt_17 = load_metric(path)
            scored.append((rt_overall, da_overall, name, path, da_17, rt_17))
            leaderboard_rows.append(
                {
                    "timestamp": pd.Timestamp.now().isoformat(sep=" "),
                    "month": month,
                    "pipeline_mode": "round4_candidate",
                    "backbone": name,
                    "output_dir": str(path),
                    "da_smape_overall": da_overall,
                    "rt_smape_overall": rt_overall,
                    "da_smape_17_24": da_17,
                    "rt_smape_17_24": rt_17,
                }
            )
        scored.sort(key=lambda x: (x[0], x[1], x[2]))
        rt_overall, da_overall, name, path, da_17, rt_17 = scored[0]
        selected_rows.append(
            {
                "month": month,
                "selected_config": name,
                "source_dir": str(path),
                "da_overall": da_overall,
                "rt_overall": rt_overall,
            }
        )
        pred = pd.read_csv(path / "predictions_raw.csv")
        pred["selected_config"] = name
        pred_tables.append(pred)

    combo_pred = pd.concat(pred_tables, ignore_index=True)
    combo_pred.to_csv(out_root / "predictions_raw.csv", index=False, encoding="utf-8-sig")
    metrics = pd.concat(
        [
            evaluate_metrics(combo_pred[combo_pred["task"] == "da"].copy(), "da"),
            evaluate_metrics(combo_pred[combo_pred["task"] == "rt"].copy(), "rt"),
        ],
        ignore_index=True,
    )
    metrics.to_csv(out_root / "metrics_by_period.csv", index=False, encoding="utf-8-sig")
    monthly = pd.DataFrame(selected_rows)
    monthly.to_csv(out_root / "monthly_summary.csv", index=False, encoding="utf-8-sig")
    aggregate = pd.DataFrame(
        [
            {
                "months": ",".join(monthly["month"].tolist()),
                "da_overall_mean": float(monthly["da_overall"].mean()),
                "rt_overall_mean": float(monthly["rt_overall"].mean()),
            }
        ]
    )
    aggregate.to_csv(out_root / "aggregate_summary.csv", index=False, encoding="utf-8-sig")
    (out_root / "batch_manifest.json").write_text(
        json.dumps({"selected": selected_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    leaderboard_rows.append(
        {
            "timestamp": pd.Timestamp.now().isoformat(sep=" "),
            "month": "2026-02_to_2026-05",
            "pipeline_mode": "round4_combo",
            "backbone": "monthly_best_combo",
            "output_dir": str(out_root),
            "da_smape_overall": float(metrics.loc[(metrics["task"] == "da") & (metrics["period"] == "overall"), "sMAPE"].iloc[0]),
            "rt_smape_overall": float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "overall"), "sMAPE"].iloc[0]),
            "da_smape_17_24": float(metrics.loc[(metrics["task"] == "da") & (metrics["period"] == "17_24"), "sMAPE"].iloc[0]),
            "rt_smape_17_24": float(metrics.loc[(metrics["task"] == "rt") & (metrics["period"] == "17_24"), "sMAPE"].iloc[0]),
        }
    )
    append_leaderboard(leaderboard_rows)
    print(monthly.to_string(index=False))
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    main()
