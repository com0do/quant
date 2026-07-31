# 可提交清单

以下清单用于“只提交稳定生产所需内容”，避免把实验垃圾一并提交。

## A. 建议提交（代码与文档）

- `quant/` 下所有实际运行代码改动
- `config/final_freeze_2025.toml`
- `config/branch_alpha_regime_switch.toml`（对照配置）
- `config/adaptive_plus_base.toml`（基础兼容配置）
- `README.md`
- `docs/INDEX.md`
- `docs/FINAL_FREEZE_RUNBOOK.md`
- `docs/ENGINE_PARAMETER_REGIME_GUIDE.md`
- `docs/SUBMISSION_CHECKLIST.md`

## B. 明确不提交（运行产物）

- `output/*`
- `data/*.db-wal`
- `data/*.db-shm`
- `data/backups/*`

## C. 可选提交（视团队规范）

- `third-party/`：仅在其中确有必须版本锁定资产时提交

## D. 提交前检查

- 回测命令能跑通：
  - `uv run python main.py --mode backtest --config config/final_freeze_2025.toml`
- 聚宽同步链路可用（至少命令可执行）：
  - `uv run python main.py --mode jq-bulk-sync --config config/final_freeze_2025.toml`
- 文档入口完整：
  - `docs/INDEX.md` 已包含冻结手册、参数引擎说明、提交清单
