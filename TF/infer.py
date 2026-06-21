import argparse
import pandas as pd

from datetime import datetime, timedelta


from TF.price_forecast_copy_分时段预测 import forecast_next_day, set_reproducibility


def predict_price_for_date(
    data_path: str,
    forecast_date: str,
    *,
    target: str = "realtime",
    sheet: int | str | None = 0,
    encoding: str | None = None,
    segment_count: int = 3,
    seed: int = 42,
    deterministic: bool = True,
) -> pd.DataFrame:
    """
    预测指定日期电价

    Parameters
    ----------
    data_path : str
        输入数据路径（csv / excel）

    forecast_date : str
        预测日期，例如 "2025-08-01"

    target : str
        预测目标：
            - "dayahead"
            - "realtime"
            - "spread"

    Returns
    -------
    pd.DataFrame
        包含两列：
            - 时刻
            - 预测值
    """

    default_skip_style = "gap" if target == "realtime" else "normal"

    args = argparse.Namespace(
        mode="forecast",
        data=data_path,
        forecast_date=forecast_date,
        target=target,
        sheet=sheet,
        encoding=encoding,
        segment_count=segment_count,
        horizon=24,
        eval_days=30,
        exog_mode="pred",
        skip_style=default_skip_style,
        seed=seed,
        deterministic=deterministic,
        dump_csv=False,
    )

    set_reproducibility(int(seed), bool(deterministic))

    return forecast_next_day(args)

def _build_date_list(start_date: str, end_date: str) -> list[str]:
    """
    构造日期列表（闭区间）
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    days = (end - start).days + 1
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def predict_price_for_range(
    data_path: str,
    start_date: str,
    end_date: str,
    *,
    target: str = "realtime",
    sheet: int | str | None = 0,
    encoding: str | None = None,
    segment_count: int = 3,
    seed: int = 42,
    deterministic: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    预测多个日期电价

    Returns
    -------
    DataFrame
        包含：
        - 时刻
        - 预测值
    """

    date_list = _build_date_list(start_date, end_date)

    results = []

    for d in date_list:

        if verbose:
            print(f"预测日期: {d}")

        df_pred = predict_price_for_date(
            data_path=data_path,
            forecast_date=d,
            target=target,
            sheet=sheet,
            encoding=encoding,
            segment_count=segment_count,
            seed=seed,
            deterministic=deterministic,
        )

        df_pred = df_pred.copy()

        results.append(df_pred)

    if not results:
        return pd.DataFrame(columns=["时刻", "预测值"])

    df_out = pd.concat(results, ignore_index=True)

    return df_out.loc[:, ["时刻", "预测值"]]

if __name__ == "__main__":
    from dotenv import load_dotenv
    import os

    # load_dotenv()

    # PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")

    # DATA_SET_NAME = os.getenv("DATA_SET_NAME")

    # data_path = os.path.join(PROJECT_ROOT, DATA_SET_NAME)

    # df_pred = predict_price_for_date(
    #     data_path=data_path,
    #     forecast_date="2026-02-07",
    #     target="realtime",
    # )

    # print(df_pred)

    load_dotenv()

    PROJECT_ROOT = os.getenv("PROJECT_ROOT", ".")
    DATA_SET_NAME = os.getenv("DATA_SET_NAME")

    data_path = os.path.join(PROJECT_ROOT, DATA_SET_NAME)

    df_pred = predict_price_for_range(
        data_path=data_path,
        start_date="2026-02-06",
        end_date="2026-02-07",
        target="realtime",
    )

    print(df_pred)
