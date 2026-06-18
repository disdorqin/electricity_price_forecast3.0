from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def clip50_smape(y_true: pd.Series, y_pred: pd.Series) -> float:
    true = y_true.to_numpy(float).copy()
    pred = y_pred.to_numpy(float).copy()
    true[true < 50] = 50
    pred[pred < 50] = 50
    denom = (abs(true) + abs(pred)) / 2
    return float((abs(true - pred) / denom).mean() * 100)


def analyze_month(pred_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(pred_path)
    df["ds"] = pd.to_datetime(df["ds"])
    rt = df[(df["task"] == "rt") & (df["period"] == "9_16")].copy().sort_values("ds")
    rt["date"] = rt["ds"].dt.strftime("%Y-%m-%d")
    rt["hour"] = rt["ds"].dt.hour
    rt["under_err"] = rt["y_true"] - rt["y_pred"]
    rt["jump"] = rt.groupby("date")["y_true"].diff().abs()
    rt["spike_flag"] = (rt["y_true"] > 500) | (rt["jump"].fillna(0) > 200)

    daily_rows: list[dict[str, object]] = []
    for date, sub in rt.groupby("date"):
        daily_rows.append(
            {
                "date": date,
                "rt_9_16_smape": clip50_smape(sub["y_true"], sub["y_pred"]),
                "mean_true": float(sub["y_true"].mean()),
                "mean_pred": float(sub["y_pred"].mean()),
                "max_true": float(sub["y_true"].max()),
                "max_pred": float(sub["y_pred"].max()),
                "mean_under_err": float(sub["under_err"].mean()),
                "max_under_err": float(sub["under_err"].max()),
                "spike_hours": int(sub["spike_flag"].sum()),
                "solar_mean": float(sub["solar"].mean()),
                "solar_drop": float(sub["solar"].max() - sub["solar"].min()),
                "load_mean": float(sub["load"].mean()),
                "load_ramp": float(sub["load"].max() - sub["load"].min()),
                "bidding_mean": float(sub["bidding_space"].mean()),
                "bidding_min": float(sub["bidding_space"].min()),
                "bidding_drop": float(sub["bidding_space"].max() - sub["bidding_space"].min()),
                "da_mean": float(sub["day_ahead_clearing_price"].mean()),
            }
        )
    daily = pd.DataFrame(daily_rows).sort_values("rt_9_16_smape", ascending=False)
    spikes = rt[rt["spike_flag"]].copy()
    spikes = spikes[
        [
            "ds",
            "date",
            "hour",
            "y_true",
            "y_pred",
            "under_err",
            "jump",
            "day_ahead_clearing_price",
            "load",
            "solar",
            "wind",
            "bidding_space",
            "renewable",
        ]
    ]
    return daily, spikes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--months",
        nargs="+",
        default=["2026-03", "2026-04"],
        help="Months to analyze, using fusion_runs/timemixer_safe_fusion/<month>_safe_rt9_16_fusion/predictions_raw.csv",
    )
    parser.add_argument("--input-root", default="fusion_runs/timemixer_safe_fusion")
    parser.add_argument("--output-root", default="fusion_runs/diagnostics")
    args = parser.parse_args()

    input_root = Path(args.input_root)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    daily_tables: list[pd.DataFrame] = []
    spike_tables: list[pd.DataFrame] = []
    for month in args.months:
        pred_path = input_root / f"{month}_safe_rt9_16_fusion" / "predictions_raw.csv"
        daily, spikes = analyze_month(pred_path)
        daily.insert(0, "month", month)
        if not spikes.empty:
            spikes.insert(0, "month", month)
            spike_tables.append(spikes)
        daily_tables.append(daily)

    daily_all = pd.concat(daily_tables, ignore_index=True)
    daily_all.to_csv(output_root / "rt916_daily_diagnostic.csv", index=False, encoding="utf-8-sig")
    if spike_tables:
        pd.concat(spike_tables, ignore_index=True).to_csv(
            output_root / "rt916_spike_hours.csv",
            index=False,
            encoding="utf-8-sig",
        )

    for month in args.months:
        sub = daily_all[daily_all["month"] == month].head(10)
        print(f"\n[{month}] worst 10 days")
        print(
            sub[
                [
                    "date",
                    "rt_9_16_smape",
                    "mean_true",
                    "mean_pred",
                    "max_true",
                    "max_pred",
                    "mean_under_err",
                    "spike_hours",
                    "solar_mean",
                    "bidding_mean",
                    "bidding_min",
                    "da_mean",
                ]
            ].to_string(index=False)
        )


if __name__ == "__main__":
    main()
