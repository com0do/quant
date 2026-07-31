from __future__ import annotations

from functools import wraps
import hmac
import time
from typing import Any, Callable, Coroutine

from fastapi import Depends, FastAPI, Header, HTTPException

from xtquant_gateway.core import GatewayCore, to_dict
from xtquant_gateway.models import AccountReq, CancelReq, FillsReq, OrderReq, OrderStatusReq, PositionsReq, QuoteReq
from xtquant_gateway.settings import GatewaySettings


def create_app(cfg: GatewaySettings) -> FastAPI:
    if not cfg.account_id or not cfg.mini_qmt_path or not cfg.token:
        raise RuntimeError("XTG_ACCOUNT_ID, XTG_MINI_QMT_PATH, XTG_TOKEN are required")
    core = GatewayCore(cfg)
    core.start()
    app = FastAPI(title="xtquant-gateway", version="2.0")

    async def verify_token(
        authorization: str | None = Header(default=None),
        x_token: str | None = Header(default=None),
    ) -> str:
        got = ""
        if authorization and authorization.lower().startswith("bearer "):
            got = authorization[7:].strip()
        elif x_token:
            got = x_token.strip()
        if not hmac.compare_digest(got, cfg.token):
            raise HTTPException(status_code=401, detail="invalid token")
        return got

    def ok(data: Any) -> dict[str, Any]:
        return {"success": True, "data": to_dict(data)}

    def meter(name: str) -> Callable:
        def deco(fn: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
            @wraps(fn)
            async def wrapped(*args, **kwargs):
                t0 = time.perf_counter()
                status = "ok"
                try:
                    return await fn(*args, **kwargs)
                except HTTPException as exc:
                    status = f"http_{exc.status_code}"
                    raise
                except Exception:
                    status = "error"
                    raise
                finally:
                    core.record_metric(name, (time.perf_counter() - t0) * 1000.0, status)

            return wrapped

        return deco

    @app.get("/health/live")
    @meter("health_live")
    async def health_live(token: str = Depends(verify_token)):
        return ok({"status": "ok"})

    @app.get("/health/trader")
    @meter("health_trader")
    async def health_trader(token: str = Depends(verify_token)):
        try:
            _ = core.trader.query_stock_asset(core.account)
            return ok({"alive": True, "reconnect_count": core.metrics_summary()["reconnect_count"]})
        except Exception as exc:
            r = core.reconnect()
            return ok({"alive": bool(r), "reason": str(exc), "reconnect_count": core.metrics_summary()["reconnect_count"]})

    @app.get("/health/quote")
    @meter("health_quote")
    async def health_quote(token: str = Depends(verify_token)):
        reason = ""
        try:
            _ = core.quote(cfg.quote_probe_code)
        except Exception as exc:
            reason = str(exc)
        rr = core.ensure_fresh_quote(cfg.quote_probe_code)
        return ok({"ok": rr.ok, "reason": reason or rr.reason, "probe_code": cfg.quote_probe_code})

    @app.get("/metrics/summary")
    @meter("metrics_summary")
    async def metrics_summary(token: str = Depends(verify_token)):
        return ok(core.metrics_summary())

    @app.post("/quote")
    @meter("quote")
    async def quote(req: QuoteReq, token: str = Depends(verify_token)):
        try:
            return ok(core.quote(req.code))
        except Exception as exc:
            core.reconnect()
            raise HTTPException(status_code=502, detail=f"quote failed: {exc}") from exc

    @app.post("/account")
    @meter("account")
    async def account(req: AccountReq, token: str = Depends(verify_token)):
        try:
            return ok(core.trader.query_stock_asset(core.account))
        except Exception:
            if not core.reconnect():
                raise
            return ok(core.trader.query_stock_asset(core.account))

    @app.post("/positions")
    @meter("positions")
    async def positions(req: PositionsReq, token: str = Depends(verify_token)):
        try:
            return ok(core.trader.query_stock_positions(core.account))
        except Exception:
            if not core.reconnect():
                raise
            return ok(core.trader.query_stock_positions(core.account))

    @app.post("/order")
    @meter("order")
    async def order(req: OrderReq, token: str = Depends(verify_token)):
        rr = core.check_risk(req.side, req.code, int(req.quantity), float(req.price or 0.0), req.price_type)
        if not rr.ok:
            raise HTTPException(status_code=400, detail=rr.reason)
        fq = core.ensure_fresh_quote(req.code)
        if not fq.ok:
            raise HTTPException(status_code=503, detail=f"stale quote guard: {fq.reason}")
        try:
            data = core.submit_order(
                code=req.code,
                side=req.side,
                quantity=int(req.quantity),
                price=req.price,
                price_type=req.price_type,
                strategy_name=req.strategy_name,
                order_remark=req.order_remark,
                client_order_id=str(req.client_order_id or ""),
            )
            return ok(data)
        except Exception:
            if not core.reconnect():
                raise
            data = core.submit_order(
                code=req.code,
                side=req.side,
                quantity=int(req.quantity),
                price=req.price,
                price_type=req.price_type,
                strategy_name=req.strategy_name,
                order_remark=req.order_remark,
                client_order_id=str(req.client_order_id or ""),
            )
            return ok(data)

    @app.post("/cancel")
    @meter("cancel")
    async def cancel(req: CancelReq, token: str = Depends(verify_token)):
        oid = int(str(req.order_id))
        try:
            r = core.trader.cancel_order_stock(core.account, oid)
        except Exception:
            if not core.reconnect():
                raise
            r = core.trader.cancel_order_stock(core.account, oid)
        ok_flag = int(r) == 0
        if ok_flag:
            row = {"order_id": str(req.order_id), "status": "canceled"}
            core.store.upsert_order(str(req.order_id), row)
        return ok({"ok": ok_flag, "cancel_result": int(r), "order_id": str(req.order_id)})

    @app.post("/order/status")
    @meter("order_status")
    async def order_status(req: OrderStatusReq, token: str = Depends(verify_token)):
        try:
            q = core.trader.query_stock_order(core.account, int(str(req.order_id)))
            if q is not None:
                d = to_dict(q)
                oid = str(d.get("order_id") or d.get("m_nOrderID") or req.order_id)
                core.store.upsert_order(oid, d)
                return ok(d)
        except Exception:
            if core.reconnect():
                try:
                    q2 = core.trader.query_stock_order(core.account, int(str(req.order_id)))
                    if q2 is not None:
                        d2 = to_dict(q2)
                        oid2 = str(d2.get("order_id") or d2.get("m_nOrderID") or req.order_id)
                        core.store.upsert_order(oid2, d2)
                        return ok(d2)
                except Exception:
                    pass
        raise HTTPException(status_code=404, detail="order not found")

    @app.post("/fills")
    @meter("fills")
    async def fills(req: FillsReq, token: str = Depends(verify_token)):
        try:
            rows = [to_dict(x) for x in (core.trader.query_stock_trades(core.account) or [])]
        except Exception:
            if not core.reconnect():
                raise
            rows = [to_dict(x) for x in (core.trader.query_stock_trades(core.account) or [])]
        if req.since_ts:
            rows = [x for x in rows if str(x.get("traded_time", "")) >= req.since_ts]
        for x in rows:
            key = "|".join(
                [
                    str(x.get("order_id", "")),
                    str(x.get("stock_code", "")),
                    str(x.get("traded_time", "")),
                    str(x.get("traded_volume", x.get("volume", ""))),
                    str(x.get("traded_price", x.get("price", ""))),
                ]
            )
            core.store.insert_fill(key, x)
        return ok(rows)

    return app
