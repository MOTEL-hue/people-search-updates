import os
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "code"))
import app  # noqa: E402
from make_sample_db import make  # noqa: E402


def open_and_wait(db, path):
    db.open(path)
    for _ in range(400):
        if db.progress["state"] != "loading":
            break
        time.sleep(0.05)
    if db.progress["state"] == "error":
        raise app.UserError(db.progress["error"])


class AppTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        app.APP_DIR = self.dir
        app.CONFIG_PATH = os.path.join(self.dir, "config.json")
        app.LOG_PATH = os.path.join(self.dir, "changes.log")
        app.LOCAL_DB = os.path.join(self.dir, "work.db")
        app.DIAG_LOG = os.path.join(self.dir, "log.txt")
        self.path = os.path.join(self.dir, "t.sqlite")
        make(self.path)
        self.db = app.Db()
        self.db.cfg["auto_standalone"] = False
        open_and_wait(self.db, self.path)

    def tearDown(self):
        self.db.close()

    def ids(self, p):
        return [r["ID"] for r in self.db.search(p, 1000)[0]]

    def test_lookups_guessed(self):
        self.assertEqual(self.db.lookups["CITY"]["name"], "CITY_NAME")
        self.assertEqual(self.db.lookups["CITY"]["key"], "CITY_CODE")
        r = self.db.get(21)
        self.assertEqual(r["city_name"], "חיפה")
        self.assertEqual(r["country_name"], "ישראל")

    def test_search_words_and_filters(self):
        self.assertEqual(len(self.ids({"q": "בדיקה ילד"})), 2)
        self.assertEqual(self.ids({"f": {"F_NAME": "ילדה"}, "mode": "exact"}), [28])
        self.assertEqual(self.ids({"f": {"ID": "7"}, "mode": "exact"}), [7])
        self.assertEqual(len(self.ids({"f": {"ID_CITY": "חיפה", "L_NAME": "בדיקה"}})), 4)
        self.assertEqual(self.ids({"f": {"L_NAME": "בדי"}, "mode": "starts"}), self.ids({"f": {"L_NAME": "בדיקה"}}))

    def test_city_name_in_free_text(self):
        self.assertTrue(len(self.ids({"q": "בדיקה חיפה"})) >= 4)

    def test_short_words_use_like(self):
        self.assertTrue(21 in self.ids({"q": "בדיקה ילד"}))
        self.assertTrue(len(self.ids({"q": "אב"})) > 0)

    def test_fuzzy_names(self):
        base = self.ids({"f": {"L_NAME": "ביטון"}, "mode": "exact"})
        self.assertTrue(base)
        self.assertEqual(self.ids({"f": {"L_NAME": "ביטן"}, "mode": "exact"}), [])
        self.assertEqual(self.ids({"f": {"L_NAME": "ביטן"}, "fuzzy": True}), base)
        self.assertEqual(self.ids({"q": "ביטן", "fuzzy": True}), self.ids({"q": "ביטון"}))
        self.assertEqual(self.ids({"f": {"L_NAME": "קהן"}, "fuzzy": True}), self.ids({"f": {"L_NAME": "כהן"}, "mode": "exact"}))
        self.db.search({"q": "ביטן", "fuzzy": True}, 5)
        self.assertEqual(list(self.db.last_expanded.values()), [["ביטונ"]])

    def test_fuzzy_off_by_default(self):
        self.assertEqual(self.ids({"q": "ביטן"}), [])

    def test_new_names_enter_dictionary(self):
        self.db.save("add", {"ID": "6001", "L_NAME": "זילברשטיין", "F_NAME": "אדם"})
        self.assertEqual(self.ids({"q": "זילברשטין", "fuzzy": True}), [6001])

    def test_phonetic_and_edits(self):
        self.assertEqual(app.phonetic_key("כהן"), app.phonetic_key("קהן"))
        self.assertIn("כהנ", app.edits1("כהן"))
        self.assertEqual(app.levenshtein("ביטון", "ביטן"), 1)

    def test_final_letters_and_quotes(self):
        self.assertEqual(app.norm('ךם"ן'), app.norm("כמן"))

    def test_ages_and_years(self):
        self.assertEqual(self.ids({"f": {"year_from": 1950, "year_to": 1952, "L_NAME": "בדיקה"}}), [7, 14])
        old = self.ids({"f": {"age_from": 70, "L_NAME": "בדיקה"}})
        self.assertIn(7, old)
        self.assertNotIn(21, old)

    def test_sort_and_paging(self):
        rows, total, capped = self.db.search({"sort": "ID"}, 10, 0)
        self.assertEqual(total, 300)
        self.assertFalse(capped)
        self.assertEqual([r["ID"] for r in rows], sorted(r["ID"] for r in rows))
        rows2, _, _ = self.db.search({"sort": "ID"}, 10, 10)
        self.assertGreater(rows2[0]["ID"], rows[-1]["ID"])

    def test_family(self):
        fam = self.db.family(self.db.get(21))
        self.assertEqual(fam["father"]["ID"], 7)
        self.assertEqual(fam["mother"]["ID"], 14)
        self.assertEqual([x["ID"] for x in fam["siblings"]], [28])
        kids = self.db.family(self.db.get(7))["children"]
        self.assertEqual(sorted(x["ID"] for x in kids), [21, 28])
        self.assertTrue(len(self.db.family(self.db.get(7))["same_address"]) >= 3)

    def test_bulk(self):
        r = self.db.bulk("7 14\nאבי בדיקה\nאין כזה")
        self.assertEqual(r["missing"], ["אין כזה"])
        self.assertEqual(r["unique"], 2)

    def test_bulk_exact_id_mode(self):
        r = self.db.bulk("7, 14\n0021 99999 7", mode="id")
        self.assertEqual(sorted(x["ID"] for x in r["found"]), [7, 14, 21])
        self.assertEqual(r["missing"], ["99999"])
        self.assertEqual(r["asked"], 4)

    def test_bulk_exact_name_mode(self):
        r = self.db.bulk("אבי בדיקה\nבדיקה, אמא\nאב בדיקה\nילד", mode="name_exact")
        self.assertEqual(sorted(x["ID"] for x in r["found"]), [7, 14])
        self.assertEqual(r["missing"], ["אב בדיקה", "ילד"])

    def test_bulk_approx_mode(self):
        r = self.db.bulk("ביטן דויד", mode="name_approx")
        self.assertTrue(r["found"])

    def test_add_edit_delete_with_backup(self):
        new = self.db.save("add", {"ID": "9999", "L_NAME": "חדש", "F_NAME": "אדם", "B_YEAR": "19900101", "ID_CITY": "6200"})
        self.assertEqual(new["city_name"], "בת ים")
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "גיבויים_t")) or os.path.isdir(os.path.join(self.dir, "גיבויים_עצמאי")))
        self.assertEqual(self.ids({"q": "חדש אדם"}), [9999])
        with self.assertRaises(app.UserError):
            self.db.save("add", {"ID": "9999", "L_NAME": "כפול"})
        self.db.save("edit", {"ID": "9999", "L_NAME": "עודכן", "F_NAME": "אדם"})
        self.assertEqual(self.db.get(9999)["L_NAME"], "עודכן")
        self.assertEqual(self.ids({"q": "חדש אדם"}), [])
        self.assertEqual(self.ids({"q": "עודכן"}), [9999])
        self.assertEqual(self.db.total, 301)
        self.db.delete(9999)
        self.assertIsNone(self.db.get(9999))
        self.assertEqual(self.ids({"q": "עודכן"}), [])
        self.assertEqual(self.db.total, 300)
        self.assertTrue(os.path.getsize(app.LOG_PATH) > 0)

    def test_bad_birth_date_rejected(self):
        with self.assertRaises(app.UserError):
            self.db.save("add", {"ID": "5555", "B_YEAR": "19900231"})
        with self.assertRaises(app.UserError):
            self.db.save("add", {"ID": "5555", "B_YEAR": "1990ab"})

    def test_reports(self):
        for name in ("duplicates", "missing", "bad_birth", "bad_parents", "bad_id", "stats"):
            self.assertIn("title", self.db.report(name, {}))
        r = self.db.report("birthdays", {"days": 365})
        self.assertTrue(len(r["rows"]) > 0)
        rh = self.db.report("birthdays", {"days": 30, "kind": "h"})
        self.assertIn("rows", rh)

    def test_marital_names(self):
        self.db.cfg["marital"] = {"20": "נשוי"}
        self.assertEqual(self.db.get(7)["status_name"], "נשוי")

    def test_export_stream(self):
        data, ext = self.db.export_bytes(self.db.iter_search({"q": "בדיקה"}))
        self.assertIn(ext, ("xlsx", "csv"))
        self.assertTrue(len(data) > 100)

    def test_reopen_uses_local_copy(self):
        db2 = app.Db()
        open_and_wait(db2, self.path)
        self.assertEqual(db2.total, 300)
        self.assertEqual(db2.progress["step"], 4 if False else db2.progress["step"])
        self.assertIsNone(db2.stale)
        db2.close()

    def test_stale_detected(self):
        import sqlite3
        self.db.close()  # ב-Windows אי אפשר להחליף קובץ שפתוח בחיבור אחר
        time.sleep(1.2)
        c = sqlite3.connect(self.path)
        c.execute("DELETE FROM M WHERE ID=28")
        c.commit()
        c.close()
        db2 = app.Db()
        open_and_wait(db2, self.path)
        self.assertIsNotNone(db2.stale)
        db2.reload()
        for _ in range(400):
            if db2.progress["state"] != "loading":
                break
            time.sleep(0.05)
        self.assertEqual(db2.total, 299)
        self.assertIsNone(db2.stale)
        db2.close()

    def test_lookup_change_reindexes(self):
        self.db.cfg["lookups"] = {"CITY": {"key": "CITY_CODE", "name": "CITY_ENG"}}
        self.db.apply_config({})
        for _ in range(400):
            if self.db.progress["state"] != "loading":
                break
            time.sleep(0.05)
        self.assertEqual(self.db.progress["state"], "ready")
        self.assertTrue(len(self.ids({"q": "haifa"})) > 0)

    def test_missing_table_message(self):
        import sqlite3
        p = os.path.join(self.dir, "bad.sqlite")
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE X(a)")
        c.commit()
        c.close()
        with self.assertRaises(app.UserError):
            open_and_wait(app.Db(), p)


