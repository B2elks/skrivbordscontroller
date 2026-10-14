#!/usr/bin/env python3
"""Telefonbrygga: 46elks websocket-samtal <-> ElevenLabs ConvAI-agenten "Skrivbordet".

Lyssnar på 127.0.0.1:8551, sökväg /elks (nginx terminerar TLS för
wss://<din domän>/elks).

46elks-protokollet (uppmätt mot riktiga samtal och 46elks dokumentation för
realtime-voice; nyckeln heter "t", inte "type", och det finns inget start_stream):

    46elks -> oss:  {"t":"hello","callid":..,"from":..,"to":..}  först
    oss -> 46elks:  {"t":"sending","format":"pcm_24000"}           vårt utgående format
    oss -> 46elks:  {"t":"listening","format":"pcm_24000"}         format vi vill ta emot
    båda hållen:    {"t":"audio","data":"<base64>"}                512 eller 480 sampel per frame
    oss -> 46elks:  {"t":"interrupt"}                              töm uppspelningsbufferten;
                                                                    kräver ny "sending" efteråt
    46elks -> oss:  {"t":"sync"}                                   buffertkvitto, ignoreras
    båda hållen:    {"t":"bye"}                                    när vi skickar bye spelar 46elks
                                                                    klart bufferten och svarar med
                                                                    egen bye; först då stänger vi

46elks skickar inga knapptryck (DTMF) över websocketen, därför finns ingen
DTMF-reserv här (se README.md).

Uppringaren tas ur hello["from"]. Alla svenska nummer (+46) släpps in utom de
egna (SKRIVBORD_EGNA_NUMMER); är SKRIVBORD_VITLISTA satt gäller i stället exakt den listan. Saknas numret,
är det dolt eller utländskt säger bryggan ingenting: den skickar bye utan att ens öppna ElevenLabs.

ElevenLabs talar pcm_16000 åt båda hållen (agentens inställning, bekräftas i
conversation_initiation_metadata). 46elks-sidan kör pcm_24000 som är det format
som uppmätts fungera; omsamplingen är egen (audioop finns inte i Python 3.13+).

Env: ELEVENLABS_API_KEY, SKRIVBORD_AGENT_ID. Valfritt: SKRIVBORD_EGNA_NUMMER och
SKRIVBORD_VITLISTA (kommaseparerade), BRYGGA_VARD, BRYGGA_PORT, ELKS_FORMAT.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
from array import array
from typing import Awaitable, Callable

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger("brygga")

SOKVAG = "/elks"
ELKS_FORMAT = os.environ.get("ELKS_FORMAT", "pcm_24000")
ELEVEN_STANDARDFORMAT = "pcm_16000"
HELLO_TIDSGRANS = 10.0     # sekunder att vänta på hello innan vi ger upp
BYE_TIDSGRANS = 30.0       # 46elks spelar klart bufferten (agentens hejdå) före sin bye
ELEVEN_API = "https://api.elevenlabs.io"


# --- nummer och vitlista ---------------------------------------------------

def normalisera_nummer(nummer: object) -> str | None:
    """Gör ett svenskt/E.164-nummer till "+46…" eller returnerar None om det
    inte går att avgöra. Mellanslag, bindestreck och parenteser tas bort;
    "00" blir "+" och en inledande nolla blir "+46"."""
    if not isinstance(nummer, str):
        return None
    n = re.sub(r"[\s\-()]", "", nummer)
    if n.startswith("00"):
        n = "+" + n[2:]
    elif n.startswith("0"):
        n = "+46" + n[1:]
    if not re.fullmatch(r"\+[1-9]\d{6,14}", n):
        return None
    return n


def las_nummerlista(text: str | None) -> frozenset[str]:
    """Kommaseparerad lista → normaliserade nummer. Ogiltiga delar hoppas över."""
    if not text:
        return frozenset()
    return frozenset(n for n in (normalisera_nummer(k.strip()) for k in text.split(",")) if n)


# De egna numren i kedjan (numret man ringer och websocket-numret) får aldrig
# styra bordet. Platsspecifika, därför ur miljön; tom som standard.
EGNA_NUMMER = las_nummerlista(os.environ.get("SKRIVBORD_EGNA_NUMMER"))


def las_vitlista() -> frozenset[str] | None:
    """None = alla svenska nummer (standard); SKRIVBORD_VITLISTA ger en exakt lista."""
    ratt = os.environ.get("SKRIVBORD_VITLISTA")
    if not ratt:
        return None
    return las_nummerlista(ratt)


def ar_svenskt(nummer: str) -> bool:
    return bool(re.fullmatch(r"\+46[1-9]\d{6,8}", nummer)) and nummer not in EGNA_NUMMER


def plocka_uppringare(hello: dict, vitlista: frozenset[str] | None = None) -> tuple[str | None, str]:
    """Returnerar (uppringare, skäl). uppringare är None om samtalet ska nekas.
    vitlista None = alla svenska nummer utom de egna."""
    nummer = normalisera_nummer(hello.get("from"))
    if nummer is None:
        return None, "från-nummer saknas eller går inte att tolka"
    if vitlista is None:
        if not ar_svenskt(nummer):
            return None, f"{nummer} är inte ett tillåtet svenskt nummer"
        return nummer, "svenskt nummer"
    if nummer not in vitlista:
        return None, f"{nummer} står inte på vitlistan"
    return nummer, "vitlistad"


def hello_for_logg(hello: dict) -> dict:
    """Hela hello-meddelandet utom eventuellt ljud, för loggen."""
    return {k: v for k, v in hello.items() if k != "data"}


# --- omsampling --------------------------------------------------------------

def hastighet(format_: str) -> int:
    m = re.fullmatch(r"pcm_(\d+)", format_ or "")
    if not m:
        raise ValueError(f"bryggan hanterar bara pcm_N, inte {format_!r}")
    return int(m.group(1))


class Omsamplare:
    """Linjär omsampling av 16-bitars mono-PCM med tillstånd mellan frames, så att
    en ström i bitar ger samma resultat (och samma antal sampel ±1) som i ett
    stycke. 46elks frames varierar (512 eller 480 sampel), så inget får bero på
    framestorleken."""

    def __init__(self, fran: int, till: int) -> None:
        self.fran = fran
        self.till = till
        self.steg = fran / till   # insampel per utsampel
        self.nollstall()

    def nollstall(self) -> None:
        self._foreg: int | None = None   # sista samplet i förra biten
        self._t = 0.0                    # nästa utsampels läge, i bitens koordinater

    def __call__(self, pcm: bytes) -> bytes:
        if self.fran == self.till:
            return pcm
        nya = array("h")
        nya.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        if sys.byteorder == "big":
            nya.byteswap()
        if not nya:
            return b""
        if self._foreg is None:
            buf = nya
        else:
            buf = array("h", [self._foreg])
            buf.extend(nya)
        ut = array("h")
        sista = len(buf) - 1
        t, steg = self._t, self.steg
        while t <= sista:
            i = int(t)
            f = t - i
            if f == 0.0 or i == sista:
                v = buf[i]
            else:
                v = buf[i] + (buf[i + 1] - buf[i]) * f
            ut.append(max(-32768, min(32767, int(round(v)))))
            t += steg
        self._t = t - sista
        self._foreg = buf[-1]
        if sys.byteorder == "big":
            ut.byteswap()
        return ut.tobytes()


# --- ElevenLabs --------------------------------------------------------------

def hamta_signerad_url() -> str:
    """Signerad websocket-URL för agenten. URL:en innehåller en token och loggas aldrig."""
    nyckel = os.environ["ELEVENLABS_API_KEY"]
    agent = os.environ["SKRIVBORD_AGENT_ID"]
    url = f"{ELEVEN_API}/v1/convai/conversation/get-signed-url?" + urllib.parse.urlencode(
        {"agent_id": agent})
    req = urllib.request.Request(url, headers={"xi-api-key": nyckel})
    with urllib.request.urlopen(req, timeout=10) as svar:
        return json.loads(svar.read())["signed_url"]


# --- själva bryggan ----------------------------------------------------------

def _t(meddelande: dict) -> str:
    return json.dumps(meddelande, ensure_ascii=False)


class Brygga:
    def __init__(self, hamta_url: Callable[[], str] = hamta_signerad_url,
                 vitlista: frozenset[str] | None = None,
                 elks_format: str = ELKS_FORMAT) -> None:
        self.hamta_url = hamta_url
        self.vitlista = las_vitlista() if vitlista is None else vitlista   # None = alla svenska
        self.elks_format = elks_format
        self.elks_hz = hastighet(elks_format)

    @staticmethod
    def kontrollera_sokvag(_conn: ServerConnection, request: Request) -> Response | None:
        if request.path.split("?", 1)[0] != SOKVAG:
            return _conn.respond(404, "Finns inte\n")
        return None

    async def __call__(self, elks: ServerConnection) -> None:
        try:
            await self.hantera(elks)
        except ConnectionClosed:
            log.info("46elks stängde förbindelsen")
        except Exception:
            log.exception("samtalet avbröts av ett fel")
            await self._lagg_pa(elks)

    async def _lagg_pa(self, elks: ServerConnection) -> None:
        """Skicka bye och vänta på 46elks bye (de spelar klart bufferten först)."""
        try:
            await elks.send(_t({"t": "bye"}))
            async with asyncio.timeout(BYE_TIDSGRANS):
                async for ram in elks:
                    if isinstance(ram, str) and json.loads(ram).get("t") == "bye":
                        log.info("46elks bekräftade bye: %s", ram[:200])
                        break
        except (ConnectionClosed, TimeoutError, ValueError):
            pass
        finally:
            await elks.close()

    async def hantera(self, elks: ServerConnection) -> None:
        try:
            async with asyncio.timeout(HELLO_TIDSGRANS):
                forsta = await elks.recv()
            hello = json.loads(forsta)
        except (TimeoutError, ValueError, TypeError):
            log.warning("ingen giltig hello inom %s s, stänger", HELLO_TIDSGRANS)
            await elks.close()
            return
        if not isinstance(hello, dict) or hello.get("t") != "hello":
            log.warning("första meddelandet var inte hello (t=%r), stänger",
                        hello.get("t") if isinstance(hello, dict) else None)
            await self._lagg_pa(elks)
            return

        log.info("hello: %s", json.dumps(hello_for_logg(hello), ensure_ascii=False))
        callid = hello.get("callid", "?")
        uppringare, skal = plocka_uppringare(hello, self.vitlista)
        if uppringare is None:
            log.warning("nekar samtal %s: %s", callid, skal)
            await self._lagg_pa(elks)
            return
        log.info("samtal %s från %s (%s)", callid, uppringare, skal)

        try:
            url = await asyncio.to_thread(self.hamta_url)
            eleven = await ws_connect(url, open_timeout=10, max_size=2**22)
        except Exception as e:
            log.error("kunde inte öppna ElevenLabs: %s", type(e).__name__)
            await self._lagg_pa(elks)
            return

        try:
            await eleven.send(_t({
                "type": "conversation_initiation_client_data",
                "dynamic_variables": {"uppringare": uppringare},
            }))
            await elks.send(_t({"t": "sending", "format": self.elks_format}))
            await elks.send(_t({"t": "listening", "format": self.elks_format}))
            await self._koppla(elks, eleven, callid)
        finally:
            await eleven.close()

    async def _koppla(self, elks: ServerConnection, eleven, callid: str) -> None:
        in_hz = out_hz = hastighet(ELEVEN_STANDARDFORMAT)
        till_eleven = Omsamplare(self.elks_hz, in_hz)
        till_elks = Omsamplare(out_hz, self.elks_hz)
        senaste_avbrott = -1
        elks_sa_bye = asyncio.Event()

        async def fran_elks() -> None:
            async for ram in elks:
                if not isinstance(ram, str):
                    continue
                try:
                    m = json.loads(ram)
                except ValueError:
                    log.warning("trasig ram från 46elks ignoreras")
                    continue
                t = m.get("t")
                if t == "audio":
                    try:
                        pcm = base64.b64decode(m.get("data", ""))
                    except ValueError:
                        continue
                    ut = till_eleven(pcm)
                    if ut:
                        await eleven.send(_t({"user_audio_chunk": base64.b64encode(ut).decode()}))
                elif t == "bye":
                    log.info("samtal %s: 46elks bye %s", callid,
                             json.dumps({k: v for k, v in m.items() if k != "t"}, ensure_ascii=False))
                    elks_sa_bye.set()
                    return
                elif t == "sync":
                    pass
                else:
                    # Okända händelser loggas (utan ljud) — så skulle t.ex. ett framtida DTMF-meddelande synas.
                    log.info("okänd händelse från 46elks: %s",
                             json.dumps(hello_for_logg(m), ensure_ascii=False)[:300])

        async def fran_eleven() -> None:
            nonlocal senaste_avbrott, till_elks
            async for ram in eleven:
                try:
                    m = json.loads(ram)
                except (ValueError, TypeError):
                    continue
                typ = m.get("type")
                if typ == "audio":
                    ev = m.get("audio_event", {})
                    if int(ev.get("event_id", 0) or 0) <= senaste_avbrott:
                        continue   # ljud som hörde till ett avbrutet svar
                    pcm = base64.b64decode(ev.get("audio_base_64", ""))
                    ut = till_elks(pcm)
                    if ut:
                        await elks.send(_t({"t": "audio", "data": base64.b64encode(ut).decode()}))
                elif typ == "interruption":
                    senaste_avbrott = int(m.get("interruption_event", {}).get("event_id", 0) or 0)
                    await elks.send(_t({"t": "interrupt"}))
                    await elks.send(_t({"t": "sending", "format": self.elks_format}))
                    till_elks.nollstall()
                    log.info("samtal %s: avbrott, utbufferten tömd", callid)
                elif typ == "ping":
                    # Svara direkt: att vänta ping_ms här skulle stoppa ljudet i samma loop.
                    ev = m.get("ping_event", {})
                    await eleven.send(_t({"type": "pong", "event_id": ev.get("event_id")}))
                elif typ == "conversation_initiation_metadata":
                    ev = m.get("conversation_initiation_metadata_event", {})
                    folj_format(ev)
                    log.info("samtal %s: ElevenLabs-konversation %s", callid, ev.get("conversation_id"))
                elif typ == "user_transcript":
                    log.info("samtal %s: uppringaren: %s", callid,
                             m.get("user_transcription_event", {}).get("user_transcript", ""))
                elif typ == "agent_response":
                    log.info("samtal %s: agenten: %s", callid,
                             m.get("agent_response_event", {}).get("agent_response", ""))
                elif typ in ("agent_tool_response", "client_tool_call", "agent_tool_request"):
                    log.info("samtal %s: verktyg: %s", callid, json.dumps(m, ensure_ascii=False)[:300])

        def folj_format(ev: dict) -> None:
            """ElevenLabs talar om vilka format agenten faktiskt använder; följ dem."""
            nonlocal till_eleven, till_elks
            try:
                ut = hastighet(ev.get("agent_output_audio_format") or ELEVEN_STANDARDFORMAT)
                inn = hastighet(ev.get("user_input_audio_format") or ELEVEN_STANDARDFORMAT)
            except ValueError as e:
                log.error("oväntat ljudformat från ElevenLabs: %s", e)
                return
            if ut != till_elks.fran:
                till_elks = Omsamplare(ut, self.elks_hz)
            if inn != till_eleven.till:
                till_eleven = Omsamplare(self.elks_hz, inn)

        a = asyncio.create_task(fran_elks())
        b = asyncio.create_task(fran_eleven())
        klar, _ = await asyncio.wait({a, b}, return_when=asyncio.FIRST_COMPLETED)
        for uppg in klar:
            fel = uppg.exception()
            if fel and not isinstance(fel, ConnectionClosed):
                log.error("samtal %s: %s: %s", callid, type(fel).__name__, fel)

        if b in klar and not elks_sa_bye.is_set():
            # Agenten lade på (end_call) eller ElevenLabs föll bort: låt 46elks spela
            # klart hejdå-repliken och vänta på deras bye.
            a.cancel()
            await asyncio.gather(a, return_exceptions=True)
            log.info("samtal %s: ElevenLabs avslutade, lägger på", callid)
            await self._lagg_pa(elks)
        else:
            b.cancel()
            await asyncio.gather(b, return_exceptions=True)
            await elks.close()
        log.info("samtal %s slut", callid)


async def kor(vard: str, port: int) -> None:
    brygga = Brygga()
    log.info("bryggan lyssnar på ws://%s:%d%s, släpper in: %s", vard, port, SOKVAG,
             "alla svenska nummer" if brygga.vitlista is None else f"{len(brygga.vitlista)} nummer")
    async with serve(brygga, vard, port, process_request=Brygga.kontrollera_sokvag,
                     max_size=2**20):
        await asyncio.Future()


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    for namn in ("ELEVENLABS_API_KEY", "SKRIVBORD_AGENT_ID"):
        if not os.environ.get(namn):
            log.error("%s saknas i miljön", namn)
            sys.exit(1)
    asyncio.run(kor(os.environ.get("BRYGGA_VARD", "127.0.0.1"),
                    int(os.environ.get("BRYGGA_PORT", "8551"))))


if __name__ == "__main__":
    main()
