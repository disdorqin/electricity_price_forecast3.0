from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pymysql
from dotenv import dotenv_values, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EPF_ROOT = PROJECT_ROOT.parent / "epf"
LOCAL_ENV = PROJECT_ROOT / ".env"
EPF_ENV = EPF_ROOT / ".env"


def _load_env_sources() -> dict[str, str]:
    load_dotenv(dotenv_path=LOCAL_ENV, override=False)
    merged: dict[str, str] = {}

    if EPF_ENV.exists():
        merged.update({k: str(v) for k, v in dotenv_values(EPF_ENV).items() if v is not None})
    if LOCAL_ENV.exists():
        merged.update({k: str(v) for k, v in dotenv_values(LOCAL_ENV).items() if v is not None})

    for key in ("DB_HOST", "DB", "DB_USER", "DB_PWD"):
        env_value = os.getenv(key)
        if env_value:
            merged[key] = env_value
    return merged


def get_db_connection():
    cfg = _load_env_sources()
    host = cfg.get("DB_HOST", "").strip().strip("'\"")
    database = cfg.get("DB", "").strip().strip("'\"")
    user = cfg.get("DB_USER", "").strip().strip("'\"")
    password = cfg.get("DB_PWD", "").strip().strip("'\"")

    if not all([host, database, user, password]):
        raise ValueError(
            "Database env vars are incomplete. Required: DB_HOST, DB, DB_USER, DB_PWD. "
            f"Looked in: {LOCAL_ENV} and fallback {EPF_ENV}"
        )

    return pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_web_grid_data() -> pd.DataFrame:
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT "
                "data_time as 时刻, "
                "price_dayahead as 日前电价, "
                "price_realtime as 实时电价, "
                "fcast_local_plant as 地方电厂总加预测值, "
                "fcast_tie_line as 联络线受电负荷预测值, "
                "fcast_wind as 风电总加预测值, "
                "fcast_solar as 光伏总加预测值, "
                "fcast_nuclear as 核电总加预测值, "
                "fcast_self_owned as 自备机组总加预测值, "
                "fcast_test_unit as 试验机组总加预测值, "
                "fcast_direct_load as 直调负荷预测值, "
                "fcast_bidding_space as 竞价空间预测值, "
                "fcast_new_energy as 新能源总加预测值, "
                "actual_local_plant as 地方电厂总加实际值, "
                "actual_tie_line as 联络线受电负荷实际值, "
                "actual_wind as 风电总加实际值, "
                "actual_solar as 光伏总加实际值, "
                "actual_nuclear as 核电总加实际值, "
                "actual_self_owned as 自备机组总加实际值, "
                "actual_test_unit as 试验机组总加实际值, "
                "actual_direct_load as 直调负荷实际值, "
                "actual_bidding_space as 竞价空间实际值, "
                "actual_new_energy as 新能源总加实际值 "
                "FROM epf_market_data;"
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["时刻"] = pd.to_datetime(frame["时刻"], errors="coerce")
    frame = frame.sort_values("时刻").reset_index(drop=True)
    return frame
