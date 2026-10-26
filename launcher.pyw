# מפעיל התוכנה. הקובץ הזה לא מתעדכן: הוא מחליף את התיקייה code אם הורד עדכון, ואז מריץ את התוכנה.
import os
import runpy
import shutil
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.join(ROOT, "code")
PREV = os.path.join(ROOT, "code_prev")
if os.path.exists(os.path.join(ROOT, "installed.flag")) and os.environ.get("LOCALAPPDATA"):
    DATA = os.path.join(os.environ["LOCALAPPDATA"], "PeopleSearch")
else:
    DATA = os.path.join(ROOT, "data")
UPD = os.path.join(DATA, "update")
NEW = os.path.join(UPD, "new_code")
READY = os.path.join(UPD, "READY")
APPROVED = os.path.join(UPD, "APPROVED")


def log(msg):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "log.txt"), "a", encoding="utf-8") as f:
            f.write("%s [launcher] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def apply_update():
    # עדכון מותקן רק אחרי אישור המשתמש (או אם הופעלה התקנה אוטומטית)
    if not (os.path.isdir(NEW) and os.path.exists(READY) and os.path.exists(APPROVED)):
        return False
    shutil.rmtree(PREV, ignore_errors=True)
    os.rename(CODE, PREV)
    try:
        shutil.move(NEW, CODE)
    except Exception:
        shutil.rmtree(CODE, ignore_errors=True)
        os.rename(PREV, CODE)
        raise
    os.remove(READY)
    os.remove(APPROVED)
    log("הותקן עדכון")
    return True


def run():
    runpy.run_path(os.path.join(CODE, "app.py"), run_name="__main__")


os.chdir(ROOT)
applied = False
try:
    applied = apply_update()
except Exception:
    log("החלפת עדכון נכשלה:\n" + traceback.format_exc())
sys.path.insert(0, CODE)
try:
    run()
except SystemExit:
    raise
except Exception:
    log("הפעלה נכשלה:\n" + traceback.format_exc())
    if applied and os.path.isdir(PREV):
        log("חוזר לגרסה הקודמת")
        shutil.rmtree(CODE, ignore_errors=True)
        os.rename(PREV, CODE)
        run()
    else:
        raise
