import argparse
import random
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score


MODEL_NAME = "TimeMixer"
TRAINING_MODE = "frozen_last_month"
RT_MODE = "rt_with_predicted_da"
INF_MODE = "direct_24"


def set_seed(seed=42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def read_csv_safely(path):
    for enc in ["gbk", "utf-8-sig", "utf-8"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path)


def load_data(data_path):
    df = read_csv_safely(data_path)
    rename_map = {
        "时刻": "ds",
        "日前电价": "day_ahead_clearing_price",
        "日前出清价": "day_ahead_clearing_price",
        "实时电价": "realtime_price",
        "直调负荷预测值": "load",
        "风电总加预测值": "wind",
        "光伏总加预测值": "solar",
        "联络线受电负荷预测值": "interconnect",
        "竞价空间预测值": "bidding_space",
        "新能源总加预测值": "renewable",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    required = [
        "ds", "day_ahead_clearing_price", "realtime_price", "load", "wind",
        "solar", "interconnect", "bidding_space"
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"数据缺少必要字段: {missing}. 当前字段: {list(df.columns)}")
    if "renewable" not in df.columns:
        df["renewable"] = df["wind"] + df["solar"]
    keep = ["ds", "day_ahead_clearing_price", "realtime_price", "load", "wind", "solar", "interconnect", "bidding_space", "renewable"]
    df = df[keep].copy()
    df["ds"] = pd.to_datetime(df["ds"])
    for c in keep:
        if c != "ds":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("ds").drop_duplicates("ds").reset_index(drop=True)
    exog_cols = ["load", "wind", "solar", "interconnect", "bidding_space", "renewable"]
    df[exog_cols] = df[exog_cols].ffill().fillna(0)
    return df


def business_hour(ts):
    h = pd.Timestamp(ts).hour
    return 24 if h == 0 else h


def assign_period(hour_business):
    h = int(hour_business)
    if 1 <= h <= 8:
        return "valley"
    if 9 <= h <= 16:
        return "solar"
    return "peak"


def date_range_days(start, end_exclusive):
    return list(pd.date_range(pd.Timestamp(start), pd.Timestamp(end_exclusive) - pd.Timedelta(days=1), freq="D"))


def add_common_columns(day_df, target_day, cutoff, is_baseline=False, baseline_name=""):
    out = pd.DataFrame({"ds": day_df["ds"].values})
    target_day = pd.Timestamp(target_day)
    decision_day = target_day - pd.Timedelta(days=1)
    out["target_day"] = target_day.date().isoformat()
    out["decision_day"] = decision_day.date().isoformat()
    out["info_cutoff"] = pd.Timestamp(cutoff).isoformat(sep=" ")
    out["hour_physical"] = pd.to_datetime(out["ds"]).dt.hour
    out["hour_business"] = out["ds"].map(business_hour).astype(int)
    out["period"] = out["hour_business"].map(assign_period)
    out["model_name"] = MODEL_NAME
    out["baseline_name"] = baseline_name
    out["is_baseline"] = is_baseline
    out["training_mode"] = TRAINING_MODE
    out["inference_mode"] = INF_MODE
    out["rt_prediction_mode"] = RT_MODE
    out["test_window_complete"] = True
    out["official_test"] = True
    return out


def make_past_features(df, cutoff, target_col, seq_len):
    idx = df.set_index("ds")
    hist = idx.loc[idx.index <= cutoff].tail(seq_len).copy()
    if len(hist) < seq_len:
        raise ValueError("历史窗口不足")
    load = hist["load"].replace(0, np.nan).to_numpy(float)
    wind = hist["wind"].to_numpy(float)
    solar = hist["solar"].to_numpy(float)
    bidding = hist["bidding_space"].to_numpy(float)
    hours = np.array([business_hour(x) for x in hist.index], dtype=float)
    past = np.vstack([
        hist[target_col].to_numpy(float),
        hist["load"].to_numpy(float),
        hist["wind"].to_numpy(float),
        hist["solar"].to_numpy(float),
        hist["interconnect"].to_numpy(float),
        hist["bidding_space"].to_numpy(float),
        hist["renewable"].to_numpy(float),
        np.nan_to_num(load - wind - solar),
        np.nan_to_num(solar / load),
        np.nan_to_num(wind / load),
        np.nan_to_num((wind + solar) / load),
        np.nan_to_num(bidding / load),
        np.sin(2 * np.pi * hours / 24),
        np.cos(2 * np.pi * hours / 24),
    ]).T
    return past


def make_future_features(df, target_day, da_values=None):
    target_day = pd.Timestamp(target_day)
    cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
    if len(cur) != 24:
        raise ValueError(f"{target_day.date()} 不足24小时")
    load = cur["load"].replace(0, np.nan).to_numpy(float)
    wind = cur["wind"].to_numpy(float)
    solar = cur["solar"].to_numpy(float)
    bidding = cur["bidding_space"].to_numpy(float)
    hours = np.array([business_hour(x) for x in cur["ds"]], dtype=float)
    if da_values is None:
        da_values = np.zeros(24, dtype=float)
    future = np.vstack([
        cur["load"].to_numpy(float),
        cur["wind"].to_numpy(float),
        cur["solar"].to_numpy(float),
        cur["interconnect"].to_numpy(float),
        cur["bidding_space"].to_numpy(float),
        cur["renewable"].to_numpy(float),
        np.nan_to_num(load - wind - solar),
        np.nan_to_num(solar / load),
        np.nan_to_num(wind / load),
        np.nan_to_num((wind + solar) / load),
        np.nan_to_num(bidding / load),
        hours,
        np.sin(2 * np.pi * hours / 24),
        np.cos(2 * np.pi * hours / 24),
        np.full(24, target_day.month, dtype=float),
        np.full(24, target_day.dayofweek, dtype=float),
        np.full(24, 1 if target_day.dayofweek >= 5 else 0, dtype=float),
        np.asarray(da_values, dtype=float),
    ]).T
    return future


def make_sample(df, target_day, target_col, seq_len, is_rt=False, da_values=None):
    target_day = pd.Timestamp(target_day)
    if is_rt:
        cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=15)
    else:
        cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=23, minutes=59, seconds=59)
    past = make_past_features(df, cutoff, target_col, seq_len)
    if is_rt:
        future = make_future_features(df, target_day, da_values=da_values)
    else:
        future = make_future_features(df, target_day, da_values=np.zeros(24))
    cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))]
    y = cur[target_col].to_numpy(float)
    if len(y) != 24 or np.isnan(y).any():
        raise ValueError("目标日标签无效")
    return past, future, y


