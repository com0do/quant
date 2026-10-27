# 新闻 / 政策 / 公告 / 基本面选股器（MVP，未验证）

状态：**仅供研究，MVP，未通过实盘验证。** 本文是 [`README.md`](README.md) 的中文对照；英文版是准本。
这个目录检验一个假设：「主升浪是利好消息带来的情绪反应」，能不能做成日频选股器。五个月样本的结论是：
**不能稳定成立**。最有价值的发现是方法层面的——用「今天的流动性」选股票池，会造成巨大的前视偏差，
让一个弱策略看起来像神策略。

## 1. 选股逻辑

在第 t 天收盘，只使用 t-1 及以前的信息：

- **公司公告**：交易所披露的公告标题，用透明的金融词典分成正面 / 负面催化。正面事件在过去 5 个交易日累加；
  过去 10 天出现任何负面公告就剔除。
- **政策消息**：新闻联播文字稿，按主题词典映射到申万一级行业，正面政策在过去 5 个交易日累加。
- **涨停原因**：同花顺涨停池的编辑理由，作为行业热度代理（过去 5 天本行业涨停家数）。
- **基本面**：季度财报指标，按保守的披露滞后使用（季报 +90 天、年报 +120 天）：ROE、营收同比、净利润同比、
  毛利率、资产负债率。
- **技术确认**：MA5 > MA10 > MA20、5 日强度、放量、距离 20 日高点。

最终 `ann_fund` 变体：

```
ann_pos_5 > 0，ann_neg_10 == 0，tech_score > 0，fund_score > 0
rank = z(ann_pos_5) + 0.5*z(tech_score) + 0.2*z(fund_score)
```

每天取前 3 只，每只三成仓，次日开盘买（开盘涨停跳过），持有 5 个交易日、出现绿柱或亏损 8% 卖出。
成本：佣金 0.03%、卖出印花税 0.05%、滑点 0.1%；近 20 日平均成交额 ≥ 5,000 万元。

其他变体是消融实验：`tech`（纯技术）、`announcement`（正公告 + 技术，剔除负面）、`policy`（政策 + 技术）、
`fundamental`、`policy_tech`、`multi`、`combined`（新闻、基本面、技术加权）。

## 2. 数据（本地，不提交 git）

数据层是 `quant/stock_data/news_store.py`，它不知道策略；默认 SQLite 文件是 `data/news/news.db`（gitignored）。
行情从 `data/market/kline` 读（本地软链接），时点股票池是 `data/market/selector_universe_pit.csv`。

本次（2026-05-01 至 2026-10-26）抓取：

| 表 | 行数 | 来源 |
|---|---:|---|
| announcements | 197,711 | akshare `stock_notice_report`，按交易日拉全市场 |
| policy_news | 2,372 | akshare `news_cctv`，按自然日 |
| limit_up | 8,486 | 同花顺涨停池，117 个交易日，`scripts/news_archive.py` |
| fundamentals | 11,533 | akshare `stock_financial_analysis_indicator`，800 只股票 |
| market_news | 271 | akshare 东财 / 财联社 / 同花顺快讯（只有最近） |

抓取脚本可重复执行，重复运行只替换行：

```bash
uv run --with akshare python research/news_selector/fetch_data.py --start 2026-05-01 --end 2026-10-26
uv run --with akshare python research/news_selector/fetch_fundamentals.py \
    --universe data/market/selector_universe_pit.csv --limit 800
uv run python scripts/news_archive.py --sources ths_limit_up --backfill-days 400
```

## 3. 最重要的结果：股票池前视偏差

第一版用**数据文件最后几行（2026 年 10 月）**的流动性选出 800 只股票，再从 5 月开始回测。这是前视偏差：
一只后来才变活跃的股票，在 5 月就已经被放进池子。结果 `ann_fund` 五个月 **+100.0%**（夏普 3.3），
其他变体也大多很好看。

把股票池改成 **2026-03-01 时点**（`build_universe.py`）后，结果塌了：

| 变体 | 全期（5-10 月） | 5-7 月 | 8-10 月 |
|---|---:|---:|---:|
| 等权股票池 | -19.3% | -13.6% | -6.6% |
| 中证 1000 | -22.7% | -17.5% | -6.3% |
| 纯技术 | -36.1% | -13.5% | -34.6% |
| 公告（剔除负面） | -16.9% | -1.2% | -14.3% |
| **ann_fund** | **+9.9%** | **+16.6%** | **-13.4%** |
| 政策 + 技术 | -37.8% | -19.7% | -27.0% |
| 纯基本面 | -34.7% | -16.4% | -14.1% |
| combined | -14.8% | -8.8% | -1.6% |

