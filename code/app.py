"""חיפוש אנשים: תוכנה מקומית שמתחברת לקובץ Access (טבלה M) וקוראת/כותבת אליו.

השרת מאזין רק למחשב המקומי (127.0.0.1), והממשק נפתח בדפדפן/חלון אפליקציה.
כל הנתונים נטענים לזיכרון (אלפי שורות), ולכן החיפוש מיידי; כתיבה חוזרת לקובץ עם גיבוי.
"""
import csv
import datetime
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
PORTABLE = os.path.basename(APP_DIR) == "code"  # מבנה נייד: תיקייה ראשית עם code (מתעדכן) ו-data (הנתונים)
ROOT_DIR = os.path.dirname(APP_DIR) if PORTABLE else APP_DIR


def _data_dir():
    """במבנה נייד הנתונים נשמרים בתיקיית data ליד התוכנה (עוברים איתה); אחרת בתיקיית המשתמש."""
    cands = []
    if PORTABLE:
        cands.append(os.path.join(ROOT_DIR, "data"))
    if os.environ.get("LOCALAPPDATA"):
        cands.append(os.path.join(os.environ["LOCALAPPDATA"], "PeopleSearch"))
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".w")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return d
        except OSError:
            continue
    return APP_DIR


DATA_DIR = _data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "changes.log")
DIAG_LOG = os.path.join(DATA_DIR, "log.txt")
VERSION = "2.0"
DEFAULT_UPDATE_URL = "https://raw.githubusercontent.com/MOTEL-hue/people-search-updates/main/manifest.json"

LABELS = {
    "ID": "ת״ז", "L_NAME": "שם משפחה", "F_NAME": "שם פרטי", "F_NAME_OLD": "שם פרטי קודם",
    "B_YEAR": "תאריך לידה", "ID_MAT_STATUS": "מצב אישי", "FATHER_NAME": "שם האב",
    "MOTHER_NAME": "שם האם", "ID_FATHER": "ת״ז האב", "ID_MOTHER": "ת״ז האם",
    "B_COUNTRY": "ארץ לידה", "ID_CITY": "עיר", "STREET": "רחוב", "HOUSE": "מספר בית", "BAIT": "דירה",
}
TEXT_FIELDS = ["L_NAME", "F_NAME", "F_NAME_OLD", "FATHER_NAME", "MOTHER_NAME", "STREET"]
HEB_RE = re.compile(r"[֐-׿]")
NIKUD_RE = re.compile(r"[֑-ׇ]")
FINALS = str.maketrans("ךםןףץ", "כמנפצ")
DEFAULT_CONFIG = {
    "db_path": "", "lookups": {}, "marital": {}, "backups_keep": 5, "page_size": 200,
}


class UserError(Exception):
    pass


