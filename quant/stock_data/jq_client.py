from __future__ import annotations

import atexit
from contextlib import contextmanager
from functools import wraps
import importlib
import os
import warnings
from urllib.parse import urlparse

warnings.filterwarnings("ignore", category=Warning, module=r"jqdatasdk\.compat\.pickle_compat")


class JqClient:
    def __init__(self) -> None:
        self.jq = None
        self.logged_in = False
        self._restore_socket = None
        self._atexit_registered = False

    def _load_env(self) -> None:
        try:
            dotenv = importlib.import_module("dotenv")
        except Exception:
            return
        load_dotenv = getattr(dotenv, "load_dotenv", None)
        if callable(load_dotenv):
            load_dotenv()

    def _enable_proxy(self) -> None:
        if self._restore_socket is not None:
            return
        proxy = (
            os.getenv("JQ_PROXY")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy")
            or os.getenv("HTTP_PROXY")
            or os.getenv("http_proxy")
        )
        if not proxy:
            return
        try:
            import socket
            socks = importlib.import_module("socks")
        except Exception:
            return
        u = urlparse(proxy)
        if not u.hostname or not u.port:
            return
        scheme = (u.scheme or "http").lower()
        if scheme in ("socks5", "socks5h"):
            ptype = socks.SOCKS5
        elif scheme in ("socks4", "socks4a"):
            ptype = socks.SOCKS4
        else:
            ptype = socks.HTTP
        self._restore_socket = socket.socket
        socks.set_default_proxy(ptype, u.hostname, u.port, username=u.username, password=u.password)
        socket.socket = socks.socksocket

    def _disable_proxy(self) -> None:
        restore_socket = self._restore_socket
        if restore_socket is None:
            return
        try:
            import socket
            socket.socket = restore_socket
        finally:
            self._restore_socket = None
        try:
            socks = importlib.import_module("socks")
            socks.set_default_proxy()
        except Exception:
            pass

    def login(self) -> None:
        if self.logged_in:
            return
        self._load_env()
        self._enable_proxy()
        try:
            jq = importlib.import_module("jqdatasdk")

            name = os.getenv("CC_JQ_NAME")
            pw = os.getenv("CC_JQ_PW")
            if not name or not pw:
                raise RuntimeError("missing CC_JQ_NAME/CC_JQ_PW")
            jq.auth(name, pw)
        except Exception:
            self._disable_proxy()
            raise
        self.jq = jq
        self.logged_in = True
        if not self._atexit_registered:
            atexit.register(self.logout)
            self._atexit_registered = True

    def logout(self) -> None:
        if self.logged_in and self.jq is not None:
            try:
                self.jq.logout()
            except Exception:
                pass
        self.logged_in = False
        self.jq = None
        self._disable_proxy()

    @contextmanager
    def session(self):
        self.login()
        try:
            yield self.jq
        finally:
            self.logout()

    def with_session(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with self.session():
                return func(*args, **kwargs)

        return wrapper

    def query_count(self) -> dict:
        self.login()
        try:
            return dict(self.jq.get_query_count())
        except Exception:
            return {}

    def get_index_stocks(self, index_code: str, date: str) -> list[str]:
        self.login()
        return list(self.jq.get_index_stocks(index_code, date=date) or [])

    def get_price_daily(self, security: str, start_date: str, end_date: str):
        self.login()
        return self.jq.get_price(
            security=security,
            start_date=start_date,
            end_date=end_date,
            frequency="daily",
            fields=["open", "high", "low", "close", "volume", "money", "paused"],
            fq="post",
            panel=False,
        )

    def get_price_minute(self, security: str, start_date: str, end_date: str):
        self.login()
        return self.jq.get_price(
            security=security,
            start_date=start_date,
            end_date=end_date,
            frequency="1m",
            fields=["open", "high", "low", "close", "volume", "money"],
            fq="post",
            panel=False,
        )

    def get_fundamentals_bundle(self, codes: list[str], date: str):
        self.login()
        if not codes:
            return None
        jqdatasdk = importlib.import_module("jqdatasdk")
        indicator = jqdatasdk.indicator
        query = jqdatasdk.query
        valuation = jqdatasdk.valuation
        q = query(
            valuation.code,
            valuation.pe_ratio,
            valuation.pb_ratio,
            valuation.turnover_ratio,
            valuation.market_cap,
            indicator.roe,
        ).filter(valuation.code.in_(codes))
        return self.jq.get_fundamentals(q, date=date)
