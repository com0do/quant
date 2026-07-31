# 最终冻结运行手册

## 1) 当前冻结配置

- 主配置：`config/final_freeze_2025.toml`
- 对照配置：`config/branch_alpha_regime_switch.toml`

这两个配置都保留在仓库中。`config/` 历史参数文件也已恢复，便于后续对比。

## 2) 最终回测执行

```bash
uv run python main.py --mode backtest --config config/final_freeze_2025.toml
```

输出文件（已清理为最小集合）：

- `output/backtest_metrics.json`
- `output/backtest_equity_curve.csv`
- `output/backtest_trades.csv`

## 3) 聚宽数据拉取能力（保留）

可继续使用以下链路：

- `uv run python main.py --mode jq-bulk-sync --config config/final_freeze_2025.toml`
- `scripts/run_tomorrow_sync.sh`
- `scripts/run_tomorrow_smallcap_sync.sh`
- `scripts/jq_bulk_sync_smallcap.py`
- `scripts/sync_index_daily_to_main.py`
- `scripts/sync_minute_smallcap_batch.py`
- `scripts/check_missing_smallcap_data.py`
- `scripts/prepare_snapshot_retry.py`

## 4) 停止过拟合的终止规则

- 不再在同一年样本内持续调参。
- 触发再优化需满足至少一条：
  - 连续 2 个月相对基准超额为负；
  - 实盘/仿真回撤超过冻结期回撤上限的 1.3 倍；
  - 市场结构发生显著变化（成交额/波动中枢持续漂移）。

## 5) 后续维护建议

- 只做“验证与监控”，不做日常参数搜索。
- 按月输出一次冻结配置相对基准的滚动评估。
- 至少累计一个季度新数据后，再进入下一轮参数重估。
