from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
import time
from typing import Any

from xtquant import xtconstant, xtdata
from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount

from xtquant_gateway.persistence import SQLiteStore
from xtquant_gateway.settings import GatewaySettings


def norm_code(code: str) -> str:
    c = str(code or "").strip()
    if "." in c:
        return c
    if c.startswith("6"):
        return f"{c}.SH"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    if c.startswith(("8", "4")):
        return f"{c}.BJ"
    return c


def to_dict(obj: Any) -> Any:
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    out: dict[str, Any] = {}
    for k in obj.__dir__():
        if str(k).startswith("_"):
            continue
        try:
            v = getattr(obj, k)
        except Exception:
            continue
        if callable(v):
            continue
        out[str(k)] = to_dict(v)
    return out


@dataclass
class RiskResult:
    ok: bool
    reason: str = ""


class GatewayCore:
    def __init__(self, cfg: GatewaySettings) -> None:
        self.cfg = cfg
        self.store = SQLiteStore(cfg.state_db_path)
        self.trader = XtQuantTrader(cfg.mini_qmt_path, int(time.time() * 1000) % 999999 + 100000)
        self.account = StockAccount(cfg.account_id)
        self.token = cfg.token
        self._lock = Lock()
        self._quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._idem: dict[str, dict[str, Any]] = {}
        self._fills: list[dict[str, Any]] = []
        self._daily_key = datetime.now().strftime("%Y-%m-%d")
        self._daily_order_count = 0
        self._daily_notional = 0.0
        self._metrics_lat: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=2000))
        self._metrics_status: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._reconnect_count = 0
        self._last_reconnect_error = ""
        self._restore_idempotency_cache()

    def _restore_idempotency_cache(self) -> None:
        try:
            restored = self.store.load_recent_idempotency(limit=20000)
        except Exception:
            restored = {}
        if not restored:
            return
        with self._lock:
            self._idem.update(restored)

    def start(self) -> None:
        self.trader.start()
        self.trader.connect()
        self.trader.subscribe(self.account)

    def reconnect(self) -> bool:
        try:
            self.trader.connect()
            self.trader.subscribe(self.account)
            self._reconnect_count += 1
            self._last_reconnect_error = ""
            return True
        except Exception as exc:
            self._last_reconnect_error = str(exc)
            return False

    def record_metric(self, endpoint: str, latency_ms: float, status: str) -> None:
        with self._lock:
            self._metrics_lat[endpoint].append(float(latency_ms))
            self._metrics_status[endpoint][status] += 1
        self.store.insert_metric(endpoint, latency_ms, status)

    def metrics_summary(self) -> dict[str, Any]:
        def pct(xs: list[float], p: float) -> float:
            if not xs:
                return 0.0
            ys = sorted(xs)
            i = int(round((len(ys) - 1) * p))
            return float(ys[max(0, min(i, len(ys) - 1))])

        out: dict[str, Any] = {}
        with self._lock:
            for ep, dq in self._metrics_lat.items():
                xs = list(dq)
                out[ep] = {
                    "count": len(xs),
                    "p50_ms": pct(xs, 0.50),
                    "p95_ms": pct(xs, 0.95),
                    "p99_ms": pct(xs, 0.99),
                    "status": dict(self._metrics_status.get(ep, {})),
                }
        return {
            "api": out,
            "reconnect_count": self._reconnect_count,
            "last_reconnect_error": self._last_reconnect_error,
        }

    def quote(self, code: str) -> dict[str, Any]:
        c = norm_code(code)
        data = xtdata.get_full_tick([c]) or {}
        row = data.get(c, {}) if isinstance(data, dict) else {}
        if not row:
            raise RuntimeError("empty quote")
        with self._lock:
            self._quote_cache[c] = (time.time(), to_dict(row))
        return to_dict(row)

    def ensure_fresh_quote(self, code: str) -> RiskResult:
        if not self.cfg.stale_quote_guard_enable:
            return RiskResult(ok=True)
        c = norm_code(code)
        now = time.time()
        ts = None
        with self._lock:
            cached = self._quote_cache.get(c)
            if cached:
                ts = cached[0]
        if ts is None or now - ts > self.cfg.stale_quote_max_age_sec:
            try:
                self.quote(c)
            except Exception as exc:
                return RiskResult(ok=False, reason=f"quote refresh failed: {exc}")
            with self._lock:
                cached2 = self._quote_cache.get(c)
                ts = cached2[0] if cached2 else None
        if ts is None:
            return RiskResult(ok=False, reason="quote unavailable")
        age = now - ts
        if age > self.cfg.stale_quote_max_age_sec:
            return RiskResult(ok=False, reason=f"stale quote age={age:.3f}s")
        return RiskResult(ok=True)

    def check_risk(self, side: str, code: str, quantity: int, price: float, price_type: str) -> RiskResult:
        if quantity <= 0:
            return RiskResult(False, "quantity must be positive")
        if quantity % max(1, self.cfg.lot_size) != 0:
            return RiskResult(False, f"quantity must be multiple of {self.cfg.lot_size}")
        if price_type == "limit" and price <= 0:
            return RiskResult(False, "limit order requires positive price")
        asset = to_dict(self.trader.query_stock_asset(self.account))
        cash = float(asset.get("cash", 0.0))
        frozen_cash = float(asset.get("frozen_cash", 0.0))
        total_asset = float(asset.get("total_asset", asset.get("equity", max(cash, 1.0))))
        available_cash = max(0.0, cash - frozen_cash)
        notional = max(0.0, float(price)) * int(quantity)
        day = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            if day != self._daily_key:
                self._daily_key = day
                self._daily_order_count = 0
                self._daily_notional = 0.0
            if self._daily_order_count >= self.cfg.max_daily_order_count:
                return RiskResult(False, "daily order count limit reached")
            if self._daily_notional + notional > self.cfg.max_daily_notional:
                return RiskResult(False, "daily notional limit reached")
        if side == "buy":
            if notional > self.cfg.max_single_order_notional:
                return RiskResult(False, "single order notional limit reached")
            reserve_need = total_asset * self.cfg.reserve_cash_pct
            if available_cash - notional < reserve_need:
                return RiskResult(False, "insufficient available cash after reserve")
        else:
            pos = self.trader.query_stock_positions(self.account) or []
            avail = 0
            c = norm_code(code)
            for p in pos:
                pd = to_dict(p)
                if str(pd.get("stock_code", "")).upper() == c.upper():
                    avail = int(pd.get("can_use_volume", pd.get("volume", 0)) or 0)
                    break
            if quantity > avail:
                return RiskResult(False, "sell quantity exceeds available position")
        return RiskResult(True)

    def submit_order(self, code: str, side: str, quantity: int, price: float | None, price_type: str, strategy_name: str, order_remark: str, client_order_id: str) -> dict[str, Any]:
        if client_order_id:
            with self._lock:
                cached = self._idem.get(client_order_id)
            if cached:
                return dict(cached)
            persisted = self.store.get_idempotency(client_order_id)
            if persisted:
                with self._lock:
                    self._idem[client_order_id] = dict(persisted)
                return dict(persisted)
        c = norm_code(code)
        px_type = int(xtconstant.FIX_PRICE if str(price_type).lower() == "limit" else xtconstant.LATEST_PRICE)
        px = float(price or 0.0) if px_type == int(xtconstant.FIX_PRICE) else 0.0
        side_code = int(xtconstant.STOCK_BUY if side == "buy" else xtconstant.STOCK_SELL)
        oid = self.trader.order_stock(self.account, c, side_code, int(quantity), px_type, px, strategy_name or "", order_remark or "")
        res = {
            "order_id": str(oid),
            "status": "new",
            "code": c,
            "side": side,
            "quantity": int(quantity),
            "price": px,
            "client_order_id": client_order_id or "",
        }
        with self._lock:
            self._orders[str(oid)] = dict(res)
            if client_order_id:
                self._idem[client_order_id] = dict(res)
            self._daily_order_count += 1
            self._daily_notional += px * int(quantity)
        self.store.upsert_order(str(oid), dict(res))
        if client_order_id:
            self.store.upsert_idempotency(client_order_id, dict(res))
        return res

