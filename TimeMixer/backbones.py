from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class MovingAvg(nn.Module):
    def __init__(self, kernel_size: int = 25):
        super().__init__()
        self.avg = nn.AvgPool1d(
            kernel_size=kernel_size,
            stride=1,
            padding=(kernel_size - 1) // 2,
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        x_t = x.transpose(1, 2)
        trend = self.avg(x_t)
        if trend.size(-1) != x_t.size(-1):
            trend = trend[..., : x_t.size(-1)]
        trend = trend.transpose(1, 2)
        seasonal = x - trend
        return seasonal, trend


class PastDecomposableMixing(nn.Module):
    def __init__(self, hidden_dim: int, scales: int = 3, dropout: float = 0.1):
        super().__init__()
        self.decomp = MovingAvg(kernel_size=25)
        self.season_mlps = nn.ModuleList(
            [
                nn.Sequential(
                    nn.Linear(hidden_dim, hidden_dim),
                    nn.GELU(),
                    nn.Dropout(dropout),
                    nn.Linear(hidden_dim, hidden_dim),
                )
                for _ in range(scales)
            ]
        )
        self.trend_mlps = nn.ModuleList(
            [
                nn.Sequential(
                    nn.Linear(hidden_dim, hidden_dim),
                    nn.GELU(),
                    nn.Dropout(dropout),
                    nn.Linear(hidden_dim, hidden_dim),
                )
                for _ in range(scales)
            ]
        )
        self.norms = nn.ModuleList([nn.LayerNorm(hidden_dim) for _ in range(scales)])

    def forward(self, xs: list[torch.Tensor]) -> list[torch.Tensor]:
        outs = []
        prev_s = None
        prev_t = None
        for i, x in enumerate(xs):
            s, t = self.decomp(x)
            if prev_s is not None:
                s = s + F.interpolate(
                    prev_s.transpose(1, 2),
                    size=s.size(1),
                    mode="linear",
                    align_corners=False,
                ).transpose(1, 2)
                t = t + F.interpolate(
                    prev_t.transpose(1, 2),
                    size=t.size(1),
                    mode="linear",
                    align_corners=False,
                ).transpose(1, 2)
            y = self.season_mlps[i](s) + self.trend_mlps[i](t)
            outs.append(self.norms[i](x + y))
            prev_s, prev_t = s, t
        return outs


class TimeMixerBackbone(nn.Module):
    def __init__(
        self,
        past_dim: int,
        future_dim: int,
        pred_len: int = 24,
        hidden_dim: int = 64,
        n_blocks: int = 2,
        scales: int = 3,
        dropout: float = 0.1,
        segment_head_mode: str = "none",
    ):
        super().__init__()
        self.scales = scales
        self.segment_head_mode = segment_head_mode
        self.past_proj = nn.Linear(past_dim, hidden_dim)
        self.future_proj = nn.Linear(future_dim, hidden_dim)
        self.blocks = nn.ModuleList(
            [
                PastDecomposableMixing(hidden_dim, scales=scales, dropout=dropout)
                for _ in range(n_blocks)
            ]
        )
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
        if self.segment_head_mode == "future_residual":
            self.future_step_head = nn.Sequential(
                nn.LayerNorm(hidden_dim),
                nn.Linear(hidden_dim, hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, 1),
            )
            self.future_residual_gate = nn.Sequential(
                nn.Linear(hidden_dim * (scales + 1), hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, pred_len),
                nn.Sigmoid(),
            )

    def make_scales(self, x: torch.Tensor) -> list[torch.Tensor]:
        xs = [x]
        cur = x
        for _ in range(1, self.scales):
            cur = F.avg_pool1d(
                cur.transpose(1, 2), kernel_size=2, stride=2, ceil_mode=True
            ).transpose(1, 2)
            xs.append(cur)
        return xs

    def forward(self, past_x: torch.Tensor, future_x: torch.Tensor) -> torch.Tensor:
        x = self.past_proj(past_x)
        xs = self.make_scales(x)
        for block in self.blocks:
            xs = block(xs)
        pooled = [s.mean(dim=1) for s in xs]
        future = self.future_proj(future_x)
        future = future + self.future_mixer(future)
        z = torch.cat(pooled + [future.mean(dim=1)], dim=-1)
        out = self.head(z)
        if self.segment_head_mode == "future_residual":
            future_residual = self.future_step_head(future).squeeze(-1)
            gate = self.future_residual_gate(z)
            out = out + gate * future_residual
        return out


class InceptionBlock1D(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        kernels = [1, 3, 5]
        self.branches = nn.ModuleList(
            [
                nn.Conv1d(
                    channels,
                    channels,
                    kernel_size=k,
                    padding=k // 2,
                )
                for k in kernels
            ]
        )
        self.mix = nn.Sequential(
            nn.Conv1d(channels * len(kernels), channels, kernel_size=1),
            nn.GELU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        ys = [branch(x) for branch in self.branches]
        return self.mix(torch.cat(ys, dim=1))


class TimesNetBackbone(nn.Module):
    """A lightweight TimesNet-style backbone that preserves the project I/O contract."""

    def __init__(
        self,
        past_dim: int,
        future_dim: int,
        pred_len: int = 24,
        hidden_dim: int = 64,
        n_blocks: int = 3,
        dropout: float = 0.1,
        segment_head_mode: str = "none",
    ):
        super().__init__()
        self.segment_head_mode = segment_head_mode
        self.past_proj = nn.Linear(past_dim, hidden_dim)
        self.future_proj = nn.Linear(future_dim, hidden_dim)
        self.time_blocks = nn.ModuleList(
            [
                nn.Sequential(
                    nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=1),
                    nn.GELU(),
                    InceptionBlock1D(hidden_dim),
                    nn.Dropout(dropout),
                )
                for _ in range(n_blocks)
            ]
        )
        self.future_mixer = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.head = nn.Sequential(
            nn.LayerNorm(hidden_dim * 2),
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, pred_len),
        )
        if self.segment_head_mode == "future_residual":
            self.future_step_head = nn.Sequential(
                nn.LayerNorm(hidden_dim),
                nn.Linear(hidden_dim, hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, 1),
            )
            self.future_residual_gate = nn.Sequential(
                nn.LayerNorm(hidden_dim * 2),
                nn.Linear(hidden_dim * 2, hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, pred_len),
                nn.Sigmoid(),
            )

    def forward(self, past_x: torch.Tensor, future_x: torch.Tensor) -> torch.Tensor:
        x = self.past_proj(past_x).transpose(1, 2)
        for block in self.time_blocks:
            x = x + block(x)
        past_pool = x.mean(dim=-1)
        future = self.future_proj(future_x)
        future = future + self.future_mixer(future)
        future_pool = future.mean(dim=1)
        z = torch.cat([past_pool, future_pool], dim=-1)
        out = self.head(z)
        if self.segment_head_mode == "future_residual":
            future_residual = self.future_step_head(future).squeeze(-1)
            gate = self.future_residual_gate(z)
            out = out + gate * future_residual
        return out


def build_backbone(
    backbone_name: str,
    past_dim: int,
    future_dim: int,
    pred_len: int,
    hidden_dim: int,
    blocks: int,
    scales: int,
    dropout: float,
    segment_head_mode: str = "none",
) -> nn.Module:
    name = backbone_name.lower()
    if name == "timemixer":
        return TimeMixerBackbone(
            past_dim=past_dim,
            future_dim=future_dim,
            pred_len=pred_len,
            hidden_dim=hidden_dim,
            n_blocks=blocks,
            scales=scales,
            dropout=dropout,
            segment_head_mode=segment_head_mode,
        )
    if name == "timesnet":
        return TimesNetBackbone(
            past_dim=past_dim,
            future_dim=future_dim,
            pred_len=pred_len,
            hidden_dim=hidden_dim,
            n_blocks=blocks,
            dropout=dropout,
            segment_head_mode=segment_head_mode,
        )
    raise ValueError(f"Unsupported backbone: {backbone_name}")