class ElectricityDailyDataset(Dataset):
    def __init__(self, past_arr, future_arr, y_arr):
        self.past = torch.tensor(past_arr, dtype=torch.float32)
        self.future = torch.tensor(future_arr, dtype=torch.float32)
        self.y = torch.tensor(y_arr, dtype=torch.float32)
    def __len__(self):
        return len(self.y)
    def __getitem__(self, idx):
        return self.past[idx], self.future[idx], self.y[idx]


class MovingAvg(nn.Module):
    def __init__(self, kernel_size=25):
        super().__init__()
        self.kernel_size = kernel_size
        self.pad = (kernel_size - 1) // 2
        self.avg = nn.AvgPool1d(kernel_size=kernel_size, stride=1, padding=self.pad)
    def forward(self, x):
        # x: B, L, C
        x_t = x.transpose(1, 2)
        trend = self.avg(x_t)
        if trend.size(-1) != x_t.size(-1):
            trend = trend[..., : x_t.size(-1)]
        trend = trend.transpose(1, 2)
        seasonal = x - trend
        return seasonal, trend


class PastDecomposableMixing(nn.Module):
    def __init__(self, hidden_dim, scales=3, dropout=0.1):
        super().__init__()
        self.decomp = MovingAvg(kernel_size=25)
        self.scales = scales
        self.season_mlps = nn.ModuleList([
            nn.Sequential(nn.Linear(hidden_dim, hidden_dim), nn.GELU(), nn.Dropout(dropout), nn.Linear(hidden_dim, hidden_dim))
            for _ in range(scales)
        ])
        self.trend_mlps = nn.ModuleList([
            nn.Sequential(nn.Linear(hidden_dim, hidden_dim), nn.GELU(), nn.Dropout(dropout), nn.Linear(hidden_dim, hidden_dim))
            for _ in range(scales)
        ])
        self.norms = nn.ModuleList([nn.LayerNorm(hidden_dim) for _ in range(scales)])
    def forward(self, xs):
        outs = []
        prev_s = None
        prev_t = None
        for i, x in enumerate(xs):
            s, t = self.decomp(x)
            if prev_s is not None:
                prev_s_down = nn.functional.interpolate(prev_s.transpose(1,2), size=s.size(1), mode="linear", align_corners=False).transpose(1,2)
                prev_t_down = nn.functional.interpolate(prev_t.transpose(1,2), size=t.size(1), mode="linear", align_corners=False).transpose(1,2)
                s = s + prev_s_down
                t = t + prev_t_down
            y = self.season_mlps[i](s) + self.trend_mlps[i](t)
            y = self.norms[i](x + y)
            outs.append(y)
            prev_s, prev_t = s, t
        return outs


