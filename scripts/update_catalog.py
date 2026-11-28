#!/usr/bin/env python3
"""בונה את נתוני האפליקציה לכל הרשתות והסניפים.

הרצה:  python3 scripts/update_catalog.py [--only ramilevy,victory] [--max-stores N]

1. מרמי לוי אונליין: שם, מחלקה ותמונה לכל מוצר (base.json, משותף לכל הרשתות, לפי ברקוד).
2. מאתרי המחירים של כל רשת (חוק שקיפות המחירים): מחירים ומבצעים לכל סניף.
   מורידים רק סניפים שפרסמו קובץ חדש מאז הפעם הקודמת.
3. כותבים לתיקייה app/data:
   stores.json                – כל הרשתות והסניפים (למסך הבחירה)
   base.json                  – שמות, תמונות ומחלקות
   chains/<רשת>.json          – המחיר הנפוץ בכל הרשת לכל מוצר, ושמות של מוצרים שאין להם תמונה
   branches/<רשת>/<סניף>.json – רק מה ששונה בסניף מהמחיר הנפוץ (כך כל הסניפים יחד קטנים)
   catalog.json               – הקטלוג הישן (רמי לוי עפולה), לטלפונים שעוד לא התעדכנו
"""
import argparse
import collections
import concurrent.futures as cf
import datetime
import gzip
import json
import pathlib
import re
import sys
import threading
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sources  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "data"
STATE = pathlib.Path.home() / ".shopping-list-updater"
CACHE = STATE / "cache"

ONLINE_STORE = "331"      # חנות רמי לוי אונליין (לשמות ולתמונות)
API = "https://www.rami-levy.co.il/api/catalog"
LEGACY_CHAIN, LEGACY_STORE = "ramilevy", "11"   # עפולה – לקובץ catalog.json הישן
PAGE = 100
MAX_PROMO_ITEMS = 100     # מבצעים כלליים על מאות מוצרים לא מוצגים
SKIP_WORDS = ("קופון", "הטבת", "הטבה", "מצטרפים", "שובר", "מתנה")
PROMO_TYPES = {"1", "3", "10"}  # סוגי מבצע עם מחיר ברור: "X ב-Y" או "ב-Y" (בלי קופונים ואחוזים)
CHANGE_DAYS = 14          # כמה ימים מסמנים מוצר שהמחיר שלו עלה או ירד
COMMON = 0.5              # מוצר שנמכר בחצי מהסניפים לפחות נכנס לקובץ הרשת
WORKERS = 4               # הורדות במקביל לכל רשת
WORKERS_BY_CHAIN = {"shufersal": 8}   # השרת של שופרסל איטי (כ-10 שניות לקובץ), אז מורידים יותר במקביל
MIN_ITEMS = 300           # מחירון קטן מזה = כנראה פגום או סניף לא פעיל

log_lock = threading.Lock()


def log(*a):
    with log_lock:
        print(*a, flush=True)


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load(path, default=None):
    try:
        if path.suffix == ".gz":
            return json.loads(gzip.decompress(path.read_bytes()))
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ---------- 1. רמי לוי אונליין: שמות, מחלקות ותמונות ----------
def fetch_online():
    products, offset, total = {}, 0, None
    while total is None or offset < total:
        body = json.dumps({"aggs": 0, "store": ONLINE_STORE, "size": PAGE, "from": offset}).encode()
        for _ in range(3):
            try:
                req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json", "User-Agent": sources.UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.load(r)
                break
            except Exception as e:
                log("retry", offset, e)
                time.sleep(3)
        else:
            raise RuntimeError("ההורדה מאתר רמי לוי נכשלה")
        total = d.get("total", 0)
        for p in d.get("data", []):
            price = (p.get("price") or {}).get("price")
            if price is None:
                continue
            b = str(p.get("barcode") or p["id"])
            products[b] = {
                "id": p["id"], "b": str(p.get("barcode") or ""), "n": p.get("name", "").strip(), "p": price,
                "d": (p.get("department") or {}).get("name", ""),
                "g": ((p.get("subGroup") or {}).get("name") or (p.get("group") or {}).get("name") or ""),
                "i": (p.get("images") or {}).get("small") or "",
                "k": 1 if (p.get("prop") or {}).get("by_kilo") else 0,
            }
        offset += PAGE
        if offset % 2000 == 0 or offset >= total:
            log(f"רמי לוי אונליין: {min(offset, total)}/{total}")
        time.sleep(0.6)
    return products