`ann_fund` 是全期唯一为正的变体，但 8-10 月亏 13.4%，t 值只有 0.34（全期 +0.34、5-7 月 +0.61、8-10 月 -1.61）。
`combined` 在每段都为负。**没有一个变体在两段都为正。**

这个教训比策略本身更重要：**股票池必须在回测开始时点构建**，不能用今天的流动性排名。修正后，
纯技术从 +52%（10 月池）变成 -36%（3 月池）。

## 4. 分组件结论

- **正公告是相对最不差的新闻组件**，但全期仍为负（-16.9%）；剔除负面公告很重要（在早期污染池上，
  不剔除的版本是 -26.8%）。5-7 月 -1.2%，8-10 月 -14.3%；统计上不显著。
- **政策消息在两段都为负**（5-7 月 -19.7%，8-10 月 -27.0%；全期 -37.8%）。新闻联播到行业的映射很粗，
  这个信号没有正向贡献。
- **基本面偏弱**：在污染池上 +8.1%，在时点池上 -34.7%；报告日 +90/120 天的滞后是保守的，
  但选出的质量/成长股在这五个月跑输。
- **纯技术不是免费午餐**：在前视池上最好，在时点池上最差。
- 全组件加权 `combined` 为负；把弱组件加进来只会稀释唯一相对稳定的组件。

## 4.1 条件事件研究：政策/行业热度 vs 公司公告（2024-2026）

PPT 的 21 个案例里，只有 4 个（约 20%）在启动前有公司层面催化；其余是行业或政策消息。前面的选股器问「能不能挑出股票」；
这个事件研究问更直接的问题：**在强龙信号已经出现的前提下，政策/行业热度能不能让未来收益稳定？公司催化信号是更好还是更差？**

数据：40,723 个可成交强龙信号（2024-01-02..2026-10-23，剔除开盘涨停买不进），5,094 只股票；8,491,580 行行业-日数据；
12,287 条新闻联播政策（2024-2026 有 11,035 条）；786,734 条公告标题（2024-01..2025-03 与 2026-05..10；2025-04..2026-04 未抓，
所以公告分年只有 2024/2026）。未来收益 = 次日开盘 → 5 个交易日后收盘；t 值按每日信号均值计算。

| 组别 | n | 5 日均值 | 5 日中位数 | 胜率 | 日度 t |
|---|---:|---:|---:|---:|---:|
| 全部信号 | 40,636 | -0.61% | -1.88% | 40.7% | -3.92 |
| 行业热度前 20% | 13,034 | -0.06% | -1.59% | 41.8% | -2.04 |
| 政策热度前 20% | 11,007 | -0.63% | -1.84% | 40.8% | -3.10 |
| 政策前 20% + 行业前 20% | 4,624 | +0.51% | -1.16% | 44.5% | -0.33 |
| 正公告 | 3,798 | -1.81% | -2.78% | 37.0% | -2.92 |
| 无公告 | 32,339 | -0.29% | -1.63% | 41.7% | -3.52 |
| 政策前 20% + 正公告 | 1,418 | -1.04% | -2.33% | 38.8% | -1.30 |
| 行业热度后 20% | 445 | +0.55% | -1.06% | 44.0% | +0.34 |

按年看，最好的组（政策前 20% + 行业前 20%）2024 年 +1.16%、2025 年 +1.03%、2026 年 **-0.96%**；合并日度 t 为 -0.33。
不是稳定的正收益。

解读：

- 政策/行业热度能**改善相对结果**（全部 -0.61% → 政策+行业前 20% +0.51%），但不足以稳定为正；最好的组在 2026 年转负。
- 公司催化信号是**最差的一组**（-1.81%；无公告 -0.29%）。这和 PPT 的约 20% 观察一致：公告能描述一部分牛股，
  但等公告公开时已经被定价；另外 80% 是政策/行业故事，标题级公告流抓不到。
- 行业热度后 20% 在这段样本里为正，但只有 445 个信号、t +0.34，不能当证据。
- 结论：80/20 的分布**描述上是对的**，但它不能变成稳定的强龙加仓规则。政策/行业过滤能减少亏损，
  不能把信号变成正期望策略。

