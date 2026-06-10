"""
日滚动预测接口 —— 静默运行，仅输出预测范围与结果路径。
"""

import argparse
import logging
from pathlib import Path
import sys
from datetime import datetime, timedelta

import pandas as pd


def merge_price_results(clf_pred_df: pd.DataFrame, price_file: str,
                        price_threshold: float = 100.0) -> pd.DataFrame:
    """将分类器结果与电价预测模型融合，输出最终预测电价。

    融合规则（参考 price_merge_clf.py）:
      - 若分类器标记为极值 (final_pred==1) 且 电价预测值 <= price_threshold, 则设为 -80
      - 否则保留电价预测值

    返回
    """
    import pandas as pd
    import numpy as np

    price_df = pd.read_excel(price_file)

    clf_pred_df["时刻"] = pd.to_datetime(clf_pred_df["时刻"])
    price_df["时刻"] = pd.to_datetime(price_df["时刻"])

    price_pred_col = "预测实时电价"
    merged = pd.merge(
        clf_pred_df[["时刻", "实时电价", "final_pred"]],
        price_df[["时刻", price_pred_col]],
        on="时刻",
        how="inner",
    )

    high_mask = (merged["final_pred"] == 1) & (merged[price_pred_col] <= price_threshold)
    merged["融合预测电价"] = np.where(high_mask, -80.0, merged[price_pred_col])

    output_cols = ["时刻", "实时电价", price_pred_col, "final_pred", "融合预测电价"]
    return merged[output_cols]


def main() -> None:
    logging.disable(logging.CRITICAL + 1)

    parser = argparse.ArgumentParser(description="日滚动两阶段级联预测")
    parser.add_argument("start_date", help="预测起始日期，例如 2026-01-06")
    parser.add_argument("end_date", help="预测结束日期，例如 2026-05-27")
    parser.add_argument("--output", "-o", default="results", help="结果输出目录 (默认: results)")
    parser.add_argument("--data", "-d", default="data/260525.xlsx", help="输入数据文件路径")
    parser.add_argument("--merge", action="store_true",
                        help="与电价预测模型融合，输出最终预测电价")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    data_file = str(Path(args.data) if Path(args.data).is_absolute() else project_root / args.data)
    output_dir = Path(args.output) if Path(args.output).is_absolute() else project_root / args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    # 结束日期是第二天0点，所以日期要加一天
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d") + timedelta(days=1)
    test_time_range = [f"{args.start_date} 01:00:00", f"{end_dt:%Y-%m-%d} 00:00:00"]

    from merge_model.core.cascade_daily import Stage2Config, prepare_dataset, run_rolling_daily_cascade

    p1_cache_path = str(Path(data_file).parent / "p1交叉概率" / "p1.xlsx")
    train_start = "2024-01-01 01:00:00"
    stage2_train_start = "2022-01-01 01:00:00"
    oof_cutoff = "2026-01-01 00:00:00"
    price_threshold = -50

    stage2_config = Stage2Config(
        feature_type="预测值",
        model_name="xgboost",
        threshold=0.5,
        gray_low=0.13,
        gray_high=0.68,
        dynamic_gray_enabled=True,
        dynamic_window_days=90,
        dynamic_min_samples=720,
        dynamic_min_positives=80,
        dynamic_recall_min=0.95,
        dynamic_precision_min=0.80,
        dynamic_coverage_min=0.75,
        dynamic_positive_coverage_min=0.66,
        dynamic_low_min=0.10,
        dynamic_low_max=0.20,
        dynamic_high_min=0.55,
        dynamic_high_max=0.80,
        dynamic_low_step=0.01,
        dynamic_high_step=0.01,
        dynamic_max_delta=0.02,
        dynamic_smooth_alpha=0.30,
    )

    df = prepare_dataset(data_file, time_col="时刻")

    print(f"分类器预测范围: {args.start_date} 至 {args.end_date}")

    results_df = run_rolling_daily_cascade(
        df=df,
        target_name="实时电价",
        price_threshold=price_threshold,
        test_time_range=test_time_range,
        train_start=train_start,
        stage2_train_start=stage2_train_start,
        stage2_config=stage2_config,
        p1_cache_path=p1_cache_path,
        oof_cutoff=oof_cutoff,
    )

    # 如果有电价预测模型结果，则保存融合分类器的电价预测结果
    if args.merge:
        price_file_candidates = [
            project_root / "data" / "融合模型预测电价数据.xlsx"
        ]
        price_file = None
        for pf in price_file_candidates:
            if pf.exists():
                price_file = str(pf)
                break
        if price_file is not None:
            merged_results_df = merge_price_results(results_df, price_file, price_threshold=100.0)
            merged_results_df_file = str(output_dir / f"{args.start_date}_{args.end_date}_merged_price.xlsx")
            merged_results_df.to_excel(merged_results_df_file, index=False)
            print(f"融合电价结果已保存至: {merged_results_df_file}")
        else:
            print("未找到电价预测模型文件，跳过融合步骤。")
    # 保存分类器结果
    clf_results_file = str(output_dir / f"{args.start_date}_{args.end_date}_clf.xlsx")
    results_df.to_excel(clf_results_file, index=False)
    print(f"分类器结果成功输出至: {clf_results_file}")


if __name__ == "__main__":
    main()
