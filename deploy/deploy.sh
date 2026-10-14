#!/usr/bin/env bash
# Driftsätter Skrivbordets server och brygga på en egen Linux-server bakom nginx.
# Körs lokalt från var som helst. Inställningar ur miljön eller deploy/deploy.env
# (se deploy/deploy.env.example).
# Går att köra om: hemligheter skapas bara om de saknas, venv bara om den saknas,
# nginx-confen läggs bara in om den saknas (certbot skriver i den) och certbot körs bara om certet saknas.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f deploy/deploy.env ]; then
    set -a; . deploy/deploy.env; set +a
fi

saknas() { echo "$1 saknas — sätt den i miljön eller i deploy/deploy.env" >&2; exit 1; }
SERVER=${SKRIVBORD_SERVER:-}; [ -n "$SERVER" ] || saknas SKRIVBORD_SERVER
DOMAN=${SKRIVBORD_DOMAN:-};   [ -n "$DOMAN" ] || saknas SKRIVBORD_DOMAN
EPOST=${CERTBOT_EPOST:-};     [ -n "$EPOST" ] || saknas CERTBOT_EPOST
case "$SERVER$DOMAN$EPOST" in *"[FYLL I"*) echo "deploy.env har kvar [FYLL I: …]" >&2; exit 1;; esac
ANV=${SKRIVBORD_ANVANDARE:-${SERVER%%@*}}
MAL=${SKRIVBORD_MAL:-/home/$ANV/skrivbord}
PORT=${SKRIVBORD_PORT:-8550}
BPORT=${SKRIVBORD_BRYGGA_PORT:-8551}
SPY=${SKRIVBORD_PYTHON:-python3.12}
HEMLIGT=/etc/skrivbord/secrets.env

if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi

echo "== Kontroller (verify)"
"$PY" checks/verify.py
echo "== Kontroller (brygga)"
[ -x brygga/.venv/bin/python ] || { echo "brygga/.venv saknas: python3 -m venv brygga/.venv && brygga/.venv/bin/pip install -r brygga/requirements.txt" >&2; exit 1; }
brygga/.venv/bin/python brygga/verify_brygga.py

echo "== Förkontroll på servern"
ssh "$SERVER" "bash -s" <<FORKONTROLL
set -euo pipefail
# Porten får bara vara upptagen om det är vår egen tjänst som håller den.
if ss -ltn | grep -q ':$PORT '; then
    if [ ! -f /etc/systemd/system/skrivbord.service ]; then
        echo "Port $PORT är upptagen av något annat än skrivbord – avbryter." >&2
        exit 1
    fi
fi
command -v $SPY >/dev/null || { echo "$SPY saknas på servern" >&2; exit 1; }
mkdir -p $MAL/data
FORKONTROLL

echo "== rsync"
# Bara serverkoden publiceras; checks/, esp32/, docs/ och deploy/ hör inte hemma där.
rsync -az --delete \
    --exclude '__pycache__/' \
    --exclude '*.db' \
    --exclude 'tests/' \
    server/ "$SERVER:$MAL/server/"
rsync -az --delete \
    --exclude '__pycache__/' --exclude '.venv/' --exclude 'verify_brygga.py' --exclude 'README.md' --exclude 'agent_id.txt' \
    brygga/ "$SERVER:$MAL/brygga/"
# Mallarna fylls i lokalt och skickas som färdiga filer.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fyll() {
    sed -e "s|@ANVANDARE@|$ANV|g" -e "s|@MAL@|$MAL|g" -e "s|@PORT@|$PORT|g" \
        -e "s|@BRYGGA_PORT@|$BPORT|g" -e "s|@DOMAN@|$DOMAN|g" "$1" > "$2"
    if grep -q '@[A-Z_]*@' "$2"; then echo "oersatt platshållare i $2" >&2; exit 1; fi
}
fyll deploy/skrivbord.service.mall "$TMP/skrivbord.service"
fyll deploy/skrivbord-brygga.service.mall "$TMP/skrivbord-brygga.service"
fyll deploy/nginx-skrivbord.conf.mall "$TMP/nginx-$DOMAN.conf"
rsync -az "$TMP/skrivbord.service" "$TMP/skrivbord-brygga.service" "$TMP/nginx-$DOMAN.conf" "$SERVER:/tmp/"

echo "== Installation på servern"
ssh "$SERVER" "bash -s" <<INSTALLATION
set -euo pipefail
cd $MAL

# Hemligheter skapas en gång och skrivs aldrig över. ELEVENLABS_API_KEY, SKRIVBORD_AGENT_ID
# och SKRIVBORD_EGNA_NUMMER läggs till för hand (se README).
if ! sudo -n test -f $HEMLIGT; then
    sudo -n install -d -m 750 -o root -g $ANV /etc/skrivbord
    {
        echo "ENHET_TOKEN=\$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
        echo "AGENT_HEMLIGHET=\$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
        echo "ADMIN_LOSEN=\$(python3 -c 'import secrets;print(secrets.token_urlsafe(12))')"
        echo "SECRET_KEY=\$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
    } | sudo -n tee $HEMLIGT >/dev/null
    sudo -n chown root:$ANV $HEMLIGT
    sudo -n chmod 640 $HEMLIGT
