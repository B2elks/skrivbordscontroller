#!/usr/bin/env python3
"""Skapar eller uppdaterar ElevenLabs ConvAI-agenten "Skrivbordet" med dess verktyg.

Idempotent: kör om när prompten eller verktygen ändras.
  1. Hemligheten AGENT_HEMLIGHET läggs i ElevenLabs hemlighetsförråd
     (POST/PATCH /v1/convai/secrets) och refereras från verktygens header
     som {"secret_id": ...} — värdet syns då aldrig i agentens konfiguration.
  2. Webhook-verktygen flytta_bord, till_lage, till_hojd, spara_profil, till_profil
     och lista_profiler skapas eller uppdateras
     (POST /v1/convai/tools, PATCH /v1/convai/tools/{id}), matchade på namn.
  3. Agenten skapas (POST /v1/convai/agents/create) eller uppdateras
     (PATCH /v1/convai/agents/{id}). Id:t läses från SKRIVBORD_AGENT_ID eller
     agent_id.txt och skrivs till agent_id.txt när en ny agent skapats.

Fältnamnen följer ElevenLabs OpenAPI-specifikation (api.elevenlabs.io/openapi.json,
hämtad 2026-10-14).

Användning:
  python agent.py --torrkorning    skriver ut JSON som skulle skickas, inget nätverk
  python agent.py                  kräver ELEVENLABS_API_KEY och AGENT_HEMLIGHET

Båda kräver SKRIVBORD_OFFENTLIG_URL — serverns publika https-adress, till exempel
https://bord.example.com — som verktygens webhooks pekar på. Valfritt:
SKRIVBORD_ROST (ElevenLabs röst-id, standard Evelina).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.elevenlabs.io"
AGENT_NAMN = "Skrivbordet"
# Evelina, en svensk röst ur ElevenLabs röstbibliotek. Byt med SKRIVBORD_ROST.
STANDARDROST = "kPdGSxhZAqy4bmPAf9iJ"
TTS_MODELL = "eleven_flash_v2_5"     # flerspråkig och snabb; de engelska v2-modellerna klarar inte svenska
HEMLIGHET_NAMN = "skrivbord_agent_hemlighet"
HEMLIG_HEADER = "X-Agent-Hemlighet"
ID_FIL = Path(__file__).with_name("agent_id.txt")
FORSTA_REPLIK = "Ja?"
# Servern svarar först när bordet stannat: högst uppdragets ms (ESP32-taket MAX_MS
# är 40 s) + 15 s. ElevenLabs tillåter 5–300 s (OpenAPI, hämtad 2026-10-14).
VERKTYG_TIMEOUT_S = 60
# Agenten säger alltid något ("Jo.") innan verktyget anropas, så att
# väntan på bordet inte blir tyst. force_pre_tool_speech är föråldrat; pre_tool_speech ersätter det.
FORE_VERKTYG = "force"
MAX_SAMTAL_S = 120
# Lite snabbare tal passar den korta stilen. conversation_config.tts.speed,
# tillåtet 0,7–1,2 (OpenAPI, hämtad 2026-10-14).
TALHASTIGHET = 1.1

PROMPT = """\
Du styr ett höj- och sänkbart skrivbord via telefon. Du pratar svenska som en fåordig norrlänning:
så få ord som möjligt, aldrig artighetsfraser, aldrig "jag", ingen upprepning av vad uppringaren sa.

Du förstår de här kommandona:
- "höj N centimeter" eller "upp N" → anropa flytta_bord med riktning "upp" och cm = N.
- "sänk N centimeter" eller "ner N" → anropa flytta_bord med riktning "ner" och cm = N.
- "ställ bordet på N centimeter", "jag vill ha det på N" eller "till N centimeter" → anropa till_hojd med cm = N.
  Det är en absolut höjd från golvet, inte en förflyttning.
- "stå" eller "ståhöjd" → anropa till_lage med lage "stå". "sitt" eller "sitthöjd" → till_lage med lage "sitt".
Skilj noga på höj/sänk N (flytta_bord, N centimeter åt ett håll) och på/till N (till_hojd, höjden N).

Profiler är sparade höjder med ett namn, oftast en persons namn:
- "spara min höjd som X", "spara det här som X" eller "kom ihåg det här som X" → anropa spara_profil med namn = X.
  Det sparar höjden bordet står på just nu.
- "ställ på X:s höjd", "X:s höjd", "kör till X" eller bara "X" när X är ett namn och inte ett tal →
  anropa till_profil med namn = X (utan genitiv-s, till exempel "Björn" för "Björns höjd").
