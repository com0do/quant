from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any


DEFAULT_QUEUE_KEY = "jq:signals"
DEFAULT_STALE_AFTER_SEC = 900


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(value: str) -> datetime:
    s = str(value or "").strip()
    if not s:
        return datetime.now(timezone.utc)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def make_client_order_id(
    strategy_name: str,
    leg_name: str,
    action: str,
    code: str,
    bar_time: str,
    target_value: float | None = None,
) -> str:
    payload = f"{strategy_name}|{leg_name}|{action}|{code}|{bar_time}|{target_value or 0.0:.2f}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"jq-{leg_name[:12]}-{digest}"


def _canonical_payload_for_sign(payload: dict[str, Any]) -> bytes:
    body = {k: v for k, v in dict(payload or {}).items() if k != "signature"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_signal_signature(payload: dict[str, Any], secret: str) -> str:
    key = str(secret or "").encode("utf-8")
    if not key:
        return ""
    body = _canonical_payload_for_sign(payload)
    return hmac.new(key, body, digestmod=hashlib.sha256).hexdigest()


def attach_signal_signature(payload: dict[str, Any], secret: str) -> dict[str, Any]:
    out = dict(payload or {})
    sig = make_signal_signature(out, secret)
    if sig:
        out["signature"] = sig
    return out


def verify_signal_signature(payload: dict[str, Any], secret: str) -> bool:
    sec = str(secret or "")
    if not sec:
        return True
    got = str((payload or {}).get("signature") or "")
    if not got:
        return False
    expected = make_signal_signature(payload, sec)
    return bool(expected) and hmac.compare_digest(got, expected)


def signal_is_stale(generated_at: str, stale_after_sec: int) -> bool:
    age = datetime.now(timezone.utc) - parse_ts(generated_at)
    return age.total_seconds() > max(1, int(stale_after_sec))


def quote_last_price(quote: dict[str, Any]) -> float | None:
    candidates = [
        quote.get("last_price"),
        quote.get("lastPrice"),
        quote.get("last"),
        quote.get("price"),
        quote.get("close"),
    ]
    for value in candidates:
        try:
            px = float(value)
        except (TypeError, ValueError):
            continue
        if px > 0:
            return px
    return None


def round_price_for_code(code: str, price: float) -> float:
    c = str(code or "")
    digits = 3 if c.startswith(("1", "5")) else 2
    return round(float(price), digits)


def lot_target_quantity(target_value: float, price: float, lot_size: int = 100) -> int:
    if target_value <= 0 or price <= 0:
        return 0
    shares = int(float(target_value) / float(price))
    lot = max(1, int(lot_size))
    return (shares // lot) * lot


@dataclass
class JqSignal:
    strategy_name: str
    leg_name: str
    action: str
    code: str
    target_value: float | None = None
    target_pct: float | None = None
    generated_at: str = ""
    bar_time: str = ""
    client_order_id: str = ""
    price_mode: str = "latest"
    note: str = ""
    source: str = "joinquant"
    schema_version: str = "v1"
    signature: str = ""

    def __post_init__(self) -> None:
        self.strategy_name = str(self.strategy_name or "")
        self.leg_name = str(self.leg_name or "")
        self.action = str(self.action or "target_value")
        self.code = str(self.code or "")
        self.generated_at = str(self.generated_at or now_utc_iso())
        self.bar_time = str(self.bar_time or self.generated_at)
        self.client_order_id = str(
            self.client_order_id
            or make_client_order_id(
                self.strategy_name,
                self.leg_name,
                self.action,
                self.code,
                self.bar_time,
                self.target_value,
            )
        )
        self.price_mode = str(self.price_mode or "latest").lower()

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "JqSignal":
        data = dict(payload or {})
        return cls(
            strategy_name=str(data.get("strategy_name") or ""),
            leg_name=str(data.get("leg_name") or ""),
            action=str(data.get("action") or "target_value"),
            code=str(data.get("code") or ""),
            target_value=(None if data.get("target_value") in (None, "") else float(data.get("target_value"))),
            target_pct=(None if data.get("target_pct") in (None, "") else float(data.get("target_pct"))),
            generated_at=str(data.get("generated_at") or ""),
            bar_time=str(data.get("bar_time") or ""),
            client_order_id=str(data.get("client_order_id") or ""),
            price_mode=str(data.get("price_mode") or "latest"),
            note=str(data.get("note") or ""),
            source=str(data.get("source") or "joinquant"),
            schema_version=str(data.get("schema_version") or "v1"),
            signature=str(data.get("signature") or ""),
        )

