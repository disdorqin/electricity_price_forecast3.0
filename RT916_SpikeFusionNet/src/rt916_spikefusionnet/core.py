import os
import sys
import json
import math
import pickle
import random
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from dotenv import load_dotenv
from sklearn.preprocessing import MinMaxScaler
from torch.utils.data import DataLoader, Dataset, Subset

from rt916_spikefusionnet.dataprocess import (
    enrich_selected_features,
    recompute_target_dependent_selected_features,
    enrich_period_local_features,
    feature_engineer_solar_terms,
    process_features,
    split_excel_by_hours,
)
from rt916_spikefusionnet.annual_model import AnnualSpikeGatedTimesNet
from rt916_spikefusionnet.annual_model_da_timemixer import DayAheadTimeMixerNet
from rt916_spikefusionnet.annual_loss import AnnualProtectedCappedLoss

warnings.filterwarnings("ignore", message="enable_nested_tensor is True")
load_dotenv()

# === 无侵入加速:TF32 + cudnn benchmark(依据 docs/项目提高速度.md)===
try:
    import torch as _torch
    if _torch.cuda.is_available():
        if bool(int(os.getenv("OPTIM_TF32", "1"))):
            _torch.backends.cuda.matmul.allow_tf32 = True
            _torch.backends.cudnn.allow_tf32 = True
        if bool(int(os.getenv("OPTIM_CUDNN_BENCHMARK", "1"))):
            _torch.backends.cudnn.benchmark = True
except Exception:
    pass

PROJECT_ROOT_ENV = os.getenv("PROJECT_ROOT") or str(Path(__file__).resolve().parents[3])
DATA_PATH = os.getenv("DATA_SET_NAME", "data/shandong_pmos_hourly.xlsx")
RAW_DF_PATH = os.path.join(PROJECT_ROOT_ENV, DATA_PATH)
PROJECT_ROOT = Path(PROJECT_ROOT_ENV)
PACKAGE_ROOT = PROJECT_ROOT / "RT916_SpikeFusionNet"
PACKAGE_OUT_ROOT = PROJECT_ROOT / "outputs" / "RT916_SpikeMarketLab" / "model_packages" / "RT916_SpikeFusionNet"
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT = "实时电价"
TEST_TOTAL_START_END_LIST = ["2026-02-01 01:00:00", "2026-02-10 00:00:00"]

def _make_inputs(target):
    base_calendar = [
        "星期",
        "是否法定或周末休息",
        "节气_sin",
        "节气_cos",
        "hour",
        "month",
        "day_of_week",
    ]
    if target == "日前电价":
        da_input_variant = os.getenv("SPIKE_DA_INPUT_VARIANT", "base").strip().lower()
        if da_input_variant == "split":
            history = [
                "直调负荷实际值",
                "联络线受电负荷实际值",
                "新能源总加实际值",
                "竞价空间实际值",
                "总用电量",
                "其他负荷总加",
                "净负荷",
                "新能源渗透率",
                "空间_新能源比",
                *base_calendar,
                "lag_24h",
                "lag_72h",
                "lag_168h",
                "lag_336h",
                "target_lag_da",
                "period_lag_d1",
                "period_lag_d2",
                "period_lag_w1",
                "period_prevday_mean",
                "period_prevday_std",
                "period_mix_anchor",
                "prevday_mean_target",
                "prevday_std_target",
                "load_gap_prevday",
                "solar_gap_prevday",
                "net_load_gap_prevday",
                "ramp_load",
                "ramp_solar",
            ]
            future = [
                "直调负荷预测值",
                "联络线受电负荷预测值",
                "新能源总加预测值",
                "竞价空间预测值",
                "总用电量预测值",
                "其他负荷总加预测值",
                "净负荷预测值",
                "新能源渗透率预测值",
                "空间_新能源比预测值",
                *base_calendar,
                "load_pred_change_24h",
                "solar_pred_change_24h",
                "ramp_load_pred",
                "ramp_solar_pred",
            ]
        else:
            history = [
                "直调负荷实际值",
                "联络线受电负荷实际值",
                "新能源总加实际值",
                "竞价空间实际值",
                "总用电量",
                "其他负荷总加",
                "净负荷",
                "新能源渗透率",
                "空间_新能源比",
                *base_calendar,
                "lag_48h",
                "lag_168h",
                "target_lag",
                "period_lag_d1",
                "period_lag_d2",
                "period_lag_w1",
                "period_prevday_mean",
                "period_prevday_std",
                "period_mix_anchor",
                "ramp_load",
                "ramp_solar",
            ]
            future = [
                "直调负荷预测值",
                "联络线受电负荷预测值",
                "新能源总加预测值",
                "竞价空间预测值",
                "总用电量预测值",
                "其他负荷总加预测值",
                "净负荷预测值",
                "新能源渗透率预测值",
                "空间_新能源比预测值",
                *base_calendar,
                "ramp_load_pred",
                "ramp_solar_pred",
            ]
    else:
        history = [
            "直调负荷实际值",
            "联络线受电负荷实际值",
            "新能源总加实际值",
            "竞价空间实际值",
            "总用电量",
            "其他负荷总加",
            "净负荷",
            "新能源渗透率",
            "空间_新能源比",
            *base_calendar,
            "lag_48h",
            "lag_168h",
            "target_lag",
            "ramp_load",
            "ramp_solar",
            "日前电价",
        ]
        future = [
            "直调负荷预测值",
            "联络线受电负荷预测值",
            "新能源总加预测值",
            "竞价空间预测值",
            "总用电量预测值",
            "其他负荷总加预测值",
            "净负荷预测值",
            "新能源渗透率预测值",
            "空间_新能源比预测值",
            *base_calendar,
            "ramp_load_pred",
            "ramp_solar_pred",
            "日前电价",
        ]
    history.append(target)
    return history, future


