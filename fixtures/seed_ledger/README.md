# Seed Ledger (示例 Fixture)

本目录包含 32 天（2026-01-25 ~ 2026-02-25）的预测账本 seed 数据，包含所有模型的预测值和实际值。

## 用途

BGEW 权重学习需要至少 30 天的真实预测+实际值配对。本 seed 数据可跳过 backfill 直接开始权重学习。

## 使用方式

将 `outputs/ledger/` 替换为本目录内容：

```bash
mkdir -p outputs/ledger
cp -r fixtures/seed_ledger/* outputs/ledger/
```

或直接复制：
```
fixtures/seed_ledger/dayahead/actual/actual_ledger.csv    → outputs/ledger/dayahead/actual/
fixtures/seed_ledger/dayahead/prediction/prediction_ledger.csv → outputs/ledger/dayahead/prediction/
...
```

## 数据来源

由 `main.py --pipeline ledger_backfill --start 2026-01-25 --end 2026-02-25` 生成。

## 注意

- 本目录是**静态 fixture**，不作为正式运行产物。
- 正式运行产物在 `outputs/ledger/`（.gitignore 忽略）。
- 如果 `outputs/ledger/` 为空，必须先运行 `ledger_backfill` 或从本目录初始化。
