"""Flask-server för Skrivbordet.

Starta med app-fabriken, t.ex. `gunicorn --threads 8 'app:skapa_app()'`.
Long-pollen håller en tråd i upp till 25 s, så arbetaren måste ha trådar.

Tre sorters klienter:
  - enheten (ESP32), bearer-token ENHET_TOKEN — /api/enhet/*
  - agenten (ElevenLabs server tools), header X-Agent-Hemlighet — /api/agent/*
  - admin, HTTP basic auth (admin / ADMIN_LOSEN) — /admin

All rörelselogik (cm→ms, klippning, position) ligger i logik.py. Här finns
bara uppdragskön runt den: ett uppdrag i taget, timeout, bara svenska nummer.

Agentens anrop är synkrona: ett godkänt uppdrag hålls öppet tills enheten
rapporterat klart (eller timeout), och svaret är då resultatet — "Bordet är
redo, det står nu på 92 centimeter." — som agenten läser upp i samma samtal.
Servern ringer aldrig ut och har ingen utgående klient alls. Väntan håller en
gunicorn-tråd (8 st) i upp till uppdragets ms + AGENT_VANTA_EXTRA_S.

Tidtagning vid kalibrering (stopp-knappen):
  Ett tidtagningsuppdrag skickas med "tidtagning": true. Enheten drar reläet
  och frågar sedan GET /api/enhet/fortsatt?id=… ungefär var 500:e ms, med
  reläet draget hela tiden (motorn går jämnt även medan frågan pågår). Svaret
  false — eller ett nätfel — får enheten att släppa direkt, så felläget är
  alltid "stanna". Admins stopp-klick sparar stopp_tid; frågan som får beskedet
  sparar stopp_levererad_tid. Uppmätt restid = reläets tid enligt enhetens egen
  klocka (ms_kord) minus fördröjningen mellan klick och leverans. Kvar som fel:
  svarets väg tillbaka till enheten och människans reaktionstid.
"""

import collections
import hmac
import math
import os
import secrets
import time
from functools import wraps

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)

import db
import logik

HEMLIGHETER = ("ENHET_TOKEN", "AGENT_HEMLIGHET", "ADMIN_LOSEN", "SECRET_KEY")

POLL_S_STANDARD = 0.2          # hur ofta long-pollen tittar i databasen
MAX_VANTA_S = 25.0             # längsta long-poll
TIMEOUT_MARGINAL_S = 30        # regel 8: ingen klar inom ms + 30 s ⇒ timeout
AGENT_VANTA_EXTRA_S = 15       # agentanropet väntar högst uppdragets ms + så här länge
MAX_MS_KORD = 600_000          # rimlighetstak för rapporterad körtid

# profil: {namn, cm} när uppdraget kör till en sparad profil — ger profiltexten när bordet stannat.
Svar = collections.namedtuple("Svar", "ok sag id profil", defaults=(None,))

STATUS_TEXT = {"väntar": "väntar på bordet", "utskickat": "kör", "klar": "klar",
               "timeout": "svarade inte", "avbrutet": "avbrutet"}


def vanta_s(arg):
    """Tolka ?vanta= och kläm till [0, 25] s. Saknat eller skräp ⇒ 25."""
    try:
        v = float(arg)
    except (TypeError, ValueError):
        return MAX_VANTA_S
    if math.isnan(v):
        return MAX_VANTA_S
    return min(max(v, 0.0), MAX_VANTA_S)


