from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from shutil import copyfile
from urllib.error import HTTPError, URLError
from urllib.request import urlretrieve

import pandas as pd
from dotenv import load_dotenv

from utils.database_operate import fetch_web_grid_data


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
CANONICAL_XLSX = DATA_DIR / "shandong_pmos_hourly.xlsx"
CANONICAL_CSV = DATA_DIR / "shandong_pmos_hourly.csv"
BASE_URL = "http://qiniu.dirx.com.cn/workspace/eprice_forecast"
TIMESTAMP_COL = "时刻"


def _max_timestamp_from_excel(path: Path) -> pd.Timestamp | None:
    try:
        df = pd.read_excel(path, usecols=[TIMESTAMP_COL])
    except Exception:
        return None
    series = pd.to_datetime(df[TIMESTAMP_COL], errors="coerce")
    if series.empty:
        return None
    return series.max()


def _max_timestamp_from_csv(path: Path) -> pd.Timestamp | None:
    try:
        df = pd.read_csv(path, usecols=[TIMESTAMP_COL], encoding="gbk")
    except Exception:
        try:
            df = pd.read_csv(path, usecols=[TIMESTAMP_COL], encoding="utf-8-sig")
        except Exception:
            return None
    series = pd.to_datetime(df[TIMESTAMP_COL], errors="coerce")
    if series.empty:
        return None
    return series.max()


def _download_latest_available_excel(max_lookback_days: int = 60) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    latest_attempted_url = None
    for delta in range(max_lookback_days):
        current_day = date.today() - timedelta(days=delta)
        filename = f"shandong_pmos_hourly_20220101_{current_day:%Y%m%d}.xlsx"
        file_url = f"{BASE_URL}/{filename}"
        latest_attempted_url = file_url
        local_file = DATA_DIR / filename
        if local_file.exists():
            return local_file
        try:
            urlretrieve(file_url, str(local_file))
            return local_file
        except HTTPError as exc:
            if getattr(exc, "code", None) == 404:
                continue
            raise
        except URLError:
            raise
    raise FileNotFoundError(f"No downloadable dataset found from cloud source. Last attempted: {latest_attempted_url}")


def _candidate_local_excel_files() -> list[Path]:
    candidates = list(DATA_DIR.glob("shandong_pmos_hourly_20220101_*.xlsx"))
    if CANONICAL_XLSX.exists():
        candidates.append(CANONICAL_XLSX)
    return sorted(set(candidates), reverse=True)


def _latest_local_dataset_source() -> tuple[Path, str]:
    csv_ts = _max_timestamp_from_csv(CANONICAL_CSV) if CANONICAL_CSV.exists() else None
    xlsx_candidates = _candidate_local_excel_files()

    best_xlsx: Path | None = None
    best_xlsx_ts: pd.Timestamp | None = None
    for candidate in xlsx_candidates:
        ts = _max_timestamp_from_excel(candidate)
        if ts is None:
            continue
        if best_xlsx_ts is None or ts > best_xlsx_ts:
            best_xlsx = candidate
            best_xlsx_ts = ts

    if csv_ts is not None and (best_xlsx_ts is None or csv_ts >= best_xlsx_ts):
        return CANONICAL_CSV, "csv"
    if best_xlsx is not None:
        return best_xlsx, "xlsx"
    raise FileNotFoundError(f"No readable local dataset candidates found under: {DATA_DIR}")


def _save_frame(df: pd.DataFrame) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_excel(CANONICAL_XLSX, index=False)
    df.to_csv(CANONICAL_CSV, index=False, encoding="gbk")
    return str(CANONICAL_XLSX)


def _sync_from_database() -> str:
    data = fetch_web_grid_data()
    if data is None or data.empty:
        raise ValueError("Database returned empty dataset")
    return _save_frame(data)


def _sync_from_http_or_local() -> str:
    try:
        source_file = _download_latest_available_excel()
        if source_file.resolve() != CANONICAL_XLSX.resolve():
            copyfile(source_file, CANONICAL_XLSX)
        df = pd.read_excel(CANONICAL_XLSX)
    except (HTTPError, URLError, FileNotFoundError):
        local_source, source_kind = _latest_local_dataset_source()
        if source_kind == "csv":
            df = pd.read_csv(local_source, encoding="gbk")
        else:
            if local_source.resolve() != CANONICAL_XLSX.resolve():
                copyfile(local_source, CANONICAL_XLSX)
            df = pd.read_excel(CANONICAL_XLSX)
    return _save_frame(df)


def sync_dataset() -> str:
    try:
        return _sync_from_database()
    except Exception:
        return _sync_from_http_or_local()


if __name__ == "__main__":
    try:
        result = sync_dataset()
        print(result)
    except Exception as exc:  # noqa: BLE001
        print(f"sync_dataset failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
