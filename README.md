# רשימת הקניות

אפליקציית רשימת קניות לטלפון, עם המחירים והתמונות של רמי לוי.

## מה יש בתיקייה
- `app/` – האפליקציה עצמה (`index.html`), הקטלוג (`catalog.json`), אייקונים, וקבצים שמאפשרים להתקין אותה במסך הבית.
- `scripts/update_catalog.py` – מוריד מחדש את כל המוצרים והמחירים מאתר רמי לוי.
- `.github/workflows/update-and-deploy.yml` – ב-GitHub: מעדכן מחירים כל בוקר ומפרסם את האפליקציה.

## הרצה על המחשב
```
python3 -m http.server 8765 --directory app
```
ואז לפתוח http://localhost:8765

## עדכון מחירים
המחשב מעדכן את המחירים אוטומטית כל בוקר ב-07:00 (או כשהוא מתעורר, אם היה כבוי) ומעלה ל-GitHub.
- היומן: `~/.shopping-list-updater/update.log`
- לעדכון ידני מיידי: `scripts/update_and_publish.sh`
- לביטול העדכון האוטומטי: `launchctl bootout gui/$(id -u)/com.shoppinglist.prices && rm ~/Library/LaunchAgents/com.shoppinglist.prices.plist`

רמי לוי חוסם את שרתי GitHub, ולכן ההורדה נעשית מהמחשב הביתי ולא מ-GitHub.

## הערות
- המחירים נלקחים מרמי לוי אונליין (חנות 331, ניתן לשנות ב-`STORE` בסקריפט) ועשויים להיות שונים מעט מהמחיר על המדף בסניף עפולה.
- הרשימה נשמרת בכל טלפון בנפרד (עדיין לא משותפת בין טלפונים).
