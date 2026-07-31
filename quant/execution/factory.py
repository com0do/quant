from __future__ import annotations

from quant.config import AppConfig, resolve_gateway_settings
from quant.execution.broker import BrokerAdapter
from quant.execution.paper_broker import PaperBroker
from quant.execution.qmt_http_broker import QmtHttpBroker


def build_broker(cfg: AppConfig) -> BrokerAdapter:
    kind = str(getattr(cfg.daemon, "broker_type", "paper")).strip().lower()
    if kind == "qmt_http":
        gs = resolve_gateway_settings(cfg)
        return QmtHttpBroker(
            base_url=gs.base_url,
            token=gs.token,
            timeout_sec=gs.timeout_sec,
            account_id=gs.account_id,
        )
    return PaperBroker(initial_cash=1_000_000.0)
