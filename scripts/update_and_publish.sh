#!/bin/bash
# מעדכן את מחירי רמי לוי מהמחשב הזה ומעלה ל-GitHub. האפליקציה מתעדכנת אחרי דקה-שתיים.
set -e
cd "$(dirname "$0")/.."
python3 scripts/update_catalog.py
git add app/catalog.json
if git diff --cached --quiet; then echo "אין שינויים במחירים"; exit 0; fi
git commit -q -m "עדכון מחירים $(date +%F)"
git push -q
echo "המחירים עודכנו והועלו ✓"
