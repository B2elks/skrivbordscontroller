"""SQLite-lagring för Skrivbordet — bara stdlib.

En anslutning per anrop (Flask-request eller long-poll-varv). Alla skrivningar
som måste vara atomära (t.ex. "skapa uppdrag om inget pågår") görs inom
transaktion(), som tar skrivlåset direkt med BEGIN IMMEDIATE.

Tider lagras som unix-sekunder (REAL). Position NULL = okänd.
"""

import contextlib
import os
import sqlite3

import logik

_ROT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAGAENDE = ("väntar", "utskickat")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kalibrering (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    min_cm        REAL,
    max_cm        REAL,
    restid_upp_s  REAL,
    restid_ner_s  REAL
);
INSERT OR IGNORE INTO kalibrering (id) VALUES (1);

CREATE TABLE IF NOT EXISTS position (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    cm          REAL,
    uppdaterad  REAL
);
INSERT OR IGNORE INTO position (id) VALUES (1);

CREATE TABLE IF NOT EXISTS uppdrag (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    rela                 TEXT NOT NULL CHECK (rela IN ('upp', 'ner')),
    ms                   INTEGER NOT NULL CHECK (ms >= 0),
    status               TEXT NOT NULL CHECK (status IN
                             ('väntar', 'utskickat', 'klar', 'timeout', 'avbrutet')),
    kalla                TEXT,
    skapad               REAL NOT NULL,
    utskickat_tid        REAL,
    klar_tid             REAL,
    ms_kord              INTEGER,
    uppringare           TEXT,
    begard_cm            REAL,
    faktisk_cm           REAL,
    start_position       REAL,
    ny_position          REAL,
    lage                 TEXT,
    tidtagning           INTEGER NOT NULL DEFAULT 0,
    stopp_tid            REAL,
    stopp_levererad_tid  REAL,
    uppmatt_ms           INTEGER
);
CREATE INDEX IF NOT EXISTS uppdrag_status ON uppdrag (status);