def log(msg):
    """יומן לאבחון תקלות (log.txt ליד התוכנה); נחתך כשהוא גדול מדי."""
    try:
        if os.path.exists(DIAG_LOG) and os.path.getsize(DIAG_LOG) > 1_000_000:
            with open(DIAG_LOG, encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-500:]
            with open(DIAG_LOG, "w", encoding="utf-8") as f:
                f.writelines(tail)
        with open(DIAG_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def hebrew_db_error(e):
    """הופך שגיאת מנוע Access להסבר בעברית עם מה לעשות."""
    t = str(e)
    low = t.lower()
    if "architecture mismatch" in low or "im002" in low or "data source name not found" in low:
        return ("מנוע ה-Access במחשב לא מתאים לתוכנה (32 מול 64 סיביות) או שחסר. "
                "התקינו 'Microsoft Access Database Engine 2016 Redistributable' בגרסת 64 סיביות מאתר מיקרוסופט. "
                "אם Office אצלכם הוא 32 סיביות, יש להתקין את גרסת 32 סיביות ולבקש ממני גרסה מתאימה.")
    if "password" in low or "not a valid password" in low:
        return "הקובץ מוגן בסיסמה, או שהסיסמה שגויה. הקלידו את הסיסמה בתיבה המתאימה."
    if "exclusively" in low or "could not lock" in low or "locked" in low:
        return "הקובץ נעול כי הוא פתוח באופן בלעדי בתוכנה אחרת (למשל Access). סגרו אותו ונסו שוב."
    if "unrecognized database format" in low or "not a valid" in low:
        return "פורמט הקובץ לא מוכר. ייתכן שהקובץ פגום, או שנוצר בגרסה חדשה מדי של Access."
    if "disk or network error" in low or "path" in low and "not" in low:
        return "אין גישה לקובץ (כונן ברשת מנותק או הרשאות). העתיקו את הקובץ לכונן המקומי ונסו שוב."
    if "no read permission" in low:
        return "אין הרשאת קריאה לחלק מהקובץ. נסו עם חשבון אחר או העתקה של הקובץ."
    return "פתיחת הקובץ נכשלה. פרטים טכניים (לשליחה אליי מלשונית אבחון): " + t[:200]


_NORM_TABLE = {ord(c): None for c in "\"'`״׳"}
_NORM_TABLE.update({c: None for c in range(0x591, 0x5c8)})
_NORM_TABLE.update({ord(a): ord(b) for a, b in zip("ךםןףץ", "כמנפצ")})


def norm(s):
    if s is None:
        return ""
    return " ".join(str(s).lower().translate(_NORM_TABLE).split())


@lru_cache(maxsize=150000)
def norm_c(s):
    """norm עם זיכרון: שמות, רחובות וערים חוזרים הרבה, ולכן ייבוא מיליוני שורות הרבה יותר זול."""
    return norm(s)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def code_of(v):
    """מפתח אחיד לקודים (מספר/טקסט): '15', 15, 15.0 -> '15'."""
    if v is None or v == "":
        return ""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(v).strip()
    except (TypeError, ValueError):
        return str(v).strip()


def parse_birth(b):
    """B_YEAR בצורת yyyymmdd (גם חלקית) -> (שנה, חודש, יום) או None."""
    s = re.sub(r"\D", "", str(b or ""))
    if len(s) < 4:
        return None
    y = int(s[:4])
    m = int(s[4:6]) if len(s) >= 6 else 0
    d = int(s[6:8]) if len(s) >= 8 else 0
    return y, m, d


def full_date(bd):
    if not bd or not bd[1] or not bd[2]:
        return None
    try:
        return datetime.date(*bd)
    except ValueError:
        return None


def age_of(bd, today=None):
    if not bd or bd[0] < 1800:
        return None
    today = today or datetime.date.today()
    dt = full_date(bd)
    if dt:
        return today.year - dt.year - ((today.month, today.day) < (dt.month, dt.day))
    return today.year - bd[0]


def israeli_id_valid(v):
    s = re.sub(r"\D", "", str(v or "")).zfill(9)
    if len(s) != 9 or int(s) == 0:
        return False
    tot = 0
    for i, ch in enumerate(s):
        n = int(ch) * (1 + i % 2)
        tot += n - 9 if n > 9 else n
    return tot % 10 == 0


LOCAL_DB = os.path.join(DATA_DIR, "work.db")


def set_low_priority(low):
    """בזמן בניית העותק התוכנה רצה בעדיפות נמוכה, כדי שהמחשב ימשיך להגיב."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000 if low else 0x00000020)
    except Exception:
        pass
BATCH = 20000
MAX_EXPORT = 500_000
QUOTE_HI = "\U0010ffff"


_PH = str.maketrans({"ת": "ט", "ק": "כ", "ע": "א", "ש": "ס", "ח": "כ", "ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})
_VOWELS = set("אויה")
HEB_LETTERS = "אבגדהוזחטיכלמנסעפצקרשת"


def phonetic_key(w):
    """מפתח צליל גס: מאחד אותיות דומות (ת/ט, כ/ק/ח, ס/ש, א/ע) ומוריד אותיות ניקוד (א,ו,י,ה) שאינן ראשונות."""
    w = str(w).translate(_PH)
    if not w:
        return ""
    out = [w[0]]
    for ch in w[1:]:
        if ch in _VOWELS or ch == out[-1]:
            continue
        out.append(ch)
    return "".join(out)


def edits1(w):
    """כל המילים במרחק עריכה אחד (מחיקה, הוספה, החלפה, היפוך שתי אותיות)."""
    letters = HEB_LETTERS + "ךםןףץ"
    splits = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    out = {a + b[1:] for a, b in splits if b}
    out |= {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
    out |= {a + c + b[1:] for a, b in splits if b for c in letters}
    out |= {a + c + b for a, b in splits for c in letters}
    out.discard(w)
    return out


def levenshtein(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def adopt_legacy_files():
    """קבצים מגרסאות קודמות (ליד התוכנה או בתיקיית המשתמש) עוברים לתיקיית הנתונים. העברה מיידית באותו כונן."""
    srcs = []
    for d in (APP_DIR, ROOT_DIR, os.path.join(os.environ.get("LOCALAPPDATA", ""), "PeopleSearch") if os.environ.get("LOCALAPPDATA") else None):
        if d and os.path.abspath(d) != os.path.abspath(DATA_DIR) and d not in srcs:
            srcs.append(d)
    for name in ("work.db", "work.db-wal", "work.db-shm", "config.json", "changes.log"):
        new = os.path.join(DATA_DIR, name)
        if os.path.exists(new):
            continue
        for d in srcs:
            old = os.path.join(d, name)
            if os.path.exists(old):
                if name.startswith("work.db-") and not os.path.exists(os.path.join(DATA_DIR, "work.db")):
                    continue
                try:
                    shutil.move(old, new)
                    log("הועבר מהתיקייה הישנה: " + old)
                except OSError as e:
                    log("העברת %s נכשלה: %r" % (old, e))
                break


def esc_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fts_term(w):
    return '"%s"' % w.replace('"', '""')


class Db:
    """מקור האמת הוא קובץ ה-Access. לחיפוש מהיר נבנה עותק מקומי (SQLite) עם אינדקסים, כך שהזיכרון נשאר קטן
    גם כשיש מיליוני רשומות. כתיבה נעשית ל-Access, ואז משתקפת בעותק."""
    TABLE = "M"
    STEPS = 4

    def __init__(self):
        self.lock = threading.RLock()
        self.path = None
        self.conn = None  # חיבור ל-Access
        self.kind = None
        self.local = None  # חיבור לעותק המקומי
        self.cfg = load_config()
        self.cols = []
        self.types = {}
        self.derived = []
        self.total = 0
        self.stale = None
        self.last_expanded = {}
        self.standalone = False
        self.marital_codes = []
        self.lookups = {}  # table -> {"cols","key","name","map","rows"}
        self.progress = {"state": "idle", "stage": "", "step": 0, "done": 0, "total": 0, "t0": 0, "error": None}

    # ---------- חיבור ----------
    def open(self, path, password=None, confirm_replace=False):
        if self.standalone and not confirm_replace:
            raise UserError("אתם במצב עצמאי: העותק המקומי הוא המקור היחיד. פתיחת קובץ Access תחליף אותו ותמחק את כל השינויים שנעשו בו. "
                            "אשרו את ההחלפה כדי להמשיך.")
        path = (path or "").strip().strip('"')
        if not path:
            raise UserError("לא נבחר קובץ. לחצו 'בחירת קובץ' או הדביקו נתיב מלא.")
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise UserError("הקובץ לא נמצא: " + path)
        with self.lock:
            self.close()
            if path.lower().endswith((".sqlite", ".db")):
                import sqlite3
                conn = sqlite3.connect(path, check_same_thread=False)
                kind = "sqlite"
            else:
                try:
                    import pyodbc
                except ImportError:
                    raise UserError("חסרה הספרייה pyodbc (ראו README).")
                drivers = [d for d in pyodbc.drivers() if "Access Driver" in d]
                if not drivers:
                    log("אין דרייבר Access. דרייברים: %s" % pyodbc.drivers())
                    raise UserError(
                        "לא נמצא מנוע Access במחשב. יש להתקין בחינם 'Microsoft Access Database Engine 2016 Redistributable' "
                        "(64 סיביות) מאתר מיקרוסופט, או להתקין Office.")
                cs = "Driver={%s};DBQ=%s;" % (drivers[-1], path)
                if password:
                    cs += "PWD=%s;" % password
                log("פותח קובץ: %s (דרייבר: %s)" % (path, drivers[-1]))
                try:
                    conn = pyodbc.connect(cs, autocommit=False)
                except pyodbc.Error as e:
                    log("שגיאת חיבור: %r" % (e,))
                    raise UserError(hebrew_db_error(e))
                kind = "access"
            self.conn, self.kind, self.path = conn, kind, path
        self.start_load()

    def adopt(self, path):
        """מצרף עותק מהיר (work.db) קיים מתיקייה אחרת, כדי שלא יהיה צורך לבנות אותו מחדש."""
        import sqlite3
        path = (path or "").strip().strip('"')
        if not os.path.isfile(path):
            raise UserError("הקובץ לא נמצא: " + path)
        if os.path.abspath(path) == os.path.abspath(LOCAL_DB):
            return
        try:
            c = sqlite3.connect(path)
            ok = c.execute("SELECT v FROM meta WHERE k='done'").fetchone()
            c.close()
        except sqlite3.Error:
            ok = None
        if not ok or ok[0] != "1":
            raise UserError("זה לא קובץ עותק מהיר תקין של התוכנה (work.db).")
        if self.standalone or self.progress["state"] == "loading":
            raise UserError("אי אפשר לצרף עותק כרגע.")
        self.close()
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(LOCAL_DB + ext)
            except OSError:
                pass
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(path + ext):
                try:
                    os.replace(path + ext, LOCAL_DB + ext)
                except OSError:
                    shutil.copy2(path + ext, LOCAL_DB + ext)
        log("צורף עותק מהיר קיים: " + path)

    def start_load(self, force=False):
        if not self.conn:
            raise UserError("לא נבחר קובץ")
        if self.progress["state"] == "loading":
            return
        self.progress = {"state": "loading", "stage": "מתחיל", "step": 0, "done": 0, "total": 0, "t0": time.time(), "error": None}
        threading.Thread(target=self._load_thread, args=(force,), daemon=True).start()

    def _load_thread(self, force):
        set_low_priority(True)
        try:
            self._load_all(force)
            self.progress["state"] = "ready"
            log("מוכן: %d רשומות, %.1f שניות" % (self.total, time.time() - self.progress["t0"]))
            self.cfg["db_path"] = self.path
            self.cfg["standalone"] = False
            self.standalone = False
            save_config(self.cfg)
            if self.cfg.get("auto_standalone", True):
                self.detach()
        except Exception as e:  # noqa
            import traceback
            log("טעינת הנתונים נכשלה: %r\n%s" % (e, traceback.format_exc()))
            msg = str(e) if isinstance(e, UserError) else hebrew_db_error(e)
            self.progress["state"] = "error"
            self.close()
            self.progress["error"] = msg
        finally:
            set_low_priority(False)

    def close(self):
        for c in (self.conn, self.local):
            if c:
                try:
                    c.close()
                except Exception:
                    pass
        self.conn = self.local = self.path = None
        self.lookups = {}
        if self.progress["state"] != "error":
            self.progress["state"] = "idle"

    @property
    def connected(self):
        return self.local is not None and (self.conn is not None or self.standalone) and self.progress["state"] == "ready"

    def _stage(self, step, name, total=0):
        self.progress.update(step=step, stage=name, done=0, total=total)
        log("שלב %d/%d: %s%s" % (step, self.STEPS, name, " (%d)" % total if total else ""))

    def _ready(self):
        if self.progress["state"] == "loading":
            raise UserError("הנתונים עדיין נטענים, המתינו לסיום.")
        if not self.local or not (self.conn or self.standalone):
            raise UserError("לא נבחר קובץ")

    # ---------- קריאה מהקובץ המקורי ----------
    @staticmethod
    def _clean(row):
        for n, v in row.items():
            t = type(v)
            if t is int or t is str or v is None:
                continue
            if hasattr(v, "is_integer") or t.__name__ == "Decimal":
                v = float(v)
                row[n] = int(v) if v == int(v) else v
            elif isinstance(v, (datetime.datetime, datetime.date)):
                row[n] = v.strftime("%Y%m%d")
        return row

    @staticmethod
    def _col_types(desc, names, sample):
        types = {}
        for n, d in zip(names, desc):
            tc = d[1]
            if tc in (int, bool):
                types[n] = "int"
            elif tc is float or (tc is not None and getattr(tc, "__name__", "") == "Decimal"):
                types[n] = "float"
            elif tc is str:
                types[n] = "str"
            else:
                vals = [r[n] for r in sample if r[n] not in (None, "")]
                if vals and all(isinstance(v, int) for v in vals):
                    types[n] = "int"
                elif vals and all(isinstance(v, (int, float)) for v in vals):
                    types[n] = "float"
                else:
                    types[n] = "str"
        return types

    def _count(self, table):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM [%s]" % table)
            return int(cur.fetchone()[0])
        except Exception:
            return 0
        finally:
            cur.close()

    def _has_table(self, name):
        """בודק קיום טבלה בלי לקרוא את MSysObjects (שבדרך כלל חסומה לקריאה דרך ODBC)."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM [%s] WHERE 1=0" % name)
            return True
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            return False
        finally:
            cur.close()

    def _read_small(self, table):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM [%s]" % table)
        desc = cur.description
        names = [d[0] for d in desc]
        rows = [self._clean(dict(zip(names, r))) for r in cur.fetchall()]
        cur.close()
        return names, self._col_types(desc, names, rows[:2000]), rows

    def _guess_lookup(self, names, types, rows):
        keyc = None
        for n in names:
            if types[n] in ("int", "float") and re.search(r"ID|CODE|KOD|NUM|CITY|COUNTRY", n.upper()):
                keyc = n
                break
        keyc = keyc or next((n for n in names if types[n] in ("int", "float")), names[0])
        best, best_score = None, -1
        for n in names:
            if n == keyc or types[n] != "str":
                continue
            vals = [str(r[n]) for r in rows[:300] if r[n]]
            if not vals:
                continue
            score = sum(1 for v in vals if HEB_RE.search(v)) / len(vals)
            if score > best_score:
                best, best_score = n, score
        namec = best or next((n for n in names if n != keyc), keyc)
        return keyc, namec

    def _load_lookups(self):
        out = {}
        for t in ("CITY", "COUNTRY", "StreetList"):
            if not self._has_table(t):
                continue
            if self._count(t) > 300000:
                log("טבלת העזר %s גדולה מדי ולכן מדולגת" % t)
                continue
            try:
                ln, lt, lr = self._read_small(t)
            except Exception as e:
                log("קריאת %s נכשלה: %r" % (t, e))
                continue
            k, n = self._guess_lookup(ln, lt, lr)
            ov = self.cfg.get("lookups", {}).get(t, {})
            k = ov.get("key") if ov.get("key") in ln else k
            n = ov.get("name") if ov.get("name") in ln else n
            out[t] = {"cols": ln, "key": k, "name": n, "count": len(lr),
                      "map": {code_of(r[k]): str(r[n] or "") for r in lr if code_of(r[k]) != ""}}
            if t == "StreetList":
                out[t]["names"] = sorted({str(r[n]) for r in lr if r.get(n)})[:8000]
        self.lookups = out

    def _maps(self):
        return (self.lookups.get("CITY", {}).get("map", {}), self.lookups.get("COUNTRY", {}).get("map", {}),
                {code_of(k): v for k, v in self.cfg.get("marital", {}).items()})

    def _lk_sig(self):
        import hashlib
        city, country, _ = self._maps()
        return hashlib.md5(json.dumps([sorted(city.items()), sorted(country.items())], ensure_ascii=False).encode()).hexdigest()

    # ---------- העותק המקומי ----------
    SCHEMA = "3"
    DERIVED = ("L_NAME", "F_NAME")  # לשדות האלה נשמר עותק מנורמל עם אינדקס

    def _fts_text(self, row, city, country):
        """טקסט החיפוש החופשי של שורה (מנורמל): שמות, הורים, רחוב, עיר, ארץ, ת״ז ומספר בית."""
        nz = norm_c
        c = nz(city.get(code_of(row.get("ID_CITY")), ""))
        k = nz(country.get(code_of(row.get("B_COUNTRY")), ""))
        house = row.get("HOUSE")
        parts = [nz(row.get(f)) for f in TEXT_FIELDS] + [c, k, str(row.get("ID")), "" if house is None else str(house)]
        return " ".join(x for x in parts if x)

    def _prep(self, row):
        return [row.get(c) for c in self.cols] + [norm_c(row.get(f)) for f in self.DERIVED if f in self.cols]

    def _all_cols_sql(self):
        return ",".join('"%s"' % c for c in self.cols + self.derived)

    def _set_derived(self):
        self.derived = ["n_" + f for f in self.DERIVED if f in self.cols]

    def _meta(self, key=None, val=None):
        if val is None:
            r = self.local.execute("SELECT v FROM meta WHERE k=?", [key]).fetchone()
            return r[0] if r else None
        self.local.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", [key, str(val)])

    def _file_key(self):
        st = os.stat(self.path)
        return "%d|%d" % (st.st_size, int(st.st_mtime))

    def _open_local(self):
        import sqlite3
        self.local = sqlite3.connect(LOCAL_DB, check_same_thread=False)
        self.local.row_factory = sqlite3.Row
        self.local.execute("PRAGMA cache_size=-64000")
        self.local.execute("PRAGMA temp_store=FILE")
        self.local.create_function("nrm", 1, norm_c, deterministic=True)

    FTS_SQL = "CREATE VIRTUAL TABLE fts USING fts5(s_all, content='', contentless_delete=1, tokenize='unicode61', detail=column)"

    def _create_tables(self, db, name="m"):
        defs = []
        for c in self.cols:
            if c == "ID":
                defs.append('"ID" INTEGER PRIMARY KEY')
            else:
                defs.append('"%s" %s' % (c, {"int": "INTEGER", "float": "REAL"}.get(self.types[c], "TEXT")))
        defs += ['"%s" TEXT' % d for d in self.derived]
        db.execute("CREATE TABLE %s (%s)" % (name, ",".join(defs)))
        db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        db.execute("CREATE TABLE words (w TEXT, kind TEXT, c INTEGER, pk TEXT, PRIMARY KEY (w, kind)) WITHOUT ROWID")
        db.execute(self.FTS_SQL)

    def _create_indexes(self, db):
        for name, cols, part in (("i_name", ["n_L_NAME", "n_F_NAME", "B_YEAR"], ""),
                                 ("i_father", ["ID_FATHER"], '"ID_FATHER"<>0'), ("i_mother", ["ID_MOTHER"], '"ID_MOTHER"<>0'),
                                 ("i_city", ["ID_CITY", "STREET", "HOUSE"], "")):
            if all(c in self.cols + self.derived for c in cols):
                db.execute('CREATE INDEX %s ON m (%s)%s' % (name, ",".join('"%s"' % c for c in cols), (" WHERE " + part) if part else ""))
        db.execute("CREATE INDEX i_words_pk ON words (pk)")

    def _build_words(self, db):
        """מילון שמות (משפחה/פרטי) לחיפוש מקורב ותיקון שגיאות כתיב."""
        db.execute("DELETE FROM words")
        for kind, col in (("L", "n_L_NAME"), ("F", "n_F_NAME")):
            if col not in self.derived:
                continue
            acc = {}
            for w, c in db.execute('SELECT "%s", COUNT(*) FROM m WHERE "%s"<>\'\' GROUP BY 1' % (col, col)):
                for part in str(w).split():
                    acc[part] = acc.get(part, 0) + c
            db.executemany("INSERT OR REPLACE INTO words (w, kind, c, pk) VALUES (?,?,?,?)", [(w, kind, c, phonetic_key(w)) for w, c in acc.items()])
        db.commit()

    def _add_words(self, row):
        for kind, col in (("L", "L_NAME"), ("F", "F_NAME")):
            for part in norm_c(row.get(col)).split():
                self.local.execute("INSERT INTO words (w, kind, c, pk) VALUES (?,?,1,?) ON CONFLICT(w, kind) DO UPDATE SET c=c+1",
                                   [part, kind, phonetic_key(part)])

    def _local_fresh(self):
        """האם העותק המקומי תואם את הקובץ. מחזיר: 'fresh' | 'stale' | None (צריך לבנות מחדש)."""
        import sqlite3
        if not os.path.exists(LOCAL_DB):
            return None
        try:
            self._open_local()
            if self._meta("done") != "1" or self._meta("cols") != json.dumps(self.cols):
                return None
            same_file = self._meta("path") == self.path
            if not same_file and self._meta("file_key") != self._file_key():
                return None
            if not same_file:
                self._meta("path", self.path)
                self.local.commit()
            if self._meta("schema") != self.SCHEMA:
                self._migrate_local()
            if self._meta("lk_sig") != self._lk_sig():
                self._reindex()
            if self._meta("file_key") == self._file_key():
                self.stale = None
                return "fresh"
            n_local = self.local.execute("SELECT COUNT(*) FROM m").fetchone()[0]
            if self._meta("self_written") == "1" and n_local == self._count(self.TABLE):
                self._meta("file_key", self._file_key())
                self.local.commit()
                self.stale = None
                return "fresh"
            self.stale = "הקובץ ב-Access השתנה מאז העדכון האחרון של העותק המקומי."
            return "stale"
        except (sqlite3.Error, KeyError) as e:
            log("העותק המקומי לא תקין: %r" % (e,))
            return None

    def _migrate_local(self):
        """משדרג עותק מקומי של גרסה קודמת לעותק קטן ומהיר יותר, בלי לקרוא שוב את קובץ ה-Access."""
        import sqlite3
        log("משדרג עותק מקומי ישן")
        self._stage(2, "משדרג את העותק הקיים (בלי לקרוא שוב את Access)")
        old_size = os.path.getsize(LOCAL_DB)
        if shutil.disk_usage(os.path.dirname(LOCAL_DB)).free < old_size * 0.8:
            raise UserError("אין מספיק מקום פנוי בדיסק לשדרוג העותק (נדרשים כ-%d MB פנויים)." % (old_size * 0.8 // 2**20))
        self.local.commit()
        self.local.close()
        self.local = None
        new = LOCAL_DB + ".new"
        for ext in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(new + ext)
            except OSError:
                pass
        db = sqlite3.connect(new)
        try:
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.create_function("nrm", 1, norm_c, deterministic=True)
            self._create_tables(db)
            db.execute("ATTACH ? AS old", [LOCAL_DB])
            cols = self._all_cols_sql()
            self._stage(2, "מעתיק את הנתונים לעותק החדש")
            db.execute("INSERT INTO m (%s) SELECT %s FROM old.m" % (cols, cols))
            db.commit()
            self._stage(3, "בונה חיפוש טקסט")
            db.execute("INSERT INTO fts(rowid, s_all) SELECT ID, s_all FROM old.m")
            db.execute("INSERT INTO meta SELECT k, v FROM old.meta")
            db.commit()
            db.execute("DETACH old")
            self._stage(3, "בונה אינדקסים")
            self._create_indexes(db)
            self.local = db
            self._build_words(db)
            self._meta("schema", self.SCHEMA)
            db.commit()
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.close()
            self.local = None
        except Exception:
            try:
                db.close()
            except Exception:
                pass
            self.local = None
            for ext in ("", "-wal", "-shm", "-journal"):
                try:
                    os.remove(new + ext)
                except OSError:
                    pass
            raise
        for ext in ("-wal", "-shm", "-journal"):
            try:
                os.remove(LOCAL_DB + ext)
            except OSError:
                pass
        os.replace(new, LOCAL_DB)
        self._open_local()
        log("שדרוג העותק הסתיים: %d MB -> %d MB" % (old_size // 2**20, os.path.getsize(LOCAL_DB) // 2**20))

    def _rebuild_fts(self):
        """בונה מחדש את אינדקס החיפוש החופשי (למשל אחרי שינוי טבלת ערים/ארצות)."""
        self._stage(4, "מעדכן את אינדקס החיפוש", self.local.execute("SELECT COUNT(*) FROM m").fetchone()[0])
        city, country, _ = self._maps()
        self.local.execute("DROP TABLE IF EXISTS fts")
        self.local.execute(self.FTS_SQL)
        sel = "SELECT %s FROM m WHERE ID>? ORDER BY ID LIMIT %d" % (",".join('"%s"' % c for c in self.cols), BATCH)
        last, done = -2 ** 63, 0
        idx = self.cols.index("ID")
        while True:
            chunk = self.local.execute(sel, [last]).fetchall()
            if not chunk:
                break
            self.local.executemany("INSERT INTO fts(rowid, s_all) VALUES (?, ?)",
                                   [(r[idx], self._fts_text(dict(zip(self.cols, tuple(r))), city, country)) for r in chunk])
            last = chunk[-1][idx]
            done += len(chunk)
            self.progress["done"] = done
        self._meta("lk_sig", self._lk_sig())
        self.local.commit()

    _reindex = _rebuild_fts

    def _build_local(self):
        """בונה עותק מקומי מהקובץ: קריאה בחתיכות (זיכרון קבוע), אינדקסים ואינדקס חיפוש טקסט."""
        need = os.path.getsize(self.path) * 2
        free = shutil.disk_usage(os.path.dirname(LOCAL_DB)).free
        if free < need:
            raise UserError("אין מספיק מקום פנוי בדיסק לעותק המהיר: נדרשים בערך %d MB ופנויים %d MB. "
                            "פנו מקום בדיסק ונסו שוב." % (need // 2**20, free // 2**20))
        if self.local:
            self.local.close()
            self.local = None
        for ext in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(LOCAL_DB + ext)
            except OSError:
                pass
        self._open_local()
        db = self.local
        db.execute("PRAGMA journal_mode=OFF")
        db.execute("PRAGMA synchronous=OFF")
        self._create_tables(db)
        city, country, _ = self._maps()
        ins = "INSERT OR REPLACE INTO m (%s) VALUES (%s)" % (self._all_cols_sql(), ",".join("?" * (len(self.cols) + len(self.derived))))
        total = self._count(self.TABLE)
        self._stage(2, "קורא מהקובץ ובונה עותק מהיר (פעם אחת)", total)
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM [%s]" % self.TABLE)
        names = [d[0] for d in cur.description]
        n = 0
        while True:
            chunk = cur.fetchmany(BATCH)
            if not chunk:
                break
            rows = [self._clean(dict(zip(names, r))) for r in chunk]
            db.executemany(ins, [self._prep(r) for r in rows])
            db.executemany("INSERT INTO fts(rowid, s_all) VALUES (?, ?)", [(r["ID"], self._fts_text(r, city, country)) for r in rows])
            n += len(rows)
            self.progress["done"] = n
            if n % 100000 < BATCH:
                log("נקראו %d רשומות (%.1f שניות)" % (n, time.time() - self.progress["t0"]))
        cur.close()
        db.commit()
        self._stage(3, "בונה אינדקסים")
        self._create_indexes(db)
        self._stage(4, "בונה מילון לחיפוש מקורב")
        self._build_words(db)
        db.commit()
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        for k, v in (("path", self.path), ("cols", json.dumps(self.cols)), ("file_key", self._file_key()),
                     ("lk_sig", self._lk_sig()), ("self_written", "0"), ("schema", self.SCHEMA), ("done", "1")):
            self._meta(k, v)
        db.commit()

    def _load_all(self, force=False):
        self._stage(1, "בודק את הקובץ")
        if not self._has_table(self.TABLE):
            raise UserError("בקובץ אין טבלה בשם M, ולכן הוא לא מתאים לתוכנה.")
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM [%s]" % self.TABLE)
        desc = cur.description
        names = [d[0] for d in desc]
        sample = [self._clean(dict(zip(names, r))) for r in cur.fetchmany(2000)]
        cur.close()
        if "ID" not in names:
            raise UserError("בטבלה M אין עמודה ID.")
        self.cols, self.types = names, self._col_types(desc, names, sample)
        self._set_derived()
        self._load_lookups()
        state = None if force else self._local_fresh()
        if state is None:
            self._build_local()
            self.stale = None
        else:
            self._stage(2, "פותח את העותק המהיר")
        self.total = self.local.execute("SELECT COUNT(*) FROM m").fetchone()[0]
        self.marital_codes = []
        if "ID_MAT_STATUS" in self.cols:
            cached = self._meta("marital_codes") if state is not None else None
            self.marital_codes = json.loads(cached) if cached else [
                (code_of(r[0]), r[1]) for r in self.local.execute('SELECT "ID_MAT_STATUS", COUNT(*) FROM m GROUP BY 1')]
        self._store_state()

    def _store_state(self):
        """שומר בעותק המקומי את מה שנדרש כדי לעבוד גם בלי קובץ ה-Access (מצב עצמאי)."""
        self._meta("types", json.dumps(self.types))
        self._meta("marital_codes", json.dumps(self.marital_codes))
        self._meta("lookups", json.dumps({t: {k: v for k, v in lk.items()} for t, lk in self.lookups.items()}, ensure_ascii=False))
        self._meta("src_path", self.path or "")
        self.local.commit()

    def detach(self):
        """מנתק את העותק המקומי מקובץ ה-Access. מיידי, כי כל הנתונים כבר בעותק."""
        self._ready()
        if self.standalone:
            raise UserError("כבר במצב עצמאי")
        with self.lock:
            self._store_state()
            self._meta("standalone", "1")
            self.local.commit()
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
            self.standalone = True
            self.cfg["standalone"] = True
            save_config(self.cfg)
            log("נותק מקובץ ה-Access: עובדים במצב עצמאי על work.db")

    def open_standalone(self):
        import sqlite3
        if not os.path.exists(LOCAL_DB):
            raise UserError("העותק המקומי (work.db) לא נמצא. פתחו שוב את קובץ ה-Access.")
        with self.lock:
            self.close()
            self._open_local()
            try:
                if self._meta("done") != "1" or self._meta("standalone") != "1":
                    raise UserError("העותק המקומי לא במצב עצמאי תקין. פתחו שוב את קובץ ה-Access.")
                self.cols = json.loads(self._meta("cols"))
                self.types = json.loads(self._meta("types"))
                self._set_derived()
                if self._meta("schema") != self.SCHEMA:
                    self._migrate_local()
                self.types = json.loads(self._meta("types"))
                self.lookups = json.loads(self._meta("lookups") or "{}")
                self.marital_codes = [tuple(x) for x in json.loads(self._meta("marital_codes") or "[]")]
            except (sqlite3.Error, TypeError, ValueError) as e:
                log("פתיחת מצב עצמאי נכשלה: %r" % (e,))
                self.local.close()
                self.local = None
                raise UserError("העותק המקומי פגום. פתחו שוב את קובץ ה-Access כדי לבנות אותו מחדש.")
            self._set_derived()
            self.path = self._meta("src_path") or None
            self.standalone = True
            self.total = self.local.execute("SELECT COUNT(*) FROM m").fetchone()[0]
            self.progress = {"state": "ready", "stage": "", "step": 4, "done": 0, "total": 0, "t0": time.time(), "error": None}
            log("נפתח במצב עצמאי: %d רשומות" % self.total)

    def reload(self):
        if self.standalone:
            raise UserError("במצב עצמאי אין קובץ Access לעדכן ממנו.")
        self.progress["state"] = "idle"
        self.start_load(force=True)

    def apply_config(self, old_lookups):
        """שינוי הגדרות: טבלאות עזר משנות את טקסט החיפוש, ושמות מצב אישי רק את התצוגה."""
        self._ready()
        if self.standalone and self.cfg.get("lookups", {}) != old_lookups:
            self.cfg["lookups"] = old_lookups
            save_config(self.cfg)
            raise UserError("במצב עצמאי אי אפשר לשנות את עמודות הקוד והשם של טבלאות העזר (רק שמות מצב אישי).")
        if self.cfg.get("lookups", {}) != old_lookups:
            self.progress = {"state": "loading", "stage": "מעדכן", "step": 4, "done": 0, "total": 0, "t0": time.time(), "error": None}

            def job():
                try:
                    self._load_lookups()
                    self._reindex()
                    self.progress["state"] = "ready"
                except Exception as e:  # noqa
                    log("עדכון אינדקס נכשל: %r" % (e,))
                    self.progress.update(state="error", error="עדכון האינדקס נכשל: %s" % e)
            threading.Thread(target=job, daemon=True).start()

    # ---------- הצגה ----------
    def _pub(self, r):
        d = {c: r[c] for c in self.cols}
        city, country, marital = self._maps()
        d["city_name"] = city.get(code_of(d.get("ID_CITY")), "")
        d["country_name"] = country.get(code_of(d.get("B_COUNTRY")), "")
        d["status_name"] = marital.get(code_of(d.get("ID_MAT_STATUS")), "")
        d["age"] = age_of(parse_birth(d.get("B_YEAR")))
        return d

    def _select(self, where="", args=(), order="", limit=None, offset=0):
        sql = "SELECT %s FROM m" % ",".join('"%s"' % c for c in self.cols)
        if where:
            sql += " WHERE " + where
        if order:
            sql += " ORDER BY " + order
        if limit is not None:
            sql += " LIMIT %d OFFSET %d" % (limit, offset)
        return [self._pub(r) for r in self.local.execute(sql, list(args))]

    def get(self, pid):
        self._ready()
        try:
            pid = int(float(pid))
        except (TypeError, ValueError):
            return None
        rows = self._select('"ID"=?', [pid])
        return rows[0] if rows else None

    # ---------- חיפוש ----------
    def _codes(self, kind, v, mode):
        m = self.lookups.get("CITY" if kind == "city" else "COUNTRY", {}).get("map", {})
        nv = norm(v)
        out = set()
        for c, n in m.items():
            nn = norm(n)
            if (mode == "exact" and nn == nv) or (mode != "exact" and all(w in nn for w in nv.split())):
                out.add(c)
        if re.fullmatch(r"\d+", str(v).strip()):
            out.add(code_of(v))
        return [int(c) for c in out if re.fullmatch(r"-?\d+", c)][:5000]

    def similar_words(self, w, kinds="LF", limit=25):
        """מילים דומות מהמילון: שגיאות כתיב (מרחק עריכה 1, ובמילים ארוכות גם 2) ומילים בעלות צליל זהה."""
        if len(w) < 2 or not re.fullmatch(r"[א-ת]+", w):
            return []
        ph = ",".join("?" * len(kinds))
        found = {}

        def look(cands):
            cands = list(cands)
            for i in range(0, len(cands), 800):
                part = cands[i:i + 800]
                for cw, c in self.local.execute(
                        'SELECT w, SUM(c) FROM words WHERE kind IN (%s) AND w IN (%s) GROUP BY w' % (ph, ",".join("?" * len(part))),
                        list(kinds) + part):
                    found[cw] = (levenshtein(w, cw), -c)
        look(edits1(w))
        for cw, c in self.local.execute('SELECT w, SUM(c) FROM words WHERE kind IN (%s) AND pk=? GROUP BY w ORDER BY 2 DESC LIMIT 200' % ph,
                                        list(kinds) + [phonetic_key(w)]):
            if cw != w and cw not in found:
                found[cw] = (min(levenshtein(w, cw), 2) + 0.5, -c)
        if len(w) >= 5:
            for cw, c in self.local.execute(
                    'SELECT w, SUM(c) FROM words WHERE kind IN (%s) AND w >= ? AND w < ? GROUP BY w' % ph, list(kinds) + [w[:2], w[:2] + QUOTE_HI]):
                if cw != w and cw not in found and levenshtein(w, cw, 2) <= 2:
                    found[cw] = (levenshtein(w, cw, 2), -c)
        found.pop(w, None)
        return [cw for cw, _ in sorted(found.items(), key=lambda x: x[1])[:limit]]

    def _fts(self, words, cl, args, fuzzy=False, expanded=None):
        """חיפוש חופשי: כל מילה חייבת להופיע (כתחילת מילה). במצב מקורב נוספות גם מילים דומות."""
        parts = []
        for w in words:
            term = fts_term(w) + "*"
            if fuzzy:
                sim = self.similar_words(w)
                if sim:
                    if expanded is not None:
                        expanded[w] = sim
                    term = "(%s OR %s)" % (term, " OR ".join(fts_term(x) for x in sim))
            parts.append(term)
        if parts:
            cl.append('"ID" IN (SELECT rowid FROM fts WHERE fts MATCH ?)')
            args.append(" AND ".join(parts))

    def _cx(self, k):
        """ביטוי SQL של שדה טקסט מנורמל: עמודה מוכנה עם אינדקס, או חישוב בזמן השאילתה."""
        return '"n_%s"' % k if "n_" + k in self.derived else 'nrm("%s")' % k

    def _where(self, p):
        f = p.get("f") or {}
        mode = p.get("mode") or "contains"
        cl, args = [], []
        words = norm(p.get("q", "")).split()
        fuzzy = bool(p.get("fuzzy"))
        expanded = {}
        self._fts(words, cl, args, fuzzy, expanded)
        for k in TEXT_FIELDS:
            if f.get(k) and k in self.cols:
                nv, col = norm(f[k]), self._cx(k)
                if not nv:
                    continue
                kinds = {"L_NAME": "L", "F_NAME": "F", "F_NAME_OLD": "F", "FATHER_NAME": "F", "MOTHER_NAME": "F"}.get(k)
                if fuzzy and kinds and len(nv.split()) == 1:
                    sim = self.similar_words(nv, kinds)
                    expanded[nv] = sim
                    allv = [nv] + sim
                    cl.append("%s IN (%s)" % (col, ",".join("?" * len(allv))))
                    args += allv
                elif mode == "exact":
                    cl.append('%s = ?' % col)
                    args.append(nv)
                elif mode == "starts":
                    cl.append('%s >= ? AND %s < ?' % (col, col))
                    args += [nv, nv + QUOTE_HI]
                else:
                    for w in nv.split():
                        cl.append('%s LIKE ? ESCAPE \'\\\'' % col)
                        args.append("%" + esc_like(w) + "%")
        self.last_expanded = expanded
        v = str(f.get("ID", "")).strip()
        if v:
            if mode == "exact" and re.fullmatch(r"\d+", v):
                cl.append('"ID" = ?')
                args.append(int(v))
            else:
                cl.append('CAST("ID" AS TEXT) LIKE ? ESCAPE \'\\\'')
                args.append(("" if mode == "starts" else "%") + esc_like(v.lstrip("0") or "0") + "%")
        for k in ("ID_FATHER", "ID_MOTHER", "HOUSE", "BAIT", "ID_MAT_STATUS"):
            v = str(f.get(k, "")).strip()
            if v and k in self.cols:
                cl.append('"%s" = ?%s' % (k, (' AND "%s"<>0' % k) if k in ("ID_FATHER", "ID_MOTHER") else ""))
                args.append(int(v) if re.fullmatch(r"\d+", v) else v)
        for k, kind in (("ID_CITY", "city"), ("B_COUNTRY", "country")):
            v = str(f.get(k, "")).strip()
            if v and k in self.cols:
                codes = self._codes(kind, v, mode)
                cl.append('"%s" IN (%s)' % (k, ",".join("?" * len(codes))) if codes else "0")
                args += codes

        def num(x):
            try:
                return int(x)
            except (TypeError, ValueError):
                return None
        yf, yt, af, at = (num(f.get(x)) for x in ("year_from", "year_to", "age_from", "age_to"))
        if "B_YEAR" in self.cols:
            if yf is not None:
                cl.append('"B_YEAR" >= ?')
                args.append("%04d" % yf)
            if yt is not None:
                cl.append('"B_YEAR" < ?')
                args.append("%04d" % (yt + 1))
            today = datetime.date.today()

            def years_ago(n):
                try:
                    return today.replace(year=today.year - n)
                except ValueError:
                    return today.replace(year=today.year - n, day=28)
            if af is not None:
                cl.append('"B_YEAR" <> \'\' AND "B_YEAR" <= ?')
                args.append(years_ago(af).strftime("%Y%m%d"))
            if at is not None:
                cl.append('"B_YEAR" > ?')
                args.append(years_ago(at + 1).strftime("%Y%m%d"))
            if num(f.get("born_month")):
                cl.append('substr("B_YEAR",5,2) = ?')
                args.append("%02d" % num(f["born_month"]))
        return " AND ".join("(%s)" % c for c in cl), args

    SORTS = {"L_NAME": '"n_L_NAME"', "F_NAME": '"n_F_NAME"', "age": '"B_YEAR" DESC',
             "city_name": '"ID_CITY"', "country_name": '"B_COUNTRY"', "status_name": '"ID_MAT_STATUS"'}

    def _order(self, p):
        sort = p.get("sort") or "L_NAME"
        col = self.SORTS.get(sort, '"%s"' % sort if sort in self.cols else '"ID"')
        if col.endswith(" DESC"):
            return col.replace(" DESC", "") + (" ASC" if p.get("desc") else " DESC")
        return col + (" DESC" if p.get("desc") else "")

    def search(self, p, limit=200, offset=0):
        """מחזיר (שורות, סה״כ, האם הסה״כ קטוע)."""
        self._ready()
        where, args = self._where(p)
        with self.lock:
            cap = 100000
            total = self.local.execute("SELECT COUNT(*) FROM (SELECT 1 FROM m%s LIMIT %d)" % (
                (" WHERE " + where) if where else "", cap + 1), args).fetchone()[0]
            capped = total > cap
            if capped:
                total = cap
            rows = self._select(where, args, self._order(p), limit, offset)
        return rows, total, capped

    def iter_search(self, p, cap=MAX_EXPORT):
        self._ready()
        where, args = self._where(p)
        sql = "SELECT %s FROM m%s ORDER BY %s LIMIT %d" % (",".join('"%s"' % c for c in self.cols),
                                                         (" WHERE " + where) if where else "", self._order(p), cap)
        with self.lock:
            cur = self.local.execute(sql, args)
            for chunk in iter(lambda: cur.fetchmany(5000), []):
                for r in chunk:
                    yield self._pub(r)

    def family(self, r):
        self._ready()
        pid = r["ID"]
        f_id, m_id = code_of(r.get("ID_FATHER")), code_of(r.get("ID_MOTHER"))
        ok = lambda c: c not in ("", "0")  # noqa
        father = self.get(f_id) if ok(f_id) else None
        mother = self.get(m_id) if ok(m_id) else None
        cond, a = [], []
        if ok(f_id):
            cond.append('("ID_FATHER"=? AND "ID_FATHER"<>0)')
            a.append(int(f_id))
        if ok(m_id):
            cond.append('("ID_MOTHER"=? AND "ID_MOTHER"<>0)')
            a.append(int(m_id))
        sib = self._select('"ID"<>? AND (%s)' % " OR ".join(cond), [pid] + a, '"ID"', 200) if cond else []
        sib_ids = {x["ID"] for x in sib} | {pid}
        maybe = []
        if all(c in self.cols for c in ("L_NAME", "FATHER_NAME", "MOTHER_NAME")) and r.get("FATHER_NAME") and r.get("MOTHER_NAME"):
            maybe = [x for x in self._select('"n_L_NAME"=? AND nrm("FATHER_NAME")=? AND nrm("MOTHER_NAME")=? AND "ID"<>?',
                                             [norm(r["L_NAME"]), norm(r["FATHER_NAME"]), norm(r["MOTHER_NAME"]), pid], '"ID"', 100)
                     if x["ID"] not in sib_ids]
        kids = self._select('("ID_FATHER"=? AND "ID_FATHER"<>0) OR ("ID_MOTHER"=? AND "ID_MOTHER"<>0)', [pid, pid], '"B_YEAR"', 200) if pid and "ID_FATHER" in self.cols else []
        addr = []
        if r.get("STREET") and "ID_CITY" in self.cols and "HOUSE" in self.cols:
            addr = self._select('"ID_CITY" IS ? AND "STREET"=? AND "HOUSE" IS ? AND "ID"<>?',
                                [r.get("ID_CITY"), r["STREET"], r.get("HOUSE"), pid], '"ID"', 60)
        return {"father": father, "mother": mother, "siblings": sib, "maybe_siblings": maybe, "children": kids, "same_address": addr}

    # ---------- כתיבה ----------
    def backup(self, force=False):
        """גיבוי מלא של הקובץ. בקובץ גדול זה איטי, ולכן אוטומטית פעם בחצי שעה לכל היותר."""
        if not self.path:
            return None
        if not force and time.time() - getattr(self, "_last_backup", 0) < 1800:
            return None
        if self.standalone:
            return self._backup_local()
        folder = os.path.join(os.path.dirname(self.path), "גיבויים_" + os.path.splitext(os.path.basename(self.path))[0])
        os.makedirs(folder, exist_ok=True)
        ext = os.path.splitext(self.path)[1]
        base = os.path.splitext(os.path.basename(self.path))[0]
        keep = int(self.cfg.get("backups_keep") or 5)
        free = shutil.disk_usage(folder).free
        if free < os.path.getsize(self.path) * 1.2:
            raise UserError("אין מספיק מקום פנוי בדיסק לגיבוי הקובץ (גודלו %d MB). פנו מקום או העבירו גיבויים ישנים." % (
                os.path.getsize(self.path) // 2**20))
        dst = os.path.join(folder, "%s_%s%s" % (base, datetime.datetime.now().strftime("%Y%m%d_%H%M%S"), ext))
        shutil.copy2(self.path, dst)
        self._last_backup = time.time()
        files = sorted(os.path.join(folder, x) for x in os.listdir(folder))
        for old in files[:-keep]:
            try:
                os.remove(old)
            except OSError:
                pass
        return dst

    def _backup_local(self):
        import sqlite3
        folder = os.path.join(os.path.dirname(LOCAL_DB), "גיבויים_עצמאי")
        os.makedirs(folder, exist_ok=True)
        if shutil.disk_usage(folder).free < os.path.getsize(LOCAL_DB) * 1.2:
            raise UserError("אין מספיק מקום פנוי בדיסק לגיבוי העותק (%d MB)." % (os.path.getsize(LOCAL_DB) // 2**20))
        dst = os.path.join(folder, "work_%s.db" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        out = sqlite3.connect(dst)
        try:
            self.local.backup(out)
        finally:
            out.close()
        self._last_backup = time.time()
        keep = int(self.cfg.get("backups_keep") or 5)
        for old in sorted(os.path.join(folder, x) for x in os.listdir(folder))[:-keep]:
            try:
                os.remove(old)
            except OSError:
                pass
        return dst

    def _coerce(self, col, v):
        t = self.types.get(col, "str")
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        if t in ("int", "float"):
            try:
                x = float(str(v).strip())
            except ValueError:
                raise UserError("בשדה %s צריך להיות מספר" % LABELS.get(col, col))
            return int(x) if t == "int" else x
        return str(v).strip()

    def _validate(self, data):
        if data.get("B_YEAR"):
            s = re.sub(r"\D", "", str(data["B_YEAR"]))
            if re.search(r"[^\d\s/.-]", str(data["B_YEAR"])) or len(s) not in (4, 8):
                raise UserError("תאריך לידה צריך להיות שנה (1950) או תאריך מלא בצורה 19500231 (שנה-חודש-יום)")
            if len(s) == 8 and full_date(parse_birth(s)) is None:
                raise UserError("תאריך הלידה אינו תאריך אמיתי")
            data["B_YEAR"] = s

    def _log(self, action, before, after):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": datetime.datetime.now().isoformat(timespec="seconds"), "action": action,
                                    "before": before, "after": after}, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _apply_row(self, pid, row):
        """מכניס/מחליף/מוחק רשומה בעותק המקומי (כולל אינדקס החיפוש). row=None פירושו מחיקה."""
        db = self.local
        if db.execute('SELECT 1 FROM m WHERE "ID"=?', [pid]).fetchone():
            db.execute("DELETE FROM fts WHERE rowid=?", [pid])
            db.execute('DELETE FROM m WHERE "ID"=?', [pid])
        if row is not None:
            city, country, _ = self._maps()
            vals = self._prep(row)
            db.execute("INSERT INTO m (%s) VALUES (%s)" % (self._all_cols_sql(), ",".join("?" * len(vals))), vals)
            db.execute("INSERT INTO fts(rowid, s_all) VALUES(?, ?)", [pid, self._fts_text(row, city, country)])
            self._add_words(row)
        db.commit()
        self.total = db.execute("SELECT COUNT(*) FROM m").fetchone()[0]

    def _mirror(self, pid):
        """משקף רשומה אחת מהקובץ אל העותק המקומי."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM [%s] WHERE [ID]=?" % self.TABLE, [pid])
            names = [d[0] for d in cur.description]
            fetched = cur.fetchone()
        finally:
            cur.close()
        self._apply_row(pid, self._clean(dict(zip(names, fetched))) if fetched else None)
        self._meta("self_written", "1")
        self._meta("file_key", self._file_key())
        self.local.commit()

    def save(self, mode, data):
        self._ready()
        with self.lock:
            data = {k: v for k, v in data.items() if k in self.cols}
            self._validate(data)
            pid = self._coerce("ID", data.get("ID"))
            if pid is None:
                raise UserError("חובה למלא ת״ז/מזהה")
            existing = self.get(pid)
            if mode == "add" and existing:
                raise UserError("כבר קיים אדם עם המזהה %s (%s %s)" % (pid, existing.get("F_NAME") or "", existing.get("L_NAME") or ""))
            if mode == "edit" and not existing:
                raise UserError("האדם לא נמצא (אולי נמחק בינתיים)")
            vals = {c: self._coerce(c, data.get(c)) for c in self.cols if c in data}
            self.backup()
            if self.standalone:
                row = {c: existing[c] for c in self.cols} if existing else {}
                row.update(vals)
                row["ID"] = pid
                self._apply_row(pid, row)
                self._log(mode, existing, vals)
                return self.get(pid)
            cur = self.conn.cursor()
            try:
                if mode == "add":
                    cols = list(vals)
                    cur.execute("INSERT INTO [%s] (%s) VALUES (%s)" % (
                        self.TABLE, ",".join("[%s]" % c for c in cols), ",".join("?" * len(cols))), [vals[c] for c in cols])
                else:
                    cols = [c for c in vals if c != "ID"]
                    cur.execute("UPDATE [%s] SET %s WHERE [ID]=?" % (self.TABLE, ",".join("[%s]=?" % c for c in cols)),
                                [vals[c] for c in cols] + [pid])
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                log("שמירה נכשלה: %r" % (e,))
                raise UserError("השמירה נכשלה. ייתכן שהקובץ נעול על ידי משתמש אחר או שהנתון לא תקין. (%s)" % str(e)[:150])
            finally:
                cur.close()
            self._mirror(pid)
            self._log(mode, existing, vals)
            return self.get(pid)

    def delete(self, pid):
        self._ready()
        with self.lock:
            r = self.get(pid)
            if not r:
                raise UserError("האדם לא נמצא")
            self.backup()
            if self.standalone:
                self._apply_row(r["ID"], None)
                self._log("delete", r, None)
                return
            cur = self.conn.cursor()
            try:
                cur.execute("DELETE FROM [%s] WHERE [ID]=?" % self.TABLE, [r["ID"]])
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                log("מחיקה נכשלה: %r" % (e,))
                raise UserError("המחיקה נכשלה. ייתכן שהקובץ נעול או שרשומות אחרות תלויות באדם הזה. (%s)" % str(e)[:150])
            finally:
                cur.close()
            self._mirror(r["ID"])
            self._log("delete", r, None)

    # ---------- חיפוש מרובה ----------
    def _exact_name(self, line):
        """התאמה מדויקת של שם פרטי + שם משפחה (בכל סדר). עם פסיק/טאב: צד אחד פרטי והשני משפחה."""
        parts = [norm(p) for p in re.split(r"[,;\t]+", line) if norm(p)]
        pairs = []
        if len(parts) == 2:
            pairs = [(parts[0], parts[1]), (parts[1], parts[0])]
        else:
            words = norm(line.replace(",", " ").replace(";", " ")).split()
            for i in range(1, len(words)):
                a, b = " ".join(words[:i]), " ".join(words[i:])
                pairs += [(a, b), (b, a)]
        if not pairs or "n_F_NAME" not in self.derived:
            return []
        cond = " OR ".join('("n_F_NAME"=? AND "n_L_NAME"=?)' for _ in pairs)
        return self._select(cond, [x for p in pairs for x in p], '"ID"', 200)

    def bulk(self, text, fuzzy=False, mode="auto"):
        """חיפוש רשימה. mode: auto | id (ת״ז מדויקת) | name_exact (שם פרטי ומשפחה מדויקים) | name_approx (מקורב)."""
        self._ready()
        if mode == "name_approx":
            fuzzy = True
        found, missing, seen = [], [], set()
        if mode == "id":
            tokens, dup = [], set()
            for t in re.findall(r"\d+", text or ""):
                t = t.lstrip("0") or "0"
                if t not in dup:
                    dup.add(t)
                    tokens.append(t)
            lines = [(t, "id") for t in tokens[:50000]]
        else:
            lines = [(l.strip(), mode) for l in re.split(r"[\r\n]+", text or "") if l.strip()]
        for line, kind in lines:
            matches = []
            if kind == "id" or (kind == "auto" and re.fullmatch(r"[\d\s,;\t-]+", line)):
                for tok in re.findall(r"\d+", line) if kind != "id" else [line]:
                    r = self.get(tok)
                    matches.append((tok, [r] if r else []))
            elif kind == "name_exact":
                matches.append((line, self._exact_name(line)))
            else:
                words = norm(re.sub(r"[,;\t]+", " ", line)).split()
                cl, args = [], []
                if words:
                    self._fts(words, cl, args, fuzzy)
                    if not fuzzy:
                        for w in words:
                            cl.append('nrm(coalesce("F_NAME",\'\')||\' \'||coalesce("L_NAME",\'\')||\' \'||coalesce("F_NAME_OLD",\'\')) LIKE ? ESCAPE \'\\\'')
                            args.append("%" + esc_like(w) + "%")
                matches.append((line, self._select(" AND ".join(cl), args, '"ID"', 50) if words else []))
            for label, ms in matches:
                if not ms:
                    missing.append(label)
                for d in ms:
                    d = dict(d)
                    d["query"] = label
                    d["multi"] = len(ms) > 1
                    found.append(d)
                    seen.add(d["ID"])
            if len(found) > 20000:
                break
        return {"found": found, "missing": missing, "unique": len(seen), "asked": len(lines)}

    # ---------- דוחות ----------
    def _limited(self, where, args=(), order='"ID"', limit=2000):
        rows = self._select(where, args, order, limit)
        return rows

    def report(self, name, p):
        self._ready()
        c = self.cols
        if name == "duplicates":
            if not all(x in c for x in ("F_NAME", "L_NAME", "B_YEAR")):
                raise UserError("חסרות עמודות לדוח הזה")
            groups = []
            for r in self.local.execute('SELECT "n_F_NAME","n_L_NAME","B_YEAR",COUNT(*) k FROM m WHERE "n_F_NAME"<>\'\' AND "n_L_NAME"<>\'\' '
                                        'GROUP BY 1,2,3 HAVING k>1 ORDER BY k DESC LIMIT 300').fetchall():
                groups.append(self._select('"n_F_NAME"=? AND "n_L_NAME"=? AND "B_YEAR" IS ?', [r[0], r[1], r[2]], '"ID"', 20))
            return {"title": "כפילויות אפשריות (אותו שם ואותו תאריך לידה). מוצגות עד 300 קבוצות", "groups": groups}
        if name == "missing":
            fields = [f for f in (p.get("fields") or ["B_YEAR", "ID_CITY", "STREET", "FATHER_NAME", "MOTHER_NAME"]) if f in c]
            cond = " OR ".join('"%s" IS NULL OR "%s"=\'\' OR "%s"=0' % (f, f, f) for f in fields)
            return {"title": "רשומות עם שדות חסרים (%s). מוצגות עד 2000" % ", ".join(LABELS.get(f, f) for f in fields),
                    "rows": self._limited(cond)}
        if name == "bad_birth":
            y = datetime.date.today().year
            cond = ('"B_YEAR" IS NOT NULL AND "B_YEAR"<>\'\' AND (length("B_YEAR") NOT IN (4,8) OR "B_YEAR" GLOB \'*[^0-9]*\' '
                    'OR substr("B_YEAR",1,4)<\'1850\' OR substr("B_YEAR",1,4)>\'%d\' '
                    'OR (length("B_YEAR")=8 AND (substr("B_YEAR",5,2) NOT BETWEEN \'01\' AND \'12\' OR substr("B_YEAR",7,2) NOT BETWEEN \'01\' AND \'31\')))' % y)
            return {"title": "תאריכי לידה לא תקינים. מוצגים עד 2000", "rows": self._limited(cond)}
        if name == "bad_parents":
            out = []
            for k, nk, label in (("ID_FATHER", "FATHER_NAME", "האב"), ("ID_MOTHER", "MOTHER_NAME", "האם")):
                if k not in c or "F_NAME" not in c:
                    continue
                for r in self._select('"%s" IS NOT NULL AND "%s"<>0 AND NOT EXISTS (SELECT 1 FROM m p WHERE p."ID"=m."%s")' % (k, k, k), [], '"ID"', 1000):
                    r["problem"] = "ת״ז %s (%s) לא קיימת בטבלה" % (label, r[k])
                    out.append(r)
                if nk in c:
                    for r in self._select('"%s" IS NOT NULL AND "%s"<>0 AND nrm("%s")<>\'\' AND EXISTS (SELECT 1 FROM m p WHERE p."ID"=m."%s" AND p."n_F_NAME"<>nrm(m."%s"))' % (
                            k, k, nk, k, nk), [], '"ID"', 1000):
                        par = self.get(r[k])
                        r["problem"] = "שם %s: כתוב '%s' אבל בטבלה '%s'" % (label, r[nk], par["F_NAME"] if par else "")
                        out.append(r)
            return {"title": "קישורי הורים בעייתיים. מוצגים עד 1000 לכל סוג", "rows": out}
        if name == "bad_id":
            out = []
            cur = self.local.execute('SELECT "ID" FROM m')
            for chunk in iter(lambda: cur.fetchmany(20000), []):
                for (i,) in chunk:
                    if not israeli_id_valid(i):
                        out.append(i)
                if len(out) >= 2000:
                    break
            rows = [self.get(i) for i in out[:2000]]
            return {"title": "ת״ז שאינה עוברת בדיקת ספרת ביקורת (אם המזהה אינו ת״ז, התעלמו). מוצגות עד 2000", "rows": [r for r in rows if r]}
        if name == "birthdays":
            return self._birthdays(int(p.get("days") or 30), p.get("kind") or "g")
        if name == "stats":
            def top(sql, fn=lambda x: x, n=15):
                return [(fn(r[0]) or "(ללא)", r[1]) for r in self.local.execute(sql % n).fetchall()]
            city, country, marital = self._maps()
            stats = {}
            if "ID_CITY" in c:
                stats["לפי עיר"] = top('SELECT "ID_CITY", COUNT(*) k FROM m GROUP BY 1 ORDER BY k DESC LIMIT %d',
                                       lambda v: city.get(code_of(v)) or code_of(v))
            if "B_COUNTRY" in c:
                stats["לפי ארץ לידה"] = top('SELECT "B_COUNTRY", COUNT(*) k FROM m GROUP BY 1 ORDER BY k DESC LIMIT %d',
                                            lambda v: country.get(code_of(v)) or code_of(v))
            if "ID_MAT_STATUS" in c:
                stats["לפי מצב אישי"] = top('SELECT "ID_MAT_STATUS", COUNT(*) k FROM m GROUP BY 1 ORDER BY k DESC LIMIT %d',
                                            lambda v: marital.get(code_of(v)) or ("קוד " + code_of(v) if code_of(v) else ""))
            if "B_YEAR" in c:
                stats["לפי עשור לידה"] = sorted(top('SELECT substr("B_YEAR",1,3)||\'0\', COUNT(*) k FROM m WHERE length("B_YEAR")>=4 GROUP BY 1 LIMIT %d', n=40))
            if "L_NAME" in c:
                stats["שמות משפחה נפוצים"] = top('SELECT "n_L_NAME", COUNT(*) k FROM m GROUP BY 1 ORDER BY k DESC LIMIT %d')
            if "F_NAME" in c:
                stats["שמות פרטיים נפוצים"] = top('SELECT "n_F_NAME", COUNT(*) k FROM m GROUP BY 1 ORDER BY k DESC LIMIT %d')
            return {"title": "סטטיסטיקה", "total": self.total, "stats": stats}
        raise UserError("דוח לא מוכר")

    def _birthdays(self, days, kind):
        today = datetime.date.today()
        want = {}  # yyyymmdd -> (ימים עד, תיאור)
        if kind == "g":
            for i in range(days + 1):
                d = today + datetime.timedelta(days=i)
                want[d.strftime("%m%d")] = i
            mmdd = list(want)
            rows = self._select('length("B_YEAR")=8 AND substr("B_YEAR",5,4) IN (%s)' % ",".join("?" * len(mmdd)), mmdd, '"B_YEAR"', 5000)
            for r in rows:
                s = str(r["B_YEAR"])
                r["days_left"] = want[s[4:]]
                r["turning"] = (today + datetime.timedelta(days=r["days_left"])).year - int(s[:4])
            rows.sort(key=lambda r: r["days_left"])
            return {"title": "ימי הולדת קרובים (לועזי). מוצגים עד 5000", "rows": rows}
        try:
            from pyluach import dates
        except ImportError:
            raise UserError("חסרה הספרייה pyluach לחישוב תאריך עברי")
        cand = {}
        for i in range(days + 1):
            d = today + datetime.timedelta(days=i)
            h = dates.GregorianDate(d.year, d.month, d.day).to_heb()
            for hy in range(5640, h.year + 1):
                try:
                    g = dates.HebrewDate(hy, h.month, h.day).to_pydate()
                except Exception:
                    continue
                cand[g.strftime("%Y%m%d")] = (i, hy, h.year)
        keys = list(cand)
        rows = []
        for i in range(0, len(keys), 5000):
            part = keys[i:i + 5000]
            rows += self._select('"B_YEAR" IN (%s)' % ",".join("?" * len(part)), part, '"B_YEAR"', 5000)
        for r in rows:
            left, hy, now = cand[str(r["B_YEAR"])]
            r["days_left"] = left
            r["turning"] = now - hy
        rows.sort(key=lambda r: r["days_left"])
        return {"title": "ימי הולדת קרובים (לפי תאריך עברי). מוצגים עד 5000", "rows": rows[:5000]}

    # ---------- ייצוא ----------
    def export_bytes(self, people):
        heads = [LABELS.get(c, c) for c in self.cols] + ["שם עיר", "שם ארץ לידה", "מצב אישי (שם)", "גיל"]

        def line(p):
            return [p.get(c) for c in self.cols] + [p.get("city_name"), p.get("country_name"), p.get("status_name"), p.get("age")]
        try:
            from openpyxl import Workbook
            wb = Workbook(write_only=True)
            ws = wb.create_sheet("אנשים")
            ws.sheet_view.rightToLeft = True
            ws.append(heads)
            n = 0
            for p in people:
                ws.append(line(p))
                n += 1
                if n >= 1048000:
                    break
            bio = io.BytesIO()
            wb.save(bio)
            return bio.getvalue(), "xlsx"
        except ImportError:
            sio = io.StringIO()
            w = csv.writer(sio)
            w.writerow(heads)
            for p in people:
                w.writerow(line(p))
            return sio.getvalue().encode("utf-8-sig"), "csv"


DB = Db()
TOKEN = secrets.token_urlsafe(24)
STATE = {"last_ping": time.time(), "pinged": False, "port": 0}


def pick_file_dialog(kind="access"):
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        if os.name == "nt":
            ps = ("Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.OpenFileDialog;"
                  "$d.Filter='Access|*.accdb;*.mdb|DB|*.db|All|*.*';if($d.ShowDialog() -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8;$d.FileName}")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="utf-8")
            return out.stdout.strip()
        raise UserError("אין חלון בחירת קבצים. הדביקו את הנתיב המלא של הקובץ.")
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    root.lift()
    root.focus_force()
    types = [("עותק מהיר", "*.db")] if kind == "db" else [("Access", "*.accdb *.mdb"), ("הכול", "*.*")]
    path = filedialog.askopenfilename(title="בחירת קובץ", filetypes=types + [("הכול", "*.*")])
    root.destroy()
    return path


def diag_text():
    import platform
    import struct
    lines = ["גרסת תוכנה: " + VERSION, "זמן: " + time.strftime("%Y-%m-%d %H:%M:%S"),
             "Python: %s (%d סיביות)" % (platform.python_version(), struct.calcsize("P") * 8),
             "מערכת: " + platform.platform(), "תיקיית התוכנה: " + APP_DIR, "תיקיית נתונים: " + DATA_DIR]
    try:
        import pyodbc
        lines.append("pyodbc: %s" % pyodbc.version)
        lines.append("דרייברים של Access: %s" % ([d for d in pyodbc.drivers() if "Access" in d] or "לא נמצאו"))
        lines.append("כל הדרייברים: %s" % pyodbc.drivers())
    except ImportError as e:
        lines.append("pyodbc לא מותקן: %s" % e)
    try:
        import openpyxl
        lines.append("openpyxl: " + openpyxl.__version__)
    except ImportError:
        lines.append("openpyxl: לא מותקן (הייצוא יהיה ל-CSV)")
    p = DB.cfg.get("db_path") or ""
    lines.append("קובץ שהוגדר: %s" % (p or "(אין)"))
    if p:
        lines.append("הקובץ קיים: %s" % os.path.isfile(p))
        if os.path.isfile(p):
            lines.append("גודל: %.1f MB" % (os.path.getsize(p) / 1e6))
    lines.append("מחובר: %s" % DB.connected)
    lines.append("מצב עצמאי (בלי Access): %s" % DB.standalone)
    if DB.connected:
        lines.append("עמודות M: %s" % ", ".join(DB.cols))
        lines.append("סוגי עמודות: %s" % DB.types)
        lines.append("רשומות: %d" % DB.total)
        if os.path.exists(LOCAL_DB):
            lines.append("עותק מקומי (work.db): %.1f MB" % (os.path.getsize(LOCAL_DB) / 1e6))
        if DB.stale:
            lines.append("אזהרה: " + DB.stale)
        for t, v in list(DB.lookups.items()):
            lines.append("טבלת עזר %s: %d שורות, קוד=%s, שם=%s, עמודות=%s" % (t, v["count"], v["key"], v["name"], v["cols"]))
    lines.append("\n--- יומן (200 שורות אחרונות) ---")
    try:
        with open(DIAG_LOG, encoding="utf-8", errors="replace") as f:
            lines += [l.rstrip("\n") for l in f.readlines()[-200:]]
    except OSError:
        lines.append("(אין יומן עדיין)")
    return "\n".join(lines)


def state_payload():
    if DB.progress["state"] == "loading":
        return {"connected": False, "loading": True, "labels": LABELS}
    d = {"connected": DB.connected, "path": DB.path, "labels": LABELS, "text_fields": TEXT_FIELDS,
         "page_size": DB.cfg.get("page_size", 200)}
    if DB.connected:
        nid = DB.local.execute('SELECT MAX("ID") FROM m').fetchone()[0] or 0
        d.update({
            "cols": DB.cols, "types": DB.types, "total": DB.total, "marital": DB.cfg.get("marital", {}),
            "marital_codes": sorted(DB.marital_codes, key=lambda x: (x[0] == "", x[0])),
            "lookups": {t: {"cols": v["cols"], "key": v["key"], "name": v["name"], "count": v["count"]} for t, v in DB.lookups.items()},
            "next_id": nid + 1, "stale": DB.stale, "standalone": DB.standalone,
            "cities": sorted(DB.lookups.get("CITY", {}).get("map", {}).values()),
            "countries": sorted(DB.lookups.get("COUNTRY", {}).get("map", {}).values()),
            "city_map": DB.lookups.get("CITY", {}).get("map", {}),
            "country_map": DB.lookups.get("COUNTRY", {}).get("map", {}),
            "streets": DB.lookups.get("StreetList", {}).get("names", []),
        })
    return d


UPDATE = {"state": "idle", "version": None, "notes": "", "error": None, "checked": 0}
UPDATE_DIR = os.path.join(DATA_DIR, "update")


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def check_for_update(force=False):
    """בודק אם יש גרסה חדשה, מוריד אותה לתיקיית הכנה ומאמת. ההחלפה בפועל נעשית בהפעלה הבאה (launcher.pyw)."""
    import hashlib
    import io
    import urllib.error
    import urllib.request
    import zipfile
    url = DB.cfg.get("update_url") or DEFAULT_UPDATE_URL
    if not url or not PORTABLE:
        UPDATE.update(state="disabled")
        return
    if not force and (not DB.cfg.get("auto_update", True) or time.time() - UPDATE["checked"] < 6 * 3600):
        return
    if not url.lower().startswith("https://"):
        UPDATE.update(state="error", error="כתובת העדכונים חייבת להתחיל ב-https")
        return
    UPDATE.update(state="checking", error=None, checked=time.time())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PeopleSearch/" + VERSION})
        with urllib.request.urlopen(req, timeout=15) as r:
            man = json.loads(r.read(200000).decode("utf-8"))
        ver = str(man["version"])
        if version_tuple(ver) <= version_tuple(VERSION):
            UPDATE.update(state="current", version=ver)
            return
        if os.path.exists(os.path.join(UPDATE_DIR, "READY")) and open(os.path.join(UPDATE_DIR, "READY")).read().strip() == ver:
            UPDATE.update(state="ready", version=ver, notes=man.get("notes", ""))
            return
        UPDATE.update(state="downloading", version=ver, notes=man.get("notes", ""))
        os.makedirs(UPDATE_DIR, exist_ok=True)
        for name in ("READY", "APPROVED"):
            try:
                os.remove(os.path.join(UPDATE_DIR, name))
            except OSError:
                pass
        stage = os.path.join(UPDATE_DIR, "new_code")
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage)

        def fetch(u, limit):
            if not u.lower().startswith("https://"):
                raise ValueError("כתובת ההורדה חייבת להתחיל ב-https")
            buf = bytearray()
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "PeopleSearch/" + VERSION}), timeout=60) as r:
                for chunk in iter(lambda: r.read(65536), b""):
                    buf += chunk
                    if len(buf) > limit:
                        raise ValueError("קובץ העדכון גדול מדי")
            return bytes(buf)

        def check_sha(data, expected):
            if hashlib.sha256(data).hexdigest().lower() != str(expected).lower():
                raise ValueError("חתימת קובץ העדכון לא תואמת. העדכון לא הותקן.")

        if man.get("files"):
            for f in man["files"]:
                rel = os.path.normpath(f["path"])
                if rel.startswith("..") or os.path.isabs(rel):
                    raise ValueError("נתיב לא תקין בעדכון")
                data = fetch(f["url"], 8 * 2**20)
                check_sha(data, f["sha256"])
                target = os.path.join(stage, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as out:
                    out.write(data)
        else:
            data = fetch(man["url"], 40 * 2**20)
            check_sha(data, man["sha256"])
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    target = os.path.normpath(os.path.join(stage, info.filename))
                    if not target.startswith(os.path.normpath(stage) + os.sep):
                        raise ValueError("קובץ עדכון לא תקין")
                z.extractall(stage)
        for need in ("app.py", os.path.join("static", "index.html")):
            if not os.path.exists(os.path.join(stage, need)):
                raise ValueError("חסר קובץ בעדכון: " + need)
        with open(os.path.join(stage, "app.py"), encoding="utf-8") as f:
            compile(f.read(), "app.py", "exec")
        with open(os.path.join(UPDATE_DIR, "READY"), "w") as f:
            f.write(ver)
        if DB.cfg.get("auto_install", False):
            with open(os.path.join(UPDATE_DIR, "APPROVED"), "w") as f:
                f.write(ver)
        log("עדכון %s הורד ומוכן להפעלה הבאה" % ver)
        UPDATE.update(state="ready", version=ver)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            UPDATE.update(state="current", error=None)  # ערוץ העדכונים עדיין לא פורסם
            return
        log("בדיקת עדכון נכשלה: %r" % (e,))
        UPDATE.update(state="error", error="השרת החזיר שגיאה %s" % e.code, checked=time.time() - 5.5 * 3600)
    except Exception as e:  # noqa
        log("בדיקת עדכון נכשלה: %r" % (e,))
        UPDATE.update(state="error", error=str(e)[:200], checked=time.time() - 5.5 * 3600)


def update_loop():
    time.sleep(8)
    while True:
        try:
            check_for_update()
        except Exception:
            pass
        time.sleep(1800)


RUNNING_PATH = os.path.join(DATA_DIR, "running.json")


def other_instance_url():
    """אם התוכנה כבר פועלת, מחזיר את הכתובת שלה כדי לפתוח אליה חלון במקום להפעיל עותק שני."""
    import urllib.request
    try:
        with open(RUNNING_PATH, encoding="utf-8") as f:
            info = json.load(f)
        url = "http://127.0.0.1:%d/" % info["port"]
        with urllib.request.urlopen(url + "ping", timeout=2) as r:
            if r.read(50) == b"PeopleSearch":
                return url
    except Exception:
        pass
    return None


def clear_running():
    try:
        os.remove(RUNNING_PATH)
    except OSError:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "PeopleSearch"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/ping":
            return self._send(200, "PeopleSearch", "text/plain")
        if path == "/":
            with open(os.path.join(RES_DIR, "static", "index.html"), encoding="utf-8") as f:
                return self._send(200, f.read().replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, {"error": "אין הרשאה"})
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            self._route(path, body)
        except UserError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa
            import traceback
            log("שגיאה בבקשה %s\n%s" % (path, traceback.format_exc()))
            self._send(500, {"error": "אירעה שגיאה לא צפויה. פתחו את הלשונית 'אבחון' ושלחו לי את התוכן שלה. (%s)" % str(e)[:120]})

    def _need_db(self):
        if not DB.connected:
            raise UserError("לא נבחר קובץ")

    def _route(self, path, b):
        if path == "/api/ping":
            STATE["last_ping"], STATE["pinged"] = time.time(), True
            return self._send(200, {"ok": True})
        if path == "/api/state":
            return self._send(200, state_payload())
        if path == "/api/diag":
            return self._send(200, {"text": diag_text()})
        if path == "/api/clientlog":
            log("שגיאת ממשק: %s" % str(b.get("msg"))[:500])
            return self._send(200, {"ok": True})
        if path == "/api/browse":
            return self._send(200, {"path": pick_file_dialog(b.get("kind") or "access")})
        if path == "/api/adopt":
            DB.adopt(b.get("path", ""))
            return self._send(200, {"ok": True})
        if path == "/api/open":
            DB.open(b.get("path", ""), b.get("password"), bool(b.get("confirm_replace")))
            return self._send(200, {"loading": True})
        if path == "/api/progress":
            pr = dict(DB.progress)
            pr["elapsed"] = round(time.time() - pr["t0"], 1) if pr["t0"] else 0
            pr["steps"] = Db.STEPS
            return self._send(200, pr)
        if path == "/api/quit":
            self._send(200, {"ok": True})
            threading.Timer(0.3, lambda: (clear_running(), os._exit(0))).start()
            return
        if path == "/api/update":
            if b.get("check"):
                threading.Thread(target=check_for_update, args=(True,), daemon=True).start()
            return self._send(200, dict(UPDATE, current=VERSION, enabled=bool(DB.cfg.get("update_url") or DEFAULT_UPDATE_URL) and PORTABLE,
                                        auto=bool(DB.cfg.get("auto_update", True)), auto_install=bool(DB.cfg.get("auto_install", False))))
        if path == "/api/update_settings":
            if "auto" in b:
                DB.cfg["auto_update"] = bool(b["auto"])
            if "auto_install" in b:
                DB.cfg["auto_install"] = bool(b["auto_install"])
            save_config(DB.cfg)
            return self._send(200, {"ok": True})
        if path == "/api/restart":
            launcher = os.path.join(ROOT_DIR, "launcher.pyw")
            exe = os.path.join(ROOT_DIR, "python", "pythonw.exe")
            if not (os.path.exists(launcher) and os.path.exists(exe)):
                raise UserError("הפעלה מחדש אוטומטית אפשרית רק בגרסה הניידת. סגרו ופתחו את התוכנה ידנית.")
            if b.get("install") and os.path.exists(os.path.join(UPDATE_DIR, "READY")):
                with open(os.path.join(UPDATE_DIR, "APPROVED"), "w") as f:
                    f.write("1")
            self._send(200, {"ok": True})

            def go():
                clear_running()
                subprocess.Popen([exe, launcher], cwd=ROOT_DIR)
                os._exit(0)
            threading.Timer(0.5, go).start()
            return
        self._need_db()
        if path == "/api/reload":
            DB.reload()
            return self._send(200, {"loading": True})
        if path == "/api/search":
            size = min(int(b.get("limit") or DB.cfg.get("page_size", 200)), 1000)
            rows, total, capped = DB.search(b, size, int(b.get("offset") or 0))
            return self._send(200, {"total": total, "capped": capped, "rows": rows, "expanded": DB.last_expanded})
        if path == "/api/person":
            r = DB.get(b.get("id"))
            if not r:
                raise UserError("לא נמצא")
            return self._send(200, {"person": r, "family": DB.family(r)})
        if path == "/api/save":
            return self._send(200, {"person": DB.save(b.get("mode"), b.get("data") or {})})
        if path == "/api/delete":
            DB.delete(b.get("id"))
            return self._send(200, {"ok": True})
        if path == "/api/bulk":
            return self._send(200, DB.bulk(b.get("text", ""), bool(b.get("fuzzy")), b.get("mode") or "auto"))
        if path == "/api/report":
            return self._send(200, DB.report(b.get("name"), b))
        if path == "/api/detach":
            DB.detach()
            return self._send(200, state_payload())
        if path == "/api/backup":
            return self._send(200, {"file": DB.backup(force=True)})
        if path == "/api/config":
            old = json.loads(json.dumps(DB.cfg.get("lookups", {})))
            if "lookups" in b:
                DB.cfg["lookups"] = b["lookups"]
            if "marital" in b:
                DB.cfg["marital"] = {code_of(k): v for k, v in b["marital"].items() if str(v).strip()}
            save_config(DB.cfg)
            DB.apply_config(old)
            return self._send(200, {"loading": DB.progress["state"] == "loading"})
        if path == "/api/export":
            if b.get("ids") is not None:
                people = (r for r in (DB.get(i) for i in b["ids"][:MAX_EXPORT]) if r)
            else:
                people = DB.iter_search(b)
            data, ext = DB.export_bytes(people)
            ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == "xlsx" else "text/csv"
            return self._send(200, data, ctype, {"X-Ext": ext})
        raise UserError("בקשה לא מוכרת")


def open_window(url):
    pf = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")]
    cands = []
    for base in pf:
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    for exe in cands:
        if os.path.exists(exe):
            try:
                subprocess.Popen([exe, "--app=" + url, "--window-size=1280,860"])
                return
            except OSError:
                pass
    webbrowser.open(url)


def watchdog():
    while True:
        time.sleep(5)
        if STATE["pinged"] and time.time() - STATE["last_ping"] > 1800:
            clear_running()
            os._exit(0)


def main():
    adopt_legacy_files()
    DB.cfg = load_config()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        DB.cfg["db_path"] = sys.argv[1]
    if "--no-window" not in sys.argv:
        running = other_instance_url()
        if running:
            open_window(running)
            return
    log("התוכנה הופעלה (גרסה %s)" % VERSION)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    STATE["port"] = port
    try:
        with open(RUNNING_PATH, "w", encoding="utf-8") as f:
            json.dump({"port": port, "pid": os.getpid()}, f)
    except OSError:
        pass
    p = DB.cfg.get("db_path")
    opened = False
    if os.path.exists(LOCAL_DB):
        try:
            DB.open_standalone()
            opened = True
        except Exception as e:  # noqa
            log("אין עותק עצמאי מוכן (%s)" % (e,))
    if not opened and p and os.path.isfile(p):
        try:
            DB.open(p)
        except Exception as e:  # noqa
            try:
                print("auto-open failed:", e)
            except Exception:
                pass
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    url = "http://127.0.0.1:%d/" % port
    try:
        print("Running at", url)
    except Exception:
        pass
    if "--no-window" not in sys.argv:
        threading.Timer(0.4, open_window, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        clear_running()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log("קריסה בהפעלה:\n" + traceback.format_exc())
        raise