def _heltal(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def _tal(v):
    """Formulärfält → float (decimalkomma godtas). '' ⇒ None. Skräp ⇒ ValueError."""
    v = (v or "").strip().replace(",", ".")
    if not v:
        return None
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(v)
    return x


class Bord:
    """Uppdragskön och tillståndet runt den. Varje metod öppnar en egen anslutning,
    så objektet går att dela mellan trådar."""

    VANTAR_MAX_S = 60              # ett uppdrag som enheten inte hämtat på en minut går ut
    MAX_TIDTAGNING_MS = 60_000     # tidtagning/kör till botten; enheten kapar själv vid MAX_MS

    def __init__(self, sokvag, klocka=time.time):
        self.sokvag = sokvag
        self.klocka = klocka
        db.anslut(sokvag).close()   # skapa schemat en gång

    # --- krokar ---
    # Anropas efter commit när ett uppdrag blivit klart eller fått timeout. Svaret
    # ges i samtalet (vanta_pa), så krokarna loggar bara; kontrollerna kan byta ut dem.

    def vid_klar(self, uppdrag):
        if uppdrag.get("kalla") == "agent":
            self._logga("klar", f"Uppdrag {uppdrag['id']} klart — svar i samtalet.")

    def vid_timeout(self, uppdrag):
        if uppdrag.get("kalla") == "agent":
            self._logga("timeout", f"Uppdrag {uppdrag['id']} fick timeout — svar i samtalet.")

    # --- intern infrastruktur ---

    def _anslut(self):
        return db.anslut(self.sokvag, skapa=False)

    def _datum(self, t=None):
        return time.strftime("%Y-%m-%d", time.localtime(self.klocka() if t is None else t))

    def _logga(self, typ, text):
        conn = self._anslut()
        try:
            db.logga(conn, self.klocka(), typ, text)
        finally:
            conn.close()

    def _tx(self, fn):
        """Kör fn(conn, krokar) i en skrivtransaktion, efter att ha städat bort
        utgångna uppdrag. Krokar körs efter commit och får aldrig fälla anropet."""
        krokar = []
        conn = self._anslut()
        try:
            with db.transaktion(conn):
                self._stada(conn, krokar)
                resultat = fn(conn, krokar)
        finally:
            conn.close()
        for namn, u in krokar:
            try:
                getattr(self, namn)(u)
            except Exception as e:  # en trasig krok får inte stoppa enheten eller agenten
                self._logga("fel", f"Kroken {namn} för uppdrag {u['id']} kastade: {type(e).__name__}: {e}")
        return resultat

    def _stada(self, conn, krokar):
        """Regel 8, kontrolleras lat vid varje anrop (och i varje long-poll-varv)."""
        nu = self.klocka()
        for u in db.uppdrag_med_status(conn, "utskickat"):
            if nu > u["utskickat_tid"] + u["ms"] / 1000 + TIMEOUT_MARGINAL_S:
                db.uppdatera_uppdrag(conn, u["id"], status="timeout")
                db.satt_position(conn, None, nu)
                db.logga(conn, nu, "timeout",
                         f"Uppdrag {u['id']} ({u['rela']} {u['ms']} ms) rapporterades aldrig klart. Positionen är okänd.")
                krokar.append(("vid_timeout", dict(u, status="timeout")))
        for u in db.uppdrag_med_status(conn, "väntar"):
            if nu > u["skapad"] + self.VANTAR_MAX_S:
                # Bordet har inte rört sig, så positionen står kvar.
                db.uppdatera_uppdrag(conn, u["id"], status="timeout")
                db.logga(conn, nu, "timeout",
                         f"Uppdrag {u['id']} hämtades aldrig av bordet och har strukits.")
                krokar.append(("vid_timeout", dict(u, status="timeout")))

    # --- läsning ---

    def _las(self, fn):
        conn = self._anslut()
        try:
            return fn(conn)
        finally:
            conn.close()

    def stada(self):
        self._tx(lambda conn, k: None)

    def kalibrering(self):
        return self._las(db.hamta_kalibrering)

    def position(self):
        return self._las(db.hamta_position)

    def uppdrag(self, id_):
        return self._las(lambda c: db.hamta_uppdrag(c, id_))

    def pagaende(self):
        return self._las(db.pagaende_uppdrag)

    def antal_uppdrag(self):
        return self._las(db.antal_uppdrag)

    def enhet(self):
        return self._las(db.hamta_enhet)

    def dygn(self):
        return self._las(lambda c: db.hamta_dygn(c, self._datum()))

    def handelser(self, n=20):
        return self._las(lambda c: db.senaste_handelser(c, n))

    def senaste_tidtagning(self):
        return self._las(lambda c: (c.execute(
            "SELECT * FROM uppdrag WHERE tidtagning=1 ORDER BY id DESC LIMIT 1").fetchone()))

    # --- skrivning ---

    def spara_kalibrering(self, kal):
        def fn(conn, k):
            db.spara_kalibrering(conn, kal)
            db.logga(conn, self.klocka(), "kalibrering",
                     f"Kalibrering: {kal.min_cm}–{kal.max_cm} cm, upp {kal.restid_upp_s} s, ner {kal.restid_ner_s} s.")
        self._tx(fn)

    def satt_position(self, cm):
        self._tx(lambda conn, k: db.satt_position(conn, cm, self.klocka()))

    def _nekat_nummer(self, kalla, uppringare):
        """Svar med nekandetexten om numret inte får styra bordet, annars None. Admin går alltid."""
        if kalla != "admin" and not logik.far_styra(uppringare):
            self._logga("nekad", f"{kalla}: nummer {uppringare!r} är inte ett tillåtet svenskt nummer.")
            return Svar(False, logik.TEXT_EJ_TILLATET, None)
        return None

    def begar_rorelse(self, kalla, uppringare=None, riktning=None, cm=None, lage=None, hojd=None,
                      profil=None):
        """Gemensam väg för admin och agent. Returnerar Svar(ok, sag, id, profil).
        Exakt en av riktning+cm (relativ), lage (stå/sitt), hojd (absolut cm) eller
        profil (namn på sparad höjd — körs exakt som hojd=profilens cm).
        Agenten kräver ett svenskt nummer. Inget tak på antal rörelser (beslut 2026-10-14);
        dygnsräknaren finns kvar som statistik."""
        nekat = self._nekat_nummer(kalla, uppringare)
        if nekat:
            return nekat
        nummer = logik.normalisera_nummer(uppringare) if kalla != "admin" else None

        def fn(conn, krokar):
            nu = self.klocka()
            kal = db.hamta_kalibrering(conn)
            if not kal.giltig():
                return Svar(False, logik.TEXT_OKALIBRERAT, None)
            if db.pagaende_uppdrag(conn):
                return Svar(False, logik.TEXT_ROR_SIG_REDAN, None)
            pos = db.hamta_position(conn)
            vald = None
            if profil is not None:
                vald = logik.valj_profil(profil, db.hamta_profiler(conn))
                if isinstance(vald, logik.Nekande):
                    return Svar(False, vald.sag, None)
                plan = logik.planera_profil(kal, pos, vald)
            elif lage is not None:
                plan = logik.planera_lage(kal, lage, pos)
            elif hojd is not None:
                plan = logik.planera_hojd(kal, pos, hojd)
            else:
                plan = logik.planera_relativ(kal, pos, riktning, cm)
            if isinstance(plan, logik.Nekande):
                return Svar(False, plan.sag, None)
            datum = self._datum(nu)
            # Stå/sitt och höjder i ändläget bär läge: klar() nollställer då positionen exakt.
            lage_norm = plan.lage
            relativ = lage is None and hojd is None and profil is None
            id_ = db.nytt_uppdrag(
                conn, rela=plan.rela, ms=plan.ms, status="väntar", kalla=kalla, skapad=nu,
                uppringare=nummer, begard_cm=logik._cm(cm) if relativ else None,
                faktisk_cm=plan.faktisk_cm, start_position=pos, ny_position=plan.ny_position,
                lage=lage_norm)
            db.oka_dygn(conn, datum, "rorelser")
            vad = lage_norm or f"{plan.rela} {logik.avrunda(plan.faktisk_cm)} cm"
            if hojd is not None:
                vad = f"höjd {_sv(plan.ny_position)} cm, {vad}"
            if vald is not None:
                vad = f"profil {vald['namn']} ({_sv(vald['cm'])} cm), {vad}"
            db.logga(conn, nu, "uppdrag", f"Uppdrag {id_} från {kalla}: {vad} ({plan.rela} {plan.ms} ms).")
            return Svar(True, plan.sag, id_, {"namn": vald["namn"], "cm": vald["cm"]} if vald else None)

        return self._tx(fn)

    # --- profiler ---

    def profiler(self):
        return self._las(lambda c: logik.sortera_profiler(db.hamta_profiler(c)))

    def spara_profil(self, kalla, namn, uppringare=None):
        """Spara bordets aktuella position under namn (skriv över om namnet finns)."""
        nekat = self._nekat_nummer(kalla, uppringare)
        if nekat:
            return nekat

        def fn(conn, krokar):
            nu = self.klocka()
            kal = db.hamta_kalibrering(conn)
            if kal.giltig() and db.pagaende_uppdrag(conn):
                # Positionen är planerad, inte uppnådd, medan bordet rör sig.
                return Svar(False, logik.TEXT_ROR_SIG_REDAN, None)
            s = logik.planera_spara_profil(kal, db.hamta_position(conn), namn, db.hamta_profiler(conn))
            if isinstance(s, logik.Nekande):
                return Svar(False, s.sag, None)
            db.spara_profil(conn, s.nyckel, s.namn, s.cm, nu)
            var = "" if s.gammal_cm is None else f" (var {_sv(s.gammal_cm)} cm)"
            db.logga(conn, nu, "profil", f"Profil {s.namn} sparad på {_sv(s.cm)} cm{var} från {kalla}.")
            return Svar(True, s.sag, None)
        return self._tx(fn)

    def lista_profiler(self, kalla, uppringare=None):
        nekat = self._nekat_nummer(kalla, uppringare)
        if nekat:
            return nekat
        return Svar(True, logik.text_profiler(self.profiler()), None)

    def ta_bort_profil(self, nyckel):
        """Bara admin. Tar bort på exakt nyckel."""
        def fn(conn, krokar):
            p = next((p for p in db.hamta_profiler(conn) if p["nyckel"] == nyckel), None)
            if p is None or not db.ta_bort_profil(conn, nyckel):
                return Svar(False, "Profilen finns inte.", None)
            db.logga(conn, self.klocka(), "profil", f"Profil {p['namn']} ({_sv(p['cm'])} cm) borttagen.")
            return Svar(True, f"Profilen {p['namn']} är borttagen.", None)
        return self._tx(fn)

    def _kalibreringsuppdrag(self, rela, lage=None, tidtagning=False):
        def fn(conn, krokar):
            nu = self.klocka()
            if db.pagaende_uppdrag(conn):
                return Svar(False, logik.TEXT_ROR_SIG_REDAN, None)
            id_ = db.nytt_uppdrag(conn, rela=rela, ms=self.MAX_TIDTAGNING_MS, status="väntar",
                                  kalla="admin", skapad=nu, start_position=db.hamta_position(conn),
                                  lage=lage, tidtagning=1 if tidtagning else 0)
            db.oka_dygn(conn, self._datum(nu), "rorelser")
            vad = "Kör till botten" if lage == "botten" else f"Tidtagning {rela}"
            db.logga(conn, nu, "uppdrag", f"Uppdrag {id_}: {vad}.")
            return Svar(True, f"{vad} startad.", id_)
        return self._tx(fn)

    def till_botten(self):
        return self._kalibreringsuppdrag("ner", lage="botten")

    def tidtagning_start(self, riktning):
        if riktning not in ("upp", "ner"):
            return Svar(False, "Okänd riktning.", None)
        return self._kalibreringsuppdrag(riktning, tidtagning=True)

    def tidtagning_stopp(self):
        def fn(conn, krokar):
            nu = self.klocka()
            u = db.pagaende_uppdrag(conn)
            if not u or not u["tidtagning"]:
                return Svar(False, "Ingen tidtagning pågår.", None)
            if u["status"] == "väntar":
                # Bordet har inte börjat röra sig — stryk uppdraget.
                db.uppdatera_uppdrag(conn, u["id"], status="avbrutet", stopp_tid=nu)
                db.logga(conn, nu, "tidtagning", f"Tidtagning {u['id']} stoppad innan bordet hann starta.")
                return Svar(True, "Stoppad innan bordet startade.", u["id"])
            if u["stopp_tid"] is None:
                db.uppdatera_uppdrag(conn, u["id"], stopp_tid=nu)
                db.logga(conn, nu, "tidtagning", f"Stopp för tidtagning {u['id']}.")
            return Svar(True, "Stopp skickat.", u["id"])
        return self._tx(fn)

    def spara_matning(self, id_):
        def fn(conn, krokar):
            u = db.hamta_uppdrag(conn, id_)
            if not u or not u["tidtagning"] or u["status"] != "klar" or not u["uppmatt_ms"]:
                return Svar(False, "Det finns ingen uppmätt restid att spara.", None)
            gammal = db.hamta_kalibrering(conn)
            s = u["uppmatt_ms"] / 1000
            ny = logik.Kalibrering(gammal.min_cm, gammal.max_cm,
                                   s if u["rela"] == "upp" else gammal.restid_upp_s,
                                   s if u["rela"] == "ner" else gammal.restid_ner_s)
            db.spara_kalibrering(conn, ny)
            db.logga(conn, self.klocka(), "kalibrering", f"Restid {u['rela']} satt till {s:.2f} s från mätning.")
            return Svar(True, f"Restid {u['rela']} sparad: {_sv(s, 2)} s.", id_)
        return self._tx(fn)

    # --- enheten ---

    def enhet_sedd(self, version=None, startad=False):
        self._tx(lambda conn, k: db.enhet_sedd(conn, self.klocka(), version, startad))

    def hamta_nasta(self):
        """Nästa väntande uppdrag, markerat utskickat, eller None."""
        def fn(conn, krokar):
            r = conn.execute("SELECT * FROM uppdrag WHERE status='väntar' ORDER BY id LIMIT 1").fetchone()
            if not r:
                return None
            nu = self.klocka()
            db.uppdatera_uppdrag(conn, r["id"], status="utskickat", utskickat_tid=nu)
            db.enhet_sedd(conn, nu)
            return dict(r, status="utskickat", utskickat_tid=nu)
        return self._tx(fn)

    def start(self, version):
        """Enheten har startat om. Ett uppdrag som var utskickat kan ha körts till
        hälften — positionen går inte att lita på."""
        def fn(conn, krokar):
            nu = self.klocka()
            for u in db.uppdrag_med_status(conn, "utskickat"):
                db.uppdatera_uppdrag(conn, u["id"], status="avbrutet")
                db.satt_position(conn, None, nu)
                db.logga(conn, nu, "avbrutet",
                         f"Bordet startade om under uppdrag {u['id']}. Positionen är okänd.")
            db.enhet_sedd(conn, nu, version, startad=True)
            db.logga(conn, nu, "enhet", f"Bordet startade (version {version}).")
        self._tx(fn)

    def klar(self, id_, ms_kord):
        """Idempotent på id. Returnerar None om id saknas, annars True om det
        här anropet avslutade uppdraget och False om det redan var avslutat."""
        def fn(conn, krokar):
            nu = self.klocka()
            u = db.hamta_uppdrag(conn, id_)
            if u is None:
                return None
            db.enhet_sedd(conn, nu)
            if u["status"] not in db.PAGAENDE:
                if u["status"] != "klar":
                    db.logga(conn, nu, "sen",
                             f"Sen klar för uppdrag {id_} (status {u['status']}) ignorerad; positionen står kvar som okänd.")
                return False
            kal = db.hamta_kalibrering(conn)
            uppmatt = None
            if u["tidtagning"]:
                pos = None
                if u["stopp_tid"] is not None and u["stopp_levererad_tid"] is not None:
                    fordrojning = round((u["stopp_levererad_tid"] - u["stopp_tid"]) * 1000)
                    uppmatt = max(0, ms_kord - fordrojning)
                text = (f"Tidtagning {id_}: reläet drogs {ms_kord} ms, uppmätt restid {uppmatt} ms."
                        if uppmatt is not None else
                        f"Tidtagning {id_}: reläet drogs {ms_kord} ms utan stoppklick — ingen mätning.")
            elif u["lage"] == "botten":
                pos = kal.min_cm if logik._ar_tal(kal.min_cm) else None
                text = f"Uppdrag {id_}: bordet är i botten."
            else:
                pos = logik.position_efter(kal, u["start_position"], u["rela"], ms_kord, u["lage"])
                if u["lage"] and kal.giltig() and ms_kord < kal.restid(u["rela"]) * 1000:
                    pos = None  # körde kortare än full restid — ändläget är inte säkert nått
                text = f"Uppdrag {id_} klart efter {ms_kord} ms."
            falt = {} if u["tidtagning"] else {"ny_position": pos}   # planerad → uppskattad efter körning
            db.uppdatera_uppdrag(conn, id_, status="klar", klar_tid=nu, ms_kord=ms_kord, uppmatt_ms=uppmatt, **falt)
            db.satt_position(conn, pos, nu)
            pos_text = "okänd" if pos is None else f"{_sv(pos)} cm"
            db.logga(conn, nu, "klar", f"{text} Position: {pos_text}.")
            krokar.append(("vid_klar", dict(u, status="klar", ms_kord=ms_kord, ny_position=pos, uppmatt_ms=uppmatt)))
            return True
        return self._tx(fn)

    def vanta_pa(self, id_, extra_s=AGENT_VANTA_EXTRA_S, poll_s=POLL_S_STANDARD, profil=None):
        """Vänta tills uppdraget är klart eller fått timeout, högst ms + extra_s
        (väggklocka, oberoende av KLOCKA). Returnerar meningen agenten ska säga.
        profil = {namn, cm} ger profiltexten ("Bordet står nu på Björns höjd …")."""
        u = self.uppdrag(id_)
        slut = time.monotonic() + (u["ms"] if u else 0) / 1000 + extra_s
        while True:
            self.stada()                      # upptäcker timeout enligt regel 8
            u = self.uppdrag(id_)
            if u is None:
                return logik.TEXT_SVARAR_INTE
            if u["status"] == "klar":
                pos = u["ny_position"]
                if pos is None or not logik._ar_tal(pos):
                    return logik.TEXT_KLAR_OKAND_POSITION
                if profil is not None:
                    return logik.text_profil_klar(profil["namn"], profil["cm"], pos)
                return logik.text_klar(pos, u["begard_cm"], u["faktisk_cm"])
            if u["status"] not in db.PAGAENDE or time.monotonic() >= slut:
                if u["status"] in db.PAGAENDE:
                    self._logga("timeout", f"Uppdrag {id_}: inget klart inom ms + {extra_s:g} s, "
                                           f"agenten fick 'svarar inte'.")
                return logik.TEXT_SVARAR_INTE
            time.sleep(poll_s)

    def fortsatt(self, id_):
        """Ska enheten fortsätta hålla reläet under tidtagning? Allt utom ett
        pågående, ostoppat tidtagningsuppdrag ger False."""
        def fn(conn, krokar):
            u = db.hamta_uppdrag(conn, id_) if id_ is not None else None
            nu = self.klocka()
            db.enhet_sedd(conn, nu)
            if not u or u["status"] != "utskickat" or not u["tidtagning"]:
                return False
            if u["stopp_tid"] is not None:
                if u["stopp_levererad_tid"] is None:
                    db.uppdatera_uppdrag(conn, id_, stopp_levererad_tid=nu)
                return False
            return True
        return self._tx(fn)


# --- presentation ---

def _sv(x, dec=1):
    if x is None:
        return "—"
    return f"{x:.{dec}f}".replace(".", ",")


def _klockslag(t):
    return "—" if t is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


# --- appen ---

def skapa_app(config=None):
    config = dict(config or {})
    cfg = {k: os.environ.get(k) for k in HEMLIGHETER}
    cfg["DB"] = db.standard_sokvag()
    cfg.update(config)
    saknas = [k for k in HEMLIGHETER if not cfg.get(k)]
    if saknas:
        raise RuntimeError("Vägrar starta — saknar hemligheter: " + ", ".join(saknas))

    app = Flask(__name__)
    app.secret_key = cfg["SECRET_KEY"]
    app.json.ensure_ascii = False   # agenten och loggar läser å, ä, ö som de är
    testning = bool(cfg.get("TESTING"))
    app.config.update(
        TESTING=testning,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(cfg.get("SESSION_COOKIE_SECURE", not testning)),
    )
    bord = Bord(cfg["DB"], cfg.get("KLOCKA") or time.time)
    app.extensions["bord"] = bord
    poll_s = float(cfg.get("POLL_S") or POLL_S_STANDARD)
    enhet_token = cfg["ENHET_TOKEN"].encode()
    agent_hemlighet = cfg["AGENT_HEMLIGHET"].encode()
    admin_losen = cfg["ADMIN_LOSEN"].encode()
    app.jinja_env.filters["sv"] = _sv
    app.jinja_env.filters["klockslag"] = _klockslag

    def lika(a, b):
        return hmac.compare_digest(a, b)

    def kraver_enhet(f):
        @wraps(f)
        def inre(*a, **kw):
            h = request.headers.get("Authorization", "")
            if not h.startswith("Bearer ") or not lika(h[7:].strip().encode(), enhet_token):
                return jsonify({"fel": "obehörig"}), 401
            return f(*a, **kw)
        return inre

    def kraver_agent(f):
        @wraps(f)
        def inre(*a, **kw):
            h = request.headers.get("X-Agent-Hemlighet", "")
            if not h or not lika(h.encode(), agent_hemlighet):
                return jsonify({"fel": "obehörig"}), 401
            return f(*a, **kw)
        return inre

    def kraver_admin(f):
        @wraps(f)
        def inre(*a, **kw):
            auth = request.authorization
            ok = (auth is not None and auth.type == "basic"
                  and lika((auth.username or "").encode(), b"admin")
                  & lika((auth.password or "").encode(), admin_losen))
            if not ok:
                return Response("Inloggning krävs.\n", 401,
                                {"WWW-Authenticate": 'Basic realm="Skrivbordet", charset="UTF-8"'})
            if request.method == "POST":
                skickad = request.form.get("csrf", "")
                vantad = session.get("csrf", "")
                if not skickad or not vantad or not lika(skickad.encode(), vantad.encode()):
                    abort(400, "Ogiltig eller saknad formulärtoken. Ladda om sidan.")
            return f(*a, **kw)
        return inre

    # --- enheten ---

    @app.get("/api/enhet/uppdrag")
    @kraver_enhet
    def enhet_uppdrag():
        slut = time.monotonic() + vanta_s(request.args.get("vanta"))
        bord.enhet_sedd()
        while True:
            u = bord.hamta_nasta()
            if u:
                svar = {"id": u["id"], "rela": u["rela"], "ms": u["ms"]}
                if u["tidtagning"]:
                    svar["tidtagning"] = True
                return jsonify(svar)
            kvar = slut - time.monotonic()
            if kvar <= 0:
                return "", 204
            time.sleep(min(poll_s, kvar))

    @app.post("/api/enhet/klar")
    @kraver_enhet
    def enhet_klar():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"fel": "kroppen ska vara JSON {id, ms_kord}"}), 400
        id_, ms_kord = _heltal(data.get("id")), _heltal(data.get("ms_kord"))
        if id_ is None or ms_kord is None:
            return jsonify({"fel": "id och ms_kord ska vara heltal"}), 400
        ms_kord = min(max(ms_kord, 0), MAX_MS_KORD)
        resultat = bord.klar(id_, ms_kord)
        if resultat is None:
            return jsonify({"fel": "okänt uppdrag"}), 404
        return jsonify({"ok": True, "redan": not resultat})

    @app.post("/api/enhet/start")
    @kraver_enhet
    def enhet_start():
        data = request.get_json(silent=True)
        version = data.get("version") if isinstance(data, dict) else None
        bord.start(str(version)[:64] if version is not None else "okänd")
        return jsonify({"ok": True})

    @app.get("/api/enhet/fortsatt")
    @kraver_enhet
    def enhet_fortsatt():
        return jsonify({"fortsatt": bool(bord.fortsatt(_heltal(request.args.get("id"))))})

    # --- agenten ---

    agent_vanta_extra_s = float(cfg["AGENT_VANTA_EXTRA_S"]) if cfg.get("AGENT_VANTA_EXTRA_S") is not None \
        else AGENT_VANTA_EXTRA_S

    def _agentsvar(fn):
        """Nekanden svaras direkt. Ett godkänt uppdrag väntas ut, och svaret är
        resultatet (klar-texten eller 'svarar inte') i stället för bekräftelsen."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = {}
        try:
            s = fn(data)
            if s.ok and s.id is not None:
                s = Svar(True, bord.vanta_pa(s.id, agent_vanta_extra_s, poll_s, s.profil), s.id)
        except Exception as e:  # agenten ska alltid få en mening att säga
            bord._logga("fel", f"Agentanrop kastade: {type(e).__name__}: {e}")
            s = Svar(False, "Något gick fel. Försök igen.", None)
        return jsonify({"ok": bool(s.ok), "sag": s.sag})

    @app.post("/api/agent/flytta")
    @kraver_agent
    def agent_flytta():
        return _agentsvar(lambda d: bord.begar_rorelse(
            "agent", uppringare=d.get("uppringare"), riktning=d.get("riktning"), cm=d.get("cm")))

    @app.post("/api/agent/lage")
    @kraver_agent
    def agent_lage():
        return _agentsvar(lambda d: bord.begar_rorelse(
            "agent", uppringare=d.get("uppringare"), lage=d.get("lage") or ""))

    @app.post("/api/agent/hojd")
    @kraver_agent
    def agent_hojd():
        return _agentsvar(lambda d: bord.begar_rorelse(
            "agent", uppringare=d.get("uppringare"), hojd=d.get("cm")))

    @app.post("/api/agent/profil/spara")
    @kraver_agent
    def agent_profil_spara():
        return _agentsvar(lambda d: bord.spara_profil("agent", d.get("namn"), uppringare=d.get("uppringare")))

    @app.post("/api/agent/profil/till")
    @kraver_agent
    def agent_profil_till():
        return _agentsvar(lambda d: bord.begar_rorelse(
            "agent", uppringare=d.get("uppringare"), profil=d.get("namn") or ""))

    @app.post("/api/agent/profil/lista")
    @kraver_agent
    def agent_profil_lista():
        return _agentsvar(lambda d: bord.lista_profiler("agent", uppringare=d.get("uppringare")))

    # Ingen agentväg för att ta bort profiler — det görs bara på adminsidan.

    # --- admin ---

    def _tillbaka(svar=None, text=None):
        if svar is not None:
            flash(svar.sag, "ok" if svar.ok else "fel")
        elif text:
            flash(text, "ok")
        return redirect(url_for("admin"))

    @app.get("/admin")
    @kraver_admin
    def admin():
        bord.stada()
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        kal = bord.kalibrering()
        enhet = bord.enhet()
        senast = enhet.get("senast_sedd")
        pagaende = bord.pagaende()
        tt = bord.senaste_tidtagning()
        return render_template(
            "admin.html",
            csrf=session["csrf"],
            position=bord.position(),
            kal=kal,
            hastighet={r: kal.hastighet(r) for r in ("upp", "ner")},
            enhet=enhet,
            sedan_s=None if senast is None else max(0, bord.klocka() - senast),
            pagaende=pagaende,
            status_text=STATUS_TEXT,
            tidtagning=dict(tt) if tt else None,
            handelser=bord.handelser(20),
            dygn=bord.dygn(),
            max_cm=logik.MAX_BEGARD_CM,
            profiler=bord.profiler(),
            max_profiler=logik.MAX_PROFILER,
            max_profilnamn=logik.MAX_PROFILNAMN,
        )

    @app.post("/admin/flytta")
    @kraver_admin
    def admin_flytta():
        return _tillbaka(bord.begar_rorelse("admin", riktning=request.form.get("riktning"),
                                            cm=request.form.get("cm")))

    @app.post("/admin/hojd")
    @kraver_admin
    def admin_hojd():
        return _tillbaka(bord.begar_rorelse("admin", hojd=request.form.get("cm")))

    @app.post("/admin/lage")
    @kraver_admin
    def admin_lage():
        return _tillbaka(bord.begar_rorelse("admin", lage=request.form.get("lage") or ""))

    @app.post("/admin/profil/spara")
    @kraver_admin
    def admin_profil_spara():
        return _tillbaka(bord.spara_profil("admin", request.form.get("namn")))

    @app.post("/admin/profil/till")
    @kraver_admin
    def admin_profil_till():
        return _tillbaka(bord.begar_rorelse("admin", profil=request.form.get("nyckel") or ""))

    @app.post("/admin/profil/ta-bort")
    @kraver_admin
    def admin_profil_ta_bort():
        return _tillbaka(bord.ta_bort_profil(request.form.get("nyckel") or ""))

    @app.post("/admin/kalibrering")
    @kraver_admin
    def admin_kalibrering():
        try:
            v = {k: _tal(request.form.get(k)) for k in ("min_cm", "max_cm", "restid_upp_s", "restid_ner_s")}
        except ValueError:
            return _tillbaka(Svar(False, "Kalibreringen sparades inte: skriv siffror, t.ex. 72,5.", None))
        fel = []
        for k in ("min_cm", "max_cm"):
            if v[k] is not None and not (20 <= v[k] <= 200):
                fel.append("höjderna ska ligga mellan 20 och 200 cm")
        for k in ("restid_upp_s", "restid_ner_s"):
            if v[k] is not None and not (0 < v[k] <= 120):
                fel.append("restiderna ska vara mellan 0 och 120 sekunder")
        if v["min_cm"] is not None and v["max_cm"] is not None and v["max_cm"] <= v["min_cm"]:
            fel.append("högsta höjd ska vara större än lägsta")
        if fel:
            return _tillbaka(Svar(False, "Kalibreringen sparades inte: " + "; ".join(sorted(set(fel))) + ".", None))
        bord.spara_kalibrering(logik.Kalibrering(**v))
        return _tillbaka(text="Kalibreringen är sparad.")

    @app.post("/admin/botten")
    @kraver_admin
    def admin_botten():
        return _tillbaka(bord.till_botten())

    @app.post("/admin/tidtagning/start")
    @kraver_admin
    def admin_tidtagning_start():
        return _tillbaka(bord.tidtagning_start(request.form.get("riktning")))

    @app.post("/admin/tidtagning/stopp")
    @kraver_admin
    def admin_tidtagning_stopp():
        return _tillbaka(bord.tidtagning_stopp())

    @app.post("/admin/tidtagning/spara")
    @kraver_admin
    def admin_tidtagning_spara():
        return _tillbaka(bord.spara_matning(_heltal(request.form.get("id"))))

    return app