HISTORY_INPUT, FUTURE_INPUT = _make_inputs(OUTPUT)

CONFIG = {
    "OUTPUT": OUTPUT,
    "TRAIN_STEPS": 1,
    "SEED": 42,
    "SAVE_ROOT_DIR": PACKAGE_OUT_ROOT / "artifacts" / f"{OUTPUT}_分段",
    "PREDICT_RESULT_DIR": PACKAGE_OUT_ROOT
    / f"{OUTPUT}_分段"
    / f"{TEST_TOTAL_START_END_LIST[0][:10]}_{TEST_TOTAL_START_END_LIST[1][:10]}_预测结果",
    "TEST_TOTAL_START_END_LIST": TEST_TOTAL_START_END_LIST,
    "INPUT_LEN_LIST": 8,
    "OUTPUT_LEN_LIST": 8,
    "HISTORY_INPUT": HISTORY_INPUT,
    "FUTURE_INPUT": FUTURE_INPUT,
    "D_MODEL": 64,
    "E_LAYERS": 1,
    "TOP_K": 2,
    "NUM_KERNELS": 3,
    "DROPOUT": 0.1,
    "LR": 3e-4,
    "BATCH_SIZE": 64,
    "EPOCHS": 12,
    "PATIENCE": 4,
    "WEIGHT_DECAY": 1e-5,
    "VAL_RATIO": 0.15,
    "TAIL_LOW_Q": 0.10,
    "TAIL_HIGH_Q": 0.90,
    "TAIL_ALPHA": 1.0,
    "TAIL_DIFF_ALPHA": 0.25,
    "HUBER_BETA": 0.05,
    "MSE_GAMMA": 0.2,
    "DELTA_SCALE": 0.15,
    "RUN_ID": "",
}

def _new_run_id():
    return datetime.now().strftime("run_%Y%m%d_%H%M%S")



def _slice_recent_train_window(df, end_ts, months=12):
    end_ts = pd.Timestamp(end_ts)
    start_ts = end_ts - pd.DateOffset(months=int(months))
    return df[(df["时刻"] >= start_ts) & (df["时刻"] <= end_ts)].copy()


def _inject_predicted_da_for_rt(df, asof_ts=None, external_da_pred_df=None):
    """
    For RT inference only:
    - Keep D-day known part unchanged.
    - Replace DA feature after asof by predicted DA (if provided).
    """
    if CONFIG["OUTPUT"] != "实时电价":
        return df
    if external_da_pred_df is None or len(external_da_pred_df) == 0:
        return df

    out = df.copy()
    out["时刻"] = pd.to_datetime(out["时刻"])

    da_df = external_da_pred_df.copy()
    if "时刻" not in da_df.columns or "pred_da" not in da_df.columns:
        raise ValueError("external_da_pred_df 必须包含列: 时刻, pred_da")
    da_df["时刻"] = pd.to_datetime(da_df["时刻"])
    da_df = da_df[["时刻", "pred_da"]].drop_duplicates(subset=["时刻"], keep="last")
    out = out.merge(da_df, on="时刻", how="left")

    if asof_ts is not None:
        mask = out["时刻"] > pd.Timestamp(asof_ts)
        out.loc[mask, "日前电价"] = out.loc[mask, "pred_da"].where(
            out.loc[mask, "pred_da"].notna(),
            out.loc[mask, "日前电价"],
        )
    else:
        out["日前电价"] = out["pred_da"].where(out["pred_da"].notna(), out["日前电价"])

    out = out.drop(columns=["pred_da"])
    return out


def apply_asof_cutoff_for_inference(df, asof_ts):
    """
    Real-world information boundary for inference:
    only realtime price is unavailable after asof_ts,
    so replace it by day-ahead price.
    """
    if asof_ts is None:
        return df

    out = df.copy()
    out["时刻"] = pd.to_datetime(out["时刻"])
    mask_future_unknown = out["时刻"] > pd.Timestamp(asof_ts)

    rt_col = CONFIG["OUTPUT"]
    if rt_col == "实时电价":
        if rt_col in out.columns and "日前电价" in out.columns:
            out.loc[mask_future_unknown, rt_col] = out.loc[mask_future_unknown, "日前电价"]
    elif rt_col == "日前电价":
        # 纯日前预测：D+1 日前不可用，避免目标泄露
        if rt_col in out.columns:
            out.loc[mask_future_unknown, rt_col] = np.nan
    return out