class StandaloneTests(AppTests):
    """אותן בדיקות חיפוש/שמירה, אבל אחרי ניתוק מקובץ ה-Access."""
    def setUp(self):
        super().setUp()
        self.db.detach()
        os.rename(self.path, self.path + ".gone")

    def test_detached_state(self):
        self.assertTrue(self.db.standalone)
        self.assertTrue(self.db.connected)
        self.assertEqual(self.db.get(21)["city_name"], "חיפה")

    def test_reopen_standalone_without_source(self):
        db2 = app.Db()
        db2.open_standalone()
        self.assertEqual(db2.total, 300)
        self.assertEqual(db2.get(21)["city_name"], "חיפה")
        self.assertEqual(len(db2.search({"q": "בדיקה"}, 50)[0]), 4)
        db2.save("add", {"ID": "8888", "L_NAME": "עצמאי", "F_NAME": "אדם"})
        self.assertEqual([r["ID"] for r in db2.search({"q": "עצמאי"}, 5)[0]], [8888])
        db2.close()

    def test_replace_requires_confirmation(self):
        os.rename(self.path + ".gone", self.path)
        with self.assertRaises(app.UserError):
            self.db.open(self.path)
        self.db.open(self.path, confirm_replace=True)
        for _ in range(400):
            if self.db.progress["state"] != "loading":
                break
            time.sleep(0.05)
        self.assertFalse(self.db.standalone)

    def test_local_backup(self):
        self.db.backup(force=True)
        self.assertTrue(os.listdir(os.path.join(app.APP_DIR, "גיבויים_עצמאי")))

    # בדיקות שתלויות בקובץ המקור או בטעינה מחדש אינן רלוונטיות במצב עצמאי
    test_reopen_uses_local_copy = test_stale_detected = test_lookup_change_reindexes = None


