#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Iran–Israel–US War Status Monitor
جمع‌آوری فیدهای خبری چندزبانه، حذف تکرار، دسته‌بندی موضوعی، خلاصه وضعیت.
خروجی: digest.txt (فارسی) + items.jsonl + state.json
"""

import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import fx

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state.json")
DIGEST = os.path.join(BASE, "digest.txt")
ITEMS = os.path.join(BASE, "items.jsonl")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) war-monitor/1.0"}
FETCH_TIMEOUT = 25

# ----------------------------------------------------------------- sources
FEEDS = [
    {"name": "Google News (EN)", "lang": "en",
     "url": "https://news.google.com/rss/search?q=iran+OR+israel+OR+hezbollah+war+strike+missile&hl=en&gl=US&ceid=US:en"},
    {"name": "Google News (US-Iran)", "lang": "en",
     "url": "https://news.google.com/rss/search?q=iran+usa+military+persian+gulf+nuclear&hl=en&gl=US&ceid=US:en"},
    {"name": "Bing News", "lang": "en",
     "url": "https://www.bing.com/news/search?q=iran+israel+war+strike&format=rss"},
    # ---- ایران: فقط ایران اینترنشنال (فید فارسی هم دارد) ----
    {"name": "Iran International", "lang": "en", "url": "https://www.iranintl.com/feed"},
    # ---- آمریکا ----
    {"name": "Fox News (latest)", "lang": "en", "url": "https://feeds.foxnews.com/foxnews/latest"},
    {"name": "Fox News (world)", "lang": "en", "url": "https://feeds.foxnews.com/foxnews/world"},
    {"name": "Fox News (politics)", "lang": "en", "url": "https://feeds.foxnews.com/foxnews/politics"},
    # ---- اسرائیل: فید مستقیم ----
    {"name": "Jerusalem Post", "lang": "en", "url": "https://www.jpost.com/rss/rssfeedsfrontpage.aspx"},
    {"name": "Israel National News", "lang": "en",
     "url": "https://www.israelnationalnews.com/Rss.aspx?act=.1"},
    {"name": "Times of Israel", "lang": "en", "url": "https://feeds.feedburner.com/TheTimesOfIsrael"},
    {"name": "Algemeiner", "lang": "en", "url": "https://www.algemeiner.com/feed/"},
    {"name": "JNS", "lang": "en", "url": "https://www.jns.org/feed/"},
    # ---- اسرائیل: فید مستقیم 403 می‌ده، از طریق گوگل‌نیوز site: ----
    {"name": "GN site:timesofisrael", "lang": "en",
     "url": "https://news.google.com/rss/search?q=site%3Atimesofisrael.com+iran+israel+war&hl=en&gl=US&ceid=US:en"},
    {"name": "GN site:ynetnews", "lang": "en",
     "url": "https://news.google.com/rss/search?q=site%3Aynetnews.com+iran+israel+war&hl=en&gl=US&ceid=US:en"},
    {"name": "GN site:israelhayom", "lang": "en",
     "url": "https://news.google.com/rss/search?q=site%3Aisraelhayom.com+iran+israel+war&hl=en&gl=US&ceid=US:en"},
    {"name": "GN site:haaretz", "lang": "en",
     "url": "https://news.google.com/rss/search?q=site%3Ahaaretz.com+iran+israel+war&hl=en&gl=US&ceid=US:en"},
    {"name": "GN site:i24news", "lang": "en",
     "url": "https://news.google.com/rss/search?q=site%3Ai24news.tv+iran+israel+war&hl=en&gl=US&ceid=US:en"},
    # ---- بین‌المللی ----
    {"name": "BBC Persian", "lang": "fa", "url": "https://feeds.bbci.co.uk/persian/rss.xml"},
    {"name": "BBC World", "lang": "en", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Al Jazeera", "lang": "en", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "France24", "lang": "en", "url": "https://www.france24.com/en/rss"},
    {"name": "DW", "lang": "en", "url": "https://rss.dw.com/rdf/rss-en-all"},
    {"name": "Al-Monitor", "lang": "en", "url": "https://www.al-monitor.com/rss"},
    {"name": "Guardian World", "lang": "en", "url": "https://www.theguardian.com/world/rss"},
]

# ---- منابع وابسته به حکومت/سپاه: در هیچ شکلی پذیرفته نمی‌شوند ----
IR_SOURCES = (
    "farsnews.ir", "tasnimnews.com", "irna.ir", "isna.ir", "mehrnews.com",
    "press.tv", "presstv.ir", "alalam.ir", "irib", "kayhan.ir", "jahannews.ir",
    "aftabnews.ir", "entekhab.ir", "fararu.com", "defapress.ir", "sahamnews.ir",
    "yjc.ir", "mizanonline.ir", "tabnak.ir", "shana.news", "iribnews",
    "hemayatonline.ir", "qudon.ir", "tnews.ir", "rasanews.ir", "farsnews",
)


def is_ir_source(link, src_url="", src_name=""):
    blob = f"{link or ''} {src_url or ''} {src_name or ''}".lower()
    return any(d in blob for d in IR_SOURCES)

# ----------------------------------------------------------------- filters
EN_PARTY = re.compile(
    r"israel|israeli|tehran|netanyahu|hezbollah|hizbullah|gaza|hamas|"
    r"palestini|\bidf\b|pentagon|white house|middle east|west bank|"
    r"leban|syria|yemen|houthi|hormuz|zionist|us forces?|u\.s\. forces|"
    r"american forces?|axis of resistance", re.I)

FA_PARTY = re.compile(
    r"اسرائیل|تل\s*آویو|نتانیاهو|حزب\s*الله|حماس|فلسطین|صهیونیست|"
    r"آمریکا|امریکا|پنتاگون|کاخ\s*سفید|لبنان|سوریا|یمن|انصارالله|"
    r"تنگه\s*هرمز|خلیج\s*فارس|ارتش اسرائیل|کرانه باختری|غزه")

# وقتی فقط «ایران» اومده، باید کنارش اصطلاح درگیری هم باشه
CONFLICT = re.compile(
    r"\bwar\b|strike|airstrike|attack|missile|drone|airstrike|cease-?fire|"
    r"\bnuclear\b|sanction|hostage|invasion|bombing|escalat|"
    r"جنگ|حمله|موشک|پهپاد|بمب|آتش\s*بس|تحریم|هسته\s*ای|گروگان|شبیخون", re.I)

GENERIC_IR = re.compile(r"ایران|iran", re.I)

# نویز آشکار: ورزش، انتخابات، یادداشت
NOISE = re.compile(
    r"\b(football|soccer|coach|tournament|olympic|athlet|rugby|cricket|"
    r"baseball|basketball|tennis|sports?|elections?\b|polls?\b|opinion|"
    r"editorial|column|premier league|nba|formula|grand prix|weather|"
    r"blockbuster)\b|"
    r"(فوتبال|بیسبال|بسکتبال|انتخابات|نظرسنجی|لیگ برتر|قهرمانی|ورزشگاه|"
    r"یادداشت|سرمقاله)",
    re.I)


def is_relevant(title):
    if NOISE.search(title):
        return False
    if EN_PARTY.search(title) or FA_PARTY.search(title):
        return True
    return bool(GENERIC_IR.search(title) and CONFLICT.search(title))

CAT_EMOJI = {"military": "⚔️", "diplomacy": "🤝", "nuclear": "☢️",
             "sanctions": "⛔️", "energy": "🛢️", "other": "📰"}

CATS = [
    ("military", re.compile(
        r"strike|airstrike|air strike|attack|missile|rocket|drone|uav|bomb|"
        r"bombard|shelling|clash|killed|wounded|injured|explosion|intercept|"
        r"casualt|military target|armed|gunfire|naval|warship|troops|"
        r"حمله|موشک|پهپاد|بمب|انفجار|کشته|زخمی|صابت|تیراندازی|درگیری|"
        r"ارتش|نیروی\s*هوایی|نظامی|بمباران|راکت|شلیک", re.I)),
    ("diplomacy", re.compile(
        r"negotiat|talks?|diplomac|envoy|cease-?fire|summit|statement|"
        r"warns?|threatens?|sanction talk|mediat|un security council|"
        r"مذاکره|دیپلماسی|آتش\s*بس|توافق|هشدار|تهدید|شورای\s*امنیت|فرستاده", re.I)),
    ("nuclear", re.compile(
        r"nuclear|uranium|enrich|iaea|centrifug|warhead|bomb program|"
        r"هسته\s*ای|اورانیوم|غنی\s*سازی|آژانس بین\s*المللی|سلاح هسته", re.I)),
    ("sanctions", re.compile(
        r"sanction|embargo|asset freeze|secondary sanction|tariff|"
        r"تحریم|مجازات|توقیف دارایی|محدودیت بانکی", re.I)),
    ("energy", re.compile(
        r"oil|tanker|strait|hormuz|shipping|crude|nat(?:ural)? gas|refinery|"
        r"ناوگان|نفتکش|تنگه|نفت|سوخت|پالایشگاه|کشتی", re.I)),
]

ESCALATION = re.compile(
    r"strike|airstrike|attack|missile|rocket|drone|bomb|killed|wounded|"
    r"explosion|intercept|clash|invasion|war|"
    r"حمله|موشک|پهپاد|بمب|انفجار|کشته|زخمی|درگیری|جنگ|صابت", re.I)


# ----------------------------------------------------------------- helpers
def fetch(feed):
    try:
        r = requests.get(feed["url"], headers=UA, timeout=FETCH_TIMEOUT)
        if r.status_code != 200 or not r.text.strip():
            return feed, None, f"HTTP {r.status_code} ({len(r.content)}B)"
        return feed, r.text, None
    except Exception as e:  # noqa: BLE001
        return feed, None, f"{type(e).__name__}: {e}"


TAG = lambda t: re.compile(rf"<{t}[^>]*>(.*?)</{t}>", re.I | re.S)


def strip_tags(s):
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    s = html.unescape(s)                 # اول escape باز بشه تا تگ‌ها دیده بشن
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)                 # موجودیت‌های داخل متن
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_desc(desc, title):
    """متن خبر رو برای نقل‌قول تمیز می‌کنه؛ اگه بی‌ارزشه برمی‌گردونه None"""
    d = (desc or "").strip()
    if not d or len(d) < 40:
        return None
    if d.lower().startswith(("http://", "https://")):
        return None
    if "<a href" in d.lower() or d.count("news.google.com"):
        return None
    nt = re.sub(r"[^\w\u0600-\u06FF]+", "", d).lower()
    tt = re.sub(r"[^\w\u0600-\u06FF]+", "", title or "").lower()
    if tt and (nt == tt or (len(nt) > 20 and nt.startswith(tt))):
        return None                     # عین تیتره، ارزش نقل‌قول نداره
    return d[:240]


def unwrap_link(link):
    """لینک‌های ریدایرکتی بینگ/MSN رو به آدرس اصلی تبدیل می‌کنه"""
    if not link:
        return link
    if "bing.com/news/apiclick" in link or "msn.com" in link:
        m = re.search(r"[?&]url=([^&]+)", link)
        if m:
            from urllib.parse import unquote
            real = unquote(m.group(1))
            if real.startswith("http"):
                return real
    return link


def parse_feed(text):
    """RSS <item> و Atom <entry> رو با regex می‌خونه (فیدهای خراب رو هم رد می‌کنه)"""
    out = []
    blocks = re.findall(r"<(?:item|entry)[^>]*>.*?</(?:item|entry)>", text, re.I | re.S)
    for b in blocks:
        def g(tag, block=b):
            m = TAG(tag).search(block)
            return m.group(1) if m else ""
        title = strip_tags(g("title"))
        link = ""
        m = TAG("link").search(b)
        if m:
            raw = m.group(1).strip()
            if raw.startswith("<") or "href=" in raw:
                hm = re.search(r'href="([^"]+)"', raw)
                link = hm.group(1) if hm else ""
            else:
                link = strip_tags(raw)
        pub = g("pubDate") or g("published") or g("updated") or g("dc:date") or ""
        src = g("source") or g("dc:creator") or ""
        sm = re.search(r"<source[^>]*url=[\"']([^\"']+)[\"']", b, re.I)
        src_url = sm.group(1) if sm else ""
        desc = strip_tags(g("description") or g("summary"))[:400]
        if title:
            out.append({"title": title, "link": link, "pub": pub.strip(),
                        "source_tag": strip_tags(src), "src_url": src_url,
                        "desc": desc})
    return out


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(s[:25].replace("Z", "+0000"), fmt.replace("%z", "%z"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def norm_title(t):
    t = re.sub(r"\s*[-–|]\s*[^-–|]{2,40}$", "", t.strip())  # حذف " - منبع"
    t = t.lower()
    return re.sub(r"[^\w\u0600-\u06FF]+", "", t)


def classify(title, desc=""):
    text = f"{title} {desc}"
    cats = [c for c, rx in CATS if rx.search(text)]
    return cats or ["other"]


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"seen": {}, "last_cutoff": None, "last_run": None}


def save_state(st):
    now = time.time()
    st["seen"] = {k: v for k, v in st["seen"].items() if now - v < 7 * 86400}
    st["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ----------------------------------------------------------------- collect
def collect():
    st = load_state()
    now = datetime.now(timezone.utc)
    problems, items, all_count = [], [], 0
    ir_blocked = [0]

    # تاریخچه فایل هم مبنای «دیده‌شده» هست؛ اگه state پاک بشه، خبر تکراری نمی‌خوره
    seen = set(st.get("seen", {}))
    if os.path.exists(ITEMS):
        with open(ITEMS, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(norm_title(json.loads(line).get("title", "")))
                except Exception:
                    continue

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(fetch, FEEDS))

    for feed, text, err in results:
        if err:
            problems.append(f"{feed['name']}: {err}")
            continue
        parsed = parse_feed(text)
        if not parsed:
            problems.append(f"{feed['name']}: 0 آیتم (ساختار ناشناخته)")
            continue
        for it in parsed:
            all_count += 1
            if is_ir_source(it.get("link"), it.get("src_url"), it.get("source_tag")):
                ir_blocked[0] += 1
                continue
            if not is_relevant(it["title"]):
                continue
            dt = parse_date(it["pub"])
            age_h = (now - dt).total_seconds() / 3600 if dt else None
            if age_h is not None and age_h > 72:
                continue  # قدیمی‌تر از ۷۲ ساعت رو نادیده بگیر
            if age_h is not None and age_h < 0:
                age_h = 0.0  # ساعت فید جلوتر از UTC: صفر کن
            key = norm_title(it["title"])
            if not key or key in seen:
                continue
            seen.add(key)
            st["seen"][key] = time.time()
            title = it["title"]
            if feed["name"].startswith(("GN site:", "Google News", "Bing News")):
                title = re.sub(r"\s*[-–|]\s*[^-–|]{2,40}$", "", title).strip()
            items.append({
                "title": title, "link": unwrap_link(it["link"]), "src": feed["name"],
                "desc": (it.get("desc") or "")[:320],
                "lang": feed["lang"], "time": dt.isoformat() if dt else None,
                "age_h": round(age_h, 1) if age_h is not None else None,
                "cats": classify(it["title"], it["desc"]),
                "esc": bool(ESCALATION.search(it["title"])),
            })

    items.sort(key=lambda x: (x["age_h"] is None, x["age_h"] if x["age_h"] is not None else 0))
    with open(ITEMS, "a", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return items, problems, all_count, st, ir_blocked[0]


# ----------------------------------------------------------------- report
def load_history(now, hours=24.0):
    """همه آیتم‌های ثبت‌شده در بازه مشخص (از روی زمان ثبت، نه «جدید بودن»)"""
    hist = []
    if not os.path.exists(ITEMS):
        return hist
    with open(ITEMS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except Exception:
                continue
            if not it.get("time"):
                continue
            try:
                t = datetime.fromisoformat(it["time"])
            except Exception:
                continue
            age = (now - t).total_seconds() / 3600
            if 0 <= age <= hours:
                it["age_h"] = round(age, 2)
                hist.append(it)
    hist.sort(key=lambda x: x["age_h"])
    return hist


def status_level(items):
    h6 = [i for i in items if i["age_h"] is not None and i["age_h"] <= 6 and i["esc"]]
    h24 = [i for i in items if i["age_h"] is not None and i["age_h"] <= 24 and i["esc"]]
    mil24 = [i for i in items if i["age_h"] is not None and i["age_h"] <= 24
             and "military" in i["cats"]]
    if len(h6) >= 8:
        return "🔴 تشدید فعال", h6, h24, mil24
    if len(h6) >= 3:
        return "🟠 تنش بالا", h6, h24, mil24
    if len(h6) >= 1:
        return "🟡 درگیری پراکنده / تنش", h6, h24, mil24
    if len(h24) >= 3 or len(mil24) >= 3:
        return "🟡 آرام‌نسبی (خبر نظامی در ۲۴ ساعت هست)", h6, h24, mil24
    return "🟢 سکوت نسبی (خبر نظامی جدید در ۲۴ ساعت نیومده)", h6, h24, mil24


def trend_verdict(st, now, esc6):
    """روند تنش رو با قرائت‌های قبلی مقایسه می‌کنه (مرجع: حدود ۶ ساعت پیش)"""
    hist_t = st.get("trend") or []
    hist_t.append({"ts": now.isoformat(timespec="seconds"), "esc6": int(esc6)})
    st["trend"] = hist_t[-800:]

    prev = [r for r in hist_t[:-1]
            if now.timestamp() - datetime.fromisoformat(r["ts"]).timestamp() >= 3600
            and r.get("esc6") is not None]
    if not prev:
        return "ℹ️ هنوز داده کافی برای مقایسه روند جمع نشده (چند ساعت دیگه تکمیل می‌شه)"

    target = now.timestamp() - 6 * 3600
    best, bd = prev[-1], 1e18
    for r in prev:
        t = datetime.fromisoformat(r["ts"]).timestamp()
        d = abs(t - target)
        if d < bd:
            bd, best = d, r
    bt = datetime.fromisoformat(best["ts"])
    ago = (now - bt).total_seconds() / 3600
    delta = esc6 - best["esc6"]
    ctx = f"(مقایسه با قرائت {ago:.1f} ساعت پیش: {best['esc6']} → {esc6})"

    if delta >= 4:
        return f"🔴 شدیداً به جنگ نزدیک‌تر شدیم — {delta:+d} تیتر تشدیدآمیز {ctx}"
    if delta >= 2:
        return f"🟠 تنش داره بالا می‌ره — {delta:+d} {ctx}"
    if delta <= -4:
        return f"🟢 کاهش چشمگیر تنش — فاصله گرفتیم {delta:+d} {ctx}"
    if delta <= -2:
        return f"🟢 تنش داره می‌خوابه — {delta:+d} {ctx}"
    return f"🟡 تقریباً ثابت — تغییر محسوسی نیست ({delta:+d}) {ctx}"


def build_digest(items, problems, all_count, st):
    now = datetime.now(timezone.utc)
    hist = load_history(now, 24.0)          # وضعیت از تاریخچه ۲۴ ساعته
    lvl, h6, h24, mil24 = status_level(hist)
    cats, srcs = {}, {}
    for i in hist:
        for c in i["cats"]:
            cats[c] = cats.get(c, 0) + 1
        srcs[i["src"]] = srcs.get(i["src"], 0) + 1
    # اول جدیدترین‌ها، بعد اولویت با مرتبط‌های جنگی/دیپلماتیک
    headlines = sorted(hist, key=lambda x: x.get("age_h")
                       if x.get("age_h") is not None else 999)
    REL_CATS = {"military", "diplomacy", "nuclear", "sanctions", "energy"}
    rel = [i for i in headlines if REL_CATS & set(i.get("cats") or [])]
    rest = [i for i in headlines if i not in rel]
    headlines = rel + rest[:4]
    fresh = bool(items)

    # ---- جمع‌آوری در غیاب کاربر ----
    gap_h = None
    if st.get("last_run"):
        try:
            last = datetime.fromisoformat(st["last_run"])
            gap_h = (now - last).total_seconds() / 3600
        except Exception:
            gap_h = None

    fa = {k: v for k, v in {"military": "نظامی", "diplomacy": "دیپلماسی",
                            "nuclear": "هسته‌ای", "sanctions": "تحریم",
                            "energy": "انرژی/دریا", "other": "عمومی"}.items()}
    L = []
    L.append("📊 گزارش مانیتور وضعیت جنگی ایران / اسرائیل / آمریکا")
    L.append(f"زمان گزارش: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    if gap_h is not None:
        if gap_h >= 1:
            L.append(f"📥 آخرین اجرا {gap_h:.1f} ساعت پیش بود — "
                     f"{len(items)} خبر در این فاصله جمع‌آوری شد:")
            # گروه‌بندی بر اساس ساعت
            groups = {}
            for i in items:
                hr = (i.get("time") or "?")[:13]
                groups.setdefault(hr, []).append(i)
            for hr in sorted(groups):
                bucket = groups[hr]
                L.append(f"  ▸ {hr}:00 — {len(bucket)} خبر")
                for i in bucket[:4]:
                    em = "".join(CAT_EMOJI.get(c, "📰") for c in (i.get("cats") or ["other"]))
                    L.append(f"      {em} {i['title'][:110]}")
                    L.append(f"         📌 {i.get('src', '؟')}")
                if len(bucket) > 4:
                    L.append(f"      … و {len(bucket) - 4} خبر دیگر (در items.jsonl)")
        else:
            L.append(f"📥 آخرین اجرا {gap_h:.1f} ساعت پیش")
    else:
        L.append("📥 اولین اجرا (ایجاد تاریخچه)")
    L.append("")
    trend = trend_verdict(st, now, len(h6))
    L.append(f"🎯 وضعیت (بر اساس {len(hist)} عنوان در ۲۴ ساعت اخیر): {lvl}")
    L.append(f"📈 روند: {trend}")
    L.append(f"⚔️ تشدیدآمیز در ۶ ساعت: {len(h6)} | در ۲۴ ساعت: {len(h24)}")
    L.append(f"🎖 عنوان نظامی در ۲۴ ساعت: {len(mil24)} | خبر جدیدِ این نوبت: {len(items)}")
    L.append("")
    L.append("🗂 دسته‌بندی ۲۴ ساعت: " + " · ".join(
        f"{CAT_EMOJI.get(c, '📰')} {fa.get(c, c)} {n}"
        for c, n in sorted(cats.items(), key=lambda x: -x[1])))
    top_srcs = sorted(srcs.items(), key=lambda x: -x[1])[:8]
    L.append("📡 منابع ۲۴ ساعت: " + "، ".join(f"{s} ({n})" for s, n in top_srcs))
    L.append("")
    L.append("📰 خبرهای تازه (اول مرتبط‌ترین‌ها، از جدیدترین):" if fresh
             else "📰 خبرهای تازه (از جدیدترین):")
    for i in headlines[:8]:
        em = "".join(CAT_EMOJI.get(c, "📰") for c in (i.get("cats") or ["other"]))
        age = i.get("age_h")
        when = f"{age} ساعت پیش" if age is not None else "زمان نامشخص"
        L.append(f"{em} {i.get('title', '')}")
        L.append(f"   📌 منبع: {i.get('src', '؟')}")
        d = clean_desc(i.get("desc"), i.get("title"))
        if d:
            L.append(f"   🗞 «{d}»")
        if i.get("link"):
            L.append(f"   🔗 {i['link']}")
        L.append(f"   ⏱ {when}")
    if problems:
        L.append("")
        L.append("— خطای پوشش (نباید به‌عنوان «خبر نیست» تفسیر شود) —")
        for p in problems:
            L.append(f"  ⚠ {p}")
    L.append("")
    L.append("⚠️ این گزارش از روی عناوین RSS ساخته شده، ارزیابی میدانی مستقل نیست.")
    L.append(f"تعداد کل آیتم خوانده‌شده از فیدها: {all_count}")
    return "\n".join(L)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "fx":
        # فقط قیمت ارز — برای اجرای هر دقیقه
        rec, prev = fx.snapshot()
        print(fx.fmt(rec, prev))
        tl = fx.trend_line(fx.history_points(20))
        if tl:
            print("\nروند آخرین نوبت‌ها:")
            print(tl)
        return

    items, problems, all_count, st, ir_blocked = collect()
    digest = build_digest(items, problems, all_count, st)

    try:
        rec, prev = fx.snapshot()
        digest = fx.fmt(rec, prev) + "\n\n" + digest
    except Exception as e:  # noqa: BLE001
        digest = f"💰 نرخ ارز در دسترس نبود: {type(e).__name__}\n\n{digest}"

    digest += f"\nخبرهای حذف‌شده از منابع وابسته (سپاه/حکومت): {ir_blocked}"
    with open(DIGEST, "w", encoding="utf-8") as f:
        f.write(digest + "\n")
    save_state(st)
    print(digest)


if __name__ == "__main__":
    main()
