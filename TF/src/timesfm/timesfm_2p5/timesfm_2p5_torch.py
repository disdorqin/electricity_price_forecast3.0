# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""TimesFM models.

本模块实现了TimesFM 2.5模型（200M参数版本）的PyTorch版本。
TimesFM是Google开发的大规模时间序列预测模型，采用Transformer架构。

在项目中的角色：
- 本项目电价预测系统的核心模型之一
- 通过runners/run_timesfm.py调用
- 支持日前电价和实时电价预测
- 支持单日预测和区间预测两种模式

模型特点：
- 基于Transformer的时序预测架构
- 使用分块（patching）技术处理长序列
- 支持分位数预测（quantile forecasting）
- 支持自回归解码生成多步预测
"""

import dataclasses
import logging
import math
import os
from pathlib import Path
from typing import Dict, Optional, Sequence, Union

import numpy as np
import torch
from huggingface_hub import ModelHubMixin, hf_hub_download
from safetensors.torch import load_file, save_file
from torch import nn

from .. import configs
from ..torch import dense, transformer, util
from . import timesfm_2p5_base

revin = util.revin


class TimesFM_2p5_200M_torch_module(nn.Module):
  """TimesFM 2.5 with 200M parameters.
  
  TimesFM 2.5模型的PyTorch模块实现，包含200M参数。
  
  架构组件：
  1. Tokenizer（分词器）：将输入时间序列转换为嵌入向量
  2. Stacked Transformers（堆叠Transformer）：20层Transformer编码器
  3. Output Projection（输出投影）：将隐藏状态映射到预测值
  
  关键参数说明：
  - p (input_patch_len): 输入分块长度 = 32
  - o (output_patch_len): 输出分块长度 = 128
  - os (output_quantile_len): 分位数输出长度 = 1024
  - m: 输出分块数 = o/p = 4
  - x: Transformer层数 = 20
  - h: 注意力头数 = 16
  - md: 模型维度 = 1280
  - hd: 每个头的维度 = md/h = 80
  - q: 分位数数量 + 1 = 10
  - aridx: 自回归解码使用的分位数索引 = 5（中位数）
  
  Attributes
  ----------
  config : TimesFM_2p5_200M_Definition
      模型配置定义
  device : torch.device
      模型运行的设备（CUDA或CPU）
  device_count : int
      可用的GPU数量
  """

  config = timesfm_2p5_base.TimesFM_2p5_200M_Definition()

  def __init__(self):
    """初始化TimesFM 2.5模型模块。"""
    super().__init__()

    # ==================== 命名常量定义 ====================
    # 这些常量来自模型配置，定义了模型的关键超参数
    self.p = self.config.input_patch_len  # 输入分块长度：32个时间步
    self.o = self.config.output_patch_len  # 输出分块长度：128个时间步
    self.os = self.config.output_quantile_len  # 分位数输出长度：1024
    self.m = self.o // self.p  # 每个输出分块包含的输入分块数：4
    self.x = self.config.stacked_transformers.num_layers  # Transformer层数：20
    self.h = self.config.stacked_transformers.transformer.num_heads  # 注意力头数：16
    self.md = self.config.stacked_transformers.transformer.model_dims  # 模型维度：1280
    self.hd = self.md // self.h  # 每个注意力头的维度：80
    self.q = len(self.config.quantiles) + 1  # 分位数数量 + 1：10
    self.aridx = self.config.decode_index  # 自回归解码使用的分位数索引：5（中位数）

    # ==================== 网络层定义 ====================
    # Tokenizer：将输入时间序列转换为模型可处理的嵌入向量
    # 输入：(batch, seq_len)，输出：(batch, num_patches, model_dims)
    self.tokenizer = dense.ResidualBlock(self.config.tokenizer)
    
    # 堆叠的Transformer层：20层Transformer编码器
    # 每层包含多头自注意力和前馈网络
    self.stacked_xf = nn.ModuleList(
      [
        transformer.Transformer(self.config.stacked_transformers.transformer)
        for _ in range(self.x)
      ]
    )
    
    # 点预测输出投影：将Transformer输出映射到点预测值
    self.output_projection_point = dense.ResidualBlock(
      self.config.output_projection_point
    )
    
    # 分位数预测输出投影：将Transformer输出映射到分位数预测值
    self.output_projection_quantiles = dense.ResidualBlock(
      self.config.output_projection_quantiles
    )

    # ==================== 设备配置 ====================
    # 自动检测并配置计算设备（GPU优先）
    if torch.cuda.is_available():
      self.device = torch.device("cuda:0")  # 使用第一个GPU
      self.device_count = torch.cuda.device_count()  # 获取可用GPU数量
    else:
      self.device = torch.device("cpu")  # 回退到CPU
      self.device_count = 1

  def load_checkpoint(self, path: str, **kwargs):
    """Loads a PyTorch TimesFM model from a checkpoint.
    
    从检查点文件加载模型权重。
    
    Parameters
    ----------
    path : str
        检查点文件路径（safetensors格式）
    **kwargs : dict
        额外参数，支持torch_compile选项控制是否编译模型
    
    加载流程：
    1. 使用safetensors加载权重
    2. 将权重加载到模型
    3. 将模型移动到指定设备
    4. 可选：使用torch.compile优化模型
    5. 设置模型为评估模式
    """
    # 从safetensors文件加载张量
    tensors = load_file(path)
    # 将权重加载到模型（严格模式：所有权重必须匹配）
    self.load_state_dict(tensors, strict=True)
    # 将模型移动到计算设备（GPU或CPU）
    self.to(self.device)
    
    # 检查是否需要编译模型（用于加速推理）
    torch_compile = True
    if "torch_compile" in kwargs:
      torch_compile = kwargs["torch_compile"]
    if torch_compile:
      print("Compiling model...")
      self = torch.compile(self)

    # 设置模型为评估模式（关闭Dropout等训练专用层）
    self.eval()

  def forward(
    self,
    inputs: torch.Tensor,
    masks: torch.Tensor,
    decode_caches: list[util.DecodeCache] | None = None,
  ):
    """前向传播函数。
    
    执行模型的前向传播，将输入时间序列转换为预测输出。
    
    Parameters
    ----------
    inputs : torch.Tensor
        输入时间序列，形状：(batch_size, num_patches, patch_len)
    masks : torch.Tensor
        掩码张量，标记缺失值位置，形状：(batch_size, num_patches, patch_len)
    decode_caches : list[DecodeCache] | None
        解码缓存，用于自回归生成时存储中间状态
    
    Returns
    -------
    tuple
        - input_embeddings: 输入嵌入
        - output_embeddings: Transformer输出嵌入
        - output_ts: 点预测输出
        - output_quantile_spread: 分位数预测输出
    new_decode_caches : list[DecodeCache]
        更新后的解码缓存
    """
    # 将输入和掩码拼接在一起作为tokenizer的输入
    # 形状：(batch, num_patches, patch_len * 2)
    tokenizer_inputs = torch.cat([inputs, masks.to(inputs.dtype)], dim=-1)
    # 通过tokenizer获取输入嵌入
    input_embeddings = self.tokenizer(tokenizer_inputs)

    # 如果没有提供解码缓存，初始化为None列表
    if decode_caches is None:
      decode_caches = [None] * self.x

    # 通过堆叠的Transformer层
    output_embeddings = input_embeddings
    new_decode_caches = []
    for i, layer in enumerate(self.stacked_xf):
      # 每层Transformer处理并更新缓存
      output_embeddings, new_cache = layer(
        output_embeddings, masks[..., -1], decode_caches[i]
      )
      new_decode_caches.append(new_cache)
    
    # 输出投影：将嵌入映射到预测值
    output_ts = self.output_projection_point(output_embeddings)
    output_quantile_spread = self.output_projection_quantiles(output_embeddings)

    return (
      input_embeddings,
      output_embeddings,
      output_ts,
      output_quantile_spread,
    ), new_decode_caches

  def decode(self, horizon: int, inputs, masks):
    """Decodes the time series.
    
    解码时间序列，生成多步预测。
    
    解码流程：
    1. Prefill阶段：处理输入上下文，初始化运行统计量
    2. 自回归解码：逐步生成未来预测值
    
    Parameters
    ----------
    horizon : int
        预测步数（预测多少个未来时间步）
    inputs : torch.Tensor
        输入时间序列，形状：(batch_size, context_len)
    masks : torch.Tensor
        输入掩码，形状：(batch_size, context_len)
    
    Returns
    -------
    tuple
        - renormed_outputs: 预填充阶段的预测输出
        - renormed_quantile_spread: 分位数扩散预测
        - ar_renormed_outputs: 自回归阶段的预测输出
    """

    with torch.no_grad():  # 禁用梯度计算，节省内存
      # ==================== 初始化变量 ====================
      batch_size, context = inputs.shape[0], inputs.shape[1]
      # 计算需要的解码步数
      num_decode_steps = (horizon - 1) // self.o
      # 输入分块数
      num_input_patches = context // self.p
      # 解码缓存大小
      decode_cache_size = num_input_patches + num_decode_steps * self.m

      # ==================== Prefill阶段 ====================
      # 将输入reshape为分块形式
      patched_inputs = torch.reshape(inputs, (batch_size, -1, self.p))
      patched_masks = torch.reshape(masks, (batch_size, -1, self.p))

      # 初始化运行统计量（用于RevIN归一化）
      n = torch.zeros(batch_size, device=inputs.device)  # 样本数
      mu = torch.zeros(batch_size, device=inputs.device)  # 均值
      sigma = torch.zeros(batch_size, device=inputs.device)  # 标准差
      patch_mu = []
      patch_sigma = []
      # 逐分块更新统计量
      for i in range(num_input_patches):
        (n, mu, sigma), _ = util.update_running_stats(
          n, mu, sigma, patched_inputs[:, i], patched_masks[:, i]
        )
        patch_mu.append(mu)
        patch_sigma.append(sigma)
      last_n, last_mu, last_sigma = n, mu, sigma
      context_mu = torch.stack(patch_mu, dim=1)
      context_sigma = torch.stack(patch_sigma, dim=1)

      # 初始化解码缓存
      decode_caches = [
        util.DecodeCache(
          next_index=torch.zeros(batch_size, dtype=torch.int32, device=inputs.device),
          num_masked=torch.zeros(batch_size, dtype=torch.int32, device=inputs.device),
          key=torch.zeros(
            batch_size,
            decode_cache_size,
            self.h,
            self.hd,
            device=inputs.device,
          ),
          value=torch.zeros(
            batch_size,
            decode_cache_size,
            self.h,
            self.hd,
            device=inputs.device,
          ),
        )
        for _ in range(self.x)
      ]

      # RevIN归一化输入
      normed_inputs = revin(patched_inputs, context_mu, context_sigma, reverse=False)
      normed_inputs = torch.where(patched_masks, 0.0, normed_inputs)
      # 前向传播获取预填充输出
      (_, _, normed_outputs, normed_quantile_spread), decode_caches = self(
        normed_inputs, patched_masks, decode_caches
      )
      # RevIN反归一化输出
      renormed_outputs = torch.reshape(
        revin(normed_outputs, context_mu, context_sigma, reverse=True),
        (batch_size, -1, self.o, self.q),
      )
      renormed_quantile_spread = torch.reshape(
        revin(normed_quantile_spread, context_mu, context_sigma, reverse=True),
        (batch_size, -1, self.os, self.q),
      )[:, -1, ...]

      # ==================== 自回归解码阶段 ====================
      ar_outputs = []
      # 获取最后一个分块的预测作为下一个输入
      last_renormed_output = renormed_outputs[:, -1, :, self.aridx]

      # 逐步生成预测
      for _ in range(num_decode_steps):
        # 将输出reshape为新的输入分块
        new_patched_input = torch.reshape(
          last_renormed_output, (batch_size, self.m, self.p)
        )
        new_mask = torch.zeros_like(new_patched_input, dtype=torch.bool)

        # 更新运行统计量
        n, mu, sigma = last_n, last_mu, last_sigma
        new_mus, new_sigmas = [], []
        for i in range(self.m):
          (n, mu, sigma), _ = util.update_running_stats(
            n, mu, sigma, new_patched_input[:, i], new_mask[:, i]
          )
          new_mus.append(mu)
          new_sigmas.append(sigma)
        last_n, last_mu, last_sigma = n, mu, sigma
        new_mu = torch.stack(new_mus, dim=1)
        new_sigma = torch.stack(new_sigmas, dim=1)

        # 归一化并前向传播
        new_normed_input = revin(new_patched_input, new_mu, new_sigma, reverse=False)
        (_, _, new_normed_output, _), decode_caches = self(
          new_normed_input, new_mask, decode_caches
        )

        # 反归一化并保存输出
        new_renormed_output = torch.reshape(
          revin(new_normed_output, new_mu, new_sigma, reverse=True),
          (batch_size, self.m, self.o, self.q),
        )
        ar_outputs.append(new_renormed_output[:, -1, ...])
        last_renormed_output = new_renormed_output[:, -1, :, self.aridx]

      # 合并自回归输出
      if num_decode_steps > 0:
        ar_renormed_outputs = torch.stack(ar_outputs, dim=1)
      else:
        ar_renormed_outputs = None

    return renormed_outputs, renormed_quantile_spread, ar_renormed_outputs

  def forecast_naive(
    self, horizon: int, inputs: Sequence[np.ndarray]
  ) -> list[np.ndarray]:
    """Forecasts the time series.
    
    简单预测接口，用于调试目的。不使用高级预测标志，预测质量可能次优。
    
    在项目中的使用：
    - 主要用于模型测试和调试
    - 生产环境使用更复杂的compile方法
    
    Parameters
    ----------
    horizon : int
        预测步数
    inputs : Sequence[np.ndarray]
        输入时间序列列表，每个元素是一个numpy数组
    
    Returns
    -------
    list[np.ndarray]
        预测结果列表，每个元素对应一个输入序列的预测
    """
    outputs = []
    for each_input in inputs:
      # 转换为PyTorch张量
      input_t = torch.tensor(each_input, dtype=torch.float32)
      mask = torch.zeros_like(input_t, dtype=torch.bool)
      # 计算需要的前置填充长度（使输入长度能被patch_len整除）
      len_front_mask = self.p - (len(each_input) % self.p)
      if len_front_mask < self.p:
        # 在序列前填充零
        input_t = torch.cat(
          [torch.zeros(len_front_mask, dtype=torch.float32), input_t], dim=0
        )
        mask = torch.cat([torch.ones(len_front_mask, dtype=torch.bool), mask], dim=0)
      # 添加batch维度
      input_t = input_t[None, ...]
      mask = mask[None, ...]
      # 解码生成预测
      t_pf, _, t_ar = self.decode(horizon, input_t, mask)
      # 合并预填充和自回归输出
      to_concat = [t_pf[:, -1, ...]]
      if t_ar is not None:
        to_concat.append(t_ar.reshape(1, -1, self.q))
      torch_forecast = torch.cat(to_concat, dim=1)[..., :horizon]
      torch_forecast = torch_forecast.squeeze(0)
      # 转换为numpy并添加到输出列表
      outputs.append(torch_forecast.detach().cpu().numpy())
    return outputs


class TimesFM_2p5_200M_torch(timesfm_2p5_base.TimesFM_2p5, ModelHubMixin):
  """PyTorch implementation of TimesFM 2.5 with 200M parameters.
  
  TimesFM 2.5模型的PyTorch实现包装类，继承自基础TimesFM类和HuggingFace ModelHubMixin。
  
  在项目中的角色：
  - 本项目使用的核心预测模型之一
  - 通过HuggingFace Hub加载预训练权重
  - 支持模型保存和加载
  - 提供编译优化后的快速推理接口
  
  Attributes
  ----------
  model : TimesFM_2p5_200M_torch_module
      实际的PyTorch模型模块
  forecast_config : configs.ForecastConfig
      预测配置
  global_batch_size : int
      全局批次大小
  compiled_decode : callable
      编译后的解码函数（用于加速）
  """

  model: nn.Module = TimesFM_2p5_200M_torch_module()

  @classmethod
  def _from_pretrained(
    cls,
    *,
    model_id: str = "google/timesfm-2.5-200m-pytorch",
    revision: Optional[str],
    cache_dir: Optional[Union[str, Path]],
    force_download: bool = True,
    proxies: Optional[Dict] = None,
    resume_download: Optional[bool] = None,
    local_files_only: bool,
    token: Optional[Union[str, bool]],
    **model_kwargs,
  ):
    """
    Loads a PyTorch safetensors TimesFM model from a local path or the Hugging
    Face Hub. This method is the backend for the `from_pretrained` class
    method provided by `ModelHubMixin`.
    
    从HuggingFace Hub或本地路径加载预训练模型。
    
    在项目中的使用：
    - 项目启动时加载TimesFM模型
    - 首次运行时会从HuggingFace下载模型权重
    - 支持本地缓存，避免重复下载
    
    Parameters
    ----------
    model_id : str
        HuggingFace模型ID或本地路径
    revision : Optional[str]
        模型版本
    cache_dir : Optional[Union[str, Path]]
        缓存目录
    force_download : bool
        是否强制重新下载
    proxies : Optional[Dict]
        代理设置
    resume_download : Optional[bool]
        是否断点续传
    local_files_only : bool
        是否仅使用本地文件
    token : Optional[Union[str, bool]]
        HuggingFace访问令牌
    **model_kwargs : dict
        额外模型参数
    
    Returns
    -------
    TimesFM_2p5_200M_torch
        加载好的模型实例
    """
    # 创建模型实例
    instance = cls(**model_kwargs)
    # 下载配置文件用于HF跟踪
    _ = hf_hub_download(
      repo_id="google/timesfm-2.5-200m-pytorch",
      filename="config.json",
      force_download=True,
    )
    print("Downloaded.")

    # 确定模型权重路径
    model_file_path = ""
    if os.path.isdir(model_id):
      # 从本地目录加载
      logging.info("Loading checkpoint from local directory: %s", model_id)
      model_file_path = os.path.join(model_id, "model.safetensors")
      if not os.path.exists(model_file_path):
        raise FileNotFoundError(f"model.safetensors not found in directory {model_id}")
    else:
      # 从HuggingFace Hub下载
      logging.info("Downloading checkpoint from Hugging Face repo %s", model_id)
      model_file_path = hf_hub_download(
        repo_id=model_id,
        filename="model.safetensors",
        revision=revision,
        cache_dir=cache_dir,
        force_download=force_download,
        proxies=proxies,
        resume_download=resume_download,
        token=token,
        local_files_only=local_files_only,
      )

    logging.info("Loading checkpoint from: %s", model_file_path)
    # 加载权重到模型
    instance.model.load_checkpoint(model_file_path, **model_kwargs)
    return instance

  def _save_pretrained(self, save_directory: Union[str, Path]):
    """
    Saves the model's state dictionary to a safetensors file. This method
    is called by the `save_pretrained` method from `ModelHubMixin`.
    
    将模型保存到safetensors文件。
    
    Parameters
    ----------
    save_directory : Union[str, Path]
        保存目录
    """
    if not os.path.exists(save_directory):
      os.makedirs(save_directory)

    weights_path = os.path.join(save_directory, "model.safetensors")
    save_file(self.model.state_dict(), weights_path)

  def compile(self, forecast_config: configs.ForecastConfig, **kwargs) -> None:
    """Attempts to compile the model for fast decoding.
    
    编译模型以加速解码推理。
    
    在项目中的使用：
    - 模型加载后调用，优化推理性能
    - 配置各种预测选项（归一化、分位数、回测等）
    
    支持的预测标志：
    - normalize_inputs: 是否对输入进行RevIN归一化
    - infer_is_positive: 推断输入是否为正值
    - use_continuous_quantile_head: 使用连续分位数头
    - return_backcast: 返回回测值
    - fix_quantile_crossing: 修复分位数交叉问题
    - force_flip_invariance: 强制翻转不变性
    
    Parameters
    ----------
    forecast_config : configs.ForecastConfig
        预测配置，包含各种预测选项
    **kwargs : dict
        额外参数传递给model.compile()
    """
    # 计算全局批次大小
    self.global_batch_size = (
      forecast_config.per_core_batch_size * self.model.device_count
    )

    # 快捷引用
    fc = forecast_config

    # 验证并调整上下文长度（必须是patch大小的倍数）
    if fc.max_context % self.model.p != 0:
      logging.info(
        "When compiling, max context needs to be multiple of the patch size"
        " %d. Using max context = %d instead.",
        self.model.p,
        new_context := math.ceil(fc.max_context / self.model.p) * self.model.p,
      )
      fc = dataclasses.replace(fc, max_context=new_context)
    # 验证并调整预测长度（必须是输出patch大小的倍数）
    if fc.max_horizon % self.model.o != 0:
      logging.info(
        "When compiling, max horizon needs to be multiple of the output patch"
        " size %d. Using max horizon = %d instead.",
        self.model.o,
        new_horizon := math.ceil(fc.max_horizon / self.model.o) * self.model.o,
      )
      fc = dataclasses.replace(fc, max_horizon=new_horizon)
    # 验证上下文+预测长度不超过限制
    if fc.max_context + fc.max_horizon > self.model.config.context_limit:
      raise ValueError(
        "Context + horizon must be less than the context limit."
        f" {fc.max_context} + {fc.max_horizon} >"
        f" {self.model.config.context_limit}."
      )
    # 验证连续分位数头的使用条件
    if fc.use_continuous_quantile_head and (fc.max_horizon > self.model.os):
      raise ValueError(
        f"Continuous quantile head is not supported for horizons > {self.model.os}."
      )
    self.forecast_config = fc

    def _compiled_decode(horizon, inputs, masks):
      """编译后的解码函数。
      
      内部函数，实现了完整的预测流程，包括：
      1. 输入验证和转换
      2. 可选的输入归一化
      3. 模型解码
      4. 可选的翻转不变性处理
      5. 连续分位数头处理
      6. 分位数交叉修复
      7. 输出反归一化
      
      Parameters
      ----------
      horizon : int
          预测步数
      inputs : list[np.ndarray]
          输入时间序列列表
      masks : list[np.ndarray]
          输入掩码列表
      
      Returns
      -------
      tuple
          - 中位数预测值 (batch, horizon)
          - 完整分位数预测 (batch, horizon, num_quantiles)
      """
      # 验证预测长度
      if horizon > fc.max_horizon:
        raise ValueError(
          f"Horizon must be less than the max horizon. {horizon} > {fc.max_horizon}."
        )

      # 将numpy输入转换为PyTorch张量
      inputs = (
        torch.from_numpy(np.array(inputs)).to(self.model.device).to(torch.float32)
      )
      masks = torch.from_numpy(np.array(masks)).to(self.model.device).to(torch.bool)
      batch_size = inputs.shape[0]

      # 推断输入是否为正值
      if fc.infer_is_positive:
        is_positive = torch.all(inputs >= 0, dim=-1, keepdim=True)
      else:
        is_positive = None

      # 可选：输入归一化（RevIN）
      if fc.normalize_inputs:
        mu = torch.mean(inputs, dim=-1, keepdim=True)
        sigma = torch.std(inputs, dim=-1, keepdim=True)
        inputs = revin(inputs, mu, sigma, reverse=False)
      else:
        mu, sigma = None, None

      # 模型解码
      pf_outputs, quantile_spreads, ar_outputs = self.model.decode(
        forecast_config.max_horizon, inputs, masks
      )
      # 合并预填充和自回归输出
      to_cat = [pf_outputs[:, -1, ...]]
      if ar_outputs is not None:
        to_cat.append(ar_outputs.reshape(batch_size, -1, self.model.q))
      full_forecast = torch.cat(to_cat, dim=1)

      # 辅助函数：翻转分位数顺序
      def flip_quantile_fn(x):
        return torch.cat([x[..., :1], torch.flip(x[..., 1:], dims=(-1,))], dim=-1)

      # 可选：翻转不变性处理（通过对称性提高鲁棒性）
      if fc.force_flip_invariance:
        flipped_pf_outputs, flipped_quantile_spreads, flipped_ar_outputs = (
          self.model.decode(forecast_config.max_horizon, -inputs, masks)
        )
        flipped_quantile_spreads = flip_quantile_fn(flipped_quantile_spreads)
        flipped_pf_outputs = flip_quantile_fn(flipped_pf_outputs)
        to_cat = [flipped_pf_outputs[:, -1, ...]]
        if flipped_ar_outputs is not None:
          to_cat.append(flipped_ar_outputs.reshape(batch_size, -1, self.model.q))
        flipped_full_forecast = torch.cat(to_cat, dim=1)
        # 平均原始预测和翻转预测
        quantile_spreads = (quantile_spreads - flipped_quantile_spreads) / 2
        pf_outputs = (pf_outputs - flipped_pf_outputs) / 2
        full_forecast = (full_forecast - flipped_full_forecast) / 2

      # 可选：使用连续分位数头
      if fc.use_continuous_quantile_head:
        for quantile_index in [1, 2, 3, 4, 6, 7, 8, 9]:
          full_forecast[:, :, quantile_index] = (
            quantile_spreads[:, : fc.max_horizon, quantile_index]
            - quantile_spreads[:, : fc.max_horizon, 5]
            + full_forecast[:, : fc.max_horizon, 5]
          )
      # 截取到目标预测长度
      full_forecast = full_forecast[:, :horizon, :]

      # 可选：返回回测值（历史重建）
      if fc.return_backcast:
        full_backcast = pf_outputs[:, :-1, : self.model.p, :].reshape(
          batch_size, -1, self.model.q
        )
        full_forecast = torch.cat([full_backcast, full_forecast], dim=1)

      # 可选：修复分位数交叉问题（确保分位数单调性）
      if fc.fix_quantile_crossing:
        for i in [4, 3, 2, 1]:
          full_forecast[:, :, i] = torch.where(
            full_forecast[:, :, i] < full_forecast[:, :, i + 1],
            full_forecast[:, :, i],
            full_forecast[:, :, i + 1],
          )
        for i in [6, 7, 8, 9]:
          full_forecast[:, :, i] = torch.where(
            full_forecast[:, :, i] > full_forecast[:, :, i - 1],
            full_forecast[:, :, i],
            full_forecast[:, :, i - 1],
          )

      # 可选：输出反归一化
      if fc.normalize_inputs:
        full_forecast = revin(full_forecast, mu, sigma, reverse=True)

      # 可选：强制正值约束
      if is_positive is not None:
        full_forecast = torch.where(
          is_positive[..., None],
          torch.maximum(full_forecast, torch.zeros_like(full_forecast)),
          full_forecast,
        )

      # 转换为numpy并返回
      full_forecast = full_forecast.detach().cpu().numpy()
      return full_forecast[..., 5], full_forecast

    self.compiled_decode = _compiled_decode
