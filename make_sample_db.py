"""יוצר קובץ בדיקה (SQLite) עם נתונים מומצאים, באותו מבנה של הטבלה M. לבדיקות בלבד."""
import random
import sqlite3
import sys

COLS = ("ID INTEGER PRIMARY KEY, L_NAME TEXT, F_NAME TEXT, F_NAME_OLD TEXT, B_YEAR TEXT, ID_MAT_STATUS INTEGER, "
        "FATHER_NAME TEXT, MOTHER_NAME TEXT, ID_FATHER INTEGER, ID_MOTHER INTEGER, B_COUNTRY INTEGER, "
        "ID_CITY INTEGER, STREET TEXT, HOUSE INTEGER, BAIT INTEGER")


def make(path, n=300, seed=1):
    rnd = random.Random(seed)
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE IF EXISTS M; DROP TABLE IF EXISTS CITY; DROP TABLE IF EXISTS COUNTRY; DROP TABLE IF EXISTS StreetList;")
    con.execute("CREATE TABLE M (%s)" % COLS)
    con.execute("CREATE TABLE CITY (CITY_CODE INTEGER, CITY_ENG TEXT, CITY_NAME TEXT)")
    con.execute("CREATE TABLE COUNTRY (CODE INTEGER, COUNTRY_NAME TEXT)")
    con.execute("CREATE TABLE StreetList (STREET_NAME TEXT)")
    con.executemany("INSERT INTO CITY VALUES (?,?,?)", [(4000, "Haifa", "חיפה"), (6200, "Bat Yam", "בת ים"), (5000, "Tel Aviv", "תל אביב")])
    con.executemany("INSERT INTO COUNTRY VALUES (?,?)", [(15, "ישראל"), (210, "רומניה"), (230, "פולין")])
    con.executemany("INSERT INTO StreetList VALUES (?)", [("הרצל",), ("ביאליק",), ("אלנבי",)])
    last = ["כהן", "לוי", "מזרחי", "ביטון", "פרידמן", "אברהם"]
    first = ["דוד", "משה", "רחל", "שרה", "יוסף", "מרים", "אברהם", "חנה"]
    rows = []
    for i in range(1, n + 1):
        y = rnd.randint(1930, 2015)
        rows.append((i * 7, rnd.choice(last), rnd.choice(first), None, "%d%02d%02d" % (y, rnd.randint(1, 12), rnd.randint(1, 28)),
                     rnd.choice([10, 20, 30, 40]), rnd.choice(first), rnd.choice(first), None, None,
                     rnd.choice([15, 210, 230]), rnd.choice([4000, 6200, 5000]), rnd.choice(["הרצל", "ביאליק", "אלנבי"]), rnd.randint(1, 40), 0))
    # משפחה מוכרת לבדיקות: אב, אם, שני ילדים
    rows[0] = (7, "בדיקה", "אבי", None, "19500101", 20, "", "", None, None, 15, 4000, "הרצל", 5, 0)
    rows[1] = (14, "בדיקה", "אמא", None, "19520202", 20, "", "", None, None, 15, 4000, "הרצל", 5, 0)
    rows[2] = (21, "בדיקה", "ילד", None, "19800303", 10, "אבי", "אמא", 7, 14, 15, 4000, "הרצל", 5, 0)
    rows[3] = (28, "בדיקה", "ילדה", None, "19820404", 10, "אבי", "אמא", 7, 14, 15, 4000, "הרצל", 5, 0)
    con.executemany("INSERT INTO M VALUES (%s)" % ",".join("?" * 15), rows)
    con.commit()
    con.close()


if __name__ == "__main__":
    make(sys.argv[1] if len(sys.argv) > 1 else "sample.sqlite")