# ---------- 2. מחירון ומבצעים של סניף ----------
def parse_prices(xml):
    out = {}
    for _, el in ET.iterparse(sources.io.BytesIO(xml)):
        tag = el.tag.split("}")[-1]
        if tag.lower() == "item":
            f = {c.tag.split("}")[-1].lower(): (c.text or "").strip() for c in el}
            code = f.get("itemcode", "")
            try:
                price = float(f.get("itemprice") or 0)
            except ValueError:
                price = 0
            if code and price > 0:
                try:
                    unit = float(f.get("unitofmeasureprice") or 0)
                except ValueError:
                    unit = 0
                out[code] = [f.get("itemname", ""), round(price, 2), round(unit, 2), f.get("unitofmeasure", ""),
                             1 if f.get("bisweighted", "0") == "1" else 0]
            el.clear()
    return out


def parse_promos(xml):
    """כל המבצעים האפשריים, עם תאריכים. מסננים לפי היום רק בזמן הבנייה."""
    out = {}
    for _, el in ET.iterparse(sources.io.BytesIO(xml)):
        if el.tag.split("}")[-1] != "Promotion":
            continue
        club = re.match(r"\s*(\d*)", el.findtext("ClubID") or "0").group(1) or "0"
        items = el.findall(".//PromotionItem")
        desc = (el.findtext("PromotionDescription") or "").strip()
        if club != "0" or not 0 < len(items) <= MAX_PROMO_ITEMS or any(w in desc for w in SKIP_WORDS):
            el.clear()
            continue
        start = (el.findtext("PromotionStartDateTime") or "")[:10]
        end = (el.findtext("PromotionEndDateTime") or "")[:10]
        m = re.search(r"(?:מעל|מע)\s?(\d+)", desc)          # "מעל 75" = בקנייה מעל 75 ש"ח
        min_buy = int(m.group(1)) if m else 0
        try:
            min_buy = max(min_buy, int(float(el.findtext(".//MinPurchaseAmount") or 0)))
        except ValueError:
            pass
        m = re.search(r"מו\s?(\d+)", desc)                  # "מו3" = עד 3 יחידות
        limit = int(m.group(1)) if m else 0
        for it in items:
            try:
                if (it.findtext("RewardType") or "").strip() not in PROMO_TYPES:
                    continue
                code = (it.findtext("ItemCode") or "").strip()
                q = max(1, int(float(it.findtext("MinQty") or 1)))
                x = float(it.findtext("DiscountedPrice") or 0)
            except ValueError:
                continue
            if x <= 0 or not code:
                continue
            cand = {"t": desc, "q": q, "x": round(x, 2), "s": start, "e": end}
            if min_buy:
                cand["m"] = min_buy
            if limit:
                cand["l"] = limit
            out.setdefault(code, []).append(cand)
        el.clear()
    return out


def best_promo(cands, price, today):
    def ok(pr):
        if not pr["s"] <= today <= pr["e"]:
            return False
        unit = pr["x"] / pr["q"]
        if unit >= price - 0.005:
            return False                       # לא באמת זול יותר
        if unit < price * 0.35 and not (pr.get("m") or pr.get("l")):
            return False                       # הנחה חריגה בלי תנאי: כנראה שיוך שגוי במחירון
        return True
    good = [c for c in cands or () if ok(c)]
    if not good:
        return None
    c = min(good, key=lambda c: (1 if c.get("m") else 0, c["x"] / c["q"]))   # עדיף מבצע בלי תנאי
    out = {k: v for k, v in c.items() if k not in ("s", "e")}
    out["e"] = c["e"][8:10] + "/" + c["e"][5:7]
    return out


def stamp_of(name):
    m = re.search(r"-(\d{8})-?(\d{4})", name or "")
    return f"{m.group(1)[6:8]}/{m.group(1)[4:6]} {m.group(2)[:2]}:{m.group(2)[2:]}" if m else ""