class ElectricityDataset(Dataset):
    def __init__(self, data, target, seq_len=72, pred_len=8, step=1):
        self.X, self.y = [], []

        hist_dim = len(CONFIG["HISTORY_INPUT"])
        fut_dim = len(CONFIG["FUTURE_INPUT"])
        target_pad_dim = hist_dim - fut_dim
        if target_pad_dim < 0:
            raise ValueError(f"FUTURE_INPUT 维度({fut_dim})不能大于 HISTORY_INPUT 维度({hist_dim})")

        for i in range(0, len(data) - seq_len + 1, step):
            hist_len = seq_len - pred_len - pred_len

            hist_patch = data[i : i + hist_len, :hist_dim]

            today_fut = data[
                i + hist_len : i + hist_len + pred_len,
                hist_dim : hist_dim + fut_dim,
            ]
            if CONFIG["OUTPUT"] == "日前电价":
                today_target = data[
                    i + hist_len : i + hist_len + pred_len,
                    hist_dim - 1,
                ].reshape(-1, 1)
                if target_pad_dim == 1:
                    today_patch = np.concatenate([today_fut, today_target], axis=1)
                else:
                    today_pad = np.zeros((pred_len, target_pad_dim), dtype=np.float32)
                    today_pad[:, -1:] = today_target
                    today_patch = np.concatenate([today_fut, today_pad], axis=1)
            else:
                today_patch = np.concatenate([today_fut, np.zeros((pred_len, target_pad_dim), dtype=np.float32)], axis=1)

            future_feats = data[
                i + hist_len + pred_len : i + seq_len,
                hist_dim : hist_dim + fut_dim,
            ]
            future_patch = np.concatenate([future_feats, np.zeros((pred_len, target_pad_dim), dtype=np.float32)], axis=1)

            full_x = np.concatenate([hist_patch, today_patch, future_patch], axis=0)
            full_y = target[i + seq_len - pred_len : i + seq_len]

            self.X.append(full_x)
            self.y.append(full_y)

        if len(self.X) > 0:
            self.X = torch.tensor(np.array(self.X), dtype=torch.float32)
            self.y = torch.tensor(np.array(self.y), dtype=torch.float32).squeeze(-1)
        else:
            self.X = torch.empty(0)
            self.y = torch.empty(0)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


class TailWeightedHuberMSELoss(nn.Module):
    def __init__(
        self,
        low_thr,
        high_thr,
        alpha=1.0,
        diff_alpha=0.25,
        huber_beta=0.05,
        mse_gamma=0.2,
    ):
        super().__init__()
        self.low_thr = float(low_thr)
        self.high_thr = float(high_thr)
        self.alpha = float(alpha)
        self.diff_alpha = float(diff_alpha)
        self.huber_beta = float(huber_beta)
        self.mse_gamma = float(mse_gamma)

    def forward(self, pred, target):
        err = pred - target

        tail_mask = ((target <= self.low_thr) | (target >= self.high_thr)).float()
        tail_weight = 1.0 + self.alpha * tail_mask

        diff = torch.zeros_like(target)
        if target.size(1) > 1:
            diff[:, 1:] = torch.abs(target[:, 1:] - target[:, :-1])

        diff_scale = diff / (diff.mean(dim=1, keepdim=True) + 1e-6)
        diff_weight = self.diff_alpha * diff_scale.detach()

        weight = tail_weight + diff_weight
        huber = F.smooth_l1_loss(pred, target, reduction="none", beta=self.huber_beta)
        mse = err ** 2
        return (weight * huber).mean() + self.mse_gamma * (weight * mse).mean()


