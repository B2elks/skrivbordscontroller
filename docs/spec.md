# Skrivbordet — röststyrt höj- och sänkbart bord

Kravspecifikationen som `checks/verify.py` och `brygga/verify_brygga.py` bevisar.
Byggguiden finns i [README.md](../README.md).

## Mål

Ring ett nummer, säg "sänk bordet tio centimeter" (eller "stå"/"sitt", eller "ställ det på 100").
Bordet rör sig medan samtalet pågår, och när det stannat säger agenten i samma samtal:
"Klart. 98 centimeter." Agenten är fåordig med flit: svenska, inga artighetsfraser,
högst åtta ord per svar från servern (verify räknar).

**Servern ringer aldrig ut.** Det finns ingen utgående klient i `server/`, och verify bevisar det.

## Hårdvara

- ESP32-C3 SuperMini, MicroPython 1.29.
- Tvåkanals relämodul, **aktivt hög** som standard (pinne hög = relä draget);
  `AKTIVT_HOG = False` i `esp32/config.py` för en aktivt låg modul.
  - GPIO5 = relä UPP, GPIO6 = relä NER.
- Reläerna sitter parallellt över bordets upp-/nedknapp: bordet rör sig så länge reläet är draget.
- Ingen höjdsensor. Positionen uppskattas av tid × hastighet och nollställs i ändlägena.

## Delar

| Del | Var | Ansvar |
|---|---|---|
| `server/` | Flask + SQLite, systemd `skrivbord` :8550 bakom nginx, `SKRIVBORD_OFFENTLIG_URL` | All logik: nummerkontroll, position, cm→ms, uppdragskö, adminsida |
| `brygga/` | Python websockets, systemd `skrivbord-brygga` :8551 via nginx `wss://<domän>/elks` | 46elks websocket-samtal ↔ ElevenLabs-agent |
| `esp32/` | MicroPython på kortet | Long-poll, kör relä exakt N ms, rapportera klar |
| `checks/verify.py` | lokalt + deploy-spärr | Körbara krav |

## Telefoni

- Inkommande: ett vanligt 46elks-nummer med `voice_start = {"connect":"<websocket-numret>"}` →
  ett 46elks websocket-nummer med `websocket_url = wss://<domän>/elks`. Båda numren hör till
  `SKRIVBORD_EGNA_NUMMER` och får aldrig själva styra bordet.
- ElevenLabs-agent "Skrivbordet", svenska, röst Evelina (`SKRIVBORD_ROST` byter). Uppringarens nummer
  skickas som dynamisk variabel `uppringare` vid samtalsstart.
- Agentens verktyg (ElevenLabs server tools = webhook mot servern, delad hemlighet i header):
  - `flytta_bord(riktning: "upp"|"ner", cm: 1–80)`
  - `till_lage(lage: "stå"|"sitt")`
  - `till_hojd(cm: number)` — absolut höjd från golvet ("ställ bordet på 100 centimeter")
  - `spara_profil(namn)` — "spara min höjd som Björn", "kom ihåg det här som Björn"
  - `till_profil(namn)` — "ställ på Björns höjd" (`pre_tool_speech: "force"`, väntar som till_hojd)
  - `lista_profiler()` — "vilka profiler finns"
  - `end_call` (inbyggt)
- Agenten säger "Jo." innan ett rörelseverktyg anropas (`pre_tool_speech: "force"`), väntar tyst på
  svaret och läser upp `sag` ordagrant. `end_call` när uppringaren säger hej då/tack/nej.
  Verktygens `response_timeout_secs` = 60 (ElevenLabs tillåter 5–300); samtal högst 120 s.
  Förstår den inte säger den "Igen?". Ingen DTMF-reserv: 46elks skickar inga knapptryck över
  websocketen (se `brygga/README.md`).

## Server — kontrakt

### Enheten (ESP32), bearer-token `ENHET_TOKEN`

- `GET /api/enhet/uppdrag?vanta=25` — long-poll. Svar `204` (inget) eller
  `{"id": int, "rela": "upp"|"ner", "ms": int}`.
- `POST /api/enhet/klar` `{"id": int, "ms_kord": int}` — idempotent på `id`.
- `POST /api/enhet/start` `{"version": str}` — vid uppstart. Ett uppdrag som var utskickat men
  aldrig rapporterat klart ⇒ positionen markeras **okänd**.

### Agenten, header `X-Agent-Hemlighet`

- `POST /api/agent/flytta` `{"uppringare", "riktning", "cm"}`
- `POST /api/agent/lage` `{"uppringare", "lage"}`
- `POST /api/agent/hojd` `{"uppringare", "cm"}` — målet klipps till [min, max]; mål i ändläget körs
  som stå/sitt (full restid + marginal, positionen nollställs), annars tidsstyrt som en relativ flytt.
