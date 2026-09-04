# -*- coding: utf-8 -*-
"""
聚宽最小 demo：set_context + CapitalAllocator 无参回调

目的：
  1. 看清 _CTX / context_holder 如何把 context 传给 used_of / sell_some
  2. 看清高优先级 ensure_nominal 如何挤占低优先级持仓
  3. 对比「有 set_context」vs「故意不 set」时回调行为

用法：复制全文到聚宽策略编辑器，分钟或日频回测均可。
建议：2019-01-01 ~ 2019-03-01，初始资金 100000，基准随意。

日志关键字：
  【DEMO·阶段】 【DEMO·回调】 【DEMO·挤占】 【DEMO·对比】
"""

import numpy as np

# ---------- 开关：设为 False 可观察 context_holder() 为 None 时回调退化 ----------
ENABLE_SET_CONTEXT = True

SID_HI = "S_hi"   # 高优先级，名义 60%
SID_LO = "S_lo"   # 低优先级，名义 40%

ETF_A = "510300.XSHG"  # 沪深300ETF
ETF_B = "510500.XSHG"  # 中证500ETF

_CTX = {}


def context_holder():
    return _CTX.get("ctx")


def set_context(context):
    _CTX["ctx"] = context


# =====================================================================
# 精简版 CapitalAllocator
#
# strategies 只要是「可按 sid 索引 + 可迭代」的 mapping 即可，不必是 dict。
# 这里传 g.accounts 的引用（不是拷贝）：后续 register 只改 dict 内容，
# 已创建的 allocator 也能看到新 sid——因此 register 时不应反复 new allocator。
# =====================================================================
class CapitalAllocator:
    def __init__(self, strategies, used_of, sell_some):
        self.strategies = strategies   # 引用 g.accounts，注册新 sid 后自动可见
        self.used_of = used_of
        self.sell_some = sell_some

    def idle_pool(self, total_value):
        used_all = sum(self.used_of(s) for s in self.strategies)
        return max(0.0, total_value - used_all)

    def usable(self, sid, total_value):
        base = total_value * self.strategies[sid].base_ratio
        others_idle = 0.0
        for o, acc in self.strategies.items():
            if o == sid:
                continue
            others_idle += max(0.0, total_value * acc.base_ratio - self.used_of(o))
        return base + others_idle

    def ensure_nominal(self, sid, total_value):
        acc = self.strategies[sid]
        base_value = total_value * acc.base_ratio
        shortage = max(0.0, base_value - self.idle_pool(total_value) - self.used_of(sid))
        if shortage <= 1e-9:
            return 0.0
        lower = sorted(
            (s for s in self.strategies if self.strategies[s].priority < acc.priority),
            key=lambda s: self.strategies[s].priority,
        )
        reclaimed = 0.0
        for other in lower:
            if shortage <= 1e-9:
                break
            over = max(0.0, self.used_of(other) - total_value * self.strategies[other].base_ratio)
            if over <= 1e-9:
                continue
            got = self.sell_some(other, min(over, shortage))
            log.info("【DEMO·挤占】%s 从 %s 回收 %.0f 元 (超额 %.0f)", sid, other, got, over)
            reclaimed += got
            shortage -= got
        return reclaimed


def _allocator_used_of(sid):
    ctx = context_holder()
    log.info(
        "【DEMO·回调】used_of(%s) ctx=%s",
        sid,
        "OK" if ctx is not None else "None",
    )
    return _used_of(ctx, sid)


def _allocator_sell_some(sid, amount):
    ctx = context_holder()
    log.info(
        "【DEMO·回调】sell_some(%s, %.0f) ctx=%s",
        sid,
        amount,
        "OK" if ctx is not None else "None",
    )
    return _sell_some(ctx, sid, amount)


def _used_of(context, sid):
    if context is None:
        return 0.0
    total = 0.0
    for code in g.accounts[sid].holdings:
        pos = context.portfolio.positions.get(code)
        if pos:
            total += pos.value
    return total


