from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass
class GatewaySettings:
    account_id: str
    mini_qmt_path: str
    token: str
    mini_qmt_exe_path: str = ""
    host: str = "127.0.0.1"
    port: int = 18080
    state_db_path: str = "data/xtquant_gateway.db"
    stale_quote_guard_enable: bool = True
    stale_quote_max_age_sec: float = 8.0
    quote_probe_code: str = "000001.SZ"
    lot_size: int = 100
    reserve_cash_pct: float = 0.02
    max_single_order_notional: float = 300000.0
    max_daily_order_count: int = 300
    max_daily_notional: float = 20000000.0

    @staticmethod
    def _load_env_file(path: str) -> None:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            # Keep explicit process env higher priority than .env defaults.
            if key and key not in os.environ:
                os.environ[key] = value

    @staticmethod
    def from_env(env_file: str = "") -> "GatewaySettings":
        default_env = str((Path(__file__).resolve().parents[1] / "deploy" / "gateway_server" / ".env"))
        GatewaySettings._load_env_file(env_file or os.getenv("XTG_ENV_FILE", "").strip() or default_env)

        def _b(name: str, default: bool) -> bool:
            v = os.getenv(name)
            if v is None:
                return default
            return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

        return GatewaySettings(
            account_id=os.getenv("XTG_ACCOUNT_ID", "").strip(),
            mini_qmt_path=os.getenv("XTG_MINI_QMT_PATH", "").strip(),
            mini_qmt_exe_path=os.getenv("XTG_MINI_QMT_EXE_PATH", "").strip(),
            token=os.getenv("XTG_TOKEN", "").strip(),
            host=os.getenv("XTG_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.getenv("XTG_PORT", "18080")),
            state_db_path=os.getenv("XTG_STATE_DB_PATH", "data/xtquant_gateway.db").strip(),
            stale_quote_guard_enable=_b("XTG_STALE_QUOTE_GUARD_ENABLE", True),
            stale_quote_max_age_sec=float(os.getenv("XTG_STALE_QUOTE_MAX_AGE_SEC", "8.0")),
            quote_probe_code=os.getenv("XTG_QUOTE_PROBE_CODE", "000001.SZ").strip() or "000001.SZ",
            lot_size=int(os.getenv("XTG_LOT_SIZE", "100")),
            reserve_cash_pct=float(os.getenv("XTG_RESERVE_CASH_PCT", "0.02")),
            max_single_order_notional=float(os.getenv("XTG_MAX_SINGLE_ORDER_NOTIONAL", "300000")),
            max_daily_order_count=int(os.getenv("XTG_MAX_DAILY_ORDER_COUNT", "300")),
            max_daily_notional=float(os.getenv("XTG_MAX_DAILY_NOTIONAL", "20000000")),
        )
