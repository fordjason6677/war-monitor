#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FX module — قیمت لحظه‌ای دلار (تومان)
  bonbast  : بازار آزاد (خرید/فروش) — از /json با توکن سرور
  wallex   : تتر/تومان زنده (orderbook) — میانگین معامله
  official : نرخ رسمی IRR (روزانه) — open.er-api.com

تاریخچه: fx.jsonl  (هر نوبت یک خط JSON)
"""

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
FXLOG = os.path.join(BASE, "fx.jsonl")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
TEHRAN = timezone(timedelta(hours=3, minutes=30))


def bonbast():
    """بازار آزاد بانبست — خرید/فروش دلار به تومان"""
    s = requests.Session()
    s.headers.update(dict(UA, **{"X-Requested-With": "XMLHttpRequest",
                                 "Referer": "https://www.bonbast.com/"}))
    home = s.get("https://www.bonbast.com/", timeout=15)
    m = re.search(r'param:\s*"([^"]+)"', home.text)
    if not m:
        return {"src": "bonbast", "err": "param not found"}
    r = s.post("https://www.bonbast.com/json", data={"param": m.group(1)}, timeout=15)
    d = r.json()
    if "usd1" not in d:
        return {"src": "bonbast", "err": f"no usd: {str(d)[:80]}"}
    return {"src": "bonbast", "buy": int(d["usd1"]), "sell": int(d["usd2"]),
            "eur": int(d.get("eur1") or 0), "gold18": int(d.get("gol18") or 0),
            "btc": float(d.get("bitcoin") or 0), "ounce": float(d.get("ounce") or 0),
            "updated": d.get("created")}


def wallex():
    """تتر/تومان زنده از والکس (میانگین معامله + خرید/فروش)"""
    r = requests.get("https://api.wallex.ir/v1/markets", headers=UA, timeout=15)
    sym = r.json()["result"]["symbols"].get("USDTTMN")
    if not sym:
        return {"src": "wallex", "err": "USDTTMN missing"}
    st = sym.get("stats") or {}
    f = lambda k: float(st[k]) if st.get(k) not in (None, "-", "") else None
    return {"src": "wallex USDT/TMN", "last": f("lastPrice"),
            "bid": f("bidPrice"), "ask": f("askPrice")}


def official():
    """نرخ رسمی دلار (روزانه)"""
    r = requests.get("https://open.er-api.com/v6/latest/USD", headers=UA, timeout=15)
    d = r.json()
    if d.get("result") != "success":
        return {"src": "er-api", "err": d.get("error-type", "?")}
    irr = d["rates"]["IRR"]
    return {"src": "er-api (official)", "irr": irr, "toman": round(irr / 10),
            "updated": d.get("time_last_update_utc")}


def snapshot(log=True):
    """هر سه منبع موازی بگیر، تاریخچه رو ثبت کن"""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        b, w, o = list(ex.map(lambda f: f(), (bonbast, wallex, official)))
    now = datetime.now(timezone.utc)
    rec = {"ts": now.isoformat(timespec="seconds"),
           "tehran": now.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M:%S"),
           "bonbast": b, "wallex": w, "official": o}
    prev = last()
    if log:
        with open(FXLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec, prev


def last():
    if not os.path.exists(FXLOG):
        return None
    with open(FXLOG, encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except Exception:
        return None


def _delta(cur, prev):
    if not prev:
        return ""
    pb = (prev.get("bonbast") or {}).get("buy")
    cb = (cur.get("bonbast") or {}).get("buy")
    if not pb or not cb:
        return ""
    d = cb - pb
    pct = d / pb * 100
    sign = "+" if d > 0 else ""
    arrow = "▲" if d > 0 else ("▼" if d < 0 else "=")
    return f"  {arrow} {sign}{d:,} تومان نسبت به آخرین نوبت ({sign}{pct:.2f}%)"


def fmt(rec, prev=None):
    L = []
    b, w, o = rec.get("bonbast") or {}, rec.get("wallex") or {}, rec.get("official") or {}
    L.append("💰 نرخ لحظه‌ای ارز (تومان)")
    L.append(f"زمان تهران: {rec['tehran']}")
    if "buy" in b:
        L.append(f"  بازار آزاد (بانبست): فروش {b['sell']:,} | خرید {b['buy']:,}"
                 f"{'  ← ' + b['updated'] if b.get('updated') else ''}")
        dl = _delta(rec, prev)
        if dl:
            L.append(dl)
    else:
        L.append(f"  بازار آزاد: ❌ {b.get('err')}")
    if w.get("last"):
        L.append(f"  تتر/تومان زنده (والکس): {w['last']:,.0f}  "
                 f"(خرید {w['bid']:,.0f} / فروش {w['ask']:,.0f})")
    else:
        L.append(f"  تتر/تومان: ❌ {w.get('err')}")
    if o.get("toman"):
        L.append(f"  نرخ رسمی: {o['toman']:,} تومان ({o['irr']:,.0f} ریال)"
                 f"{'  ← ' + str(o.get('updated'))[:16] if o.get('updated') else ''}")
    else:
        L.append(f"  نرخ رسمی: ❌ {o.get('err')}")
    return "\n".join(L)


def history_points(n=12):
    """آخرین n نقطه از تاریخچه برای رسم روند"""
    if not os.path.exists(FXLOG):
        return []
    pts = []
    with open(FXLOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            buy = (r.get("bonbast") or {}).get("buy")
            if buy:
                pts.append((r.get("tehran", "?"), buy))
    return pts[-n:]


if __name__ == "__main__":
    rec, prev = snapshot()
    print(fmt(rec, prev))
    pts = history_points()
    if pts:
        print("\nآخرین نوبت‌ها:")
        for t, p in pts:
            print(f"  {t}  {p:,}")


def trend_line(points):
    """نمودار متنی ساده از روند"""
    if len(points) < 2:
        return ""
    vals = [p for _, p in points]
    lo, hi = min(vals), max(vals)
    bars = ""
    for t, v in points:
        w = 1 if hi == lo else 1 + int((v - lo) / (hi - lo) * 18)
        bars += f"  {t[11:16]}  {'█' * w} {v:,}\n"
    return bars.rstrip()