def _sell_some(context, sid, amount):
    if context is None:
        return 0.0
    released = 0.0
    for code in list(g.accounts[sid].holdings):
        if amount - released <= 1e-9:
            break
        pos = context.portfolio.positions.get(code)
        if not pos or pos.closeable_amount <= 0:
            continue
        if pos.value <= (amount - released):
            o = order_target_value(code, 0)
            if o is not None:
                released += pos.value
                g.accounts[sid].holdings.remove(code)
                g.stock_owner.pop(code, None)
        else:
            target = pos.value - (amount - released)
            o = order_target_value(code, target)
            if o is not None:
                released += amount - released
    return released


def init_allocator():
    """创建 CapitalAllocator（进程内只需一次；模拟盘隔日重启后再调一次）。"""
    if g.__allocator is not None:
        return g.__allocator
    alloc = CapitalAllocator(
        strategies=g.accounts,
        used_of=_allocator_used_of,
        sell_some=_allocator_sell_some,
    )
    g.__allocator = alloc
    _CTX["allocator"] = alloc
    return alloc


def rebuild_allocator():
    """进程重启后 allocator 对象丢失时调用：清空引用并重建（不是每次 register 都该调）。"""
    g.__allocator = None
    _CTX.pop("allocator", None)
    return init_allocator()


class StrategyAccount(object):
    """普通 class 即可；聚宽 Py3 也有 dataclass，但 dict / 简单 class 更省心、更好 pickle。"""
    __slots__ = ("base_ratio", "priority", "holdings")

    def __init__(self, base_ratio, priority):
        self.base_ratio = base_ratio
        self.priority = priority
        self.holdings = []


def register_strategy(sid, base_ratio, priority):
    """只改 g.accounts，不重建 allocator（allocator 持有同一 dict 引用）。"""
    g.accounts[sid] = StrategyAccount(base_ratio, priority)


def demo_open(context, code, value, sid):
    if ENABLE_SET_CONTEXT:
        set_context(context)
    total = context.portfolio.total_value
    alloc = g.__allocator
    alloc.ensure_nominal(sid, total)
    grant = min(value, alloc.usable(sid, total))
    if grant < 3000:
        log.info("【DEMO·买入跳过】%s grant=%.0f 不足", code, grant)
        return
    o = order_value(code, grant)
    if o is not None and getattr(o, "filled", 0) > 0:
        g.accounts[sid].holdings.append(code)
        g.stock_owner[code] = sid
        log.info("【DEMO·买入】sid=%s %s 约 %.0f 元", sid, code, grant)


def _log_snapshot(context, tag):
    total = context.portfolio.total_value
    alloc = g.__allocator
    log.info("【DEMO·%s】dt=%s total=%.0f cash=%.0f idle=%.0f holder_is_ctx=%s",
             tag,
             context.current_dt,
             total,
             context.portfolio.available_cash,
             alloc.idle_pool(total),
             context_holder() is context)
    for sid in (SID_HI, SID_LO):
        acc = g.accounts[sid]
        log.info(
            "  %s pri=%d base=%.0f used=%.0f usable=%.0f holdings=%s",
            sid,
            acc.priority,
            total * acc.base_ratio,
            alloc.used_of(sid),
            alloc.usable(sid, total),
            acc.holdings,
        )


def _log_framework_state(context, where):
    """观察聚宽生命周期：g 持久化 vs 模块级 _CTX 是否还在。"""
    rp = context.run_params
    has_accounts = hasattr(g, "accounts") and g.accounts is not None
    accounts_len = len(g.accounts) if has_accounts else 0
    accounts_keys = list(g.accounts.keys()) if has_accounts and g.accounts else []
    has_allocator = getattr(g, "__allocator", None) is not None
    ctx_in_holder = context_holder() is not None
    holder_same = context_holder() is context if ctx_in_holder else False
    log.info(
        "【DEMO·框架|%s】run_type=%s freq=%s dt=%s",
        where, rp.type, rp.frequency, context.current_dt,
    )
    log.info(
        "  g.accounts: exists=%s len=%d keys=%s",
        has_accounts, accounts_len, accounts_keys,
    )
    log.info(
        "  g.__allocator: %s | _CTX.ctx: %s | holder_is_param_ctx: %s | demo_day=%s",
        has_allocator,
        ctx_in_holder,
        holder_same,
        getattr(g, "demo_day", "<unset>"),
    )
    if has_accounts and accounts_keys:
        for sid in accounts_keys:
            acc = g.accounts[sid]
            log.info(
                "    [%s] base=%.2f pri=%d holdings=%s",
                sid, acc.base_ratio, acc.priority, acc.holdings,
            )