class TimeMixer(nn.Module):
    """A compact PyTorch TimeMixer-style model with multi-scale downsampling and past decomposable mixing.
    Input: past_x (B, seq_len, past_dim), future_x (B, 24, future_dim)
    Output: (B, 24)
    """
    def __init__(self, past_dim, future_dim, seq_len=168, pred_len=24, hidden_dim=64, n_blocks=2, scales=3, dropout=0.1):
        super().__init__()
        self.seq_len = seq_len
        self.pred_len = pred_len
        self.scales = scales
        self.past_proj = nn.Linear(past_dim, hidden_dim)
        self.future_proj = nn.Linear(future_dim, hidden_dim)
        self.blocks = nn.ModuleList([PastDecomposableMixing(hidden_dim, scales=scales, dropout=dropout) for _ in range(n_blocks)])
        self.future_mixer = nn.Sequential(
            nn.LayerNorm(hidden_dim),
            nn.Linear(hidden_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_dim * (scales + 1), hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, pred_len),
        )
    def make_scales(self, x):
        xs = [x]
        cur = x
        for _ in range(1, self.scales):
            cur = nn.functional.avg_pool1d(cur.transpose(1,2), kernel_size=2, stride=2, ceil_mode=True).transpose(1,2)
            xs.append(cur)
        return xs
    def forward(self, past_x, future_x):
        x = self.past_proj(past_x)
        xs = self.make_scales(x)
        for block in self.blocks:
            xs = block(xs)
        pooled = [s.mean(dim=1) for s in xs]
        f = self.future_proj(future_x)
        f = f + self.future_mixer(f)
        f_pool = f.mean(dim=1)
        z = torch.cat(pooled + [f_pool], dim=-1)
        return self.head(z)


def build_arrays(df, days, target_col, seq_len, is_rt=False, pred_da_map=None):
    past_list, future_list, y_list = [], [], []
    for d in days:
        try:
            da_vals = None
            if is_rt:
                cur = df[(df["ds"] >= d) & (df["ds"] < d + pd.Timedelta(days=1))].copy()
                if pred_da_map is None:
                    da_vals = cur["day_ahead_clearing_price"].to_numpy(float)
                else:
                    da_vals = np.array([pred_da_map.get(ts, np.nan) for ts in cur["ds"]], dtype=float)
                    da_vals = np.where(np.isnan(da_vals), cur["day_ahead_clearing_price"].to_numpy(float), da_vals)
            past, future, y = make_sample(df, d, target_col, seq_len, is_rt=is_rt, da_values=da_vals)
            past_list.append(past)
            future_list.append(future)
            y_list.append(y)
        except Exception:
            continue
    if not past_list:
        raise ValueError("没有可用样本")
    return np.stack(past_list), np.stack(future_list), np.stack(y_list)