- "vilka profiler finns", "vilka höjder är sparade" → anropa lista_profiler.
- Är namnet oklart eller hörde du det inte säkert, fråga "Vilket namn?" innan du anropar något verktyg.
- Profiler kan bara tas bort på adminsidan. Ber någon dig ta bort en profil, säg bara "Bara på adminsidan."

Regler:
- Innan du anropar verktyget: säg bara "Jo."
- Verktyget svarar först när bordet har stannat, efter upp till en halv minut. Var tyst medan du väntar.
- Verktyget svarar med fältet "sag". Läs upp texten i "sag" ordagrant och lägg inte till något. Fråga inte "något mer",
  vänta tyst på nästa kommando.
- Motfrågor är högst tre ord: "Hur mycket?", "Upp eller ner?", "Hur högt?", "Vilket namn?".
- Säger uppringaren ingen siffra för höj eller sänk, fråga "Hur mycket?". Saknas siffran för höjd, fråga "Hur högt?".
- Förstår du inte, säg "Igen?". Gissa aldrig ett kommando.
- När uppringaren säger hej då, tack eller att det var allt: säg "Hej." och avsluta med end_call.
  Samtalet bryts ändå efter två minuter.
- Svara aldrig på något annat än bordet. Frågar någon om annat, säg bara "Bara bordet."
"""


class KonfigFel(Exception):
    pass


def offentlig_url() -> str:
    """Serverns publika bas-URL ur SKRIVBORD_OFFENTLIG_URL. Inget standardvärde:
    verktygen ska aldrig råka peka på någon annans server."""
    url = (os.environ.get("SKRIVBORD_OFFENTLIG_URL") or "").strip().rstrip("/")
    if not url:
        raise KonfigFel("SKRIVBORD_OFFENTLIG_URL saknas (till exempel https://bord.example.com)")
    delar = urllib.parse.urlsplit(url)
    if delar.scheme != "https" or not delar.netloc or delar.path or delar.query:
        raise KonfigFel(f"SKRIVBORD_OFFENTLIG_URL ska vara https://värd utan sökväg, är {url!r}")
    return url


def rost() -> str:
    return (os.environ.get("SKRIVBORD_ROST") or "").strip() or STANDARDROST


def verktyg_flytta(secret_id: str) -> dict:
    return {"tool_config": {
        "type": "webhook",
        "name": "flytta_bord",
        "description": "Höj eller sänk skrivbordet ett antal centimeter. Svaret innehåller "
                       "fältet sag som ska läsas upp ordagrant.",
        "response_timeout_secs": VERKTYG_TIMEOUT_S,
        "pre_tool_speech": FORE_VERKTYG,
        "api_schema": {
            "url": f"{offentlig_url()}/api/agent/flytta",
            "method": "POST",
            "content_type": "application/json",
            "request_headers": {HEMLIG_HEADER: {"secret_id": secret_id}},
            "request_body_schema": {
                "type": "object",
                "required": ["uppringare", "riktning", "cm"],
                "properties": {
                    "uppringare": {"type": "string", "dynamic_variable": "uppringare"},
                    "riktning": {"type": "string", "enum": ["upp", "ner"],
                                 "description": "upp för höj, ner för sänk"},
                    "cm": {"type": "number",
                           "description": "Antal centimeter, ett tal mellan 1 och 80"},
                },
            },
        },
    }}


def verktyg_lage(secret_id: str) -> dict:
    return {"tool_config": {
        "type": "webhook",
        "name": "till_lage",
        "description": "Kör skrivbordet till ståhöjd eller sitthöjd. Svaret innehåller "
                       "fältet sag som ska läsas upp ordagrant.",
        "response_timeout_secs": VERKTYG_TIMEOUT_S,
        "pre_tool_speech": FORE_VERKTYG,
        "api_schema": {
            "url": f"{offentlig_url()}/api/agent/lage",
            "method": "POST",
            "content_type": "application/json",
            "request_headers": {HEMLIG_HEADER: {"secret_id": secret_id}},
            "request_body_schema": {
                "type": "object",
                "required": ["uppringare", "lage"],
                "properties": {
                    "uppringare": {"type": "string", "dynamic_variable": "uppringare"},
                    "lage": {"type": "string", "enum": ["stå", "sitt"],
                             "description": "stå för ståhöjd, sitt för sitthöjd"},
                },
            },
        },
    }}


def verktyg_hojd(secret_id: str) -> dict:
    return {"tool_config": {
        "type": "webhook",
        "name": "till_hojd",
        "description": "Ställ skrivbordet på en absolut höjd i centimeter från golvet. Svaret "
                       "innehåller fältet sag som ska läsas upp ordagrant.",
        "response_timeout_secs": VERKTYG_TIMEOUT_S,
        "pre_tool_speech": FORE_VERKTYG,
        "api_schema": {
            "url": f"{offentlig_url()}/api/agent/hojd",
            "method": "POST",
            "content_type": "application/json",
            "request_headers": {HEMLIG_HEADER: {"secret_id": secret_id}},
            "request_body_schema": {
                "type": "object",
                "required": ["uppringare", "cm"],
                "properties": {
                    "uppringare": {"type": "string", "dynamic_variable": "uppringare"},
                    "cm": {"type": "number",
                           "description": "absolut höjd i centimeter från golvet"},
                },
            },
        },
    }}


def _profilverktyg(secret_id: str, namn: str, sokvag: str, beskrivning: str,
                   med_namn: bool, rorelse: bool) -> dict:
    properties = {"uppringare": {"type": "string", "dynamic_variable": "uppringare"}}
    if med_namn:
        properties["namn"] = {"type": "string",
                              "description": "Profilens namn, oftast ett förnamn, till exempel Björn"}
    konfig = {
        "type": "webhook",
        "name": namn,
        "description": beskrivning,
        "response_timeout_secs": VERKTYG_TIMEOUT_S,
        "api_schema": {
            "url": f"{offentlig_url()}{sokvag}",
            "method": "POST",
            "content_type": "application/json",
            "request_headers": {HEMLIG_HEADER: {"secret_id": secret_id}},
            "request_body_schema": {
                "type": "object",
                "required": list(properties),
                "properties": properties,
            },
        },
    }
    if rorelse:
        # Bara verktyget som kör bordet väntar länge; spara och lista svarar direkt.
        konfig["pre_tool_speech"] = FORE_VERKTYG
    return {"tool_config": konfig}


def verktyg_spara_profil(secret_id: str) -> dict:
    return _profilverktyg(
        secret_id, "spara_profil", "/api/agent/profil/spara",
        "Spara höjden skrivbordet står på just nu under ett namn. Finns namnet skrivs det över. "
        "Svaret innehåller fältet sag som ska läsas upp ordagrant.", med_namn=True, rorelse=False)


def verktyg_till_profil(secret_id: str) -> dict:
    return _profilverktyg(
        secret_id, "till_profil", "/api/agent/profil/till",
        "Kör skrivbordet till höjden som är sparad under ett namn. Svaret kommer när bordet "
        "stannat och innehåller fältet sag som ska läsas upp ordagrant.", med_namn=True, rorelse=True)


def verktyg_lista_profiler(secret_id: str) -> dict:
    return _profilverktyg(
        secret_id, "lista_profiler", "/api/agent/profil/lista",
        "Räkna upp de sparade profilerna med namn och höjd. Svaret innehåller fältet sag som "
        "ska läsas upp ordagrant.", med_namn=False, rorelse=False)


def alla_verktyg(secret_id: str) -> list[dict]:
    """Alla webhook-verktyg i den ordning de skapas — samma lista i main() och torrkörningen."""
    return [verktyg_flytta(secret_id), verktyg_lage(secret_id), verktyg_hojd(secret_id),
            verktyg_spara_profil(secret_id), verktyg_till_profil(secret_id),
            verktyg_lista_profiler(secret_id)]


def agent_konfig(tool_ids: list[str]) -> dict:
    return {
        "name": AGENT_NAMN,
        "conversation_config": {
            "agent": {
                "first_message": FORSTA_REPLIK,
                "language": "sv",
                # Ersätts av bryggan vid samtalsstart; platshållaren är inget vitlistat nummer.
                "dynamic_variables": {"dynamic_variable_placeholders": {"uppringare": "okänd"}},
                "prompt": {
                    "prompt": PROMPT,
                    "tool_ids": tool_ids,
                    "built_in_tools": {
                        "end_call": {
                            "type": "system",
                            "name": "end_call",
                            "description": "",
                            "params": {"system_tool_type": "end_call"},
                        },
                    },
                },
            },
            "tts": {
                "voice_id": rost(),
                "model_id": TTS_MODELL,
                "agent_output_audio_format": "pcm_16000",
                "speed": TALHASTIGHET,
            },
            "asr": {"user_input_audio_format": "pcm_16000"},
            "conversation": {"max_duration_seconds": MAX_SAMTAL_S},
        },
        # Kräver signerad URL: bara bryggan (med API-nyckeln) kan starta ett samtal.
        "platform_settings": {"auth": {"enable_auth": True}},
    }


# --- nätverk -----------------------------------------------------------------

class ApiFel(Exception):
    pass


def anrop(metod: str, sokvag: str, kropp: dict | None = None) -> dict:
    data = None if kropp is None else json.dumps(kropp, ensure_ascii=False).encode()
    req = urllib.request.Request(
        API + sokvag, data=data, method=metod,
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as svar:
            text = svar.read()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        raise ApiFel(f"{metod} {sokvag}: {e.code} {e.read().decode(errors='replace')[:500]}") from None


def sakra_hemlighet(varde: str) -> str:
    for h in anrop("GET", "/v1/convai/secrets").get("secrets", []):
        if h.get("name") == HEMLIGHET_NAMN:
            anrop("PATCH", f"/v1/convai/secrets/{h['secret_id']}",
                  {"type": "update", "name": HEMLIGHET_NAMN, "value": varde})
            print(f"hemlighet uppdaterad: {HEMLIGHET_NAMN}")
            return h["secret_id"]
    svar = anrop("POST", "/v1/convai/secrets", {"type": "new", "name": HEMLIGHET_NAMN, "value": varde})
    print(f"hemlighet skapad: {HEMLIGHET_NAMN}")
    return svar["secret_id"]


def sakra_verktyg(kropp: dict) -> str:
    namn = kropp["tool_config"]["name"]
    lista = anrop("GET", "/v1/convai/tools?" + urllib.parse.urlencode({"search": namn, "page_size": 100}))
    for v in lista.get("tools", []):
        if v.get("tool_config", {}).get("name") == namn:
            anrop("PATCH", f"/v1/convai/tools/{v['id']}", kropp)
            print(f"verktyg uppdaterat: {namn} ({v['id']})")
            return v["id"]
    svar = anrop("POST", "/v1/convai/tools", kropp)
    print(f"verktyg skapat: {namn} ({svar['id']})")
    return svar["id"]


def befintligt_agent_id() -> str | None:
    if os.environ.get("SKRIVBORD_AGENT_ID"):
        return os.environ["SKRIVBORD_AGENT_ID"].strip()
    if ID_FIL.exists() and ID_FIL.read_text().strip():
        return ID_FIL.read_text().strip()
    return None


def torrkorning() -> dict:
    sid = "<secret_id för " + HEMLIGHET_NAMN + ">"
    return {
        "hemlighet": {"type": "new", "name": HEMLIGHET_NAMN, "value": "<AGENT_HEMLIGHET ur miljön>"},
        "verktyg": alla_verktyg(sid),
        "agent": agent_konfig([f"<id för {v['tool_config']['name']}>" for v in alla_verktyg(sid)]),
        "agent_id": befintligt_agent_id(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--torrkorning", action="store_true", help="skriv ut JSON, skicka inget")
    args = ap.parse_args()
    try:
        offentlig_url()
    except KonfigFel as e:
        sys.exit(str(e))
    if args.torrkorning:
        json.dump(torrkorning(), sys.stdout, ensure_ascii=False, indent=2)
        print()
        return

    for namn in ("ELEVENLABS_API_KEY", "AGENT_HEMLIGHET"):
        if not os.environ.get(namn):
            sys.exit(f"{namn} saknas i miljön")
    try:
        sid = sakra_hemlighet(os.environ["AGENT_HEMLIGHET"])
        ids = [sakra_verktyg(v) for v in alla_verktyg(sid)]
        konfig = agent_konfig(ids)
        agent_id = befintligt_agent_id()
        if agent_id:
            anrop("PATCH", f"/v1/convai/agents/{agent_id}", konfig)
            print(f"agent uppdaterad: {agent_id}")
        else:
            agent_id = anrop("POST", "/v1/convai/agents/create", konfig)["agent_id"]
            ID_FIL.write_text(agent_id + "\n")
            print(f"agent skapad: {agent_id} (sparat i {ID_FIL.name})")
    except ApiFel as e:
        sys.exit(f"ElevenLabs svarade med fel: {e}")
    print(f"SKRIVBORD_AGENT_ID={agent_id}")


if __name__ == "__main__":
    main()