def initialize(context):
    set_benchmark("510300.XSHG")
    set_option("use_real_price", True)
    set_slippage(PriceRelatedSlippage(0.0001), type="fund")
    set_order_cost(
        OrderCost(
            open_tax=0, close_tax=0,
            open_commission=0.0001, close_commission=0.0001,
            close_today_commission=0, min_commission=5,
        ),
        type="fund",
    )
    log.set_level("order", "error")

    g.accounts = {}
    g.stock_owner = {}
    g.__allocator = None
    g.demo_day = 0

    register_strategy(SID_HI, 0.6, priority=2)
    register_strategy(SID_LO, 0.4, priority=1)
    init_allocator()   # 全部注册完再创建一次 allocator

    if ENABLE_SET_CONTEXT:
        set_context(context)

    log.info("【DEMO·启动】ENABLE_SET_CONTEXT=%s", ENABLE_SET_CONTEXT)
    _log_framework_state(context, "initialize 结束")

    run_daily(demo_phase, "09:35")
    run_daily(demo_no_set_context_test, "10:00")


def process_initialize(context):
    """模拟/实盘每次进程启动都执行（含隔日重启）；回测里也会跑一次（在 initialize 之后）。"""
    _log_framework_state(context, "process_initialize 入口")

    has_accounts = hasattr(g, "accounts") and g.accounts
    if not has_accounts:
        log.warn("【DEMO·进程恢复】g.accounts 不存在或为空 → 走「首次注册」分支（正常仅 initialize 后不应出现）")
        g.accounts = {}
        g.stock_owner = {}
        register_strategy(SID_HI, 0.6, priority=2)
        register_strategy(SID_LO, 0.4, priority=1)
        init_allocator()
    else:
        log.info(
            "【DEMO·进程恢复】g.accounts 已存在(len=%d) → 只 rebuild_allocator，保留 holdings",
            len(g.accounts),
        )
        rebuild_allocator()

    if ENABLE_SET_CONTEXT:
        set_context(context)

    _log_framework_state(context, "process_initialize 结束")


def demo_phase(context):
    if ENABLE_SET_CONTEXT:
        set_context(context)

    g.demo_day = getattr(g, "demo_day", 0) + 1
    day = g.demo_day
    log.info("=" * 50)
    log.info("【DEMO·阶段】第 %d 个交易日", day)
    _log_snapshot(context, "阶段前")

    # 第 1 天：低优先级先满仓借位（占用 > 名义 40%）
    if day == 1:
        log.info("【DEMO·阶段说明】S_lo 买入 500ETF，预期占用超过名义 40%%")
        demo_open(context, ETF_B, 80000, SID_LO)

    # 第 3 天：高优先级要买，触发 ensure_nominal 挤占 S_lo
    elif day == 3:
        log.info("【DEMO·阶段说明】S_hi 买入 300ETF，应触发 ensure_nominal 挤占 S_lo")
        demo_open(context, ETF_A, 50000, SID_HI)

    # 第 5 天：仅观察，不再下单
    elif day == 5:
        log.info("【DEMO·阶段说明】仅观察容器状态，不下单")

    _log_snapshot(context, "阶段后")


def demo_no_set_context_test(context):
    """对比实验：本函数故意不 set_context，看回调里 context_holder 是否仍是上一时刻的引用。"""
    log.info("-" * 40)
    log.info("【DEMO·对比】本 bar 未调用 set_context；holder is 入参 context? %s",
             context_holder() is context)
    if context_holder() is not None:
        log.info("【DEMO·对比】holder.current_dt=%s  入参 current_dt=%s",
                 context_holder().current_dt,
                 context.current_dt)
    # 直接调用 used_of，观察日志里 ctx=OK 还是 None（取决于 ENABLE_SET_CONTEXT 与是否曾 set）
    g.__allocator.used_of(SID_LO)
