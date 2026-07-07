"""
config.py — P3 shadow-only correction 的可配置旋钮。

所有旋钮集中在此，便于 ablation（阶段 E）通过覆盖配置比较变体。
默认值来自阶段 A 经验提炼（2.0_exp extreme/ 与 2.5 保守护栏）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json


@dataclass
class P3Config:
    # ---- 模块开关 ----
    negative_classifier_enabled: bool = True
    spike_classifier_enabled: bool = True
    residual_corrector_enabled: bool = True

    # ---- 负价修正 ----
    NEG_THRESH: float = 0.5            # 负价触发概率阈值
    NEG_LABEL: float = -50.0          # 负价标签阈值（actual <= NEG_LABEL）
    NEG_ACT_PRED_CAP: float = 100.0   # 保守护栏：仅当 original <= 此值才推向 -80
    NEG_FLOOR_TARGET: float = -80.0   # 山东负价地板

    # ---- 尖峰修正 ----
    SPK_THRESH: float = 0.60          # 尖峰触发概率阈值（保守，避免过度触发）
    SPK_LABEL: float = 500.0          # 尖峰标签阈值（actual > SPK_LABEL）
    SPK_MIN_ORIGINAL: float = 250.0   # 仅当 original 已中高位才上行 lift（防误伤正常时段）
    SPK_LIFT_RATIO: float = 0.35      # 上行 lift 比例
    SPK_LIFT_ABS: float = 350.0       # 上行 lift 绝对值上限
    SPK_9_16_BOOST: float = 1.15      # 9_16 时段 boost

    # ---- 残差校正 ----
    RESIDUAL_ALPHA: float = 0.5       # 残差校准强度
    RESIDUAL_ERROR_GATE: bool = True  # 误差门控（样本不足/偏差不显著则不校）
    RESIDUAL_MIN_SAMPLES: int = 5     # 分段最小样本

    # ---- 护栏 ----
    CAP_ABS: float = 350.0            # 总修正绝对值上限
    CAP_RATIO: float = 0.35           # 总修正相对 original 比例上限
    PRICE_FLOOR: float = -100.0       # 价格生理下界
    PRICE_CEIL: float = 1500.0        # 价格生理上界
    ROLLBACK_MIN_CONF: float = 0.30   # 低于此置信 → 回滚

    # ---- cutoff / 评估 ----
    CUTOFF: str = "D14"
    RUN_ID: str = "p3_rt_20260125_20260225_v1"

    def to_dict(self):
        return asdict(self)

    def to_json(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)


def default_config() -> P3Config:
    return P3Config()


def optimized_config() -> P3Config:
    """阶段 D/E 调优后的候选配置（通过全部 16 条标准，见 p3 最终报告）。

    调优依据（ablation + 阈值收扫）：
    - 负价修正为绝对主力（negΔsMAPE≈-24）；但默认宽松配置假阳性伤正常时段(+3.46)，
      故收紧 NEG_THRESH=0.80 且 NEG_ACT_PRED_CAP=50（仅当 fused 已很低才推 -80），
      把正常时段损伤压到 +0.33（不明显），假阳性降至 5。
    - 尖峰修正保留（spikeΔ≈-3.7，正常仅 +0.17，安全）。
    - 残差校正经 ablation 证实无收益（overallΔ=+0.29）且引入 279 次多余修正 → 丢弃。
    - cap / rollback 均开启（安全网）。
    """
    c = P3Config()
    c.negative_classifier_enabled = True
    c.spike_classifier_enabled = True
    c.residual_corrector_enabled = False
    c.NEG_THRESH = 0.80
    c.NEG_ACT_PRED_CAP = 50.0
    c.SPK_THRESH = 0.60
    c.SPK_MIN_ORIGINAL = 250.0
    c.CAP_ABS = 350.0
    c.ROLLBACK_MIN_CONF = 0.30
    c.RUN_ID = "p3_rt_20260125_20260225_v1_cand"
    return c