def refresh_store(src, chain, store, files, state, today):
    """מוריד את הסניף אם יש קובץ חדש. מחזיר True אם השתנה."""
    key = f"{chain}/{store}"
    path = CACHE / chain / f"{store}.json.gz"
    if state.get(key) == list(files) and path.exists():
        return False
    old = load(path, {})
    items = parse_prices(sources.unpack(src.download(files[0])))
    if len(items) < MIN_ITEMS:
        raise RuntimeError(f"מחירון חלקי ({len(items)} מוצרים)")
    promos = parse_promos(sources.unpack(src.download(files[1]))) if files[1] else {}
    # שינויי מחיר מהמחירון הקודם של אותו סניף
    limit = (datetime.date.fromisoformat(today) - datetime.timedelta(days=CHANGE_DAYS)).isoformat()
    changes = {c: v for c, v in (old.get("changes") or {}).items() if v[1] > limit and c in items}
    for c, it in (old.get("items") or {}).items():
        if c in items and abs(items[c][1] - it[1]) >= 0.01:
            changes[c] = [it[1], today]
    for c in list(changes):
        if abs(changes[c][0] - items[c][1]) < 0.01:
            del changes[c]                     # חזר למחיר הקודם
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(
        {"at": stamp_of(files[0]), "items": items, "promos": promos, "changes": changes},
        ensure_ascii=False, separators=(",", ":")).encode(), 6))
    state[key] = list(files)
    return True


def refresh_chain(key, make, state, today, max_stores=None):
    src = make()
    files = src.files()
    stores = [s for s in src.stores() if s["id"] in files]
    if max_stores:
        stores = stores[:max_stores]
    done = failed = 0

    def one(s):
        for attempt in range(2):
            try:
                return refresh_store(src, key, s["id"], files[s["id"]], state, today)
            except Exception as e:
                if attempt:
                    log(f"  {key} סניף {s['id']} ({s['name']}): נכשל – {e}")
                    return None
                time.sleep(5)

    with cf.ThreadPoolExecutor(WORKERS_BY_CHAIN.get(key, WORKERS)) as ex:
        for i, r in enumerate(ex.map(one, stores), 1):
            done += bool(r)
            failed += r is None
            if i % 25 == 0:
                log(f"  {key}: {i}/{len(stores)}")
    log(f"{key}: {len(stores)} סניפים, {done} עודכנו, {failed} נכשלו")
    return stores


# ---------- 3. בנייה ----------
def norm_index(online):
    """קוד במחירון -> ברקוד באתר. פירות וירקות מופיעים באתר בקוד קצר (100) ובמחירון כ-7290000000100."""
    rev = {}
    for b in online:
        if b.isdigit() and len(b) < 13:
            rev["729" + b.zfill(10)] = b
            rev[b.zfill(13)] = b
    return rev


SHUF_IMG = "https://res.cloudinary.com/shufersal/image/upload/f_auto,q_auto/v1551800922/prod/product_images/products_medium/"  # 180px – חד בטלפון
SHUF_RE = re.compile(r"/v1551800922/prod/product_images/products_small/(\w+?)_S_P_(\w+)_1\.png$")


def shufersal_images():
    """תמונות מוצרים מאתר שופרסל אונליין (כל הקטלוג, פעם בשבוע) -> {ברקוד: [קידומת, מק"ט]}.
    לרוב המוצרים בלי תמונה ברמי לוי יש תמונה שם."""
    path = STATE / "shufersal_images.json"
    cached = load(path)
    if cached and time.time() - path.stat().st_mtime < 7 * 86400:
        return cached
    out, page, pages = {}, 0, 1
    try:
        while page < pages:
            url = f"https://www.shufersal.co.il/online/he/search/results?q=&limit=100&page={page}"
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": sources.BROWSER_UA, "Accept": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        d = json.load(r)
                    break
                except Exception:
                    time.sleep(3)
            else:
                raise RuntimeError(f"עמוד {page} נכשל")
            pages = d["pagination"]["numberOfPages"]
            for x in d["results"]:
                for im in x.get("images") or []:
                    m = SHUF_RE.search(im.get("url") or "")
                    if m:
                        sku = m.group(2)
                        code = "729000" + sku if sku.isdigit() and len(sku) == 7 else sku
                        out[code] = [m.group(1), sku]
                        break
            page += 1
            time.sleep(0.4)
        log(f"תמונות שופרסל: {len(out)}")
        dump(path, out)
        return out
    except Exception as e:
        log("תמונות שופרסל לא זמינות, משתמשים במה שנשמר:", e)
        return cached or {}