CREATE TABLE IF NOT EXISTS handelse (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    tid   REAL NOT NULL,
    typ   TEXT NOT NULL,
    text  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dygn (
    datum              TEXT PRIMARY KEY,
    rorelser           INTEGER NOT NULL DEFAULT 0,
    ateruppringningar  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS enhet (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    version     TEXT,
    senast_sedd REAL,
    startad     REAL
);
INSERT OR IGNORE INTO enhet (id) VALUES (1);

CREATE TABLE IF NOT EXISTS profil (
    nyckel  TEXT PRIMARY KEY,
    namn    TEXT NOT NULL,
    cm      REAL NOT NULL,
    skapad  REAL NOT NULL,
    andrad  REAL NOT NULL
);
"""


def standard_sokvag():
    return os.environ.get("SKRIVBORD_DB") or os.path.join(_ROT, "data", "skrivbord.db")


def anslut(sokvag=None, skapa=True):
    sokvag = sokvag or standard_sokvag()
    katalog = os.path.dirname(sokvag)
    if katalog:
        os.makedirs(katalog, exist_ok=True)
    conn = sqlite3.connect(sokvag, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    if skapa:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    return conn


@contextlib.contextmanager
def transaktion(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --- kalibrering och position ---

def hamta_kalibrering(conn):
    r = conn.execute("SELECT min_cm, max_cm, restid_upp_s, restid_ner_s FROM kalibrering").fetchone()
    return logik.Kalibrering(r["min_cm"], r["max_cm"], r["restid_upp_s"], r["restid_ner_s"])


def spara_kalibrering(conn, kal):
    conn.execute("UPDATE kalibrering SET min_cm=?, max_cm=?, restid_upp_s=?, restid_ner_s=?",
                 (kal.min_cm, kal.max_cm, kal.restid_upp_s, kal.restid_ner_s))


def hamta_position(conn):
    return conn.execute("SELECT cm FROM position").fetchone()["cm"]


def satt_position(conn, cm, tid):
    conn.execute("UPDATE position SET cm=?, uppdaterad=?", (cm, tid))


# --- uppdrag ---

def nytt_uppdrag(conn, **falt):
    kolumner = ", ".join(falt)
    platser = ", ".join("?" for _ in falt)
    cur = conn.execute(f"INSERT INTO uppdrag ({kolumner}) VALUES ({platser})", tuple(falt.values()))
    return cur.lastrowid


def hamta_uppdrag(conn, id_):
    r = conn.execute("SELECT * FROM uppdrag WHERE id=?", (id_,)).fetchone()
    return dict(r) if r else None


def pagaende_uppdrag(conn):
    r = conn.execute("SELECT * FROM uppdrag WHERE status IN (?, ?) ORDER BY id LIMIT 1", PAGAENDE).fetchone()
    return dict(r) if r else None


def uppdrag_med_status(conn, status):
    return [dict(r) for r in conn.execute("SELECT * FROM uppdrag WHERE status=? ORDER BY id", (status,))]


def uppdatera_uppdrag(conn, id_, **falt):
    satt = ", ".join(f"{k}=?" for k in falt)
    conn.execute(f"UPDATE uppdrag SET {satt} WHERE id=?", (*falt.values(), id_))


def senaste_uppdrag(conn, n=10):
    return [dict(r) for r in conn.execute("SELECT * FROM uppdrag ORDER BY id DESC LIMIT ?", (n,))]


def antal_uppdrag(conn):
    return conn.execute("SELECT COUNT(*) FROM uppdrag").fetchone()[0]


# --- händelser ---

def logga(conn, tid, typ, text):
    conn.execute("INSERT INTO handelse (tid, typ, text) VALUES (?, ?, ?)", (tid, typ, text))


def senaste_handelser(conn, n=20):
    return [dict(r) for r in conn.execute("SELECT * FROM handelse ORDER BY id DESC LIMIT ?", (n,))]


# --- dygnsräknare ---

def hamta_dygn(conn, datum):
    r = conn.execute("SELECT rorelser, ateruppringningar FROM dygn WHERE datum=?", (datum,)).fetchone()
    return {"datum": datum, "rorelser": r["rorelser"] if r else 0,
            "ateruppringningar": r["ateruppringningar"] if r else 0}


def oka_dygn(conn, datum, falt):
    if falt not in ("rorelser", "ateruppringningar"):
        raise ValueError(falt)
    conn.execute("INSERT OR IGNORE INTO dygn (datum) VALUES (?)", (datum,))
    conn.execute(f"UPDATE dygn SET {falt} = {falt} + 1 WHERE datum=?", (datum,))


def reservera_dygn(conn, datum, falt, tak):
    """Räkna upp falt om det ligger under taket. Anropas inom transaktion() så att
    samtidiga anrop inte tillsammans kan passera taket. True om platsen togs."""
    if hamta_dygn(conn, datum)[falt] >= tak:
        return False
    oka_dygn(conn, datum, falt)
    return True


# --- enheten ---

def enhet_sedd(conn, tid, version=None, startad=False):
    conn.execute("UPDATE enhet SET senast_sedd=?", (tid,))
    if version is not None:
        conn.execute("UPDATE enhet SET version=?", (version,))
    if startad:
        conn.execute("UPDATE enhet SET startad=?", (tid,))


def hamta_enhet(conn):
    return dict(conn.execute("SELECT version, senast_sedd, startad FROM enhet").fetchone())


# --- profiler ---

def hamta_profiler(conn):
    """Alla profiler som {nyckel, namn, cm, skapad, andrad}, osorterade."""
    return [dict(r) for r in conn.execute("SELECT nyckel, namn, cm, skapad, andrad FROM profil")]


def spara_profil(conn, nyckel, namn, cm, tid):
    """Skapa eller skriv över. Visningsnamnet och skapad behålls vid överskrivning."""
    conn.execute("INSERT INTO profil (nyckel, namn, cm, skapad, andrad) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(nyckel) DO UPDATE SET cm=excluded.cm, andrad=excluded.andrad",
                 (nyckel, namn, cm, tid, tid))


def ta_bort_profil(conn, nyckel):
    """True om en profil togs bort."""
    return conn.execute("DELETE FROM profil WHERE nyckel=?", (nyckel,)).rowcount > 0