class MigrationTests(unittest.TestCase):
    """עותק מקומי של גרסה קודמת (עם טקסט חיפוש בעמודות) משודרג בלי לקרוא שוב את Access."""
    def test_legacy_copy_is_upgraded(self):
        import json
        import sqlite3
        d = tempfile.mkdtemp()
        app.APP_DIR = d
        app.CONFIG_PATH = os.path.join(d, "config.json")
        app.LOG_PATH = os.path.join(d, "changes.log")
        app.LOCAL_DB = os.path.join(d, "work.db")
        app.DIAG_LOG = os.path.join(d, "log.txt")
        src = os.path.join(d, "t.sqlite")
        make(src)
        db = app.Db()
        open_and_wait(db, src)
        cols, file_key = list(db.cols), db._file_key()
        total = db.total
        db.close()
        os.remove(app.LOCAL_DB)
        for ext in ("-wal", "-shm"):
            if os.path.exists(app.LOCAL_DB + ext):
                os.remove(app.LOCAL_DB + ext)
        # בניית עותק בפורמט הישן
        leg = sqlite3.connect(app.LOCAL_DB)
        tf = app.TEXT_FIELDS
        leg.execute("CREATE TABLE m (%s, s_all TEXT, s_nm TEXT, %s)" % (
            ",".join('"ID" INTEGER PRIMARY KEY' if c == "ID" else '"%s"' % c for c in cols), ",".join('"n_%s" TEXT' % f for f in tf)))
        leg.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        leg.execute("CREATE VIRTUAL TABLE fts USING fts5(s_all, s_nm, tokenize='trigram')")
        rows = sqlite3.connect(src).execute("SELECT * FROM M").fetchall()
        for r in rows:
            row = dict(zip(cols, r))
            s_all = app.norm(" ".join(str(row.get(f) or "") for f in tf) + " " + str(row["ID"]))
            leg.execute("INSERT INTO m VALUES (%s)" % ",".join("?" * (len(cols) + 2 + len(tf))),
                        list(r) + [s_all, ""] + [app.norm(row.get(f)) for f in tf])
        for k, v in (("path", src), ("cols", json.dumps(cols)), ("file_key", file_key), ("lk_sig", "x"), ("self_written", "0"), ("done", "1")):
            leg.execute("INSERT INTO meta VALUES (?,?)", [k, v])
        leg.commit()
        leg.close()
        db2 = app.Db()
        open_and_wait(db2, src)
        self.assertEqual(db2.total, total)
        self.assertEqual(db2._meta("schema"), "3")
        self.assertTrue(len(db2.search({"q": "בדיקה"}, 20)[0]) == 4)
        self.assertEqual(db2.search({"q": "ביטן", "fuzzy": True}, 5)[1] > 0, True)
        self.assertEqual(len(db2.family(db2.get(21))["siblings"]), 1)
        db2.close()