def city_names():
    """סמל יישוב של הלמ"ס -> שם היישוב (רוב הרשתות כותבות רק את הסמל)."""
    path = STATE / "cities.json"
    cities = load(path)
    if cities:
        return cities
    try:
        url = "https://data.gov.il/api/3/action/datastore_search?resource_id=5c78e9fa-c2e2-4771-93ff-7f400a12f7ba&limit=5000"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": sources.UA}), timeout=60) as r:
            recs = json.load(r)["result"]["records"]
        cities = {str(int(r["סמל_ישוב"])): re.sub(r"\s+", " ", r["שם_ישוב"]).strip().replace("(", "").replace(")", "")
                  for r in recs if str(r.get("סמל_ישוב", "")).strip().isdigit()}
        dump(path, cities)
        return cities
    except Exception as e:
        log("רשימת היישובים לא זמינה:", e)
        return {}


def build_chain(key, stores, online, rev, today, images=None):
    """קובץ רשת + קובץ לכל סניף. מחזיר את הסניפים שנבנו."""
    def code_of(c):
        if c in online:
            return c
        if c[:1] == "0" and c.lstrip("0") in online:
            return c.lstrip("0")
        if key == "ramilevy" or len(c) >= 8:   # קודים קצרים של רשת אחרת הם קודים פנימיים שלה
            return rev.get(c, c)
        return c

    branches = {}
    for s in stores:
        d = load(CACHE / key / f"{s['id']}.json.gz")
        if not d:
            continue
        items = {}
        for c, it in d["items"].items():
            items[code_of(c)] = it
        promos = {}
        for c, cands in d["promos"].items():
            promos.setdefault(code_of(c), []).extend(cands)
        changes = {code_of(c): v for c, v in d.get("changes", {}).items()}
        branches[s["id"]] = (d["at"], items, promos, changes)
    if not branches:
        return []

    n = len(branches)
    count, prices, names, units = collections.Counter(), collections.defaultdict(collections.Counter), {}, {}
    for at, items, promos, ch in branches.values():
        for c, (name, p, u, uu, k) in items.items():
            count[c] += 1
            prices[c][p] += 1
            if c not in online and c not in names and name:
                names[c] = [name, k]
            if u > 0 and uu and c not in units:
                units[c] = (round(u / p, 5), uu)
    common = sorted(c for c, k in count.items() if k >= n * COMMON)
    mode = {c: prices[c].most_common(1)[0][0] for c in common}

    # מבצע נפוץ ברשת (כמו מחיר נפוץ)
    branch_promos = {}
    for sid, (at, items, promos, ch) in branches.items():
        bp = {}
        for c, cands in promos.items():
            # ברמי לוי מוצר שבמבצע בסניף אבל חסר במחירון מוצג במחיר האתר – והמבצע נשאר
            price = items[c][1] if c in items else online[c]["p"] if key == "ramilevy" and c in online else None
            if price:
                pr = best_promo(cands, price, today)
                if pr:
                    bp[c] = pr
        branch_promos[sid] = bp
    pcount = collections.Counter()
    for bp in branch_promos.values():
        for c, pr in bp.items():
            pcount[(c, json.dumps(pr, sort_keys=True, ensure_ascii=False))] += 1
    chain_promos = {}
    for (c, prj), k in pcount.most_common():
        if k >= n * COMMON and c not in chain_promos:
            chain_promos[c] = json.loads(prj)

    def table(promos):
        """כל מבצע נשמר פעם אחת ברשימה, והמוצרים מצביעים עליו (מבצע אחד חל לרוב על הרבה מוצרים)"""
        pl, idx, out = [], {}, {}
        for c, pr in promos.items():
            if pr == 0:
                out[c] = -1                    # במבצע ברשת, אבל לא בסניף הזה
                continue
            k = json.dumps(pr, sort_keys=True, ensure_ascii=False)
            if k not in idx:
                idx[k] = len(pl)
                pl.append(pr)
            out[c] = idx[k]
        return pl, out

    unit_names = sorted({uu for _, uu in units.values()})
    uidx = {u: i for i, u in enumerate(unit_names)}
    cpl, cpr = table(chain_promos)
    # תמונה ממאגר שופרסל למוצרים שאין להם תמונה ברמי לוי. קוד קצר הוא קוד פנימי – רק בשופרסל עצמה
    im = {}
    # בשופרסל עצמה – תמונה של שופרסל לכל מוצר שיש לו (גם אם יש תמונה ברמי לוי); ברשתות אחרות רק למוצרים בלי תמונה
    for c in (count if key == "shufersal" else names):
        hit = (images or {}).get(c)
        if hit and (len(c) >= 8 or key == "shufersal"):
            im[c] = hit[0] if hit[1] == c or "729000" + hit[1] == c else f"{hit[0]}:{hit[1]}"
    dump(OUT / "chains" / f"{key}.json", {
        "key": key, "branches": n, "at": datetime.datetime.now().strftime("%d/%m %H:%M"),
        "codes": common, "price": [mode[c] for c in common],
        "units": unit_names, "u": {c: [r, uidx[uu]] for c, (r, uu) in units.items()},
        "names": names, "pl": cpl, "pr": cpr,
        "imgBase": SHUF_IMG, "im": im, "imOwn": key == "shufersal",
    })

    pos = {c: i for i, c in enumerate(common)}
    built = []
    for sid, (at, items, promos, ch) in branches.items():
        diff = {c: it[1] for c, it in items.items() if c not in mode or abs(mode[c] - it[1]) >= 0.005}
        miss = [pos[c] for c in common if c not in items]
        bp = branch_promos[sid]
        prd = {c: pr for c, pr in bp.items() if chain_promos.get(c) != pr}
        prd.update({c: 0 for c in chain_promos if c not in bp and (c in items or key == "ramilevy" and c in online)})
        pl, pr = table(prd)
        out = {"at": at, "p": diff, "miss": miss, "pl": pl, "pr": pr}
        if ch:
            out["ch"] = ch
        dump(OUT / "branches" / key / f"{sid}.json", out)
        built.append(sid)
    log(f"{key}: קובץ רשת עם {len(common)} מוצרים נפוצים, {len(names)} מוצרים בלי דף באתר ({len(im)} עם תמונה משופרסל), {len(chain_promos)} מבצעים נפוצים")
    return built