def set_seed(seed=42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def _build_model(num_variates, seq_len, pred_len, cfg):
    if cfg["OUTPUT"] == "日前电价":
        known_target_len = seq_len - pred_len
        da_backbone = os.getenv("SPIKE_DA_PERIOD_BACKBONE", "base").strip().lower()
        if da_backbone == "timemixer" and cfg.get("CURRENT_PERIOD_NAME") == "17-0点":
            return DayAheadTimeMixerNet(
                num_variates=num_variates,
                seq_len=seq_len,
                pred_len=pred_len,
                d_model=cfg["D_MODEL"],
                e_layers=cfg["E_LAYERS"],
                top_k=cfg["TOP_K"],
                num_kernels=cfg["NUM_KERNELS"],
                dropout=cfg["DROPOUT"],
                target_index=-1,
                known_target_len=known_target_len,
                delta_scale=cfg["DELTA_SCALE"],
            )
    else:
        known_target_len = seq_len - 2 * pred_len
    return AnnualSpikeGatedTimesNet(
        num_variates=num_variates,
        seq_len=seq_len,
        pred_len=pred_len,
        d_model=cfg["D_MODEL"],
        e_layers=cfg["E_LAYERS"],
        top_k=cfg["TOP_K"],
        num_kernels=cfg["NUM_KERNELS"],
        dropout=cfg["DROPOUT"],
        target_index=-1,
        known_target_len=known_target_len,
        delta_scale=cfg["DELTA_SCALE"],
    )


def _get_periods(df, mod):
    df_1_8, df_9_16, df_17_0 = split_excel_by_hours(df)
    mapping = {
        "stage1": [("1-8点", df_1_8)],
        "stage2": [("9-16点", df_9_16)],
        "stage3": [("17-0点", df_17_0)],
    }
    return mapping.get(mod, [("1-8点", df_1_8), ("9-16点", df_9_16), ("17-0点", df_17_0)])


def _validate(model, val_loader, scaler_y, device):
    model.eval()
    all_preds, all_trues = [], []

    with torch.no_grad():
        for batch_x, batch_y in val_loader:
            pred = model(batch_x.to(device)).float().cpu().numpy()
            all_preds.append(pred)
            all_trues.append(batch_y.numpy())

    model.train()

    if len(all_preds) == 0:
        return {}

    pred_arr = np.concatenate(all_preds).reshape(-1, 1)
    true_arr = np.concatenate(all_trues).reshape(-1, 1)
    real_preds = scaler_y.inverse_transform(pred_arr).flatten()
    real_trues = scaler_y.inverse_transform(true_arr).flatten()
    return evaluate(real_trues, real_preds)


def train(train_df, mod="all"):
    for period_name, period_data in _get_periods(train_df, mod):
        CONFIG["CURRENT_PERIOD_NAME"] = period_name
        print(f"\n{'=' * 60}\n[SpikeTimesNet] 训练 {period_name}...\n{'=' * 60}")
        train_single_period(period_name, period_data)


def train_single_period(period_name, train_df):
    set_seed(CONFIG["SEED"])

    root_dir = os.path.join(CONFIG["SAVE_ROOT_DIR"], f"TS{CONFIG['TRAIN_STEPS']}_{period_name}")
    os.makedirs(root_dir, exist_ok=True)

    if CONFIG["OUTPUT"] == "日前电价":
        train_df = enrich_period_local_features(train_df, target_col=CONFIG["OUTPUT"], pred_len=CONFIG["OUTPUT_LEN_LIST"])

    cols_for_model = list(dict.fromkeys(CONFIG["HISTORY_INPUT"] + CONFIG["FUTURE_INPUT"] + [CONFIG["OUTPUT"]]))
    df = train_df[cols_for_model].interpolate(method="linear", limit_direction="forward").copy()

    feature_cols = CONFIG["HISTORY_INPUT"] + CONFIG["FUTURE_INPUT"]
    for col in feature_cols:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.replace(" ", "").astype(float)

    input_features = df[feature_cols].values
    target_feature = df[[CONFIG["OUTPUT"]]].values

    scaler_x = MinMaxScaler(feature_range=(0, 1))
    scaler_y = MinMaxScaler(feature_range=(0, 1))
    train_data_scaled = scaler_x.fit_transform(input_features)
    train_target_scaled = scaler_y.fit_transform(target_feature)

    with open(os.path.join(root_dir, "scalar_input.pkl"), "wb") as f:
        pickle.dump(scaler_x, f)
    with open(os.path.join(root_dir, "scalar_output.pkl"), "wb") as f:
        pickle.dump(scaler_y, f)

    pred_len = CONFIG["OUTPUT_LEN_LIST"]
    seq_len = CONFIG["OUTPUT_LEN_LIST"] * CONFIG["INPUT_LEN_LIST"] + CONFIG["OUTPUT_LEN_LIST"]

    full_dataset = ElectricityDataset(
        train_data_scaled,
        train_target_scaled,
        seq_len=seq_len,
        pred_len=pred_len,
        step=CONFIG["TRAIN_STEPS"],
    )

    n_total = len(full_dataset)
    n_val = int(n_total * CONFIG["VAL_RATIO"])
    n_train = n_total - n_val

    train_dataset = Subset(full_dataset, range(n_train))
    val_dataset = Subset(full_dataset, range(n_train, n_total))

    use_cuda = torch.cuda.is_available()
    _num_workers = int(os.getenv("OPTIM_NUM_WORKERS", "4"))
    _num_workers = max(0, _num_workers)
    _pin_memory = use_cuda and bool(int(os.getenv("OPTIM_PIN_MEMORY", "1")))
    _persistent = _num_workers > 0
    _prefetch = int(os.getenv("OPTIM_PREFETCH", "2"))
    _dl_kwargs = dict(
        num_workers=_num_workers,
        pin_memory=_pin_memory,
        persistent_workers=_persistent,
    )
    if _num_workers > 0:
        _dl_kwargs["prefetch_factor"] = _prefetch
    train_loader = DataLoader(train_dataset, batch_size=CONFIG["BATCH_SIZE"], shuffle=True, **_dl_kwargs)
    val_loader = DataLoader(val_dataset, batch_size=CONFIG["BATCH_SIZE"], shuffle=False, **_dl_kwargs)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = _build_model(len(CONFIG["HISTORY_INPUT"]), seq_len, pred_len, CONFIG).to(device)

    print(
        f"  设备: {device}, 训练集: {n_train}, 验证集: {n_val}, 参数量: {sum(p.numel() for p in model.parameters()):,}"
    )

    low_thr = np.quantile(train_target_scaled, CONFIG["TAIL_LOW_Q"])
    high_thr = np.quantile(train_target_scaled, CONFIG["TAIL_HIGH_Q"])
    criterion = AnnualProtectedCappedLoss(
        low_thr=low_thr,
        high_thr=high_thr,
        alpha=CONFIG["TAIL_ALPHA"],
        diff_alpha=CONFIG["TAIL_DIFF_ALPHA"],
        huber_beta=CONFIG["HUBER_BETA"],
        mse_gamma=CONFIG["MSE_GAMMA"],
        protected_weight=1.5,
        editable_horizon=(9, 16),
    )

    optimizer = optim.AdamW(model.parameters(), lr=CONFIG["LR"], weight_decay=CONFIG["WEIGHT_DECAY"])
    scheduler = optim.lr_scheduler.CosineAnnealingLR(
        optimizer,
        T_max=CONFIG["EPOCHS"],
        eta_min=CONFIG["LR"] * 0.01,
    )

    best_val_mae = float("inf")
    best_state = None
    patience_counter = 0

    # === AMP 混合精度(依据 docs/项目提高速度.md)===
    _use_amp = use_cuda and bool(int(os.getenv("OPTIM_AMP", "1")))
    _amp_dtype = torch.bfloat16 if os.getenv("OPTIM_AMP_DTYPE", "bf16").lower() == "bf16" else torch.float16
    _scaler = torch.amp.GradScaler("cuda") if (_use_amp and _amp_dtype == torch.float16) else None
    _non_blocking = use_cuda and bool(int(os.getenv("OPTIM_NON_BLOCKING", "1")))

    for epoch in range(CONFIG["EPOCHS"]):
        model.train()
        epoch_loss = 0.0

        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(device, non_blocking=_non_blocking)
            batch_y = batch_y.to(device, non_blocking=_non_blocking)

            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", dtype=_amp_dtype, enabled=_use_amp):
                loss = criterion(model(batch_x), batch_y)
            if _scaler is not None:
                _scaler.scale(loss).backward()
                _scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                _scaler.step(optimizer)
                _scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
            epoch_loss += loss.item()

        scheduler.step()
        avg_train_loss = epoch_loss / max(1, len(train_loader))

        val_metrics = _validate(model, val_loader, scaler_y, device)
        val_mae = val_metrics.get("MAE", float("inf"))

        if val_mae < best_val_mae:
            best_val_mae = val_mae
            patience_counter = 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            patience_counter += 1

        if (epoch + 1) % 5 == 0:
            print(
                f"  Epoch [{epoch + 1:3d}/{CONFIG['EPOCHS']}]  Train Loss: {avg_train_loss:.6f}"
                f"  |  Val MAE: {val_metrics.get('MAE', 0):.2f}  RMSE: {val_metrics.get('RMSE', 0):.2f}"
                f"  R2: {val_metrics.get('R2', 0):.4f}  SMAPE: {val_metrics.get('SMAPE', 0):.4f}"
                f"  |  Best Val MAE: {best_val_mae:.2f}"
            )

        if patience_counter >= CONFIG["PATIENCE"]:
            print(f"  早停: 连续{CONFIG['PATIENCE']}轮验证集未改善")
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    model_save_path = os.path.join(
        root_dir,
        f"model_{CONFIG['INPUT_LEN_LIST'] + 1}天输出最后{pred_len}点.pth",
    )
    torch.save(model.state_dict(), model_save_path)
    final_metrics = _validate(model, val_loader, scaler_y, device)
    metrics_save_path = os.path.join(root_dir, "train_metrics.json")
    with open(metrics_save_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "best_val_mae": float(best_val_mae),
                "final_val_metrics": {k: float(v) for k, v in final_metrics.items()},
                "output": CONFIG["OUTPUT"],
                "run_id": CONFIG.get("RUN_ID", ""),
                "history_input": CONFIG["HISTORY_INPUT"],
                "future_input": CONFIG["FUTURE_INPUT"],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"  模型已保存: {model_save_path}")
    print("  最终验证集指标:")
    for k, v in final_metrics.items():
        print(f"    {k:6s}: {v:.6f}")


def inference(test_data, mod="all", asof_ts=None, external_da_pred_df=None):
    truth_df = test_data[["时刻", CONFIG["OUTPUT"]]].copy()
    test_data = _inject_predicted_da_for_rt(
        test_data,
        asof_ts=asof_ts,
        external_da_pred_df=external_da_pred_df,
    )
    test_data = apply_asof_cutoff_for_inference(test_data, asof_ts=asof_ts)
    test_data = recompute_target_dependent_selected_features(test_data, target_col=CONFIG["OUTPUT"])
    predictions = {}
    for period_name, period_data in _get_periods(test_data, mod):
        CONFIG["CURRENT_PERIOD_NAME"] = period_name
        print(f"\n[SpikeTimesNet] 推理 {period_name}...")
        predictions[period_name] = inference_single_period(period_name, period_data, truth_df=truth_df)

    merged = pd.concat(list(predictions.values()), ignore_index=False)
    return merged.sort_values("时刻").reset_index(drop=True)


def inference_single_period(period_name, test_data, truth_df=None):
    model_load_dir = os.path.join(CONFIG["SAVE_ROOT_DIR"], f"TS{CONFIG['TRAIN_STEPS']}_{period_name}")
    os.makedirs(CONFIG["PREDICT_RESULT_DIR"], exist_ok=True)

    if CONFIG["OUTPUT"] == "日前电价":
        test_data = enrich_period_local_features(test_data, target_col=CONFIG["OUTPUT"], pred_len=CONFIG["OUTPUT_LEN_LIST"])

    cols_for_model = list(dict.fromkeys(CONFIG["HISTORY_INPUT"] + CONFIG["FUTURE_INPUT"] + [CONFIG["OUTPUT"]]))
    df = test_data[cols_for_model].interpolate(method="linear", limit_direction="forward").copy()

    feature_cols = CONFIG["HISTORY_INPUT"] + CONFIG["FUTURE_INPUT"]
    for col in feature_cols:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.replace(" ", "").astype(float)

    input_features = df[feature_cols].values
    target_feature = df[[CONFIG["OUTPUT"]]].values

    with open(os.path.join(model_load_dir, "scalar_input.pkl"), "rb") as f:
        scaler_x = pickle.load(f)
    with open(os.path.join(model_load_dir, "scalar_output.pkl"), "rb") as f:
        scaler_y = pickle.load(f)

    test_data_scaled = scaler_x.transform(input_features)
    test_target_scaled = scaler_y.transform(target_feature)

    pred_len = CONFIG["OUTPUT_LEN_LIST"]
    seq_len = CONFIG["OUTPUT_LEN_LIST"] * CONFIG["INPUT_LEN_LIST"] + CONFIG["OUTPUT_LEN_LIST"]

    test_dataset = ElectricityDataset(
        test_data_scaled,
        test_target_scaled,
        seq_len=seq_len,
        pred_len=pred_len,
        step=pred_len,
    )
    test_loader = DataLoader(test_dataset, batch_size=1, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = _build_model(len(CONFIG["HISTORY_INPUT"]), seq_len, pred_len, CONFIG)
    model_path = os.path.join(
        model_load_dir,
        f"model_{CONFIG['INPUT_LEN_LIST'] + 1}天输出最后{pred_len}点.pth",
    )
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.to(device)
    model.eval()

    all_preds, all_trues = [], []
    with torch.no_grad():
        for batch_x, batch_y in test_loader:
            pred = model(batch_x.to(device)).float().cpu().numpy()
            all_preds.append(pred)
            all_trues.append(batch_y.numpy())

    pred_arr = np.concatenate(all_preds).reshape(-1, 1)
    true_arr = np.concatenate(all_trues).reshape(-1, 1)
    real_preds = scaler_y.inverse_transform(pred_arr).flatten()
    real_trues = scaler_y.inverse_transform(true_arr).flatten()

    pred_df = test_data[
        (test_data["时刻"] >= CONFIG["TEST_TOTAL_START_END_LIST"][0])
        & (test_data["时刻"] <= CONFIG["TEST_TOTAL_START_END_LIST"][1])
    ]

    n = min(len(pred_df), len(real_preds), len(real_trues))
    if truth_df is not None:
        truth_period = pred_df.iloc[:n][["时刻"]].merge(
            truth_df[["时刻", CONFIG["OUTPUT"]]],
            on="时刻",
            how="left",
        )
        result = truth_period.copy()
    else:
        result = pred_df.iloc[:n][["时刻", f"{CONFIG['OUTPUT']}"]].copy()
    result[f"预测{CONFIG['OUTPUT']}"] = real_preds[:n]
    return result


def calc_mae(y_true, y_pred):
    return np.mean(np.abs(y_true - y_pred))


def calc_mse(y_true, y_pred):
    return np.mean((y_true - y_pred) ** 2)


def calc_rmse(y_true, y_pred):
    return np.sqrt(calc_mse(y_true, y_pred))


def calc_r2(y_true, y_pred):
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    return 1 - ss_res / (ss_tot + 1e-8)


def calc_mape(y_true, y_pred, threshold=50.0):
    y_t = np.where(np.abs(y_true) < threshold, threshold, np.abs(y_true))
    return np.mean(np.abs(y_true - y_pred) / y_t)


def calc_smape(y_true, y_pred, threshold=50.0):
    y_t = np.where(y_true < threshold, threshold, y_true)
    y_p = np.where(y_pred < threshold, threshold, y_pred)
    denom = (np.abs(y_t) + np.abs(y_p)) / 2.0
    return np.mean(np.where(denom > 1e-8, np.abs(y_t - y_p) / denom, 0.0))


def calc_scr(y_true, y_pred):
    true_dir = np.sign(y_true[1:] - y_true[:-1])
    pred_dir = np.sign(y_pred[1:] - y_pred[:-1])
    return np.mean((true_dir == pred_dir).astype(float))


def evaluate(y_true, y_pred):
    return {
        "MAE": calc_mae(y_true, y_pred),
        "MSE": calc_mse(y_true, y_pred),
        "RMSE": calc_rmse(y_true, y_pred),
        "R2": calc_r2(y_true, y_pred),
        "MAPE": calc_mape(y_true, y_pred),
        "SMAPE": calc_smape(y_true, y_pred),
        "SCR": calc_scr(y_true, y_pred),
    }


def _update_config(target, start_end_list):
    CONFIG["OUTPUT"] = target
    CONFIG["TEST_TOTAL_START_END_LIST"] = start_end_list
    CONFIG["HISTORY_INPUT"], CONFIG["FUTURE_INPUT"] = _make_inputs(target)
    if target == "日前电价":
        da_input_variant = os.getenv("SPIKE_DA_INPUT_VARIANT", "base").strip().lower()
        if da_input_variant == "split":
            CONFIG["TAIL_ALPHA"] = 0.55
            CONFIG["TAIL_DIFF_ALPHA"] = 0.10
            CONFIG["MSE_GAMMA"] = 0.10
            CONFIG["DELTA_SCALE"] = 0.08
        else:
            CONFIG["TAIL_ALPHA"] = 0.85
            CONFIG["TAIL_DIFF_ALPHA"] = 0.18
            CONFIG["MSE_GAMMA"] = 0.28
            CONFIG["DELTA_SCALE"] = 0.06
    else:
        CONFIG["TAIL_ALPHA"] = 1.0
        CONFIG["TAIL_DIFF_ALPHA"] = 0.25
        CONFIG["MSE_GAMMA"] = 0.2
        CONFIG["DELTA_SCALE"] = 0.15
    base_model_root = PACKAGE_OUT_ROOT / "artifacts" / f"{target}_分段"
    base_pred_root = (
        PACKAGE_OUT_ROOT
        / f"{target}_分段"
        / f"{start_end_list[0][:10]}_{start_end_list[1][:10]}_预测结果"
    )
    rid = CONFIG.get("RUN_ID") or _new_run_id()
    CONFIG["RUN_ID"] = rid
    CONFIG["SAVE_ROOT_DIR"] = base_model_root / rid
    CONFIG["PREDICT_RESULT_DIR"] = base_pred_root / rid


def train_interface(target="实时电价", start_end_list=None, mod="all"):
    if start_end_list is None:
        start_end_list = ["2026-02-01 01:00:00", "2026-02-10 00:00:00"]

    CONFIG["RUN_ID"] = _new_run_id()
    _update_config(target, start_end_list)
    os.makedirs(CONFIG["SAVE_ROOT_DIR"], exist_ok=True)

    df_raw = pd.read_excel(RAW_DF_PATH)
    df_raw = process_features(df_raw)
    df_raw = feature_engineer_solar_terms(df_raw)
    df_raw = enrich_selected_features(df_raw, target_col=target)
    df_raw["时刻"] = pd.to_datetime(df_raw["时刻"])

    test_start = pd.Timestamp(start_end_list[0])
    train_data = _slice_recent_train_window(df_raw, test_start - pd.Timedelta(seconds=1), months=12)
    train(train_data, mod=mod)


def run(target="实时电价", start_end_list=None, mod="all", asof_ts=None, enforce_asof_cutoff=True):
    if start_end_list is None:
        start_end_list = ["2026-02-01 01:00:00", "2026-02-10 00:00:00"]

    CONFIG["RUN_ID"] = _new_run_id()
    _update_config(target, start_end_list)
    os.makedirs(CONFIG["SAVE_ROOT_DIR"], exist_ok=True)

    df_raw = pd.read_excel(RAW_DF_PATH)
    df_raw = process_features(df_raw)
    df_raw = feature_engineer_solar_terms(df_raw)
    df_raw = enrich_selected_features(df_raw, target_col=target)
    df_raw["时刻"] = pd.to_datetime(df_raw["时刻"])

    test_start = pd.Timestamp(start_end_list[0])
    train_data = _slice_recent_train_window(df_raw, test_start - pd.Timedelta(seconds=1), months=12)

    print("=" * 60)
    print(f"SpikeTimesNet 训练 | 目标: {target}")
    print("=" * 60)
    train(train_data, mod=mod)

    print("\n" + "=" * 60)
    print("SpikeTimesNet 测试集推理+评估")
    print("=" * 60)

    test_data_start = test_start - pd.Timedelta(days=CONFIG["INPUT_LEN_LIST"])
    test_data = df_raw[
        (df_raw["时刻"] >= test_data_start)
        & (df_raw["时刻"] <= pd.Timestamp(start_end_list[1]))
    ].copy()

    if enforce_asof_cutoff and asof_ts is None:
        asof_ts = test_start - pd.Timedelta(hours=10)
    result = inference(test_data, mod=mod, asof_ts=asof_ts, external_da_pred_df=None)

    if result is not None and len(result) > 0:
        y_true = result[target].values
        y_pred = result[f"预测{target}"].values
        metrics = evaluate(y_true, y_pred)

        print(f"\n测试集评估 ({len(y_true)} 条):")
        for k, v in metrics.items():
            print(f"  {k:6s}: {v:.6f}")

        if target == "实时电价" and "日前电价" in result.columns:
            profit = y_true - result["日前电价"].values
            print(f"  度电盈利均值: {profit.mean():.4f} 元/MWh")

        save_dir = CONFIG["PREDICT_RESULT_DIR"]
        result.to_csv(f"{save_dir}/预测结果.csv", index=False, encoding="utf-8-sig")
        print(f"结果已保存: {save_dir}/预测结果.csv")

    return result


def run_daily_asof_backtest(target="实时电价", start_end_list=None, mod="all", asof_hour=15, retrain_daily=False):
    """
    Daily walk-forward backtest aligned with EcoFormer process:
    - default: train once up to first-day asof, then predict day by day.
    - optional: retrain_daily=True to retrain before each prediction day.
    """
    if start_end_list is None:
        start_end_list = ["2026-02-01 01:00:00", "2026-02-10 00:00:00"]
    CONFIG["RUN_ID"] = _new_run_id()
    _update_config(target, start_end_list)
    os.makedirs(CONFIG["SAVE_ROOT_DIR"], exist_ok=True)

    df_raw = pd.read_excel(RAW_DF_PATH)
    df_raw = process_features(df_raw)
    df_raw = feature_engineer_solar_terms(df_raw)
    df_raw = enrich_selected_features(df_raw, target_col=target)
    df_raw["时刻"] = pd.to_datetime(df_raw["时刻"])

    test_start = pd.Timestamp(start_end_list[0])
    test_end = pd.Timestamp(start_end_list[1])

    print("\n" + "=" * 60)
    print("SpikeTimesNet 逐日15:00签发回测")
    print("=" * 60)

    pred_days = pd.date_range(test_start.normalize(), test_end.normalize(), freq="D")
    all_results = []

    if not retrain_daily:
        first_asof = pred_days.min() - pd.Timedelta(days=1) + pd.Timedelta(hours=asof_hour)
        train_data_once = _slice_recent_train_window(df_raw, first_asof, months=12)
        if len(train_data_once) == 0:
            print("无可用训练数据")
            return pd.DataFrame()
        print("\n" + "=" * 60)
        print(f"[Train Once] asof={first_asof} | train_rows={len(train_data_once)}")
        print("=" * 60)
        train(train_data_once, mod=mod)

    for pred_day in pred_days:
        day_start = pred_day + pd.Timedelta(hours=1)
        day_end = pred_day + pd.Timedelta(days=1)
        if day_start < test_start or day_end > test_end:
            continue

        asof_ts = pred_day - pd.Timedelta(days=1) + pd.Timedelta(hours=asof_hour)
        if retrain_daily:
            train_data = _slice_recent_train_window(df_raw, asof_ts, months=12)
            if len(train_data) == 0:
                continue
            print("\n" + "=" * 60)
            print(f"[Daily Train] asof={asof_ts} | train_rows={len(train_data)}")
            print("=" * 60)
            train(train_data, mod=mod)

        window_start = day_start - pd.Timedelta(days=CONFIG["INPUT_LEN_LIST"])
        test_data = df_raw[(df_raw["时刻"] >= window_start) & (df_raw["时刻"] <= day_end)].copy()

        CONFIG["TEST_TOTAL_START_END_LIST"] = [str(day_start), str(day_end)]
        print(f"\n[Daily] asof={asof_ts} -> predict {day_start} ~ {day_end}")
        one_day_res = inference(test_data, mod=mod, asof_ts=asof_ts, external_da_pred_df=None)
        one_day_res = one_day_res[(one_day_res["时刻"] >= day_start) & (one_day_res["时刻"] <= day_end)].copy()
        all_results.append(one_day_res)

    if len(all_results) == 0:
        print("无可用逐日结果")
        return pd.DataFrame()

    result = pd.concat(all_results, ignore_index=True)
    result = result.sort_values("时刻").drop_duplicates(subset=["时刻"], keep="last").reset_index(drop=True)

    y_true = result[target].values
    y_pred = result[f"预测{target}"].values
    metrics = evaluate(y_true, y_pred)
    print(f"\n逐日回测评估 ({len(y_true)} 条):")
    for k, v in metrics.items():
        print(f"  {k:6s}: {v:.6f}")

    save_dir = (
        PACKAGE_OUT_ROOT
        / f"{target}_分段"
        / f"{start_end_list[0][:10]}_{start_end_list[1][:10]}_逐日15点签发"
        / CONFIG["RUN_ID"]
    )
    os.makedirs(save_dir, exist_ok=True)
    result.to_csv(str(save_dir / "预测结果.csv"), index=False, encoding="utf-8-sig")
    print(f"结果已保存: {save_dir / '预测结果.csv'}")
    return result


def run_joint_da_rt_daily_backtest(start_end_list=None, mod="all", asof_hour=15):
    """
    Joint workflow:
    1) Run DA daily backtest first.
    2) Run RT daily backtest with DA predictions injected after asof.
    """
    if start_end_list is None:
        start_end_list = ["2026-02-01 01:00:00", "2026-02-10 00:00:00"]

    da_result = run_daily_asof_backtest(
        target="日前电价",
        start_end_list=start_end_list,
        mod=mod,
        asof_hour=asof_hour,
        retrain_daily=False,
    )
    if da_result is None or len(da_result) == 0:
        print("DA回测结果为空，无法执行RT联动")
        return pd.DataFrame()

    CONFIG["RUN_ID"] = _new_run_id()
    _update_config("实时电价", start_end_list)
    os.makedirs(CONFIG["SAVE_ROOT_DIR"], exist_ok=True)

    df_raw = pd.read_excel(RAW_DF_PATH)
    df_raw = process_features(df_raw)
    df_raw = feature_engineer_solar_terms(df_raw)
    df_raw = enrich_selected_features(df_raw, target_col="实时电价")
    df_raw["时刻"] = pd.to_datetime(df_raw["时刻"])

    test_start = pd.Timestamp(start_end_list[0])
    test_end = pd.Timestamp(start_end_list[1])
    pred_days = pd.date_range(test_start.normalize(), test_end.normalize(), freq="D")
    first_asof = pred_days.min() - pd.Timedelta(days=1) + pd.Timedelta(hours=asof_hour)

    train_data_once = _slice_recent_train_window(df_raw, first_asof, months=12)
    print("\n" + "=" * 60)
    print(f"[Spike Joint RT Train Once] asof={first_asof} | train_rows={len(train_data_once)}")
    print("=" * 60)
    train(train_data_once, mod=mod)

    all_rt_results = []
    for pred_day in pred_days:
        day_start = pred_day + pd.Timedelta(hours=1)
        day_end = pred_day + pd.Timedelta(days=1)
        if day_start < test_start or day_end > test_end:
            continue

        asof_ts = pred_day - pd.Timedelta(days=1) + pd.Timedelta(hours=asof_hour)
        window_start = day_start - pd.Timedelta(days=CONFIG["INPUT_LEN_LIST"])
        test_data = df_raw[(df_raw["时刻"] >= window_start) & (df_raw["时刻"] <= day_end)].copy()

        da_day = da_result[(da_result["时刻"] >= day_start) & (da_result["时刻"] <= day_end)].copy()
        if "预测日前电价" not in da_day.columns:
            raise ValueError("DA结果缺少列: 预测日前电价")
        external_da_pred_df = da_day[["时刻", "预测日前电价"]].rename(columns={"预测日前电价": "pred_da"})

        CONFIG["TEST_TOTAL_START_END_LIST"] = [str(day_start), str(day_end)]
        print(f"\n[Spike Joint Daily] asof={asof_ts} -> RT predict {day_start} ~ {day_end}")
        one_day_rt = inference(
            test_data,
            mod=mod,
            asof_ts=asof_ts,
            external_da_pred_df=external_da_pred_df,
        )
        one_day_rt = one_day_rt[(one_day_rt["时刻"] >= day_start) & (one_day_rt["时刻"] <= day_end)].copy()
        all_rt_results.append(one_day_rt)

    if len(all_rt_results) == 0:
        print("无可用RT联动结果")
        return pd.DataFrame()

    rt_result = pd.concat(all_rt_results, ignore_index=True)
    rt_result = rt_result.sort_values("时刻").drop_duplicates(subset=["时刻"], keep="last").reset_index(drop=True)

    y_true = rt_result["实时电价"].values
    y_pred = rt_result["预测实时电价"].values
    metrics = evaluate(y_true, y_pred)
    print(f"\nRT联动回测评估 ({len(y_true)} 条):")
    for k, v in metrics.items():
        print(f"  {k:6s}: {v:.6f}")

    save_dir = (
        PACKAGE_OUT_ROOT
        / "joint_da_rt"
        / f"{start_end_list[0][:10]}_{start_end_list[1][:10]}_逐日15点签发"
        / CONFIG["RUN_ID"]
    )
    os.makedirs(save_dir, exist_ok=True)
    rt_result.to_csv(str(save_dir / "预测结果_RT_DA注入.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame([metrics]).to_csv(str(save_dir / "评估_RT_DA注入.csv"), index=False, encoding="utf-8-sig")
    print(f"联动结果已保存: {save_dir / '预测结果_RT_DA注入.csv'}")
    return rt_result







