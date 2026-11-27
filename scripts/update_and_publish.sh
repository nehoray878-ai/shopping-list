#!/bin/bash
# מעדכן את המחירים של כל הרשתות מהמחשב הזה ומעלה ל-GitHub. האפליקציה מתעדכנת אחרי דקה-שתיים.
#
# המחירים לא נשמרים ב-main אלא בענף נפרד בשם data, שתמיד יש בו גרסה אחת בלבד (בלי היסטוריה),
# כדי שהמאגר לא יגדל בכל עדכון. GitHub בונה את האתר מ-main ומ-data יחד.
set -eo pipefail
cd "$(dirname "$0")/.."
REPO_URL=$(git remote get-url origin)

git pull -q --rebase || echo "git pull נכשל, ממשיכים עם הקוד הקיים"

# הגרסה שכבר באתר: כך מעלים רק קבצים שהשתנו, ורשת שהאתר שלה לא זמין כרגע נשארת עם הנתונים הקודמים
mkdir -p app/data
if [ ! -d app/data/.git ]; then
  git -C app/data init -q
  git -C app/data remote add origin "$REPO_URL"
fi
if git -C app/data fetch -q --depth=1 origin data 2>/dev/null && [ ! -f app/data/stores.json ]; then
  git -C app/data checkout -q FETCH_HEAD -- .
fi

/usr/bin/python3 scripts/update_catalog.py "$@" | { grep -v "^ *[a-z]*: [0-9]*/[0-9]*$"; true; }

cd app/data
mkdir -p .github/workflows
cat > .github/workflows/data-pushed.yml <<'EOF2'
# מסמן ל-main שיש מחירים חדשים; הפרסום עצמו ב-main (update-and-deploy.yml)
name: מחירים חדשים
on: push
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: echo "מחירים חדשים"
EOF2
git add -A
tree=$(git write-tree)
if [ "$(git rev-parse -q --verify 'FETCH_HEAD^{tree}' 2>/dev/null || true)" = "$tree" ]; then
  echo "אין שינויים במחירים"; exit 0
fi
commit=$(git -c user.name="price-updater" -c user.email="price-updater@users.noreply.github.com" \
  commit-tree "$tree" -m "מחירים $(date '+%F %H:%M')")
git update-ref refs/heads/data "$commit"
for p in 1 2 3; do
  if git push -q -f origin data; then
    git reflog expire --expire=now --all && git gc -q --prune=now
    echo "המחירים עודכנו והועלו ✓"; exit 0
  fi
  sleep 60
done
echo "ההעלאה נכשלה"; exit 1
