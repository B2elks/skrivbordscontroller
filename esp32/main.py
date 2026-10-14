"""Skrivbordet — firmware för ESP32-C3 SuperMini, MicroPython 1.29.

Flöde: WiFi → POST /api/enhet/start → loop { long-poll uppdrag → kör relä →
POST klar }. Ett klar-besked som inte gick fram skickas om innan nästa
long-poll (servern är idempotent på id). Nätverksfel ⇒ vänta och försök
igen; många fel i rad eller ett oväntat undantag ⇒ machine.reset().

Tidtagningsuppdrag ("tidtagning": true, används vid kalibrering) körs med
reläet draget medan kortet frågar /api/enhet/fortsatt var 500:e ms. Svaret
false eller ett nätfel släpper reläet direkt.

GPIO5 = relä UPP, GPIO6 = relä NER. Reläerna är aktivt höga som standard;
config.AKTIVT_HOG = False vänder nivåerna för moduler som triggar på låg nivå.
Undvik GPIO8/GPIO9 för reläerna: de är strapping-pinnar som avgör startläget.
"""

import time

import machine
import network
from machine import Pin

try:
    import requests
except ImportError:
    import urequests as requests

import config
from rela import Rela

VERSION = "1"
LONGPOLL_S = 25
MAX_FEL_I_RAD = 20

# Pinnarna skapas i släppt läge från första stund, innan något annat händer:
# låga för aktivt höga reläer (standard), höga för aktivt låga.
AKTIVT_HOG = getattr(config, "AKTIVT_HOG", True)
SLAPPT = 0 if AKTIVT_HOG else 1
_upp = Pin(5, Pin.OUT, value=SLAPPT)
_ner = Pin(6, Pin.OUT, value=SLAPPT)
rela = Rela(_upp, _ner, config.MAX_MS, aktivt_hog=AKTIVT_HOG)

_HUVUD = {"Authorization": "Bearer " + config.ENHET_TOKEN}
_wlan = network.WLAN(network.STA_IF)


def logg(*a):
    print("[skrivbord]", *a)


def wifi():
    """Anslut (eller återanslut) med återförsök. Kastar aldrig."""
    if _wlan.isconnected():
        return True
    _wlan.active(True)
    for forsok in range(1, 6):
        try:
            logg("ansluter till wifi, försök", forsok)
            _wlan.connect(config.WIFI_SSID, config.WIFI_LOSEN)
            for _ in range(40):
                if _wlan.isconnected():
                    logg("wifi ok", _wlan.ifconfig()[0])
                    return True
                time.sleep_ms(250)
        except OSError as e:
            logg("wifi-fel", e)
        try:
            _wlan.disconnect()
        except OSError:
            pass
        time.sleep(2 * forsok)
    return False


def anrop(metod, sokvag, json=None, timeout=10):
    """Returnerar (status, json|None). Kastar vid nätverksfel."""
    r = requests.request(metod, config.SERVER + sokvag, json=json, headers=_HUVUD, timeout=timeout)
    try:
        status = r.status_code
        data = None
        if status == 200:
            try:
                data = r.json()
            except ValueError:
                data = None
        return status, data
    finally:
        r.close()


def skicka_start():
    status, _ = anrop("POST", "/api/enhet/start", json={"version": VERSION})
    if status != 200:
        raise OSError("start gav %d" % status)


def skicka_klar(id_, ms_kord):
    status, _ = anrop("POST", "/api/enhet/klar", json={"id": id_, "ms_kord": ms_kord})
    # 404 = servern känner inte uppdraget; inget att skicka om.
    if status not in (200, 404):
        raise OSError("klar gav %d" % status)


def fortsatt_fn(id_):
    def fortsatt(gatt):
        status, data = anrop("GET", "/api/enhet/fortsatt?id=%d&gatt=%d" % (id_, gatt), timeout=3)
        return status == 200 and bool(data) and data.get("fortsatt") is True
    return fortsatt


def kor_uppdrag(u):
    id_, r, ms = int(u["id"]), u["rela"], int(u["ms"])
    if u.get("tidtagning"):
        logg("tidtagning", id_, r)
        return rela.kor_med_stopp(id_, r, fortsatt_fn(id_))
    logg("kör", id_, r, ms, "ms")
    return rela.kor(id_, r, ms)


def huvudloop():
    fel_i_rad = 0
    startad = False
    ej_rapporterad = None   # (id, ms_kord) som ännu inte bekräftats av servern
    while True:
        try:
            if not wifi():
                raise OSError("inget wifi")
            if not startad:
                skicka_start()
                startad = True
                logg("start rapporterad")
            if ej_rapporterad:
                skicka_klar(*ej_rapporterad)
                ej_rapporterad = None
            status, u = anrop("GET", "/api/enhet/uppdrag?vanta=%d" % LONGPOLL_S, timeout=LONGPOLL_S + 10)
            fel_i_rad = 0
            if status == 204:
                continue
            if status != 200 or not u:
                raise OSError("uppdrag gav %d" % status)
            try:
                ms_kord = kor_uppdrag(u)
            except ValueError as e:   # trasigt uppdrag — kör inte, men rapportera så kön inte låser sig
                logg("ogiltigt uppdrag", u, e)
                ms_kord = 0
            if ms_kord is None:       # id:t redan kört; servern har fått klar tidigare
                continue
            ej_rapporterad = (int(u["id"]), ms_kord)
            skicka_klar(*ej_rapporterad)
            ej_rapporterad = None
        except OSError as e:
            rela.slapp()
            fel_i_rad += 1
            logg("nätverksfel", fel_i_rad, e)
            if fel_i_rad >= MAX_FEL_I_RAD:
                logg("för många fel i rad, startar om")
                machine.reset()
            time.sleep(min(30, 2 * fel_i_rad))


def main():
    try:
        huvudloop()
    except Exception as e:  # aldrig hänga kvar i ett okänt läge
        rela.slapp()
        logg("oväntat fel, startar om om 5 s:", e)
        time.sleep(5)
        machine.reset()


main()
