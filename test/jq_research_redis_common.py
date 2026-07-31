"""
JoinQuant 研究环境可复用的 Redis 信号发送公共模块。

依赖:
- redis-py (import redis)

建议:
- 将本文件放到聚宽「研究环境」可导入目录
- 各策略通过 `from jq_research_redis_common import ...` 统一复用
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import redis


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_client_order_id(strategy_name: str, leg_name: str, action: str, code: str, bar_time: str, target_value: float) -> str:
    payload = "%s|%s|%s|%s|%.2f" % (
        strategy_name,
        leg_name,
        action,
        "%s|%s" % (code, bar_time),
        float(target_value or 0.0),
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return "jq-%s-%s" % (leg_name[:10], digest)


def _canonical_payload_for_sign(payload: dict) -> bytes:
    body = {k: v for k, v in dict(payload or {}).items() if k != "signature"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_signal_signature(payload: dict, secret: str) -> str:
    sec = str(secret or "").encode("utf-8")
    if not sec:
        return ""
    return hmac.new(sec, _canonical_payload_for_sign(payload), digestmod=hashlib.sha256).hexdigest()


def build_target_value_signal(
    *,
    strategy_name: str,
    leg_name: str,
    code: str,
    target_value: float,
    bar_time: str,
    price_mode: str = "latest",
    note: str = "",
    hmac_secret: str = "",
) -> dict:
    action = "target_value"
    out = {
        "schema_version": "v1",
        "source": "joinquant",
        "strategy_name": str(strategy_name),
        "leg_name": str(leg_name),
        "action": action,
        "code": str(code),
        "target_value": float(target_value),
        "generated_at": utc_now_iso(),
        "bar_time": str(bar_time),
        "client_order_id": make_client_order_id(
            strategy_name=strategy_name,
            leg_name=leg_name,
            action=action,
            code=code,
            bar_time=bar_time,
            target_value=target_value,
        ),
        "price_mode": str(price_mode or "latest"),
        "note": str(note or ""),
    }
    sig = make_signal_signature(out, hmac_secret)
    if sig:
        out["signature"] = sig
    return out


class RedisSignalQueueClient:
    def __init__(self, *, host: str, port: int = 6379, password: str = "", db: int = 0, queue_key: str = "jq:signals",
                 use_tls: bool = True, timeout_sec: int = 5):
        if not str(host or "").strip():
            raise ValueError("redis host is required")
        self.host = str(host).strip()
        self.port = int(port)
        self.password = str(password or "")
        self.db = int(db)
        self.queue_key = str(queue_key or "jq:signals")
        self.use_tls = bool(use_tls)
        self.timeout_sec = int(timeout_sec)

    @classmethod
    def from_config(cls, cfg: dict) -> "RedisSignalQueueClient":
        return cls(
            host=cfg.get("host", ""),
            port=cfg.get("port", 6379),
            password=cfg.get("password", ""),
            db=cfg.get("db", 0),
            queue_key=cfg.get("queue_key", "jq:signals"),
            use_tls=cfg.get("use_tls", True),
            timeout_sec=cfg.get("timeout_sec", 5),
        )

    def push_message(self, message: dict) -> int:
        client = redis.Redis(
            host=self.host,
            port=self.port,
            password=self.password or None,
            db=self.db,
            ssl=self.use_tls,
            socket_timeout=self.timeout_sec,
            decode_responses=True,
        )
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        return int(client.lpush(self.queue_key, raw))
