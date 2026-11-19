#!/usr/bin/env python3
"""בונה את קטלוג האפליקציה: שמות ותמונות מאתר רמי לוי, מחירים ומבצעים מהמחירון הרשמי של הסניף.

הרצה:  python3 scripts/update_catalog.py

1. מאתר רמי לוי אונליין: שם, מחלקה ותמונה לכל מוצר.
2. מאתר המחירים הרשמי (חוק שקיפות המחירים, publishedprices.co.il, משתמש ציבורי RamiLevi):
   המחיר בסניף והמבצעים בסניף, לפי ברקוד.
אם שלב 2 נכשל, נשארים מחירי האתר (ומסומנים ככאלה).
"""
import datetime
import gzip
import http.cookiejar
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ONLINE_STORE = "331"      # חנות רמי לוי אונליין (לשמות ולתמונות)
BRANCH_ID = "011"         # סניף עפולה, יהושע חנקין 14
BRANCH_NAME = "עפולה"
CHAIN = "7290058140886"   # קוד הרשת של רמי לוי
PORTAL = "https://url.publishedprices.co.il"
PORTAL_USER = "RamiLevi"  # משתמש ציבורי שרמי לוי מפרסם, בלי סיסמה

API = "https://www.rami-levy.co.il/api/catalog"
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "catalog.json"
UA = "family-shopping-list/1.0"
PAGE = 100
MAX_PROMO_ITEMS = 100     # מבצעים כלליים על מאות מוצרים לא מוצגים
SKIP_WORDS = ("קופון", "הטבת", "הטבה", "מצטרפים", "שובר", "מתנה")
PROMO_TYPES = {"1", "3", "10"}  # סוגי מבצע עם מחיר ברור: "X ב-Y" או "ב-Y" (בלי קופונים ואחוזים)


# ---------- 1. האתר: שמות, מחלקות ותמונות ----------
def fetch_online():
    products, offset, total = {}, 0, None
    while total is None or offset < total:
        body = json.dumps({"aggs": 0, "store": ONLINE_STORE, "size": PAGE, "from": offset}).encode()
        for _ in range(3):
            try:
                req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.load(r)
                break
            except Exception as e:
                print("retry", offset, e)
                time.sleep(3)
        else:
            raise SystemExit("ההורדה מאתר רמי לוי נכשלה")
        total = d.get("total", 0)
        for p in d.get("data", []):
            price = (p.get("price") or {}).get("price")
            if price is None:
                continue
            products[str(p.get("barcode") or p["id"])] = {
                "id": p["id"],
                "b": str(p.get("barcode") or ""),
                "n": p.get("name", "").strip(),
                "p": price,
                "d": (p.get("department") or {}).get("name", ""),
                "i": (p.get("images") or {}).get("small") or "",
                "k": 1 if (p.get("prop") or {}).get("by_kilo") else 0,
            }
        offset += PAGE
        print(f"אתר: {min(offset, total)}/{total}", flush=True)
        time.sleep(0.6)
    return products


# ---------- 2. המחירון הרשמי של הסניף ----------
class Portal:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [("User-Agent", UA)]

    def _get(self, path):
        with self.op.open(PORTAL + path, timeout=60) as r:
            return r.read()

    def _post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        with self.op.open(PORTAL + path, data=data, timeout=60) as r:
            return r.read()

    @staticmethod
    def _csrf(html):
        m = re.search(rb'name="csrftoken" content="([^"]+)"', html)
        if not m:
            raise RuntimeError("csrftoken לא נמצא")
        return m.group(1).decode()

    def login(self):
        token = self._csrf(self._get("/login"))
        self._post("/login/user", {"r": "", "username": PORTAL_USER, "password": "", "Submit": "Sign in", "csrftoken": token})
        self.token = self._csrf(self._get("/file"))

    def latest(self, prefix):
        raw = self._post("/file/json/dir", {
            "sEcho": 1, "iColumns": 5, "iDisplayStart": 0, "iDisplayLength": 200,
            "sSearch": f"-{BRANCH_ID}-", "cd": "/", "csrftoken": self.token,
        })
        names = sorted(r["name"] for r in json.loads(raw)["aaData"] if r["name"].startswith(prefix))
        if not names:
            raise RuntimeError(f"לא נמצא קובץ {prefix}")
        return names[-1]

    def download_xml(self, name):
        data = self._get("/file/d/" + urllib.parse.quote(name))
        return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def parse_prices(xml):
    out = {}
    for _, el in ET.iterparse(_stream(xml)):
        if el.tag == "Item":
            code = (el.findtext("ItemCode") or "").strip()
            try:
                out[code] = {
                    "n": (el.findtext("ItemName") or "").strip(),
                    "p": float(el.findtext("ItemPrice") or 0),
                    "k": 1 if (el.findtext("bIsWeighted") or "0").strip() == "1" else 0,
                }
            except ValueError:
                pass
            el.clear()
    return out


