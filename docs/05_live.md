# 五、实盘流水线与网关

> 本篇合并了 6 份实盘/网关文档：实盘自动化流水线、QMT 网关协议、xtquant 网关 V2 设计、部署分离、xtdata 同步策略、QKA 生产化清单。
> 准备接实盘或做运维时读。

## 目录

- [1. 实盘自动化流水线](#1-实盘自动化流水线)
- [2. QMT 网关协议](#2-qmt-网关协议)
- [3. xtquant_gateway V2 设计](#3-xtquant-gateway-v2-设计)
- [4. 网关部署分离](#4-网关部署分离)
- [5. xtdata 最小必要同步策略](#5-xtdata-最小必要同步策略)
- [6. QKA 网关生产化清单](#6-qka-网关生产化清单)

---

<a id="1-实盘自动化流水线"></a>
# 实盘自动化流水线（单一执行源）

这条流水线的目的是：让实盘策略不直接读取零散的 `output/*` 文件，而是围绕一份统一的“当日执行计划”流转。

## 目标

- 把盘前准备与盘中执行串成一条连贯的流。
- 每天只保留一份执行快照（`execution_plan`）作为交接产物。

## 主流程

1. 盘前同步（可选）：`jq-bulk-sync`
2. 盘前回测（使用当前配置）
3. 盘前扫描，构建当日观察池
4. 写入 `data/runtime/live_execution_plan.json`
5. 启动实盘守护，消费该计划中的观察池

## 入口命令

```bash
uv run python main.py --mode live-auto --config config/live_qmt_http.toml
```

只做盘前准备（推荐做成盘前定时任务）：

```bash
uv run python main.py --mode prepare-live-plan --config config/live_qmt_http.toml
```

可用环境变量：

- `LIVE_AUTO_SYNC_BEFORE_OPEN=0|1`（默认 `1`）
- `LIVE_AUTO_MAX_LOOPS=<int>`（不填表示持续运行）

## 为什么优于直接读 `output/`

- `output/` 里堆着各种分析产物，容易误引用。
- 执行交接使用一份明确 schema 的文件（`execution_plan`），包含：
  - 生成时间与来源配置
  - 盘前回测指标
  - 策略快照
  - 当日观察池

## 每日脚本

用一个脚本编排盘前动作：

```bash
bash scripts/run_daily_live_pipeline.sh
```

常用环境变量：

- `RUN_SYNC_BEFORE_OPEN=1|0`
- `RUN_GATEWAY_ACCEPTANCE=1|0`
- `RUN_TRADING_SESSION=1|0`
- `PROFILE=live`
- `CONFIG_PATH=config/live_qmt_http.toml`

---

<a id="2-qmt-网关协议"></a>

# QMT 网关协议（Linux ↔ Windows）

本项目已支持 `live-daemon` 使用 `qmt_http` broker 模式。

## 目的

- Linux 侧负责策略、信号、风控、调度。
- Windows 侧运行 miniQMT + xtquant + 网关服务。
- Linux 通过 HTTP 调用网关来查账户/持仓、提交/撤销订单。

## 配置项

在 `config/*.toml` 里：

- 通用网关段（`stock_data` 和 `execution` 均会读取）：
  - `[gateway] base_url / token / timeout_sec / account_id`
- 交易路由：
  - `[daemon] broker_type = "qmt_http"`
- 行情路由：
  - `[data] realtime_quote_source = "qmt_http"`（fallback 仍然是日频收盘价）

参考文件：`config/live_qmt_http.toml`

## 必需端点

网关需要提供如下 JSON API：

1. `POST /account`
   - 请求：`{ "account_id": "..." }`（可选）
   - 响应：`{ "data": { "cash": 0.0, "equity": 0.0, ... } }` 或原始 dict

2. `POST /positions`
   - 请求：`{ "account_id": "..." }`（可选）
   - 响应：
     - `{ "data": { "000001.SZ": 1000, ... } }`，或
     - `{ "data": [ { "code": "000001.SZ", "quantity": 1000 }, ... ] }`

3. `POST /order`
   - 请求：
     - `account_id`（可选）
     - `code`（`000001.SZ` / `600000.SH`）
     - `side`（`buy` / `sell`）
     - `quantity`（int）
     - `price`（float / null）
   - 响应：`{ "data": { "order_id": "...", "status": "new|filled|..." } }`

4. `POST /cancel`
   - 请求：`{ "account_id": "...", "order_id": "..." }`（`account_id` 可选）
   - 响应：`{ "data": { "ok": true } }` 或 `{ "ok": true }`

5. `POST /order/status`
   - 请求：`{ "account_id": "...", "order_id": "..." }`（`account_id` 可选）
   - 响应：`{ "data": { "order_id": "...", "status": "...", ... } }`

## 可选端点

客户端 helper 里已经实现，方便监控：

- `POST /quote`（`get_quote`）
- `POST /fills`（`get_fills`）

## XtQuant 接口映射

网关内部（Windows 侧）推荐这样映射：

- `xtquant.xtdata`（行情）
  - 快照/查询：`get_full_tick`、`get_market_data`、`get_market_data_ex`、`get_local_data`
  - 订阅：`subscribe_quote`、`subscribe_whole_quote`、`unsubscribe_quote`
- `xtquant.xttrader`（交易）
  - 下单：`order_stock`、`order_stock_async`
  - 撤单：`cancel_order_stock`
  - 查询：`query_stock_asset`、`query_stock_positions`、`query_stock_orders`、`query_stock_trades`
  - 回调推送：`on_stock_order`、`on_stock_trade`、`on_order_error`、`on_cancel_error`、`on_stock_asset`、`on_stock_position`

映射建议：

- `/quote` → 优先取订阅推送缓存，fallback 用 `get_full_tick`
- `/account` → `query_stock_asset`
- `/positions` → `query_stock_positions`
- `/order` → `order_stock` 或 `order_stock_async` + 本地 ack 映射
- `/cancel` → `cancel_order_stock`
- `/order/status` → 本地状态缓存 + `query_stock_order(s)` fallback
- `/fills` → `query_stock_trades` + 本地去重

## QKA 改造清单

现在的 `qka` 服务通过 `/api/<method>` 动态暴露所有 trader 方法，简单但不适合生产。

建议升级：

1. 固定端点 + 严格请求模型。
   - 只保留必要路径（`/quote`、`/account`、`/positions`、`/order`、`/cancel`、`/order/status`、`/fills`）。
   - 校验字段与范围。
2. 异步订单状态机。
   - 用 `order_stock_async` + `req_id → order_id` 映射。
   - 消费回调更新内存状态并持久化。
3. 幂等与去重。
   - `/order` 加 `client_order_id`。
   - 重复提交直接返回上次结果。
4. 服务端保护与资金管理。
   - 预检 `available_cash`、`frozen_cash`、手数、单笔名义额、可卖数量。
   - 违反规则时本地直接拒绝，不下发。
5. 可观测性。
   - 按端点统计时延、错误码、回调延迟、重连次数。
6. 可靠性。
   - 心跳 + 自动重连 + 行情过期检测 + 熔断。

## 延迟与瓶颈分析

开 QMT 之前，先优化架构本身：

1. **Python HTTP 栈开销**
   - 动态反射路由既增加开销，也带来序列化噪声。
   - 处理：固定路由 + 预校验模型 + keep-alive session。
2. **同步查询路径**
   - 每次请求都同步查账户/持仓/订单，会阻塞交易主路径。
   - 处理：回调驱动缓存，查询只作为兜底。
3. **跨 OS 网络跳转**
   - Linux → Windows 增加时延抖动。
   - 处理：同一 LAN、固定路由、不走公网、超时和重试预算都要小。
4. **代理/路由错乱**
   - 环境代理可能劫持本地地址。
   - 处理：客户端禁用代理走网关（本仓库已实现）。
5. **行情扇出压力**
   - 用 pull API 轮询大量标的会打满网关。
   - 处理：订阅推送 + 有界环形缓冲 + 批量端点。
6. **风险校验只放在策略端**
   - 如果网关不做守护，直接下单的风险敞口就存在。
   - 处理：网关侧再做一次硬校验（服务端风控墙）。

## 运行手册

1. 在装有 miniQMT 的 Windows 主机上启动网关服务。
2. 验证 Linux 侧能访问 `[gateway].base_url`。
3. 执行冒烟测试：
   - `uv run python scripts/qmt_gateway_smoke_test.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID"`
4. 启动 daemon：
   - `uv run python main.py --mode live-daemon --config config/live_qmt_http.toml`
5. 跑一遍验收清单：
   - `uv run python scripts/qmt_gateway_acceptance.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID"`
   - 可选真实小单测试：
     - `uv run python scripts/qmt_gateway_acceptance.py --base-url http://IP:18080 --token "$QMT_GATEWAY_TOKEN" --account-id "$QMT_ACCOUNT_ID" --test-order --order-code 000001.SZ --order-qty 100 --order-price 10.0`

## 安全说明

- 一定要设置 token 并限制来源 IP。
- 不要把网关直接暴露到公网。
- 优先使用 LAN/VPN + 防火墙规则。

---

<a id="3-xtquant-gateway-v2-设计"></a>

# xtquant_gateway V2 设计

这是替换旧 `qka` 动态 API 暴露方式的新版网关设计。

## 命名与范围

- 新包名：`xtquant_gateway`
- 定位：Linux 策略引擎与 Windows miniQMT/xtquant 运行时之间的桥梁，主打低时延、以安全为先、状态可持久化
- 不需要与旧的动态路由保持向后兼容

## 端点契约

- `GET /health/live`
- `GET /health/trader`
- `GET /health/quote`
- `GET /metrics/summary`
- `POST /quote`
- `POST /account`
- `POST /positions`
- `POST /order`
- `POST /cancel`
- `POST /order/status`
- `POST /fills`

## 核心设计决策

1. **低时延**
   - 固定路由 + 严格请求 schema
   - 交易会话保持长连接
   - 行情缓存，在下单前做过期检查

2. **安全**
   - 服务端风控墙（手数、可用资金、保留资金、单笔名义额、每日订单数/名义额、可卖数量）
   - 行情过期时禁止下单
   - 关键路径的重连兜底

3. **持久化**
   - 使用 sqlite（`data/xtquant_gateway.db`）存订单、幂等键、成交、API metrics
   - 启用 WAL 模式

4. **可观测**
   - 按端点统计成功/失败数与 p50/p95/p99 时延
   - 在 metrics summary 中暴露重连次数与最后一次重连错误

## 启动命令

```bash
XTG_ACCOUNT_ID=... \
XTG_MINI_QMT_PATH="C:/path/to/miniqmt" \
XTG_TOKEN=... \
uv run python scripts/run_xtquant_gateway.py
```

## 服务端与客户端分离

- Win11 网关服务端与 Linux quant 客户端使用独立的 venv。
- 具体步骤见 `docs/15_网关部署分离.md`。

## 数据边界建议

- 回测保持独立，不依赖 xtquant。
- xtquant 数据仅用于实盘交易主路径和必要的实时行情校验。
- 只同步策略需要的标的与时间窗，减少压力和复杂度。

---

<a id="4-网关部署分离"></a>

# 网关服务端与 quant 客户端的部署分离（Win10 服务端 + WSL2 客户端）

## 拓扑

- **Win10**：运行 `xtquant_gateway` 服务，连接 miniQMT + xtquant 运行时。
- **WSL2（Ubuntu/Linux）**：运行 quant 引擎/回测/实盘策略，调用网关 HTTP 端点。

## 客户端位置（重要）

- `GatewayClient` 是 quant 运行时的一部分，应保留在本仓库内（`quant/gateway`）。
- 推荐路径：在仓库根目录直接使用 quant 主环境（`uv sync --locked`）。
- 一句话：**客户端代码与客户端运行时都放在主 quant 项目里。**

## 为什么要分环境

- 避免把 Win 独有运行时依赖混进 Linux quant 研究栈
- 隔离操作风险：网关重启/升级不影响研究环境
- 网关与 quant 引擎可以按自己的节奏发版

## 环境布局

### Windows 服务端（独立 venv）

1. 在网关目录建 venv：
   - `uv venv .venv-gateway-server`
2. 激活并同步：
   - `cd deploy/gateway_server && uv sync --locked`
3. 启动服务：
   - 拷贝 `.env.example` 为 `.env` 并填值（`XTG_ACCOUNT_ID`、`XTG_MINI_QMT_PATH`、`XTG_TOKEN`）
   - 手动启动：`uv run ../../scripts/run_xtquant_gateway.py --env-file .env`
   - 一键启动（自动拉起 miniQMT + 网关）：`start_gateway_with_miniqmt.bat`

### WSL2 quant 引擎（独立 venv）

1. quant 项目 venv 保持独立：
   - `uv venv .venv-quant`
   - `uv sync --locked`

## 运行边界

- 回测/优化：完全独立于 xtquant 运行时。
- 实盘交易路径：仅通过网关。
- 实时行情校验：网关 `/quote` 只面向策略需要的标的。

## 服务端快速启动

- 单独维护一个 Windows 侧的服务端环境：
  - `cd deploy/gateway_server && uv sync --locked`
- 用已有脚本启动网关：
  - `uv run ../../scripts/run_xtquant_gateway.py --env-file .env`

## 为什么不能用 `; python ...` 这种简写

- 如果把 miniQMT 与网关放在同一个命令链里启动，miniQMT 会阻塞 shell，后续命令永远执行不到。
- 请用 detached 启动（`start` / `Popen`），或使用提供的 `start_gateway_with_miniqmt.bat`，脚本会先起 miniQMT 再起网关。

---

<a id="5-xtdata-最小必要同步策略"></a>

# xtdata 最小必要同步策略

本策略定义：从 `xtdata` 同步什么、什么时候同步、以及为什么。

## 原则

- 回测保持可复现、独立于网关运行时。
- xtdata 用于**在非交易时段补齐缺失的行情**，以及**在盘中支持实盘风控校验**。
- 只同步必要的标的与时间段，控制时延与存储压力。

## 时间窗口

- **T 日 15:20 – 次日 08:50（非盘中）**：
  - 跑增量同步任务
  - 校验数据质量与完整性
  - 必要时刷新回测数据集并重跑验证
- **盘中**：
  - 不做历史数据批量下载
  - 仅订阅/查询实时行情，用于实盘信号与风控

## 最小必要同步范围

1. 基准与状态指数
   - 日线，可选 1 分钟，用于市场状态特征
2. 候选股池
   - 策略候选池中的股票，不必全市场
3. 当前持仓 + 观察池
   - 保证分钟/日线连贯，供风控与止损逻辑使用
4. 分红/复权因子
   - 用于历史价格干净拼接

## 为什么这样做能站得住

- xtdata 文档建议单股订阅数受限，高订阅场景优先用整市订阅。
- 历史请求要控制区间/条数，避免长响应延迟。
- 板块/分类等静态数据日更/周更即可，不需要每次调用。

## 推荐使用的 xtdata 调用

- 历史增量：
  - `download_history_data2(...)`：按选定标的批量下载
- 实时：
  - `subscribe_whole_quote(...)`：整市订阅
  - `get_full_tick(...)`：点查兜底
- 交易日历：
  - `get_trading_calendar(...)`
- 复权因子：
  - `get_divid_factors(...)`

---

<a id="6-qka-网关生产化清单"></a>

# QKA 网关生产化清单

本清单用于将 `third-party/qka` 从 demo 桥接升级为可生产的网关。

## 目前进度

- 已实现：
  - 固定路由（`/quote`、`/account`、`/positions`、`/order`、`/cancel`、`/order/status`、`/fills`）
  - 严格请求模型 + token 鉴权（`Bearer` + 兼容 `X-Token`）
  - 订单 `client_order_id` 幂等缓存
  - 服务端交易前风控墙（现金/手数/可卖数量/日限）
  - 健康检查路由（`/health/live`、`/health/trader`、`/health/quote`）
  - 下单前的行情过期守护
  - 关键查询/下单路径的重连兜底
  - 订单/幂等/成交的 sqlite 持久化（`qka_gateway_state.db`）
  - `/metrics/summary`：按端点的 p50/p95/p99 + 状态计数
  - 一键验收脚本（`scripts/qmt_gateway_acceptance.py`）
- 待办：
  - 更完整的直方图 / 长期外部 metrics 沉淀（Prometheus/TSDB）

## API 表面

- 替换掉 `/api/<method>` 的动态暴露，改成固定路由：
  - `/quote`、`/account`、`/positions`、`/order`、`/cancel`、`/order/status`、`/fills`
- 添加严格的请求/响应 schema 与字段校验。
- `/order` 加 `client_order_id` 幂等键。

## 交易安全墙（服务端）

- 交易前校验：
  - 手数是 A 股常见的 100 的倍数
  - 可用现金与冻结资金
  - 单笔名义额上限
  - 每日名义额上限、每日下单数上限
  - 卖出数量 <= 可用持仓
- 校验失败时本地拒绝，不下发到 xttrader。

## 状态与恢复

- 用回调驱动状态缓存：
  - 订单状态、成交、持仓、账户
- 把关键快照持久化到本地 sqlite：
  - 挂单、最新 `order_id` 映射、去重键
- 重启时：
  - 用 xttrader 查询 API + 本地持久化缓存重建状态

## 性能与时延

- 长驻一个 xttrader 会话与一个 HTTP client session。
- 用异步下单路径 + 回调确认。
- 热路径避免频繁 `query_*` 全量轮询，用缓存 + 兜底轮询。
- 策略批量查询多个标的时，网关侧做批量行情请求。

## 运维

- 加健康检查：
  - `/health/live`、`/health/trader`、`/health/quote`
- 加 metrics：
  - 端点时延 p50/p95/p99
  - 回调延迟
  - 拒单率
  - 重连次数
- 告警：
  - 断连/重连循环
  - 行情过期超阈值
  - 连续拒单突发
