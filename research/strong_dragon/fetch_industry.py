"""Snapshot of the Shenwan (SW 2021) level-1 / level-2 industry of every A-share, from Sina's quote centre.

The classification is today's, not point-in-time; industry membership changes slowly, which is good enough for
the sector study in sector_study.py. On JoinQuant, jq_strong_dragon.py uses get_industry() instead.

usage: uv run python research/strong_dragon/fetch_industry.py
Writes research/strong_dragon/industry_sw.csv (code, sw1, sw2).
"""
from __future__ import annotations

import json
import re
import time

import pandas as pd
import requests

from sd_data import HERE, REPO

API = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


def nodes(level: str) -> dict[str, str]:
    """Node code -> name. Leaf entries look like ["<escaped name>", "", "sw1_770000"]."""
    txt = requests.get(f"{API}.getHQNodes", headers=HEADERS, timeout=30).text
    pairs = re.findall(r'\["([^"\[\]]+)","[^"]*","(' + level + r'_\d+)"\]', txt)
    return {code: json.loads(f'"{name}"') for name, code in pairs}


def members(node: str) -> list[str]:
    out, page = [], 1
    while True:
        r = requests.get(f"{API}.getHQNodeData", headers=HEADERS, timeout=30,
                         params={"page": page, "num": 100, "sort": "symbol", "asc": 1, "node": node})
        rows = json.loads(r.text) if r.text.strip().startswith("[") else []
        out += [x["symbol"] for x in rows]
        if len(rows) < 100:
            return out
        page += 1
        time.sleep(0.3)


def to_jq(symbol: str) -> str | None:
    ex, num = symbol[:2], symbol[2:]
    return {"sh": f"{num}.XSHG", "sz": f"{num}.XSHE", "bj": f"{num}.BJ"}.get(ex)


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except Exception:
        pass
    rows = {}
    for level in ("sw1", "sw2"):
        for node, name in nodes(level).items():
            for sym in members(node):
                code = to_jq(sym)
                if code:
                    rows.setdefault(code, {})[level] = name
            time.sleep(0.3)
        print(level, "done:", sum(1 for v in rows.values() if level in v), "stocks")
    df = pd.DataFrame([{"code": c, **v} for c, v in rows.items()]).sort_values("code")
    df.to_csv(HERE / "industry_sw.csv", index=False)
    print(f"wrote {HERE / 'industry_sw.csv'}: {len(df)} stocks, {df['sw1'].nunique()} SW1, {df['sw2'].nunique()} SW2")


if __name__ == "__main__":
    main()