def parse_promos(xml, today):
    best = {}
    for _, el in ET.iterparse(_stream(xml)):
        if el.tag != "Promotion":
            continue
        start = (el.findtext("PromotionStartDateTime") or "")[:10]
        end = (el.findtext("PromotionEndDateTime") or "")[:10]
        club = (el.findtext("ClubID") or "0").strip()
        items = el.findall(".//PromotionItem")
        if start <= today <= end and club == "0" and 0 < len(items) <= MAX_PROMO_ITEMS:
            desc = (el.findtext("PromotionDescription") or "").strip()
            # קופונים, הטבות ומבצעי מצטרפים לא זמינים לכל לקוח
            if any(w in desc for w in SKIP_WORDS):
                el.clear()
                continue
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
                if x <= 0:
                    continue
                cand = {"t": desc, "q": q, "x": x, "e": end[8:10] + "/" + end[5:7]}
                if min_buy:
                    cand["m"] = min_buy
                if limit:
                    cand["l"] = limit
                key = lambda c: (1 if c.get("m") else 0, c["x"] / c["q"])   # עדיף מבצע בלי תנאי
                if code not in best or key(cand) < key(best[code]):
                    best[code] = cand
        el.clear()
    return best


def _stream(data):
    import io
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    elif data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        data = data.decode("utf-16").encode("utf-8")
    return io.BytesIO(data)


def main():
    online = fetch_online()
    branch_info = None
    try:
        portal = Portal()
        portal.login()
        price_file = portal.latest(f"PriceFull{CHAIN}-")
        promo_file = portal.latest(f"PromoFull{CHAIN}-")
        print("מחירון:", price_file, "|", promo_file)
        prices = parse_prices(portal.download_xml(price_file))
        promos = parse_promos(portal.download_xml(promo_file), datetime.date.today().isoformat())
        stamp = re.search(r"-(\d{8})-(\d{4})", price_file)
        branch_info = {
            "name": BRANCH_NAME, "id": BRANCH_ID,
            "at": f"{stamp.group(1)[6:8]}/{stamp.group(1)[4:6]} {stamp.group(2)[:2]}:{stamp.group(2)[2:]}" if stamp else "",
        }
        print(f"סניף: {len(prices)} מחירים, {len(promos)} מוצרים במבצע")
    except Exception as e:
        print("המחירון של הסניף לא זמין, נשארים מחירי האתר:", e)
        prices, promos = {}, {}

    def promo_ok(pr, price):
        unit = pr["x"] / pr["q"]
        if unit >= price - 0.005:
            return False                       # לא באמת זול יותר
        if unit < price * 0.35 and not (pr.get("m") or pr.get("l")):
            return False                       # הנחה חריגה בלי תנאי: כנראה שיוך שגוי במחירון
        return True

    def branch_code(b):
        """פירות וירקות מופיעים באתר בקוד קצר (100 = עגבניה) ובמחירון כ-7290000000100."""
        if b in prices:
            return b
        if b.isdigit() and len(b) < 13:
            for c in ("729" + b.zfill(10), b.zfill(13)):
                if c in prices:
                    return c
        return None

    products, known = [], set()
    for code, p in online.items():
        bc = branch_code(p["b"])
        if bc:
            p["p"] = prices[bc]["p"]
            known.add(bc)
        elif prices:
            p["o"] = 1          # לא במחירון הסניף: מחיר האתר
        pr = promos.get(bc or p["b"])
        if pr and promo_ok(pr, p["p"]):
            p["pr"] = pr
        products.append(p)
    # מוצרים שנמכרים בסניף ואין להם דף באתר (בלי תמונה)
    known |= {p["b"] for p in products}
    for code, bp in prices.items():
        if code in known or not bp["n"] or bp["p"] <= 0:
            continue
        p = {"id": "s" + code, "b": code, "n": bp["n"], "p": bp["p"], "d": "", "i": "", "k": bp["k"]}
        pr = promos.get(code)
        if pr and promo_ok(pr, p["p"]):
            p["pr"] = pr
        products.append(p)

    out = {
        "store": ONLINE_STORE,
        "branch": branch_info,
        "updated": datetime.datetime.now().strftime("%d/%m %H:%M"),
        "imgBase": "https://img.rami-levy.co.il",
        "products": sorted(products, key=lambda x: (x["i"] == "", x["n"])),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"נשמרו {len(products)} מוצרים ל-{OUT}")


if __name__ == "__main__":
    main()