fi

[ -d venv ] || $SPY -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r server/requirements.txt

[ -d venv-brygga ] || $SPY -m venv venv-brygga
venv-brygga/bin/pip install -q --upgrade pip
venv-brygga/bin/pip install -q -r brygga/requirements.txt

sudo -n install -m 644 /tmp/skrivbord.service /etc/systemd/system/skrivbord.service
sudo -n install -m 644 /tmp/skrivbord-brygga.service /etc/systemd/system/skrivbord-brygga.service
if [ ! -f /etc/nginx/sites-available/$DOMAN ]; then
    sudo -n install -m 644 /tmp/nginx-$DOMAN.conf /etc/nginx/sites-available/$DOMAN
fi
sudo -n ln -sf /etc/nginx/sites-available/$DOMAN /etc/nginx/sites-enabled/$DOMAN
rm -f /tmp/skrivbord.service /tmp/skrivbord-brygga.service /tmp/nginx-$DOMAN.conf

sudo -n nginx -t
sudo -n systemctl reload nginx
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now skrivbord
sudo -n systemctl restart skrivbord
# Bryggan startas bara när agenten finns; annars avslutar den direkt och systemd startar om den i en loop.
if sudo -n grep -q '^SKRIVBORD_AGENT_ID=.' $HEMLIGT && sudo -n grep -q '^ELEVENLABS_API_KEY=.' $HEMLIGT; then
    sudo -n systemctl enable --now skrivbord-brygga
    sudo -n systemctl restart skrivbord-brygga
else
    echo "skrivbord-brygga startas inte: SKRIVBORD_AGENT_ID/ELEVENLABS_API_KEY saknas i $HEMLIGT" >&2
fi

if ! sudo -n test -d /etc/letsencrypt/live/$DOMAN; then
    sudo -n certbot --nginx -d $DOMAN --non-interactive --agree-tos -m "$EPOST" --redirect
fi
INSTALLATION

echo "== Spärr"
FEL=0
fel() { echo "SPÄRR FALLER: $*" >&2; FEL=1; }
status() { curl -s -o /dev/null -w '%{http_code}' "$@" || echo 000; }

# Vänta tills gunicorn svarar: nginx ger 502 medan den startar.
S=000
for _ in $(seq 1 20); do
    S=$(status "https://$DOMAN/admin")
    [ "$S" = "401" ] && break
    sleep 1
done
echo "/admin utan lösen:     $S"
[ "$S" = "401" ] || fel "/admin utan lösen gav $S, väntade 401"

LOSEN=$(ssh "$SERVER" "sudo -n grep '^ADMIN_LOSEN=' $HEMLIGT | cut -d= -f2-")
HUVUD=$(curl -sS -o /dev/null -D - -u "admin:$LOSEN" "https://$DOMAN/admin" 2>&1 | tr -d '\r' || true)
echo "/admin med lösen:      $(printf '%s\n' "$HUVUD" | head -1) / $(printf '%s\n' "$HUVUD" | grep -i '^content-type:' || true)"
printf '%s\n' "$HUVUD" | head -1 | grep -Eq '^HTTP/[0-9.]+ 200' || fel "/admin med lösen gav inte 200"
printf '%s\n' "$HUVUD" | grep -qi '^content-type: text/html' || fel "/admin är inte text/html"

S=$(status "https://$DOMAN/api/enhet/uppdrag?vanta=0")
echo "enhet utan token:      $S"
[ "$S" = "401" ] || fel "enhetens API utan token gav $S, väntade 401"

S=$(status -X POST -H 'Content-Type: application/json' -d '{}' "https://$DOMAN/api/agent/flytta")
echo "agent utan hemlighet:  $S"
[ "$S" = "401" ] || fel "agentens API utan hemlighet gav $S, väntade 401"

HTTP=$(curl -sS -o /dev/null -D - "http://$DOMAN/admin" 2>&1 | tr -d '\r' || true)
echo "http:                  $(printf '%s\n' "$HTTP" | head -1)"
printf '%s\n' "$HTTP" | head -1 | grep -Eq '^HTTP/[0-9.]+ 301' || fel "http gav inte 301"

if ssh "$SERVER" "systemctl is-active -q skrivbord-brygga"; then
    S=$(curl -s --http1.1 --max-time 3 -o /dev/null -w '%{http_code}' -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
        -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "https://$DOMAN/elks" || true)
    echo "wss /elks:             $S"
    [ "$S" = "101" ] || fel "/elks gav $S, väntade 101 (websocket-uppgradering)"
fi

if [ "$FEL" -ne 0 ]; then
    echo "Deploy klar men spärren faller." >&2
    exit 1
fi
echo "Spärren OK: https://$DOMAN/admin"