- `POST /api/agent/profil/spara` `{"uppringare", "namn"}`, `/api/agent/profil/till` `{"uppringare", "namn"}`,
  `/api/agent/profil/lista` `{"uppringare"}` — se Profiler nedan. Agenten kan inte ta bort profiler.
- Svar alltid `200` med `{"ok": bool, "sag": str}` — `sag` är meningen agenten ska säga.
- **Synkrona**: ett nekande svaras direkt. Ett godkänt uppdrag hålls öppet (databasen pollas var 0,2 s)
  tills det är `klar` eller `timeout`, högst uppdragets ms + 15 s. `sag` är då resultatet:
  `text_klar(...)` ("Klart. 92 centimeter."), `TEXT_KLAR_OKAND_POSITION`
  eller `TEXT_SVARAR_INTE`. En gunicorn-tråd (av 8) hålls under tiden.

### Admin, HTTP basic auth

- `/admin` — uppskattad position, kalibrering, senaste händelser, manuella knappar (upp/ner N cm,
  ställ på höjd N cm, stå, sitt).
- Kalibrering: "kör till botten" → skriv lägsta cm; "start upp"/"stopp" mäter restid → skriv högsta cm;
  samma nedåt. Varje värde går också att skriva in för hand.

### Profiler

Sparad höjd under ett namn, tabell `profil(nyckel, namn, cm, skapad, andrad)`.

- **Nyckel** = namnet trimmat, blanksteg kollapsade, casefold. `namn` behåller stavningen från första
  sparningen. Högst 40 tecken, minst en bokstav, högst 50 profiler (överskrivning går alltid).
- **Uppslag** (`logik.hitta_profilnyckel`): exakt nyckel först, annars utan genitiv-s och utan
  efterledet "höjd" — men bara mot en profil som finns ("Björns höjd" → Björn; "Anna-Karin" ≠ Anna).
- **Spara** kräver kalibrering, känd position och att inget uppdrag pågår; sparar positionen avrundad
  till 0,5 cm. "Sparat. Björn, 98." / "Ändrat. Björn, 98, var 104."
- **Till profil** körs exakt som `till_hojd(profilens cm)` (samma plan, samma synkrona väntan); svaret är
  "Klart. Björns, 98 centimeter." Okänt namn räknar upp profilerna som finns.
- **Lista**: "Anna 82, Björn 98." (svensk sortering).
- **Admin**: sektion Profiler — Kör hit, Ta bort, "Spara nuvarande höjd som…".

## Regler (det verify.py ska bevisa)

1. **cm→ms**: `ms = round(cm / hastighet[riktning] * 1000)`, hastighet = (max−min) / restid per riktning.
2. **Ändlägesklippning**: målet klipps till [min, max]; faktisk sträcka rapporteras. Står bordet i ändläget
   åt det hållet ⇒ inget uppdrag, agenten säger det.
3. **Stå/sitt**: full restid + 1,5 s marginal; positionen sätts till exakt max/min efteråt.
4. **Okänd position**: relativa kommandon nekas tills stå eller sitt har körts.
5. **Ett uppdrag i taget**; ett nytt under pågående ⇒ "bordet rör sig redan".
6. **Bara svenska nummer** (+46) får styra bordet, utom de egna numren i kedjan (`SKRIVBORD_EGNA_NUMMER`). Dolda och utländska nummer nekas. `SKRIVBORD_VITLISTA` i bryggan kan låsa till en exakt lista.
7. **Inget tak** på antal rörelser; dygnsräknaren finns som statistik på adminsidan.
8. **Timeout**: ingen `klar` inom ms + 30 s ⇒ positionen okänd. Agenten säger "bordet svarar inte" i
   samtalet redan när ms + 15 s gått utan klar.
11. **Ingen utringning**: ingen kodväg i `server/` kan ringa ut — inga 46elks-/TTS-moduler, ingen
    utgående HTTP-klient, ingen `/ljud`-route; agent-, admin- och timeoutflöden gör noll anslutningar.
9. **ESP32**: aldrig båda reläerna samtidigt; hårt tak per drag = `MAX_MS` (full restid + 2 s, satt i
   firmware); pinnarna i släppt läge vid uppstart; samma `id` körs aldrig två gånger.
10. **Okalibrerat bord** ⇒ alla rörelsekommandon nekas med förklaring.

## Utanför v1

Höjdsensor, flera bord, sms-styrning. (Profiler finns, men är inte knutna till
uppringarens nummer.)
