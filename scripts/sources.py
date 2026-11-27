"""מקורות המחירים של כל רשת (חוק שקיפות המחירים).

לכל רשת: רשימת הסניפים, הקובץ האחרון של "מחירים מלא" ו"מבצעים מלא" לכל סניף, והורדת קובץ.
כל הרשתות מפרסמות באותו פורמט XML, רק האתרים שונים:
- רמי לוי, יוחננוף, אושר עד: url.publishedprices.co.il (משתמש ציבורי בלי סיסמה)
- שופרסל: prices.shufersal.co.il
- ויקטורי: laibcatalog.co.il (ממשק JSON)
- קרפור: prices.carrefour.co.il
"""
import gzip
import http.cookiejar
import io
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import zipfile

UA = "family-shopping-list/1.0"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def _opener(ua=UA):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", ua)]
    return op


def _read(op, url, data=None, timeout=90, tries=3):
    for i in range(tries):
        try:
            with op.open(url, data=data, timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def unpack(data):
    """gzip / zip / XML גולמי -> bytes של XML."""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return z.read(z.namelist()[0])
    return data


def xml_text(data):
    """מחזיר את ה-XML כטקסט, לא משנה אם הוא UTF-8 או UTF-16."""
    data = unpack(data)
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8-sig", "replace")


def _parts(name):
    """PriceFull7290058140886-001-011-20261127-121500.gz -> (סניף, חותמת זמן)"""
    m = re.match(r"(?i)(?:price|promo)full\d+-(\d+)-(\d+)-(\d{8})-?(\d{4,6})", name)
    if not m:
        return None
    return str(int(m.group(2))), m.group(3) + m.group(4)[:4]


def latest_by_store(names):
    """{סניף: (שם קובץ מחירים, שם קובץ מבצעים)} – רק הקבצים החדשים ביותר."""
    out = {}
    for kind in ("pricefull", "promofull"):
        best = {}
        for n in names:
            if not n.lower().startswith(kind):
                continue
            p = _parts(n)
            if p and (p[0] not in best or p[1] > best[p[0]][1]):
                best[p[0]] = (n, p[1])
        for store, (n, _) in best.items():
            out.setdefault(store, [None, None])[0 if kind == "pricefull" else 1] = n
    return {s: tuple(v) for s, v in out.items() if v[0]}


def parse_stores(text):
    """קובץ הסניפים הסטנדרטי -> [{id, name, address, city, sub, type}]"""
    out = []
    for sub in re.findall(r"(?is)<SubChain>(.*?)</SubChain>", text) or [text]:
        m = re.search(r"(?i)<SubChainID>\s*(\d+)", sub)
        sub_id = str(int(m.group(1))) if m else ""
        for st in re.findall(r"(?is)<Store>(.*?)</Store>", sub):
            f = {k.lower(): v.strip() for k, v in re.findall(r"(?s)<(\w+)>([^<]*)</\1>", st)}
            if not f.get("storeid", "").strip().isdigit():
                continue
            out.append({
                "id": str(int(f["storeid"])),
                "name": _unescape(f.get("storename", "")),
                "address": _unescape(f.get("address", "")),
                "city": _unescape(f.get("city", "")),
                "sub": sub_id,
                "type": f.get("storetype", "1"),
            })
    return out


def _unescape(s):
    s = s.replace("&quot;", '"').replace("&amp;", "&").replace("&#39;", "'").replace("&apos;", "'")
    return re.sub(r"\s+", " ", s).strip()


# ---------- url.publishedprices.co.il (רמי לוי, יוחננוף, אושר עד) ----------
class Cerberus:
    BASE = "https://url.publishedprices.co.il"

    def __init__(self, user):
        self.user = user
        self.lock = threading.Lock()
        self.op = None

    def _login(self):
        op = _opener()
        tok = lambda h: re.search(rb'name="csrftoken" content="([^"]+)"', h).group(1).decode()
        t = tok(_read(op, self.BASE + "/login"))
        _read(op, self.BASE + "/login/user", urllib.parse.urlencode(
            {"r": "", "username": self.user, "password": "", "Submit": "Sign in", "csrftoken": t}).encode())
        self.token = tok(_read(op, self.BASE + "/file"))
        self.op = op

    def _session(self):
        with self.lock:
            if self.op is None:
                self._login()
            return self.op

    def names(self):
        op = self._session()
        raw = _read(op, self.BASE + "/file/json/dir", urllib.parse.urlencode({
            "sEcho": 1, "iColumns": 5, "iDisplayStart": 0, "iDisplayLength": 100000,
            "sSearch": "", "cd": "/", "csrftoken": self.token}).encode())
        return [r["name"] for r in json.loads(raw)["aaData"]]

    def files(self):
        self._names = self.names()
        return latest_by_store(self._names)

    def stores(self):
        names = sorted(n for n in getattr(self, "_names", None) or self.names() if n.lower().startswith("stores"))
        return parse_stores(xml_text(self.download(names[-1])))

    def download(self, name):
        return _read(self._session(), self.BASE + "/file/d/" + urllib.parse.quote(name), timeout=180)


# ---------- שופרסל ----------
class Shufersal:
    BASE = "https://prices.shufersal.co.il/FileObject/UpdateCategory"
    SKIP_SUBCHAINS = {"5", "32"}       # BE (פארם) וסיטונאות

    def __init__(self):
        self.op = _opener(BROWSER_UA)
        self.links = {}

    def _list(self, cat):
        out, page, pages = [], 1, 1
        while page <= pages:
            h = _read(self.op, f"{self.BASE}?catID={cat}&storeId=0&page={page}").decode("utf-8", "replace")
            nums = [int(x) for x in re.findall(r"page=(\d+)", h)]
            pages = max([pages] + nums)
            for link in re.findall(r'href="(https://[^"]+\.(?:gz|xml)[^"]*)"', h):
                link = link.replace("&amp;", "&")
                name = urllib.parse.unquote(link.split("?")[0].rsplit("/", 1)[-1])
                out.append(name)
                self.links[name] = link
            page += 1
        return out

    def files(self):
        return latest_by_store(self._list(2) + self._list(4))

    def stores(self):
        names = sorted(self._list(5))
        return [s for s in parse_stores(xml_text(self.download(names[-1]))) if s["sub"] not in self.SKIP_SUBCHAINS]

    def download(self, name):
        # הקישורים חתומים לזמן קצר: אם פג תוקפם, מושכים את רשימת הקבצים של הסניף מחדש
        try:
            return _read(self.op, self.links[name], timeout=180, tries=2)
        except Exception:
            p = _parts(name)
            cat = 2 if name.lower().startswith("price") else 4
            h = _read(self.op, f"{self.BASE}?catID={cat}&storeId={p[0] if p else 0}").decode("utf-8", "replace")
            for link in re.findall(r'href="(https://[^"]+)"', h):
                link = link.replace("&amp;", "&")
                if name in urllib.parse.unquote(link):
                    return _read(self.op, link, timeout=180)
            raise


# ---------- ויקטורי (laibcatalog) ----------
class Laib:
    BASE = "https://laibcatalog.co.il"

    def __init__(self, chain_id):
        self.chain_id = chain_id
        self.op = _opener(BROWSER_UA)

    def _files(self):
        if not hasattr(self, "_cache"):
            self._cache = json.loads(_read(self.op, f"{self.BASE}/webapi/api/getfiles?edi={self.chain_id}"))
        return self._cache

    def files(self):
        return latest_by_store([f["fileName"] for f in self._files()])

    def stores(self):
        names = sorted(f["fileName"] for f in self._files() if f.get("fileType", "").lower().startswith("store"))
        return parse_stores(xml_text(self.download(names[-1])))

    def download(self, name):
        return _read(self.op, f"{self.BASE}/webapi/{self.chain_id}/{urllib.parse.quote(name)}", timeout=180)


# ---------- קרפור ----------
class PublishPrice:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.op = _opener(BROWSER_UA)

    def _listing(self):
        if not hasattr(self, "_cache"):
            h = _read(self.op, self.base + "/").decode("utf-8", "replace")
            path = re.search(r"const path = ['\"]?([^'\";\s]*)", h).group(1)
            files = json.loads(re.search(r"const files = (\[.*?\]);?\s*\n", h).group(1))
            self._cache = (path, [f["name"] for f in files])
        return self._cache

    def files(self):
        return latest_by_store(self._listing()[1])

    def stores(self):
        names = sorted(n for n in self._listing()[1] if n.lower().startswith("stores"))
        return parse_stores(xml_text(self.download(names[-1])))

    def download(self, name):
        path = self._listing()[0]
        return _read(self.op, f"{self.base}/{path}/{urllib.parse.quote(name)}" if path else f"{self.base}/{name}", timeout=180)


# key, שם בעברית, צבע, יוצר המקור
CHAINS = [
    ("ramilevy", "רמי לוי", "#e30613", lambda: Cerberus("RamiLevi")),
    ("shufersal", "שופרסל", "#e2001a", Shufersal),
    ("victory", "ויקטורי", "#00843d", lambda: Laib("7290696200003")),
    ("yochananof", "יוחננוף", "#1f4e9c", lambda: Cerberus("yohananof")),
    ("osherad", "אושר עד", "#f39200", lambda: Cerberus("osherad")),
    ("carrefour", "קרפור", "#004e9f", lambda: PublishPrice("https://prices.carrefour.co.il")),
]
