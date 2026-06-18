from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

if __package__ in {None, ""}:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from fusion.contracts import standardize_prediction_table
    from fusion.weights import fit_weights_from_long_table
else:
    from .contracts import standardize_prediction_table
    from .weights import fit_weights_from_long_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fit segmented fusion weights from normalized model predictions.")
    parser.add_argument("--input", required=True, help="Path to a normalized long-table CSV.")
    parser.add_argument("--output-dir", required=True, help="Directory for weights and reports.")
    parser.add_argument("--reg", type=float, default=0.1, help="Regularization strength toward prior weights.")
    parser.add_argument("--lower-bound", type=float, default=-0.5, help="Lower bound for each model weight.")
    parser.add_argument("--upper-bound", type=float, default=1.2, help="Upper bound for each model weight.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_path)
    df = standardize_prediction_table(df)
    weights_df, report_df = fit_weights_from_long_table(
        df,
        reg=float(args.reg),
        lower_bound=float(args.lower_bound),
        upper_bound=float(args.upper_bound),
    )

    weights_df.to_csv(output_dir / "weights.csv", index=False, encoding="utf-8-sig")
    report_df.to_csv(output_dir / "fit_report.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