def train_timemixer(past, future, y, args, device):
    n = len(y)
    split = max(1, int(n * 0.85))
    train_idx = np.arange(0, split)
    valid_idx = np.arange(split, n) if split < n else np.arange(max(0, n-1), n)

    past_scaler = StandardScaler().fit(past[train_idx].reshape(-1, past.shape[-1]))
    future_scaler = StandardScaler().fit(future[train_idx].reshape(-1, future.shape[-1]))
    y_scaler = StandardScaler().fit(y[train_idx])

    def transform_past(a):
        return past_scaler.transform(a.reshape(-1, a.shape[-1])).reshape(a.shape)
    def transform_future(a):
        return future_scaler.transform(a.reshape(-1, a.shape[-1])).reshape(a.shape)
    def transform_y(a):
        return y_scaler.transform(a)

    train_ds = ElectricityDailyDataset(transform_past(past[train_idx]), transform_future(future[train_idx]), transform_y(y[train_idx]))
    valid_ds = ElectricityDailyDataset(transform_past(past[valid_idx]), transform_future(future[valid_idx]), transform_y(y[valid_idx]))
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    valid_loader = DataLoader(valid_ds, batch_size=args.batch_size, shuffle=False)

    model = TimeMixer(
        past_dim=past.shape[-1], future_dim=future.shape[-1], seq_len=args.seq_len,
        pred_len=24, hidden_dim=args.hidden_dim, n_blocks=args.blocks, scales=args.scales,
        dropout=args.dropout
    ).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    loss_fn = nn.L1Loss()
    best_state = None
    best_valid = float("inf")
    patience_left = args.patience

    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        for xb, fb, yb in train_loader:
            xb, fb, yb = xb.to(device), fb.to(device), yb.to(device)
            opt.zero_grad()
            pred = model(xb, fb)
            loss = loss_fn(pred, yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            opt.step()
            train_loss += loss.item() * len(yb)
        train_loss /= len(train_ds)
        model.eval()
        valid_loss = 0.0
        with torch.no_grad():
            for xb, fb, yb in valid_loader:
                xb, fb, yb = xb.to(device), fb.to(device), yb.to(device)
                pred = model(xb, fb)
                loss = loss_fn(pred, yb)
                valid_loss += loss.item() * len(yb)
        valid_loss /= len(valid_ds)
        print(f"epoch={epoch:03d} train_mae_scaled={train_loss:.5f} valid_mae_scaled={valid_loss:.5f}")
        if valid_loss < best_valid - 1e-5:
            best_valid = valid_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience_left = args.patience
        else:
            patience_left -= 1
            if patience_left <= 0:
                print("early stopping")
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    return {"model": model, "past_scaler": past_scaler, "future_scaler": future_scaler, "y_scaler": y_scaler}


def predict_timemixer(bundle, past, future, device, batch_size=64):
    model = bundle["model"]
    ps = bundle["past_scaler"]
    fs = bundle["future_scaler"]
    ys = bundle["y_scaler"]
    past_t = ps.transform(past.reshape(-1, past.shape[-1])).reshape(past.shape)
    future_t = fs.transform(future.reshape(-1, future.shape[-1])).reshape(future.shape)
    dummy_y = np.zeros((len(past), 24), dtype=np.float32)
    ds = ElectricityDailyDataset(past_t, future_t, dummy_y)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=False)
    model.eval()
    preds = []
    with torch.no_grad():
        for xb, fb, _ in loader:
            xb, fb = xb.to(device), fb.to(device)
            pred = model(xb, fb).cpu().numpy()
            preds.append(pred)
    pred_scaled = np.vstack(preds)
    return ys.inverse_transform(pred_scaled)


def smape(pred, true):
    pred = np.asarray(pred, dtype=float)
    true = np.asarray(true, dtype=float)
    denom = np.maximum((np.abs(pred) + np.abs(true)) / 2, 50)
    return float(np.mean(np.abs(pred - true) / denom) * 100)


