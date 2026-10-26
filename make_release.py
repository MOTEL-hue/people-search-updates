"""יוצר manifest.json לעדכון אוטומטי (רשימת קבצים עם חתימות) ותיקיית code לפרסום.

שימוש: python make_release.py [כתובת-בסיס-https] [תיקיית-פלט]
אחרי הרצה: דוחפים את code/ ואת manifest.json למאגר.
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://raw.githubusercontent.com/MOTEL-hue/people-search-updates/main"


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else BASE).rstrip("/")
    out = sys.argv[2] if len(sys.argv) > 2 else HERE
    ver = re.search(r'^VERSION = "([\d.]+)"', open(os.path.join(HERE, "code", "app.py"), encoding="utf-8").read(), re.M).group(1)
    code = os.path.join(HERE, "code")
    files = [("app.py", os.path.join(code, "app.py"))] + [
        ("static/" + f, os.path.join(code, "static", f)) for f in sorted(os.listdir(os.path.join(code, "static")))]
    entries = []
    for rel, src in files:
        entries.append({"path": rel, "url": "%s/code/%s" % (base, rel), "sha256": hashlib.sha256(open(src, "rb").read()).hexdigest()})
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"version": ver, "notes": "", "files": entries}, f, ensure_ascii=False, indent=2)
    print("גרסה", ver, "נוצרה ב", out)


if __name__ == "__main__":
    main()
