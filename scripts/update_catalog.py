#!/usr/bin/env python3
"""מוריד את קטלוג המוצרים והמחירים מאתר רמי לוי ושומר אותו ל-app/catalog.json.

הרצה:  python3 scripts/update_catalog.py
אפשר לשנות את STORE למספר חנות אחר של רמי לוי אונליין.
"""
import json, time, urllib.request, pathlib, datetime

STORE = "331"          # חנות רמי לוי אונליין שממנה נלקחים המחירים
PAGE = 100
API = "https://www.rami-levy.co.il/api/catalog"
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "catalog.json"


def fetch(offset):
    body = json.dumps({"aggs": 0, "store": STORE, "size": PAGE, "from": offset}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": "family-shopping-list/1.0",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    products, offset, total = {}, 0, None
    while total is None or offset < total:
        for attempt in range(3):
            try:
                d = fetch(offset); break
            except Exception as e:
                print("retry", offset, e); time.sleep(3)
        else:
            raise SystemExit("ההורדה נכשלה")
        total = d.get("total", 0)
        for p in d.get("data", []):
            price = (p.get("price") or {}).get("price")
            if price is None:
                continue
            img = (p.get("images") or {}).get("small") or ""
            products[p["id"]] = {
                "id": p["id"],
                "b": str(p.get("barcode") or ""),
                "n": p.get("name", "").strip(),
                "p": price,
                "d": (p.get("department") or {}).get("name", ""),
                "g": (p.get("group") or {}).get("name", ""),
                "i": img,
                "k": 1 if (p.get("prop") or {}).get("by_kilo") else 0,
            }
        offset += PAGE
        print(f"{min(offset, total)}/{total}", flush=True)
        time.sleep(0.6)
    out = {
        "store": STORE,
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "imgBase": "https://img.rami-levy.co.il",
        "products": sorted(products.values(), key=lambda x: x["n"]),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"נשמרו {len(products)} מוצרים ל-{OUT}")


if __name__ == "__main__":
    main()