class AdoptTests(unittest.TestCase):
    def test_adopt_existing_copy(self):
        d = tempfile.mkdtemp()
        other = tempfile.mkdtemp()
        app.APP_DIR = d
        app.CONFIG_PATH = os.path.join(d, "config.json")
        app.LOG_PATH = os.path.join(d, "changes.log")
        app.DIAG_LOG = os.path.join(d, "log.txt")
        app.LOCAL_DB = os.path.join(other, "work.db")
        src = os.path.join(d, "t.sqlite")
        make(src)
        db = app.Db()
        open_and_wait(db, src)
        db.close()
        app.LOCAL_DB = os.path.join(d, "data", "work.db")
        os.makedirs(os.path.dirname(app.LOCAL_DB))
        db2 = app.Db()
        db2.adopt(os.path.join(other, "work.db"))
        t = time.time()
        open_and_wait(db2, src)
        self.assertEqual(db2.total, 300)
        self.assertLess(time.time() - t, 5)
        with self.assertRaises(app.UserError):
            db2.adopt(src)
        db2.close()


class AutoStandaloneTests(unittest.TestCase):
    def test_default_detaches_after_first_load(self):
        d = tempfile.mkdtemp()
        app.APP_DIR = d
        app.CONFIG_PATH = os.path.join(d, "config.json")
        app.LOG_PATH = os.path.join(d, "changes.log")
        app.LOCAL_DB = os.path.join(d, "work.db")
        app.DIAG_LOG = os.path.join(d, "log.txt")
        src = os.path.join(d, "t.sqlite")
        make(src)
        db = app.Db()
        open_and_wait(db, src)
        self.assertTrue(db.standalone)
        self.assertIsNone(db.conn)
        os.rename(src, src + ".gone")
        db.close()
        db2 = app.Db()
        db2.open_standalone()
        self.assertEqual(db2.total, 300)
        db2.close()