局限：新闻联播 → 申万一级的映射很粗（一条标题可能命中多个主题）；行业归属是当前快照；公告覆盖缺 2025-04..2026-04；
未来收益未扣成本；信号本身已经发生在上涨之后（本研究是条件研究，不是预测第一波）。

## 5. 开源框架、LLM 与 Hermes 测试

- **akshare**：可用，作为数据层。`stock_notice_report`（按日历史、全市场）、`news_cctv`（历史）、
  `stock_info_global_em / _cls / _ths`（最近快讯）、`stock_news_em`（个股最近新闻）、
  `stock_financial_analysis_indicator`（季度指标）。东方财富的 quote-list 接口在本网络被封，
  但公告 / 新闻 / 财务接口可用。
- **SnowNLP**：不适合。测试中「股东拟减持不超过2%股份」得分 0.142（偏正面）、
  「公司签订10亿元大单，业绩预增」得分 0.025（偏负面）；通用情感模型不能当金融分类器。
  `classify.py` 用透明的金融词典。
- **LLM 层**：`llm_classify.py` 复用 `quant/ml/llm/deepseek.py` 和现有 prompt 风格；没有
  `DEEPSEEK_API_KEY` 时自动跳过；`--dry-run` 已验证。真实运行只应送少量重要政策 / 重大公告，不能对 20 万条逐条调用。
- **Hermes**：仓库桥接在 `quant/ml/hermes/`；`digest.py` 生成每日 Markdown prompt。本机没有安装 Hermes，
  所以没有跑端到端 agent；示例：
  `uv run python main.py --mode hermes-chat --prompt "$(uv run python research/news_selector/digest.py --date 2026-10-26)"`。
- FinBERT / FinGPT / 中文金融 BERT 没有安装：需要 transformers 和权重；五个月回测需要便宜的初筛，
  合理架构是「规则/LLM 只处理头部候选」，而不是每条新闻一次 LLM。

## 6. 复现

```bash
# 1. 时点股票池（2026-03 流动性）
uv run python research/news_selector/build_universe.py --asof 2026-03-01 --limit 800

# 2. 抓取本地数据（data/ 不提交）
uv run --with akshare python research/news_selector/fetch_data.py --start 2026-05-01 --end 2026-10-26
uv run --with akshare python research/news_selector/fetch_fundamentals.py \
    --universe data/market/selector_universe_pit.csv --limit 800
uv run python scripts/news_archive.py --sources ths_limit_up --backfill-days 400
uv run --with akshare python research/news_selector/fetch_history.py \
    --start 2024-01-01 --end 2026-10-26 --workers 8 --sources ann,cctv
uv run python research/news_selector/policy_event_study.py --start 2024-01-01 --end 2026-10-26

# 3. 跑选股器与消融
uv run python research/news_selector/selector.py \
    --universe data/market/selector_universe_pit.csv --limit 800 \
    --start 2026-03-01 --test-start 2026-05-01 --end 2026-10-26
```

输出：`output/news_selector/summary.json`、`equity_<variant>.csv`、`trades_<variant>.csv`（gitignored）。

## 7. 文件与局限

| 文件 | 用途 |
|---|---|
| `../../quant/stock_data/news_store.py` | 数据层：SQLite 表和读写方法 |
| `fetch_data.py` | 公告、新闻联播政策、涨停原因、最近快讯 |
| `fetch_history.py` | 长窗口公告/新闻联播回填（线程池，2024-2026） |
| `fetch_fundamentals.py` | 流动性股票池的季度基本面 |
| `build_universe.py` | 时点流动性股票池 |
| `classify.py` | 金融词典分类器（公告 / 政策 / 涨停原因） |
| `selector.py` | 特征、变体、组合回测 |
| `policy_event_study.py` | 4.1 节：40,723 个强龙信号的条件事件研究 |
| `llm_classify.py` | 可选的 DeepSeek 分类（只处理少量标题） |
| `digest.py` | 给 Hermes 的每日 Markdown 摘要 |
| `prompts/news_zh.md` | LLM prompt（严格 JSON） |

局限：只有五个月、797 只有数据的股票、头部流动性池（不含微盘）、公告只有标题没有正文、
基本面用报告日 + 固定滞后而不是真实披露时间、行业归属是当前快照（幸存者偏差）、
新闻联播到行业映射很粗、没有盘中执行模型、数据源可能变化。这是研究基线，不是生产策略。
`data/` 故意不提交；以后换成在线数据库时，只需替换 `NewsStore` 后端，选股器不用改。
