# 三、引擎与参数

> 本篇合并了 3 份引擎/运行相关文档：引擎与参数说明、冻结运行手册、可提交清单。
> 想调参数、跑回测、准备提交时读这一篇。

## 目录

- [1. 引擎与参数说明](#1-引擎与参数说明)
- [2. 冻结运行手册](#2-冻结运行手册)
- [3. 可提交清单](#3-可提交清单)

---

<a id="1-引擎与参数说明"></a>
# 引擎与参数说明（最终版）

本文面向当前冻结方案，解释三件事：

1. 参数含义（`config/final_freeze_2025.toml`）
2. 回测引擎工作流程（`quant/backtest/engine.py`）
3. `regime_select`（代码中为 `_calc_regime_switch_params`）的判定与生效方式

---

## 1) 参数含义（按模块）

### Data

- `source`：数据源，当前为 `sqlite`
- `sqlite_db_path`：主库路径（价格、快照、指数日线）
- `start_date` / `end_date`：回测时间窗
- `benchmark_index`：基准指数代码（当前中证1000）

### Strategy - 核心交易参数

- `strategy_names` / `strategy_weights`：策略列表和权重
- `buy_score_threshold` / `sell_score_threshold`：买卖分阈值
- `top_k` / `max_positions`：每次候选数量和最大持仓数
- `entry_interval_days`：调仓/开仓节奏（按交易日）
- `stop_loss_pct`：固定止损
- `trailing_stop_pct`：移动止损
- `min_hold_days`：最小持有天数

### Strategy - 风险与开仓约束

- `max_single_trade_risk_pct`：单笔风险预算
- `max_single_position_loss_pct`：单票最大可承受亏损
- `max_total_exposure_pct`：组合总仓位上限
- `open_vol_min` / `open_vol_max`：波动窗口过滤（过低无弹性，过高不稳定）
- `require_above_ma200`：是否强制 MA200 上方开仓
- `allow_short_below_ma200_exception` 与 `ma200_exception_*`：MA200 下方例外条件

### Strategy - 市场过滤与动态仓位

- `market_regime_gate_enable`：市场门控总开关
- `market_regime_min_win_prob`：市场/个股综合胜率下限
- `market_regime_min_breadth`：市场广度下限
- `monthly_dynamic_exposure_enable`：按月软动态仓位
- `monthly_exposure_min_pct` / `monthly_exposure_max_pct`：月度仓位上下界
- `crash_ret5_threshold` / `crash_ret20_threshold` / `crash_breadth_threshold`：硬崩盘判据
- `crash_cooldown_days`：崩盘后冷却空仓天数

### Strategy - Regime Select（参数切换）

- `regime_param_switch_enable`：参数切换开关
- `regime_switch_breadth_risk_on` / `regime_switch_breadth_risk_off`：风险开/关广度阈值
- `regime_switch_top_k_delta_on` / `_off`：风险态对 `top_k` 的增减
- `regime_switch_sell_thr_delta_on` / `_off`：风险态对卖阈值的偏移
- `regime_switch_entry_interval_mult_on` / `_off`：风险态对调仓频率的倍率
- `regime_switch_exposure_mult_on` / `_off`：风险态对可用仓位上限的倍率

### Execution

- `buy_fee` / `sell_fee`：交易费率
- `slippage_bps`：滑点（bps）

---

## 2) 引擎工作方式（逐日）

引擎主流程可概括为：

1. **载入与特征构建**
   - 从 SQLite 取价格、快照、因子
   - 构建宽表与技术特征（MA、MACD、量能、形态信号）

2. **每日循环**
   - 更新市场状态（广度、趋势、崩盘判据）
   - 生成当日可交易池（停牌/流动性过滤 + 可选估值过滤）
   - 计算 `buy_scores` / `sell_scores`

3. **先风控后交易**
   - 崩盘触发时先平仓并进入冷却
   - 日常风险退出：止损、移动止损、卖分信号
   - 调仓日执行目标持仓对齐与新开仓

4. **仓位与下单约束**
   - 总仓位上限约束（静态 + 月度动态 + regime_select）
   - 单笔风险预算与资金预算共同约束下单数量
   - 交易成本按 `execution` 配置统一计入

5. **输出**
   - 净值曲线、交易明细、核心指标（年化、超额、夏普、回撤）

---

## 3) regime_select 逻辑（`_calc_regime_switch_params`）

该逻辑是“参数切换”，不是直接改策略分数。

### 3.1 状态判定

输入：

- `breadth = mean(close > ma20)`（市场广度）
- `bench_ret20`（基准20日收益）

判定：

- `risk_on`：`breadth >= risk_on_threshold` 且 `bench_ret20 > 0`
- `risk_off`：`breadth <= risk_off_threshold` 或 `bench_ret20 < 0`
- 否则 `neutral`

### 3.2 参数映射

对基础参数做态依赖变换：

- `top_k = base_top_k + delta`
- `sell_threshold = base_sell_threshold + delta`
- `entry_interval_days = round(base_entry_interval * mult)`
- `exposure_cap = base_exposure_cap * mult`

并做边界保护：

- `top_k >= 1`
- `entry_interval_days >= 1`
- `sell_threshold` 限制在合理范围
- `exposure_cap` 不超过原始上限

### 3.3 直观含义

- **risk_on**：可更积极（更快调仓/更高仓位或更宽候选）
- **risk_off**：可更保守（更慢调仓/更低仓位/更严格退出）

当前冻结配置里，核心保守动作是 `risk_off` 降低有效仓位并放慢节奏。

---

## 4) 为什么要冻结参数

- 年内样本迭代过多会放大“新闻-经营-行情”同周期耦合，外推风险高
- 平台区内继续调参，收益边际下降而过拟合风险上升
- 当前方案已具备可执行收益与可控回撤，进入“监控优先”阶段更合理

---

<a id="2-冻结运行手册"></a>

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

---

<a id="3-可提交清单"></a>

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