def scr(pred, true):
    pred = np.asarray(pred, dtype=float)
    true = np.asarray(true, dtype=float)
    if len(pred) < 2:
        return np.nan
    return float(np.mean(np.sign(np.diff(pred)) == np.sign(np.diff(true))) * 100)


def evaluate_metrics(pred_df, task):
    if task == "da":
        pred_col = "pred_day_ahead_price"
        true_col = "day_ahead_clearing_price"
        group_cols = ["model_name", "baseline_name", "is_baseline", "training_mode"]
    else:
        pred_col = "pred_realtime_price"
        true_col = "realtime_price"
        group_cols = ["model_name", "baseline_name", "is_baseline", "training_mode", "rt_prediction_mode"]
    rows = []
    for key, group in pred_df.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        base = dict(zip(group_cols, key))
        for period in ["overall", "valley", "solar", "peak"]:
            sub = group if period == "overall" else group[group["period"] == period]
            pred = sub[pred_col].to_numpy(float)
            true = sub[true_col].to_numpy(float)
            mse = float(np.mean((pred - true) ** 2))
            s = smape(pred, true)
            row = {
                **base,
                "period": period,
                "n": len(sub),
                "MAE": float(np.mean(np.abs(pred - true))),
                "MSE": mse,
                "RMSE": float(np.sqrt(mse)),
                "R2": float(r2_score(true, pred)) if len(sub) > 1 and np.std(true) > 0 else np.nan,
                "sMAPE": s,
                "Accuracy": max(0, 1 - s / 100),
                "SCR": scr(pred, true),
            }
            if task == "rt":
                traded = sub["traded"].astype(int)
                profit = sub["profit_per_mwh"]
                row.update({
                    "Trade_Rate": float(traded.mean() * 100),
                    "Profit_per_MWh_mean": float(profit.mean()),
                    "Profit_per_MWh_sum": float(profit.sum()),
                    "Positive_Profit_Rate": float(((profit > 0).sum() / max(int(traded.sum()), 1)) * 100),
                })
            rows.append(row)
    return pd.DataFrame(rows)


