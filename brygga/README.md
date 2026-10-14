# Bryggan

46elks websocket-samtal ↔ ElevenLabs-agenten "Skrivbordet". Körs som systemd
`skrivbord-brygga` på 127.0.0.1:8551 och nås via `wss://<din domän>/elks`
(nginx terminerar TLS). Hela bygget beskrivs i [README.md](../README.md) i roten.

- `agent.py` skapar/uppdaterar agenten, hemligheten och webhook-verktygen
  (`--torrkorning` skriver ut JSON utan att anropa något).
- `brygga.py` tar emot samtalet, kontrollerar uppringaren och kopplar ljudet
  (46elks `pcm_24000` ↔ ElevenLabs `pcm_16000`, egen omsampling).
- `verify_brygga.py` är kontrollerna; kör med `.venv/bin/python verify_brygga.py`.

Miljö: `ELEVENLABS_API_KEY`, `SKRIVBORD_AGENT_ID` (bryggan), `AGENT_HEMLIGHET` och
`SKRIVBORD_OFFENTLIG_URL` (agent.py). Valfritt: `SKRIVBORD_EGNA_NUMMER` och
`SKRIVBORD_VITLISTA` (kommaseparerade), `SKRIVBORD_ROST`.

## Uppringaren

Tas ur `hello["from"]`. Hela hello-meddelandet loggas, så att det första riktiga
samtalet visar om `from` är den som ringde eller numret som kopplar vidare (med
`voice_start {"connect":"<websocket-numret>"}`). Saknas numret, är det inte svenskt
eller är det ett av de egna numren skickar bryggan bara `bye`. ElevenLabs öppnas aldrig.

## Ingen DTMF-reserv

46elks realtime-voice-protokoll har inga meddelanden för knapptryck: dokumentationen
listar `hello`, `audio`, `sync` och `bye` från 46elks. ElevenLabs `dtmf_input_settings`
gäller deras egna telefoniintegrationer (SIP/Twilio), inte en egen websocket-klient.
Bryggan loggar okända `t`-värden, så om 46elks någon gång skickar knapptryck syns
det i journalen.