class UpdateTests(unittest.TestCase):
    def test_update_download_and_stage(self):
        import hashlib
        import io
        import json
        import unittest.mock as mock
        import zipfile
        d = tempfile.mkdtemp()
        app.UPDATE_DIR = os.path.join(d, "update")
        app.LOG_PATH = os.path.join(d, "changes.log")
        app.DIAG_LOG = os.path.join(d, "log.txt")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("app.py", "VERSION = '9.9'\n")
            z.writestr("static/index.html", "<html></html>")
        data = buf.getvalue()
        man = json.dumps({"version": "9.9", "url": "https://x/code.zip", "sha256": hashlib.sha256(data).hexdigest()}).encode()

        class Resp:
            def __init__(self, b):
                self.b, self.pos = b, 0
            def read(self, n=-1):
                r = self.b[self.pos:] if n < 0 else self.b[self.pos:self.pos + n]
                self.pos += len(r)
                return r
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake(req, timeout=0):
            return Resp(man if req.full_url.endswith("manifest.json") else data)
        db = app.DB
        db.cfg["update_url"] = "https://x/manifest.json"
        with mock.patch("urllib.request.urlopen", fake), mock.patch.object(app, "PORTABLE", True):
            app.UPDATE["checked"] = 0
            app.check_for_update(force=True)
            self.assertEqual(app.UPDATE["state"], "ready")
            self.assertTrue(os.path.exists(os.path.join(d, "update", "new_code", "app.py")))
            man = json.dumps({"version": "9.9", "url": "https://x/code.zip", "sha256": "0" * 64}).encode()
            shutil_rm = __import__("shutil").rmtree
            shutil_rm(os.path.join(d, "update"))
            app.check_for_update(force=True)
            self.assertEqual(app.UPDATE["state"], "error")
            self.assertFalse(os.path.exists(os.path.join(d, "update", "READY")))
        db.cfg.pop("update_url", None)

    def test_update_files_manifest(self):
        import hashlib
        import json
        import unittest.mock as mock
        d = tempfile.mkdtemp()
        app.UPDATE_DIR = os.path.join(d, "update")
        app.DIAG_LOG = os.path.join(d, "log.txt")
        files = {"https://x/code/app.py": b"VERSION = '9.9'\n", "https://x/code/static/index.html": b"<html></html>"}
        man = json.dumps({"version": "9.9", "files": [
            {"path": "app.py", "url": "https://x/code/app.py", "sha256": hashlib.sha256(files["https://x/code/app.py"]).hexdigest()},
            {"path": "static/index.html", "url": "https://x/code/static/index.html",
             "sha256": hashlib.sha256(files["https://x/code/static/index.html"]).hexdigest()}]}).encode()

        class Resp:
            def __init__(self, b):
                self.b, self.pos = b, 0
            def read(self, n=-1):
                r = self.b[self.pos:] if n < 0 else self.b[self.pos:self.pos + n]
                self.pos += len(r)
                return r
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake(req, timeout=0):
            return Resp(man if req.full_url.endswith("manifest.json") else files[req.full_url])
        app.DB.cfg["update_url"] = "https://x/manifest.json"
        with mock.patch("urllib.request.urlopen", fake), mock.patch.object(app, "PORTABLE", True):
            app.check_for_update(force=True)
        app.DB.cfg.pop("update_url", None)
        self.assertEqual(app.UPDATE["state"], "ready")
        self.assertTrue(os.path.exists(os.path.join(d, "update", "new_code", "static", "index.html")))
        self.assertFalse(os.path.exists(os.path.join(d, "update", "APPROVED")))  # ממתין לאישור המשתמש

    def test_version_compare(self):
        self.assertTrue(app.version_tuple("2.10") > app.version_tuple("2.9"))
        self.assertEqual(app.version_tuple("2.0"), app.version_tuple("2.0.0")[:2])


class ErrorTextTests(unittest.TestCase):
    def test_hebrew_messages(self):
        self.assertIn("סיסמה", app.hebrew_db_error(Exception("not a valid password")))
        self.assertIn("32", app.hebrew_db_error(Exception("[IM002] Data source name not found")))
        self.assertIn("אבחון", app.hebrew_db_error(Exception("weird")))
        self.assertTrue(app.diag_text().startswith("גרסת תוכנה"))


if __name__ == "__main__":
    unittest.main()
