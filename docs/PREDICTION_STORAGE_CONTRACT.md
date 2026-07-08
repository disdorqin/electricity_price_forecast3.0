# EFM3 预测结果存储契约 (Prediction Storage Contract)

本契约规定：**3.0 主链路上产生的每一条预测结果，都必须经由 `PredictionStore` 抽象落库，绝不绕过它直接写裸 CSV。** 无论目标是 MySQL ledger（`MySQLPredictionStore`）还是本地文件（`FilePredictionStore`），上层代码（编排器、各路由器、导出器）都只面对统一的 `PredictionStore` 接口。

实现位置：
- 抽象与实现：`common/prediction_store.py`
- 编排器接线：`pipelines/full_chain_orchestrator.py`（在 5 处调用均显式传入 `prediction_store=store`）
- 导出器：`pipelines/db_exporter.export_submission_ready(...)`（从 store 读取 `selected-final`，绝不直接读 shadow CSV）

---

## 1. 接口契约 (PredictionStore ABC)

`common/prediction_store.PredictionStore` 定义以下方法（pandas-free，.backend 与 CLI 共用同一份代码）：

| 方法 | 用途 | 关键约束 |
|------|------|----------|
| `write_predictions(run_id, target_date, records)` | 写主预测 | 主链路 each-step 预测的唯一写入入口 |
| `write_shadow_predictions(run_id, target_date, records)` | 写 shadow 预测 | 与 `write_predictions` 物理隔离，互不覆盖 |
| `write_selected_final(run_id, target_date, records)` | 写选定最终价 | 仅由路由选择器产出，代表可交付结果 |
| `read_predictions(run_id, target_date, stage)` | 读某阶段预测 | `stage ∈ {predictions, shadow, selected}` |
| `export_submission_ready(run_id, target_date, output_dir, is_formal)` | 导出 submission_ready | **只读 `selected-final`**，绝混入 shadow |
| `get_db_url_info()` | 返回（脱敏后的）存储后端信息 | 用于审计日志，绝不泄露明文密码 |

两套实现：
- `MySQLPredictionStore(db_url)` — 写 `efm3` ledger 表（prediction / shadow_prediction / selected_final 等）。
- `FilePredictionStore(base_dir)` — 写 `outputs/prediction_store/<run_id>_<target_date>/{predictions,shadow,selected}.csv`，作为无 DB 时的离线兜底。
- `create_prediction_store(...)` — 工厂：有 `EFM3_DB_URL` 时返回 MySQL 实现，否则返回 File 实现。

---

## 2. 强制规则 (Hard Rules)

1. **单一写入入口**：编排器与各路由器只能调用 `store.write_predictions(...)` / `write_selected_final(...)`，**不得**自行 `open(..., "w")` 写裸 CSV。
2. **Shadow 隔离**：shadow 预测只能经 `write_shadow_predictions(...)` 写入；`export_submission_ready` 只读 `selected-final`，因此 shadow 价格**在结构上无法**进入最终交付 CSV。
3. **导出器不收 `csv_path`**：`db_exporter.export_submission_ready(run_id, target_date, prediction_store, output_dir, is_formal)` 的入参签名**必须**包含 `prediction_store`，且**不得**接受裸 `csv_path` —— 强制所有导出都经过 store 读取。
4. **编排器透传 store**：`full_chain_orchestrator` 在调用各 step（含 seasonal_da_router、db_exporter）时必须传入 `prediction_store=store`，不得隐式从文件系统重新拼装结果。
5. **无 DB 可演练**：MySQL 不可用时，编排器自动切换 `FilePredictionStore`（`db_enabled=false`），演练产物仍可被 `db_exporter` 后续读取，保证 dry_run 离线可用。

---

## 3. 反模式 (Anti-patterns, 被测试禁止)

| 反模式 | 为何禁止 | 阻断测试 |
|--------|----------|----------|
| 路由器直接 `to_csv("predictions.csv")` | 绕过 store，无法保证入库 / 无法审计 | `tests/test_all_prediction_paths_use_store.py::test_seasonal_router_accepts_prediction_store` |
| 导出器接受 `csv_path=` 直接读文件 | 绕开 store，可能混入 shadow | `tests/test_no_direct_prediction_csv_without_store.py::test_exporter_signature_requires_store` |
| 编排器导出时不传 `prediction_store=` | 导致裸 CSV 直接落盘、不入 ledger | `tests/test_no_direct_prediction_csv_without_store.py::test_orchestrator_export_passes_store` |
| shadow 出现在 submission_ready | 污染正式交付 | `tests/test_all_prediction_paths_use_store.py::test_shadow_never_selected` |

---

## 4. 测试覆盖

- `tests/test_all_prediction_paths_use_store.py`
  - `test_seasonal_router_accepts_prediction_store` — 源码级正则校验路由器接受 `prediction_store` 参数。
  - `test_orchestrator_creates_a_store` — 编排器在 dry_run / formal 下均创建 store。
  - `test_shadow_never_selected` — `FilePredictionStore` 下，shadow 价格不出现在 `export_submission_ready` 输出。
- `tests/test_no_direct_prediction_csv_without_store.py`
  - `test_exporter_signature_requires_store` — 导出器参数含 `prediction_store`、不含 `csv_path`。
  - `test_exporter_uses_selected_not_shadow` — 导出只取 `selected`，不取 `shadow`。
  - `test_orchestrator_export_passes_store` — 编排器导出调用断言 `prediction_store=store`。

---

## 5. 调用示例

```python
from common.prediction_store import create_prediction_store

store = create_prediction_store(db_url=os.environ.get("EFM3_DB_URL"))
# 主链每一步：
store.write_predictions(run_id, target_date, records)
store.write_selected_final(run_id, target_date, selected)
# 导出（强制读 store）：
from pipelines.db_exporter import export_submission_ready
out = export_submission_ready(
    run_id=run_id, target_date=target_date,
    prediction_store=store, output_dir="outputs/submission", is_formal=True,
)
```
