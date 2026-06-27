#!/usr/bin/env python
"""R3D-Tap-GEF 发布输出验证工具。

验证 final 目录中的输出文件是否满足交付要求：
  - 24 小时完整性 (hour_business 1..24)
  - 无缺失值
  - 无重复时刻
  - 可选: 计算 MAE / sMAPE (需提供真实值)

用法:
    python scripts/validate_release_output.py outputs/2026-02-01/final
    python scripts/validate_release_output.py outputs/2026-02-01/final --with-truth
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _read_csv_safe(path: Path) -> pd.DataFrame | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        return pd.read_csv(path)
    except Exception as e:
        print(f"  [WARN] Cannot read {path.name}: {e}")
        return None


def _validate_24_hours(df: pd.DataFrame, label: str) -> list[str]:
    issues: list[str] = []

    if "business_day" in df.columns:
        days = df["business_day"].dropna().unique()
        for day in sorted(days)[:1]:  # Check first day
            sub = df[df["business_day"] == day]
            hours = sorted(sub["hour_business"].dropna().astype(int).unique())
            expected = list(range(1, 25))
            if hours != expected:
                missing = set(expected) - set(hours)
                extra = set(hours) - set(expected)
                if missing:
                    issues.append(f"  [{label}] Missing hours: {sorted(missing)}")
                if extra:
                    issues.append(f"  [{label}] Extra hours: {sorted(extra)}")

    if "ds" in df.columns:
        ts = pd.to_datetime(df["ds"], errors="coerce")
        if ts.duplicated().any():
            dup_count = ts.duplicated().sum()
            issues.append(f"  [{label}] {dup_count} duplicate timestamps")

    return issues


def _validate_column(df: pd.DataFrame, col: str, label: str) -> list[str]:
    issues: list[str] = []
    if col not in df.columns:
        issues.append(f"  [{label}] Missing column: {col}")
        return issues
    null_count = df[col].isna().sum()
    if null_count > 0:
        issues.append(f"  [{label}] {col}: {null_count} null values (out of {len(df)})")
    if (df[col] == 0).all():
        issues.append(f"  [{label}] {col}: ALL values are zero!")
    return issues


def _compute_metrics(df: pd.DataFrame, pred_col: str, truth_col: str, label: str) -> dict | None:
    if pred_col not in df.columns or truth_col not in df.columns:
        return None
    valid = df.dropna(subset=[pred_col, truth_col])
    if len(valid) < 24:
        return None

    y_pred = valid[pred_col].values.astype(float)
    y_true = valid[truth_col].values.astype(float)

    mae = float(np.mean(np.abs(y_pred - y_true)))
    smape_numer = np.abs(y_pred - y_true)
    smape_denom = (np.abs(y_pred) + np.abs(y_true)) / 2.0
    smape = float(np.mean(np.where(smape_denom > 1e-10, smape_numer / smape_denom * 100, 0.0)))

    print(f"  [{label}] MAE: {mae:.2f}, sMAPE: {smape:.2f}%  (n={len(valid)})")
    return {"mae": mae, "smape": smape, "n": len(valid)}


def validate_final_directory(final_dir: str, with_truth: bool = False) -> bool:
    path = Path(final_dir)
    if not path.is_dir():
        print(f"ERROR: Directory not found: {final_dir}")
        return False

    print(f"=== Validating: {final_dir} ===\n")
    all_ok = True

    files_to_check = [
        "dayahead_final_predictions.csv",
        "realtime_final_predictions.csv",
        "submission_ready.csv",
    ]

    for fname in files_to_check:
        fpath = path / fname
        df = _read_csv_safe(fpath)
        if df is None:
            print(f"  [MISSING] {fname}")
            all_ok = False
            continue

        print(f"--- {fname} ({len(df)} rows, columns: {list(df.columns)}) ---")

        label = fname.replace(".csv", "")

        # Row count sanity
        if len(df) == 0:
            print(f"  [FAIL] {label}: empty file")
            all_ok = False
            continue
        if "dayahead" in fname and len(df) != 24:
            print(f"  [WARN] {label}: expected 24 rows, got {len(df)}")
        if "realtime" in fname and len(df) != 24:
            print(f"  [WARN] {label}: expected 24 rows, got {len(df)}")
        if "submission" in fname:
            expected_sr = 24
            if len(df) != expected_sr:
                print(f"  [WARN] {label}: expected {expected_sr} rows, got {len(df)}")

        # 24-hour integrity
        issues = _validate_24_hours(df, label)
        for issue in issues:
            print(f"  [FAIL] {issue}")
            all_ok = False

        # Column validation
        pred_cols_to_check = ["y_fused", "y_fused_corrected", "price"]
        if "dayahead" in fname:
            found = False
            for col in pred_cols_to_check:
                if col in df.columns:
                    iv = _validate_column(df, col, label)
                    for i in iv:
                        print(i)
                        all_ok = False
                    found = True
                    break
            if not found:
                print(f"  [FAIL] {label}: no prediction column found (expected one of {pred_cols_to_check})")
                all_ok = False
        elif "realtime" in fname:
            found = False
            for col in pred_cols_to_check:
                if col in df.columns:
                    iv = _validate_column(df, col, label)
                    for i in iv:
                        print(i)
                        all_ok = False
                    found = True
                    break
            if not found:
                print(f"  [FAIL] {label}: no prediction column found (expected one of {pred_cols_to_check})")
                all_ok = False
        elif "submission" in fname:
            for col in ["business_day", "hour_business", "dayahead_price", "realtime_price"]:
                if col in df.columns:
                    iv = _validate_column(df, col, label)
                    for i in iv:
                        print(i)

        # Metrics
        if with_truth:
            truth_col = next((c for c in df.columns if c in ("y_true", "真实值")), None)
            if truth_col:
                pred_col = next(
                    (c for c in df.columns if c in ("y_fused", "y_fused_corrected", "dayahead_price", "realtime_price", "price")),
                    None,
                )
                if pred_col:
                    _compute_metrics(df, pred_col, truth_col, label)

        print()

    # Check classifier report
    clf_path = path / "classifier_report.json"
    if clf_path.exists():
        try:
            with open(clf_path) as f:
                clf = json.load(f)
            print(f"--- classifier_report.json ---")
            for k, v in clf.items():
                print(f"  {k}: {v}")
            print()
        except Exception as e:
            print(f"  [WARN] Cannot parse classifier_report.json: {e}\n")

    print(f"=== Result: {'ALL OK' if all_ok else 'ISSUES FOUND'} ===")
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Validate R3D-Tap-GEF release outputs")
    parser.add_argument("final_dir", help="Path to final/ directory (e.g. outputs/2026-02-01/final)")
    parser.add_argument("--with-truth", action="store_true", help="Compute MAE/sMAPE if truth columns available")
    args = parser.parse_args()

    ok = validate_final_directory(args.final_dir, with_truth=args.with_truth)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