def plot_prediction(df, true_col, pred_col, out_dir, name):
    model_df = df[df["is_baseline"] == False].sort_values("ds")
    plt.figure(figsize=(16, 5))
    plt.plot(model_df["ds"], model_df[true_col], label="actual")
    plt.plot(model_df["ds"], model_df[pred_col], label="TimeMixer_pred")
    plt.legend()
    plt.title(name)
    plt.tight_layout()
    plt.savefig(out_dir / f"{name}.png", dpi=160)
    plt.close()
    for period in ["valley", "solar", "peak"]:
        sub = model_df[model_df["period"] == period]
        plt.figure(figsize=(16, 4))
        plt.plot(sub["ds"], sub[true_col], label="actual")
        plt.plot(sub["ds"], sub[pred_col], label="TimeMixer_pred")
        plt.legend()
        plt.title(f"{name}_{period}")
        plt.tight_layout()
        plt.savefig(out_dir / f"{name}_{period}.png", dpi=160)
        plt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-path", default="shandong_data.csv")
    parser.add_argument("--test-start", default="2026-02-24")
    parser.add_argument("--test-end-exclusive", default="2026-04-28")
    parser.add_argument("--output-dir", default="timemixer_true_outputs_20260224_20260427")
    parser.add_argument("--seq-len", type=int, default=168)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--blocks", type=int, default=2)
    parser.add_argument("--scales", type=int, default=3)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = parser.parse_args()

    set_seed(args.seed)
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    print(f"device={device}")

    test_start = pd.Timestamp(args.test_start)
    test_end = pd.Timestamp(args.test_end_exclusive)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("True TimeMixer Escort Pipeline")
    print("=" * 80)
    print("[1] Loading data...")
    df = load_data(args.data_path)
    print(f"Data rows: {len(df)}")

    test_df = df[(df["ds"] >= test_start) & (df["ds"] < test_end)]
    expected_points = (test_end - test_start).days * 24
    actual_points = len(test_df)
    print("[2] Checking test window...")
    print(f"test_start={test_start}")
    print(f"test_end_exclusive={test_end}")
    print(f"expected_points={expected_points}")
    print(f"actual_points={actual_points}")
    if actual_points != expected_points:
        raise ValueError("测试区间小时数不完整")
    print("test_window_complete=True")
    print("official_test=True")

    train_start = max(df["ds"].min().normalize() + pd.Timedelta(days=8), test_start - pd.Timedelta(days=395))
    valid_start = test_start - pd.Timedelta(days=30)
    train_days = date_range_days(train_start, valid_start)
    test_days = date_range_days(test_start, test_end)
    idx = df.set_index("ds")

    print("[3] Building DA arrays...")
    da_past, da_future, da_y = build_arrays(df, train_days, "day_ahead_clearing_price", args.seq_len, is_rt=False)
    print(f"DA train samples={len(da_y)}")
    print("[4] Training true TimeMixer DA...")
    da_bundle = train_timemixer(da_past, da_future, da_y, args, device)

    print("[5] Predicting DA...")
    da_test_past, da_test_future, _ = build_arrays(df, test_days, "day_ahead_clearing_price", args.seq_len, is_rt=False)
    da_preds = predict_timemixer(da_bundle, da_test_past, da_test_future, device, batch_size=args.batch_size)
    da_rows = []
    for target_day, pred in zip(test_days, da_preds):
        cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
        cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=23, minutes=59, seconds=59)
        out = add_common_columns(cur, target_day, cutoff, False, "")
        out["day_ahead_clearing_price"] = cur["day_ahead_clearing_price"].values
        out["pred_day_ahead_price"] = pred
        da_rows.append(out)
    da_model = pd.concat(da_rows, ignore_index=True)
    pred_da_map = da_model.set_index("ds")["pred_day_ahead_price"].to_dict()

    print("[6] Building DA baselines...")
    da_base_rows = []
    for target_day in test_days:
        cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
        cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=23, minutes=59, seconds=59)
        for baseline_name, shift_days in [("M_naive_D1_DA", 1), ("M_naive_D7_DA", 7)]:
            out = add_common_columns(cur, target_day, cutoff, True, baseline_name)
            out["day_ahead_clearing_price"] = cur["day_ahead_clearing_price"].values
            out["pred_day_ahead_price"] = idx.reindex(cur["ds"] - pd.Timedelta(days=shift_days))["day_ahead_clearing_price"].values
            da_base_rows.append(out)
    da_all = pd.concat([da_model, pd.concat(da_base_rows, ignore_index=True)], ignore_index=True)

    print("[7] Building RT arrays...")
    rt_past, rt_future, rt_y = build_arrays(df, train_days, "realtime_price", args.seq_len, is_rt=True, pred_da_map=None)
    print(f"RT train samples={len(rt_y)}")
    print("[8] Training true TimeMixer RT...")
    rt_bundle = train_timemixer(rt_past, rt_future, rt_y, args, device)

    print("[9] Predicting RT with predicted DA feature...")
    rt_test_past, rt_test_future, _ = build_arrays(df, test_days, "realtime_price", args.seq_len, is_rt=True, pred_da_map=pred_da_map)
    rt_preds = predict_timemixer(rt_bundle, rt_test_past, rt_test_future, device, batch_size=args.batch_size)
    rt_rows = []
    for target_day, pred in zip(test_days, rt_preds):
        cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
        cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=15)
        out = add_common_columns(cur, target_day, cutoff, False, "")
        out["realtime_price"] = cur["realtime_price"].values
        out["day_ahead_clearing_price"] = cur["day_ahead_clearing_price"].values
        out["pred_day_ahead_price"] = [pred_da_map[x] for x in cur["ds"]]
        out["pred_realtime_price"] = pred
        out["traded"] = (out["pred_realtime_price"] > out["day_ahead_clearing_price"]).astype(int)
        out["profit_per_mwh"] = out["traded"] * (out["realtime_price"] - out["day_ahead_clearing_price"])
        rt_rows.append(out)
    rt_model = pd.concat(rt_rows, ignore_index=True)

    print("[10] Building RT baseline...")
    rt_base_rows = []
    for target_day in test_days:
        cur = df[(df["ds"] >= target_day) & (df["ds"] < target_day + pd.Timedelta(days=1))].copy()
        cutoff = target_day - pd.Timedelta(days=1) + pd.Timedelta(hours=15)
        out = add_common_columns(cur, target_day, cutoff, True, "M_naive_D7_RT")
        out["realtime_price"] = cur["realtime_price"].values
        out["day_ahead_clearing_price"] = cur["day_ahead_clearing_price"].values
        out["pred_day_ahead_price"] = [pred_da_map[x] for x in cur["ds"]]
        out["pred_realtime_price"] = idx.reindex(cur["ds"] - pd.Timedelta(days=7))["realtime_price"].values
        out["traded"] = (out["pred_realtime_price"] > out["day_ahead_clearing_price"]).astype(int)
        out["profit_per_mwh"] = out["traded"] * (out["realtime_price"] - out["day_ahead_clearing_price"])
        rt_base_rows.append(out)
    rt_all = pd.concat([rt_model, pd.concat(rt_base_rows, ignore_index=True)], ignore_index=True)

    print("[11] Completeness checks...")
    if len(da_model) != expected_points:
        raise ValueError("DA模型预测行数不完整")
    if len(rt_model) != expected_points:
        raise ValueError("RT模型预测行数不完整")
    if not da_model.groupby("target_day")["hour_business"].nunique().eq(24).all():
        raise ValueError("DA存在非24小时完整日")
    if not rt_model.groupby("target_day")["hour_business"].nunique().eq(24).all():
        raise ValueError("RT存在非24小时完整日")
    print("DA completeness passed")
    print("RT completeness passed")

    print("[12] Evaluating metrics...")
    da_metrics = evaluate_metrics(da_all, "da")
    rt_metrics = evaluate_metrics(rt_all, "rt")

    da_cols = ["ds", "target_day", "decision_day", "info_cutoff", "hour_physical", "hour_business", "period", "day_ahead_clearing_price", "pred_day_ahead_price", "model_name", "baseline_name", "is_baseline", "training_mode", "inference_mode", "rt_prediction_mode", "test_window_complete", "official_test"]
    rt_cols = ["ds", "target_day", "decision_day", "info_cutoff", "hour_physical", "hour_business", "period", "realtime_price", "day_ahead_clearing_price", "pred_day_ahead_price", "pred_realtime_price", "rt_prediction_mode", "traded", "profit_per_mwh", "model_name", "baseline_name", "is_baseline", "training_mode", "inference_mode", "test_window_complete", "official_test"]

    print("[13] Saving outputs...")
    da_all[da_cols].to_csv(out_dir / "predictions_day_ahead_last_month.csv", index=False, encoding="utf-8-sig")
    rt_all[rt_cols].to_csv(out_dir / "predictions_realtime_last_month.csv", index=False, encoding="utf-8-sig")
    da_metrics.to_csv(out_dir / "metrics_day_ahead_by_period.csv", index=False, encoding="utf-8-sig")
    rt_metrics.to_csv(out_dir / "metrics_realtime_by_period.csv", index=False, encoding="utf-8-sig")

    print("[14] Plotting...")
    plot_prediction(da_all, "day_ahead_clearing_price", "pred_day_ahead_price", out_dir, "day_ahead_prediction_vs_actual")
    plot_prediction(rt_all, "realtime_price", "pred_realtime_price", out_dir, "realtime_prediction_vs_actual")

    print("=" * 80)
    print("Done.")
    print(f"Outputs saved to: {out_dir}")
    print("=" * 80)
    print("\nDA metrics:")
    print(da_metrics.to_string(index=False))
    print("\nRT metrics:")
    print(rt_metrics.to_string(index=False))


if __name__ == "__main__":
    main()