def legacy_catalog(online, rev, today):
    """catalog.json במבנה הישן (רמי לוי עפולה) – לטלפונים עם גרסה ישנה של האפליקציה."""
    d = load(CACHE / LEGACY_CHAIN / f"{LEGACY_STORE}.json.gz")
    if not d:
        return
    items = {}
    for c, it in d["items"].items():
        items[c if c in online else rev.get(c, c)] = (c, it)
    products, matched = [], set()
    for b, p in online.items():
        p = dict(p)
        hit = items.get(p["b"]) or items.get(b)
        if hit:
            matched.add(hit[0])
            orig, (name, price, u, uu, k) = hit
            p["p"] = price
            if u > 0 and uu:
                p["u"], p["uu"] = u, uu
            pr = best_promo(d["promos"].get(orig), price, today)
        else:
            p["o"] = 1
            pr = best_promo(d["promos"].get(p["b"]), p["p"], today)
        if pr:
            p["pr"] = pr
        ch = d.get("changes", {}).get(hit[0]) if hit else None
        if ch:
            p["pp"], p["pc"] = ch
        products.append(p)
    known = {p["b"] for p in products} | matched
    for c, (name, price, u, uu, k) in d["items"].items():
        if c in known or not name:
            continue
        p = {"id": "s" + c, "b": c, "n": name, "p": price, "d": "", "i": "", "k": k}
        pr = best_promo(d["promos"].get(c), price, today)
        if pr:
            p["pr"] = pr
        products.append(p)
    dump(OUT / "catalog.json", {
        "store": ONLINE_STORE, "branch": {"name": "עפולה", "id": "011", "at": d["at"]},
        "updated": datetime.datetime.now().strftime("%d/%m %H:%M"), "imgBase": "https://img.rami-levy.co.il",
        "products": sorted(products, key=lambda x: (x["i"] == "", x["n"])),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="רשתות מופרדות בפסיק")
    ap.add_argument("--max-stores", type=int, default=0, help="לבדיקות: כמה סניפים לכל רשת")
    ap.add_argument("--skip-download", action="store_true", help="רק לבנות מחדש מהקבצים השמורים")
    args = ap.parse_args()
    only = set(filter(None, args.only.split(",")))
    today = datetime.date.today().isoformat()
    t0 = time.time()

    online_path = STATE / "online.json.gz"
    online = None
    if not args.skip_download:
        try:
            online = fetch_online()
            online_path.parent.mkdir(parents=True, exist_ok=True)
            online_path.write_bytes(gzip.compress(json.dumps(online, ensure_ascii=False).encode()))
        except Exception as e:
            log("רמי לוי אונליין לא זמין, משתמשים בשמות מהפעם הקודמת:", e)
    online = online or load(online_path)
    if not online:
        raise SystemExit("אין שמות ותמונות של מוצרים (רמי לוי אונליין)")

    state_path = STATE / "state.json"
    state = load(state_path, {})
    old_index = load(OUT / "stores.json", {}) or {}
    chains = [c for c in sources.CHAINS if not only or c[0] in only]
    found = {}
    if not args.skip_download:
        def run(c):
            try:
                return c[0], refresh_chain(c[0], c[3], state, today, args.max_stores)
            except Exception as e:
                log(f"{c[0]}: האתר לא זמין – {e}")
                traceback.print_exc()
                return c[0], None
        with cf.ThreadPoolExecutor(len(chains)) as ex:
            for key, stores in ex.map(run, chains):
                if stores is not None:
                    found[key] = stores
        dump(state_path, state)

    rev = norm_index(online)
    cities = city_names()
    images = shufersal_images()
    keep = {k: v for k, v in (old_index.get("stores") or {}).items()}
    for key, name, color, _ in chains:
        stores = found.get(key)
        if stores is None:   # הרשת לא זמינה כרגע: משאירים את הסניפים והקבצים של הפעם הקודמת
            prev = keep.get(key)
            if prev is None:
                continue
            stores = [{"id": s[0], "name": s[1], "city": s[2], "address": s[3]} for s in prev]
        built = set(build_chain(key, stores, online, rev, today, images))
        if not built:
            log(f"{key}: אין נתונים – משאירים את הקבצים של הפעם הקודמת")
            continue
        rows = []
        for s in stores:
            if s["id"] not in built:
                continue
            city = cities.get(s["city"], "" if s["city"].isdigit() else s["city"])
            rows.append([s["id"], s["name"], city, s.get("address", "")])
        keep[key] = sorted(rows, key=lambda r: (r[2] or r[1], r[1]))
        # קבצי סניפים שנסגרו
        for f in (OUT / "branches" / key).glob("*.json"):
            if f.stem not in built:
                f.unlink()

    dump(OUT / "base.json", {
        "imgBase": "https://img.rami-levy.co.il",
        "products": sorted(({k: p[k] for k in ("id", "b", "n", "p", "d", "g", "i", "k") if k in p} for p in online.values()),
                           key=lambda x: (x["i"] == "", x["n"])),
    })
    dump(OUT / "stores.json", {
        "updated": datetime.datetime.now().strftime("%d/%m %H:%M"),
        "chains": [{"key": k, "name": n, "color": c} for k, n, c, _ in sources.CHAINS if k in keep],
        "stores": keep,
    })
    legacy_catalog(online, rev, today)
    total = sum(f.stat().st_size for f in OUT.rglob("*.json"))
    log(f"סיום: {sum(len(v) for v in keep.values())} סניפים, {total / 1e6:.1f}MB, {time.time() - t0:.0f} שניות")


if __name__ == "__main__":
    main()
