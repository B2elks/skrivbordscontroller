#!/usr/bin/env python3
"""Körbara krav för telefonbryggan och agentkonfigurationen (steg 5).

Samma mönster som checks/verify.py: CHECKS-lista, fail() samlar alla fel,
exit 1 om något faller. Beroenden: stdlib + brygga/requirements.txt.
Inget nätverk utanför 127.0.0.1; ElevenLabs fejkas med en lokal websocket-server.

    brygga/.venv/bin/python brygga/verify_brygga.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import random
import re
import subprocess
import sys
from array import array
from pathlib import Path

HAR = Path(__file__).resolve().parent
sys.path.insert(0, str(HAR))

import brygga as B  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402
from websockets.exceptions import ConnectionClosed, InvalidStatus  # noqa: E402

FEL: list[str] = []
CHECKS = []
# Fiktiva nummer ur PTS serie för fiktiv användning (070-174 06 05 – 070-174 06 99).
A = "+46701740605"
B_NR = "+46701740606"
OKANT = "+46701740607"           # svenskt, men inte på testvitlistan
EGET = "+46701740698"            # spelar "numret man ringer" i kedjan
WS_NR = "+4600000000"            # spelar websocket-numret (påhittat)
VITLISTA = frozenset({A, B_NR})
VANTA = 3.0
EXEMPEL_URL = "https://bord.example.com"
# Bryggan läser SKRIVBORD_EGNA_NUMMER vid import; testerna sätter listan direkt.
B.EGNA_NUMMER = frozenset({EGET, WS_NR})


def fail(meddelande: str) -> None:
    FEL.append(meddelande)


def check(f):
    CHECKS.append(f)
    return f


# --- hjälpare ----------------------------------------------------------------

def sinus(hz: float, fs: int, n: int, amp: float = 12000.0) -> array:
    return array("h", (int(round(amp * math.sin(2 * math.pi * hz * i / fs))) for i in range(n)))


def i_bitar(sampel: array, storlekar) -> list[bytes]:
    ut, i, k = [], 0, 0
    while i < len(sampel):
        s = storlekar[k % len(storlekar)]
        ut.append(sampel[i:i + s].tobytes())
        i += s
        k += 1
    return ut


def strom(oms: B.Omsamplare, bitar: list[bytes]) -> array:
    ut = array("h")
    for b in bitar:
        ut.frombytes(oms(b))
    return ut


def korrelation(a, b) -> float:
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    sab = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    saa = sum((x - ma) ** 2 for x in a)
    sbb = sum((y - mb) ** 2 for y in b)
    return sab / math.sqrt(saa * sbb) if saa and sbb else 0.0


def pcm_b64(sampel: array) -> str:
    return base64.b64encode(sampel.tobytes()).decode()


def antal_sampel(b64: str) -> int:
    return len(base64.b64decode(b64)) // 2


# --- nummer och vitlista -----------------------------------------------------

@check
def nummer_normaliseras():
    fall = {
        "+46701740605": A,
        "0701740605": A,
        "070-174 06 05": A,
        "0046701740606": B_NR,
        " +46 70 174 06 06 ": B_NR,
        "": None, None: None, "anonymous": None, "+46": None, 12345: None, "sip:x@y": None,
    }
    for inn, vantat in fall.items():
        if B.normalisera_nummer(inn) != vantat:
            fail(f"normalisera_nummer({inn!r}) = {B.normalisera_nummer(inn)!r}, väntade {vantat!r}")


@check
def egna_nummer_ur_miljon():
    """Inga nummer i koden: de egna numren kommer ur SKRIVBORD_EGNA_NUMMER, tom som standard."""
    kod = (HAR / "brygga.py").read_text()
    if re.search(r"\+46\d{7,}", kod):
        fail("brygga.py innehåller ett telefonnummer — de ska komma ur miljön")
    if B.las_nummerlista(None) != frozenset() or B.las_nummerlista("") != frozenset():
        fail("utan SKRIVBORD_EGNA_NUMMER ska listan vara tom")
    if B.las_nummerlista(f" {EGET} , 070-174 06 99,skräp") != frozenset({EGET, "+46701740699"}):
        fail(f"las_nummerlista tolkar inte en kommaseparerad lista: {B.las_nummerlista(EGET)}")
    p = subprocess.run([sys.executable, "-c", "import brygga; print(sorted(brygga.EGNA_NUMMER))"],
                       capture_output=True, text=True, timeout=30, cwd=HAR,
                       env={"PATH": "/usr/bin:/bin", "SKRIVBORD_EGNA_NUMMER": f"{EGET},{WS_NR}"})
    if p.returncode != 0 or p.stdout.strip() != str(sorted({EGET, WS_NR})):
        fail(f"SKRIVBORD_EGNA_NUMMER läses inte vid import: {p.stdout.strip()} {p.stderr[-200:]}")


@check
def svenska_nummer_slapps_in_utan_vitlista():
    """Standard sedan 2026-10-14: ingen lista, alla svenska nummer utom våra egna."""
    import os
    gammal = os.environ.pop("SKRIVBORD_VITLISTA", None)
    try:
        if B.las_vitlista() is not None:
            fail("utan SKRIVBORD_VITLISTA ska las_vitlista() ge None (alla svenska)")
    finally:
        if gammal is not None:
            os.environ["SKRIVBORD_VITLISTA"] = gammal
    fall = [
        ({"t": "hello", "from": OKANT}, OKANT),
        ({"t": "hello", "from": "0701740605"}, A),
        ({"t": "hello", "from": "+46846500400"}, "+46846500400"),   # fast nummer
        ({"t": "hello", "from": EGET}, None),               # eget nummer
        ({"t": "hello", "from": WS_NR}, None),
        ({"t": "hello", "from": "+4721234567"}, None),      # utländskt
        ({"t": "hello", "from": "+46"}, None),
        ({"t": "hello", "from": "anonymous"}, None),
        ({"t": "hello"}, None),
    ]
    for hello, vantat in fall:
        fick, _ = B.plocka_uppringare(hello, None)
        if fick != vantat:
            fail(f"plocka_uppringare({hello}, None) = {fick!r}, väntade {vantat!r}")


@check
def uppringare_ur_hello():
    fall = [
        ({"t": "hello", "from": A}, A),
        ({"t": "hello", "from": "0701740606"}, B_NR),
        ({"t": "hello", "from": EGET}, None),              # numret som kopplar vidare, ej vitlistat
        ({"t": "hello", "from": OKANT}, None),
        ({"t": "hello"}, None),
        ({"t": "hello", "from": ""}, None),
        ({"t": "hello", "from": None}, None),
    ]
    for hello, vantat in fall:
        fick, _ = B.plocka_uppringare(hello, VITLISTA)
        if fick != vantat:
            fail(f"plocka_uppringare({hello}) = {fick!r}, väntade {vantat!r}")


# --- omsampling --------------------------------------------------------------

@check
def omsampling_tur_och_retur_svep():
    """24k→16k→24k av en sinus i 46elks varierande frames: rätt antal sampel ±1
    i varje steg och korrelation > 0,95 mot originalet, över hela talbandet."""
    rng = random.Random(46)
    n = 24000  # en sekund
    for hz in range(100, 3501, 100):
        orig = sinus(hz, 24000, n)
        storlekar = [rng.choice((480, 512)) for _ in range(40)]
        ned = strom(B.Omsamplare(24000, 16000), i_bitar(orig, storlekar))
        if abs(len(ned) - n * 16000 / 24000) > 1:
            fail(f"24k→16k {hz} Hz: {len(ned)} sampel, väntade {n * 2 // 3} ±1")
        upp = strom(B.Omsamplare(16000, 24000), i_bitar(ned, [320, 341, 342, 160]))
        if abs(len(upp) - len(ned) * 1.5) > 1 or abs(len(upp) - n) > 1:
            fail(f"16k→24k {hz} Hz: {len(upp)} sampel, väntade {n} ±1")
        r = korrelation(orig, upp)
        if r <= 0.95:
            fail(f"tur och retur {hz} Hz: korrelation {r:.4f} ≤ 0,95")
        # Rätt ton efter nedsampling: jämför med en sinus genererad direkt i 16 kHz.
        r16 = korrelation(sinus(hz, 16000, len(ned)), ned)
        if r16 <= 0.95:
            fail(f"24k→16k {hz} Hz: korrelation mot ren 16 kHz-sinus {r16:.4f} ≤ 0,95")


@check
def omsampling_oberoende_av_framestorlek():
    orig = sinus(440, 24000, 24000)
    hel = B.Omsamplare(24000, 16000)(orig.tobytes())
    for storlekar in ([480], [512], [480, 512, 512], [1], [7, 1000]):
        bitvis = strom(B.Omsamplare(24000, 16000), i_bitar(orig, storlekar)).tobytes()
        if bitvis != hel:
            fail(f"omsampling i bitar {storlekar} skiljer sig från i ett stycke")


@check
def omsampling_kantfall():
    o = B.Omsamplare(24000, 16000)
    if o(b"") != b"":
        fail("tom bit ska ge tom utdata")
    if B.Omsamplare(24000, 24000)(b"\x01\x02") != b"\x01\x02":
        fail("samma hastighet ska släppa igenom oförändrat")
    full = array("h", [32767, -32768] * 300).tobytes()
    try:
        B.Omsamplare(16000, 24000)(full)
    except OverflowError:
        fail("fullskaligt ljud ger overflow i omsamplingen")
    for f in ("pcm_16000", "pcm_24000"):
        B.hastighet(f)
    try:
        B.hastighet("ulaw_8000")
        fail("hastighet('ulaw_8000') borde nekas")
    except ValueError:
        pass


# --- handskakning och samtalsflöde (riktiga websockets, fejkad ElevenLabs) ---

class FejkEleven:
    def __init__(self) -> None:
        self.anslutningar: list = []
        self.mottaget: asyncio.Queue = asyncio.Queue()
        self.stangd = asyncio.Event()

    async def __call__(self, conn) -> None:
        self.anslutningar.append(conn)
        try:
            async for m in conn:
                await self.mottaget.put(json.loads(m))
        except ConnectionClosed:
            pass
        finally:
            self.stangd.set()

    async def nasta(self) -> dict:
        async with asyncio.timeout(VANTA):
            return await self.mottaget.get()


async def med_bryggor(prov):
    eleven = FejkEleven()
    url_hamtningar = []

    async with serve(eleven, "127.0.0.1", 0) as el_srv:
        el_port = el_srv.sockets[0].getsockname()[1]

        def hamta_url() -> str:
            url_hamtningar.append(1)
            return f"ws://127.0.0.1:{el_port}/"

        brygga = B.Brygga(hamta_url=hamta_url, vitlista=VITLISTA, elks_format="pcm_24000")
        async with serve(brygga, "127.0.0.1", 0, process_request=B.Brygga.kontrollera_sokvag) as srv:
            port = srv.sockets[0].getsockname()[1]
            await prov(f"ws://127.0.0.1:{port}", eleven, url_hamtningar)


async def recv_json(ws) -> dict:
    async with asyncio.timeout(VANTA):
        return json.loads(await ws.recv())


async def vantar_stangning(ws) -> bool:
    try:
        async with asyncio.timeout(VANTA):
            while True:
                await ws.recv()
    except ConnectionClosed:
        return True
    except TimeoutError:
        return False


async def nekas_tyst(bas: str, eleven: FejkEleven, hamtningar: list, hello, namn: str) -> None:
    async with connect(bas + "/elks") as ws:
        await ws.send(json.dumps(hello))
        svar = await recv_json(ws)
        if svar != {"t": "bye"}:
            fail(f"{namn}: första svar {svar}, väntade bara {{'t': 'bye'}} (inget ljud, inget sending)")
        await ws.send(json.dumps({"t": "bye", "reason": "done", "message": "Call finished"}))
        if not await vantar_stangning(ws):
            fail(f"{namn}: bryggan stängde inte efter 46elks bye")
    if eleven.anslutningar or hamtningar:
        fail(f"{namn}: ElevenLabs öppnades ({len(eleven.anslutningar)} anslutningar, "
             f"{len(hamtningar)} URL-hämtningar) — ska nekas innan")


@check
def okant_nummer_nekas_utan_elevenlabs():
    async def prov(bas, eleven, h):
        await nekas_tyst(bas, eleven, h, {"t": "hello", "callid": "c1", "from": OKANT,
                                          "to": WS_NR}, "okänt nummer")
    asyncio.run(med_bryggor(prov))


@check
def vidarekopplande_nummer_nekas():
    async def prov(bas, eleven, h):
        await nekas_tyst(bas, eleven, h, {"t": "hello", "callid": "c2", "from": EGET,
                                          "to": WS_NR}, "from = eget nummer")
    asyncio.run(med_bryggor(prov))


@check
def hello_utan_fran_nekas():
    async def prov(bas, eleven, h):
        await nekas_tyst(bas, eleven, h, {"t": "hello", "callid": "c3", "to": WS_NR},
                         "hello utan from")
    asyncio.run(med_bryggor(prov))


@check
def forsta_meddelandet_maste_vara_hello():
    async def prov(bas, eleven, h):
        async with connect(bas + "/elks") as ws:
            await ws.send(json.dumps({"t": "audio", "data": pcm_b64(sinus(440, 24000, 480))}))
            svar = await recv_json(ws)
            if svar != {"t": "bye"}:
                fail(f"utan hello: svar {svar}, väntade bye")
            await ws.send(json.dumps({"t": "bye"}))
            if not await vantar_stangning(ws):
                fail("utan hello: stängdes inte")
        if eleven.anslutningar:
            fail("utan hello: ElevenLabs öppnades")
    asyncio.run(med_bryggor(prov))


@check
def fel_sokvag_ger_404():
    async def prov(bas, eleven, h):
        try:
            async with connect(bas + "/annat"):
                fail("websocket på /annat accepterades")
        except InvalidStatus as e:
            if e.response.status_code != 404:
                fail(f"/annat gav {e.response.status_code}, väntade 404")
    asyncio.run(med_bryggor(prov))


@check
def vitlistat_samtal_hela_flodet():
    async def prov(bas, eleven, h):
        async with connect(bas + "/elks") as ws:
            await ws.send(json.dumps({"t": "hello", "callid": "c4", "from": "070-174 06 05",
                                      "to": WS_NR}))
            init = await eleven.nasta()
            if init != {"type": "conversation_initiation_client_data",
                        "dynamic_variables": {"uppringare": A}}:
                fail(f"init till ElevenLabs: {init}")
            m1, m2 = await recv_json(ws), await recv_json(ws)
            if (m1, m2) != ({"t": "sending", "format": "pcm_24000"},
                            {"t": "listening", "format": "pcm_24000"}):
                fail(f"46elks fick {m1}, {m2}, väntade sending + listening pcm_24000")

            # uppringarens ljud: 512 + 480 sampel i 24 kHz ⇒ 661 ±1 sampel i 16 kHz
            ut = 0
            for s in (512, 480):
                await ws.send(json.dumps({"t": "audio", "data": pcm_b64(sinus(300, 24000, s))}))
            await ws.send(json.dumps({"t": "sync"}))
            while ut < 660:
                m = await eleven.nasta()
                if "user_audio_chunk" not in m:
                    fail(f"väntade user_audio_chunk, fick {m}")
                    break
                ut += antal_sampel(m["user_audio_chunk"])
            if abs(ut - (512 + 480) * 2 / 3) > 1:
                fail(f"ElevenLabs fick {ut} sampel, väntade 661 ±1")

            el = eleven.anslutningar[0]
            await el.send(json.dumps({"type": "conversation_initiation_metadata",
                                      "conversation_initiation_metadata_event": {
                                          "conversation_id": "conv_x",
                                          "agent_output_audio_format": "pcm_16000",
                                          "user_input_audio_format": "pcm_16000"}}))
            await el.send(json.dumps({"type": "audio", "audio_event": {
                "event_id": 1, "audio_base_64": pcm_b64(sinus(300, 16000, 320))}}))
            m = await recv_json(ws)
            if m.get("t") != "audio" or abs(antal_sampel(m["data"]) - 480) > 1:
                fail(f"46elks fick {m.get('t')} med {antal_sampel(m.get('data', ''))} sampel, väntade audio 480 ±1")

            await el.send(json.dumps({"type": "ping", "ping_event": {"event_id": 7, "ping_ms": 50}}))
            pong = await eleven.nasta()
            if pong != {"type": "pong", "event_id": 7}:
                fail(f"pong: {pong}")

            await el.send(json.dumps({"type": "interruption", "interruption_event": {"event_id": 5}}))
            i1, i2 = await recv_json(ws), await recv_json(ws)
            if (i1, i2) != ({"t": "interrupt"}, {"t": "sending", "format": "pcm_24000"}):
                fail(f"avbrott gav {i1}, {i2}, väntade interrupt + sending")
            # ljud från det avbrutna svaret (event_id ≤ 5) ska kastas, nästa svar (6) gå fram
            await el.send(json.dumps({"type": "audio", "audio_event": {
                "event_id": 4, "audio_base_64": pcm_b64(sinus(300, 16000, 320))}}))
            await el.send(json.dumps({"type": "audio", "audio_event": {
                "event_id": 6, "audio_base_64": pcm_b64(sinus(300, 16000, 160))}}))
            m = await recv_json(ws)
            if m.get("t") != "audio" or abs(antal_sampel(m["data"]) - 240) > 1:
                fail(f"efter avbrott: fick {antal_sampel(m.get('data', ''))} sampel, "
                     "väntade bara nya svarets 240 ±1")

            # agenten lägger på (end_call ⇒ ElevenLabs stänger) ⇒ bryggan skickar bye
            await el.close()
            m = await recv_json(ws)
            if m != {"t": "bye"}:
                fail(f"efter att ElevenLabs stängt: {m}, väntade bye")
            await ws.send(json.dumps({"t": "bye", "reason": "done"}))
            if not await vantar_stangning(ws):
                fail("bryggan stängde inte efter 46elks bekräftande bye")
    asyncio.run(med_bryggor(prov))


@check
def uppringaren_lagger_pa():
    async def prov(bas, eleven, h):
        async with connect(bas + "/elks") as ws:
            await ws.send(json.dumps({"t": "hello", "callid": "c5", "from": B_NR}))
            init = await eleven.nasta()
            if init.get("dynamic_variables") != {"uppringare": B_NR}:
                fail(f"init: {init}")
            await recv_json(ws), await recv_json(ws)
            await ws.send(json.dumps({"t": "bye", "reason": "hangup", "message": "the caller hung up"}))
            try:
                async with asyncio.timeout(VANTA):
                    await eleven.stangd.wait()
            except TimeoutError:
                fail("ElevenLabs stängdes inte när uppringaren lade på")
            if not await vantar_stangning(ws):
                fail("46elks-förbindelsen stängdes inte efter bye")
    asyncio.run(med_bryggor(prov))


@check
def elevenlabs_nere_ger_bye():
    async def prov(bas, eleven, h):
        brygga = B.Brygga(hamta_url=lambda: "ws://127.0.0.1:1/", vitlista=VITLISTA)
        async with serve(brygga, "127.0.0.1", 0, process_request=B.Brygga.kontrollera_sokvag) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/elks") as ws:
                await ws.send(json.dumps({"t": "hello", "from": A}))
                m = await recv_json(ws)
                if m != {"t": "bye"}:
                    fail(f"ElevenLabs onåbar: {m}, väntade bye")
    asyncio.run(med_bryggor(prov))


# --- statiska krav på koden ----------------------------------------------------

@check
def ingen_audioop_och_inga_hemligheter_i_loggen():
    kod = (HAR / "brygga.py").read_text()
    if re.search(r"^\s*(import|from)\s+audioop", kod, re.M):
        fail("brygga.py importerar audioop (finns inte i Python 3.13+)")
    for nr, rad in enumerate(kod.splitlines(), 1):
        if re.search(r"\blog\.\w+\(", rad) and re.search(r"\b(url|nyckel|signed_url|API_KEY)\b", rad):
            fail(f"brygga.py:{nr} loggar något som kan innehålla nyckel/signerad URL: {rad.strip()}")
    req = (HAR / "requirements.txt").read_text()
    for rad in req.splitlines():
        rad = rad.strip()
        if rad and not rad.startswith("#") and "==" not in rad:
            fail(f"requirements.txt: opinnad rad {rad!r}")


# --- agentkonfigurationen ---------------------------------------------------------

@check
def agent_torrkorning():
    # Utan SKRIVBORD_OFFENTLIG_URL ska agent.py vägra — inget standardvärde får peka på en riktig sajt.
    for env in ({}, {"SKRIVBORD_OFFENTLIG_URL": "http://bord.example.com"},
                {"SKRIVBORD_OFFENTLIG_URL": "https://bord.example.com/api"}):
        q = subprocess.run([sys.executable, str(HAR / "agent.py"), "--torrkorning"],
                           capture_output=True, text=True, timeout=30, env={"PATH": "/usr/bin:/bin", **env})
        if q.returncode == 0 or "SKRIVBORD_OFFENTLIG_URL" not in q.stderr:
            fail(f"agent.py med {env or 'ingen URL'} ska vägra och nämna SKRIVBORD_OFFENTLIG_URL, "
                 f"gav {q.returncode}: {q.stderr[-200:]}")
    p = subprocess.run([sys.executable, str(HAR / "agent.py"), "--torrkorning"],
                       capture_output=True, text=True, timeout=30,
                       env={"PATH": "/usr/bin:/bin", "SKRIVBORD_OFFENTLIG_URL": EXEMPEL_URL + "/"})
    if p.returncode != 0:
        fail(f"agent.py --torrkorning avslutade med {p.returncode}: {p.stderr[-300:]}")
        return
    try:
        d = json.loads(p.stdout)
    except ValueError as e:
        fail(f"agent.py --torrkorning gav ogiltig JSON: {e}")
        return

    verktyg = {v["tool_config"]["name"]: v["tool_config"] for v in d.get("verktyg", [])}
    alla = {"flytta_bord", "till_lage", "till_hojd", "spara_profil", "till_profil", "lista_profiler"}
    if set(verktyg) != alla:
        fail(f"verktyg: {sorted(verktyg)}, väntade {sorted(alla)}")
        return
    vantad = {"flytta_bord": ("/api/agent/flytta", {"riktning": ["upp", "ner"]}, {"uppringare", "riktning", "cm"}),
              "till_lage": ("/api/agent/lage", {"lage": ["stå", "sitt"]}, {"uppringare", "lage"}),
              "till_hojd": ("/api/agent/hojd", {}, {"uppringare", "cm"}),
              "spara_profil": ("/api/agent/profil/spara", {}, {"uppringare", "namn"}),
              "till_profil": ("/api/agent/profil/till", {}, {"uppringare", "namn"}),
              "lista_profiler": ("/api/agent/profil/lista", {}, {"uppringare"})}
    # Verktyg som rör bordet säger alltid något innan anropet; spara/lista svarar direkt.
    rorelse = {"flytta_bord", "till_lage", "till_hojd", "till_profil"}
    for namn, (sokvag, enums, falt) in vantad.items():
        v = verktyg[namn]
        a = v["api_schema"]
        if v.get("type") != "webhook":
            fail(f"{namn}: type {v.get('type')!r}, väntade webhook")
        if a.get("url") != EXEMPEL_URL + sokvag:
            fail(f"{namn}: url {a.get('url')!r} pekar inte på {EXEMPEL_URL}{sokvag}")
        if a.get("method") != "POST":
            fail(f"{namn}: method {a.get('method')!r}")
        h = a.get("request_headers", {}).get("X-Agent-Hemlighet")
        if not (isinstance(h, dict) and "secret_id" in h):
            fail(f"{namn}: X-Agent-Hemlighet ska vara en hemlighetsreferens {{secret_id}}, är {h!r}")
        props = a["request_body_schema"]["properties"]
        if set(props) != falt or set(a["request_body_schema"].get("required", [])) != falt:
            fail(f"{namn}: fält {sorted(props)}, väntade {sorted(falt)}")
        if props.get("uppringare", {}).get("dynamic_variable") != "uppringare" or \
                props.get("uppringare", {}).get("description"):
            fail(f"{namn}: uppringare ska komma ur dynamisk variabel, inte från LLM:en")
        if not (isinstance(v.get("response_timeout_secs"), int) and 45 <= v["response_timeout_secs"] <= 300):
            fail(f"{namn}: response_timeout_secs {v.get('response_timeout_secs')!r} ska vara 45–300 "
                 "(servern svarar först när bordet stannat)")
        if namn in rorelse and v.get("pre_tool_speech") != "force":
            fail(f"{namn}: pre_tool_speech {v.get('pre_tool_speech')!r}, väntade 'force'")
        if "force_pre_tool_speech" in v:
            fail(f"{namn}: force_pre_tool_speech är föråldrat — använd pre_tool_speech")
        for f, e in enums.items():
            if props.get(f, {}).get("enum") != e:
                fail(f"{namn}.{f}: enum {props.get(f, {}).get('enum')}, väntade {e}")
    if verktyg["flytta_bord"]["api_schema"]["request_body_schema"]["properties"]["cm"].get("type") != "number":
        fail("flytta_bord.cm ska vara number")
    hcm = verktyg["till_hojd"]["api_schema"]["request_body_schema"]["properties"]["cm"]
    if hcm.get("type") != "number" or "absolut höjd i centimeter från golvet" not in hcm.get("description", ""):
        fail(f"till_hojd.cm ska vara number med beskrivningen 'absolut höjd i centimeter från golvet', är {hcm}")
    for namn in ("spara_profil", "till_profil"):
        pn = verktyg[namn]["api_schema"]["request_body_schema"]["properties"]["namn"]
        if pn.get("type") != "string" or not pn.get("description") or "dynamic_variable" in pn:
            fail(f"{namn}.namn ska vara en string som LLM:en fyller i, med beskrivning, är {pn}")

    ag = d.get("agent", {})
    cc = ag.get("conversation_config", {})
    if ag.get("name") != "Skrivbordet":
        fail(f"agentnamn {ag.get('name')!r}")
    if cc.get("agent", {}).get("language") != "sv":
        fail("agenten är inte på svenska (language != sv)")
    if cc.get("tts", {}).get("voice_id") != "kPdGSxhZAqy4bmPAf9iJ":
        fail(f"röst {cc.get('tts', {}).get('voice_id')!r}, väntade Evelina kPdGSxhZAqy4bmPAf9iJ")
    if cc.get("agent", {}).get("first_message") != "Ja?":
        fail(f"first_message {cc.get('agent', {}).get('first_message')!r}")
    pr = cc.get("agent", {}).get("prompt", {})
    ec = pr.get("built_in_tools", {}).get("end_call") or {}
    if ec.get("params", {}).get("system_tool_type") != "end_call":
        fail("end_call saknas bland built_in_tools")
    if len(pr.get("tool_ids", [])) != 6:
        fail(f"tool_ids ska ha sex verktyg, är {pr.get('tool_ids')}")
    if "uppringare" not in cc.get("agent", {}).get("dynamic_variables", {}).get("dynamic_variable_placeholders", {}):
        fail("platshållare för dynamisk variabel uppringare saknas")
    if cc.get("conversation", {}).get("max_duration_seconds") != 120:
        fail(f"max_duration_seconds {cc.get('conversation', {}).get('max_duration_seconds')!r}, väntade 120 (två minuter)")
    if not ag.get("platform_settings", {}).get("auth", {}).get("enable_auth"):
        fail("agenten ska kräva signerad URL (enable_auth)")
    if cc.get("tts", {}).get("agent_output_audio_format") != "pcm_16000" or \
            cc.get("asr", {}).get("user_input_audio_format") != "pcm_16000":
        fail("agentens ljudformat ska vara pcm_16000 åt båda hållen (det bryggan omsamplar från/till)")
    text = json.dumps(d, ensure_ascii=False)
    for ord_ in ("end_call", "sag", "ordagrant", "till_hojd", "flytta_bord", "till_lage",
                 "Jo.", "Bara bordet.", "Hej.", "högst tre ord", "Fråga inte", "två minuter", "spara_profil", "till_profil", "lista_profiler",
                 "kom ihåg det här som", "vilka profiler finns"):
        if ord_ not in pr.get("prompt", ""):
            fail(f"prompten nämner inte {ord_!r}")
    for ord_ in ("Okej", "Jag styr bara bordet"):
        if ord_ in pr.get("prompt", ""):
            fail(f"prompten ska inte innehålla {ord_!r} — norrländskt kort")
    # Talhastighet: conversation_config.tts.speed, 0,7–1,2 enligt OpenAPI (hämtad 2026-10-14).
    if cc.get("tts", {}).get("speed") != 1.1:
        fail(f"tts.speed {cc.get('tts', {}).get('speed')!r}, väntade 1.1")
    if "<AGENT_HEMLIGHET" not in text:
        fail("torrkörningen ska visa platshållare för hemligheten, inte värdet")


@check
def agent_main_alla_verktyg():
    """Kör agent.main() i processen med fejkat ElevenLabs-API: alla sex verktyg ska
    skapas och agentens tool_ids ska innehålla exakt deras id:n. Rör inte agent_id.txt."""
    import importlib
    import os
    import tempfile
    sys.modules.pop("agent", None)
    agent = importlib.import_module("agent")
    anrop_logg = []
    skapade = []

    def fejk(metod, sokvag, kropp=None):
        anrop_logg.append((metod, sokvag, kropp))
        if metod == "GET" and sokvag.startswith("/v1/convai/secrets"):
            return {"secrets": []}
        if metod == "POST" and sokvag == "/v1/convai/secrets":
            return {"secret_id": "hemlig-id"}
        if metod == "GET" and sokvag.startswith("/v1/convai/tools"):
            return {"tools": []}
        if metod == "POST" and sokvag == "/v1/convai/tools":
            skapade.append(kropp["tool_config"]["name"])
            return {"id": f"verktyg-{len(skapade)}"}
        if metod == "PATCH" and sokvag.startswith("/v1/convai/agents/"):
            return {}
        raise AssertionError(f"oväntat anrop {metod} {sokvag}")

    gamla_env = {k: os.environ.get(k) for k in ("ELEVENLABS_API_KEY", "AGENT_HEMLIGHET", "SKRIVBORD_AGENT_ID",
                                                 "SKRIVBORD_OFFENTLIG_URL")}
    gammal_argv = sys.argv
    with tempfile.TemporaryDirectory() as tmp:
        agent.anrop = fejk
        agent.ID_FIL = Path(tmp) / "agent_id.txt"
        os.environ.update(ELEVENLABS_API_KEY="fejk", AGENT_HEMLIGHET="fejk", SKRIVBORD_AGENT_ID="test-agent",
                          SKRIVBORD_OFFENTLIG_URL=EXEMPEL_URL)
        sys.argv = ["agent.py"]
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                agent.main()
        finally:
            sys.argv = gammal_argv
            for k, v in gamla_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    vantade = sorted(["flytta_bord", "till_hojd", "till_lage", "spara_profil", "till_profil", "lista_profiler"])
    if sorted(skapade) != vantade:
        fail(f"main() skapade verktygen {skapade}, väntade {vantade}")
    patch = [k for m, s_, k in anrop_logg if m == "PATCH" and s_ == "/v1/convai/agents/test-agent"]
    if len(patch) != 1:
        fail(f"main() ska uppdatera agenten exakt en gång, gjorde {len(patch)}")
        return
    ids = patch[0]["conversation_config"]["agent"]["prompt"]["tool_ids"]
    if sorted(ids) != sorted(f"verktyg-{i}" for i in range(1, 7)):
        fail(f"agentens tool_ids ska vara alla sex verktygen, är {ids}")


@check
def systemd_enheten():
    p = HAR.parent / "deploy" / "skrivbord-brygga.service.mall"
    if not p.exists():
        fail("deploy/skrivbord-brygga.service.mall saknas")
        return
    t = p.read_text()
    for krav in ("User=@ANVANDARE@",
                 "WorkingDirectory=@MAL@/brygga",
                 "EnvironmentFile=/etc/skrivbord/secrets.env",
                 "@MAL@/venv-brygga/bin/python",
                 "BRYGGA_PORT=@BRYGGA_PORT@"):
        if krav not in t:
            fail(f"skrivbord-brygga.service.mall saknar {krav!r}")


@check
def nginx_mallen_har_websocket():
    """/elks måste uppgraderas till websocket, annars lägger 46elks på direkt."""
    p = HAR.parent / "deploy" / "nginx-skrivbord.conf.mall"
    if not p.exists():
        fail("deploy/nginx-skrivbord.conf.mall saknas")
        return
    t = p.read_text()
    m = re.search(r"location\s*=?\s*/elks\s*\{(.*?)\}", t, re.S)
    if not m:
        fail("nginx-mallen saknar location /elks")
        return
    for krav in ("proxy_pass http://127.0.0.1:@BRYGGA_PORT@", "proxy_http_version 1.1",
                 "Upgrade $http_upgrade", 'Connection "upgrade"'):
        if krav not in m.group(1):
            fail(f"nginx-mallens /elks saknar {krav!r}")
    for krav in ("server_name @DOMAN@", "proxy_pass http://127.0.0.1:@PORT@"):
        if krav not in t:
            fail(f"nginx-mallen saknar {krav!r}")


def main() -> int:
    for c in CHECKS:
        fore = len(FEL)
        try:
            c()
        except Exception as e:  # en krasch i en kontroll är också ett fel
            fail(f"{c.__name__} kraschade: {type(e).__name__}: {e}")
        print(f"{'OK  ' if len(FEL) == fore else 'FEL '} {c.__name__}")
    if FEL:
        print(f"\n{len(FEL)} fel:")
        for f in FEL:
            print("  -", f)
        return 1
    print(f"\nAlla {len(CHECKS)} kontroller passerade.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
