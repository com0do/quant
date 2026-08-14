# 迁移 & 参数稳定性验证报告（2026-08-14）

## 1. 迁移范围

从 `~/test/quant`（早期迭代仓库，不通远端）搬到当前仓库：

| 项 | 来源 | 目标 | 大小 |
|----|------|------|------|
| CSI 1000 行情 | `data/runtime/market_csi1000_latest.db` | 同名 | 353 MB |
| CSI 2000 行情 | `data/runtime/market_csi2000_latest.db` | 同名 | 194 MB |
| CSI 300/500 行情 | `data/runtime/market_csi300_500_latest.db` | 同名 | 51 MB |
| 指数成分 meta | `data/meta/index_constituents.db` | 同名 | 12 MB |
| JQ 账号 + 代理 | `.env` | 同名（gitignored） | 251 B |

**未迁移**：
- `~/test/quant/data/philip/`（18 GB 日线/分钟高频缓存，当前代码不引用）
- `~/test/quant/data/runtime/market_philip_csi2000_2025_validation.db`（244 MB 验证集，
  配置未引用）

**配置**：早已一致（`config/*.toml` 与 legacy 二进制完全相同）。

**代码修正**：`.env.example` 里的 `JQDATA_USERNAME/PASSWORD` 与代码实际读取
（`CC_JQ_NAME/CC_JQ_PW`）不匹配，已改回并补上 HTTP 代理注释。

## 2. 聚宽数据现状

| DB | 起始日 | 截止日 | 交易日 |
|----|--------|--------|--------|
| CSI 1000 | 2024-12-24 | **2026-03-13** | 293 |
| CSI 2000 | 2024-12-24 | **2026-03-13** | 293 |
| CSI 300/500 | 2024-12-16 | 2025-12-31 | 255 |

## 3. JQ 增量同步——**当前网络不可用**

诊断：
- TCP 探测 `39.107.190.114:7000`（聚宽 thrift 端口）→ **BLOCKED**
- HTTPS 443 到 `www.joinquant.com` → 通（走公司 Fortinet 代理）
- 结论：公司防火墙只放行 HTTP/HTTPS，不透传裸 TCP；JQData SDK 用 thrift RPC 走 7000
  端口，走 HTTP_PROXY 不够，需要 SOCKS 代理 / VPN 才能补数据

**当前 CSI 1000 已有 2026-01-02 → 2026-03-13 共 51 个交易日的天然样本外数据**，
在联网前也足够验证冻结参数是否失效。

## 4. `config/final_freeze_2025.toml` 参数稳定性测试

三段区间跑同一份策略（`csi1000_enhanced`，`top_k=4`，`buy_score_threshold=0.00`，
`sell_score_threshold=0.94`，regime-switch 打开）。

| 区间 | 年化收益 | 基准年化 | 超额 | Sharpe | 最大回撤 | IR | 交易数 | 期末权益 |
|------|----------|----------|------|--------|----------|------|--------|----------|
| **2025 全年 (IS)** | **+39.80%** | +29.64% | +10.17% | **2.02** | -10.12% | +0.39 | 251 | 136 796 |
| **2026-01 → 03-13 (OOS)** | -31.98% | +40.22% | **-72.20%** | -1.58 | -12.40% | -3.67 | 55 | 93 593 |
| **2025 + 2026-Q1 合并** | +34.27% | +35.95% | **-1.67%** | 1.80 | -10.12% | -0.07 | 307 | 139 653 |

**关键观察**：

1. **样本内很强**：2025 全年年化 40%、Sharpe 2.0、最大回撤 10%、251 次交易，IR +0.39——
   在样本区间上参数依然稳定。
2. **样本外掉队**：2026 Q1 CSI 1000 基准年化跑到 +40%（2 个月 +8%），策略反而 -6.4%
   （权益 100k→93.6k），主要发生在 2 月中旬到 3 月初的这段——策略保守持有，错过反弹。
3. **综合看 alpha 归零**：把 IS+OOS 一起算，年化 34.27% 对基准 35.95%，**超额已经压
   到 -1.67%**——2025 累积的超额被 2026 Q1 吃干净。
4. 回撤和波动这两个风控指标依然可控（合并区间最大回撤 -10.1%、年化波动 19.1%），
   策略"没爆"，但已经**明确失去了 Alpha**。

## 5. 结论 & 建议

- **参数不再"维持收益率"**：冻结版在 2026 Q1 已经跑输基准 14 个百分点；不能盲目上实盘。
- 建议顺序：
  1. 补上 SOCKS/VPN 后先跑 `main.py --mode jq-bulk-sync`，把 CSI 1000/2000
     数据补到 2026-08-13（今天），至少 5 个月新数据可用。
  2. 跑 `main.py --mode walk-forward --config config/final_freeze_2025.toml`，
     用滚动窗口重新验证 2026 Q1/Q2 是否形成新体制。
  3. 跑 `main.py --mode factor-analyze` 看是不是某个核心因子已经衰退（IC 掉档）。
  4. 用 `--mode optimize-fast` 在 2025 IS + 2026 Q1 OOS 上重新拟合，产出新的
     freeze 版本（`config/final_freeze_2026H1.toml`），而不是继续用 2025 冻结版。
- **不要**把这份 `final_freeze_2025.toml` 切成 `dry_run=false` 上实盘。

## 6. 产物

- `output/migration_check/metrics_baseline_2025.json`
- `output/migration_check/metrics_oos_2026q1.json`
- `output/migration_check/metrics_full_2025_to_2026q1.json`
- 对应 equity curve CSV 各一份

（都在 `output/` 下，已被 `.gitignore` 排除，只有这份 report 会入 git。）
