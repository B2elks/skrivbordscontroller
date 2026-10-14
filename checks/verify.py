#!/usr/bin/env python3
"""Körbar verifiering för Skrivbordet — inga beroenden utanför stdlib.

Mönstret: varje byggsteg lägger till kontrollfunktioner i CHECKS. Varje kontroll
anropar fail() för varje problem den hittar (samlar alla fel, avbryter inte på
första). main() kör alla kontroller och avslutar med kod 1 om något föll.

Kör:  .venv/bin/python checks/verify.py
"""

import os
import sys

# Gör server/ importerbar oavsett var skriptet startas ifrån.
_ROT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER = os.path.join(_ROT, "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

# De egna numren i kedjan är platsspecifika och kommer ur miljön. Verifieringen sätter
# ett fiktivt (PTS serie för fiktiv användning, 070-174 06 05 – 070-174 06 99) innan
# server/logik.py importeras, så att regeln "egna nummer får inte styra" prövas.
EGET_NUMMER = "+46701740698"
os.environ["SKRIVBORD_EGNA_NUMMER"] = EGET_NUMMER + ", +4600000000"

_fel = []
TOL = 1e-6


def fail(msg):
    """Registrera ett fel. Avbryter inte — kontrollen kör klart och samlar allt."""
    _fel.append(msg)


def _logik():
    import logik
    return logik


# Två kalibreringar för svepen: olika restid per riktning, och lika restider
# med udda gränser (inte jämna halvcentimeter).
def _kalibreringar(lg):
    return [
        lg.Kalibrering(min_cm=65.0, max_cm=125.0, restid_upp_s=22.0, restid_ner_s=20.0),
        lg.Kalibrering(min_cm=70.3, max_cm=118.7, restid_upp_s=18.0, restid_ner_s=18.0),
    ]


def _halvsteg(min_cm, max_cm):
    """Alla startpositioner min..max i 0,5 cm-steg, plus exakt max."""
    ut = []
    i = 0
    while True:
        p = min_cm + i * 0.5
        if p > max_cm + 1e-9:
            break
        ut.append(p)
        i += 1
    if abs(ut[-1] - max_cm) > 1e-9:
        ut.append(max_cm)
    return ut


# --- Steg 1: logik ---

def check_logik_importerbar():
    try:
        _logik()
    except Exception as e:
        fail(f"server/logik.py går inte att importera: {e}")


def check_hastighet_och_cm_till_ms():
    lg = _logik()
    k = lg.Kalibrering(65.0, 125.0, 22.0, 20.0)
    if not k.giltig():
        fail("65–125 cm, 22/20 s ska vara giltig kalibrering")
    if abs(k.hastighet("upp") - 60 / 22) > TOL:
        fail(f"hastighet upp {k.hastighet('upp')} ≠ 60/22")
    if abs(k.hastighet("ner") - 3.0) > TOL:
        fail(f"hastighet ner {k.hastighet('ner')} ≠ 3,0")
    # Regel 1: ms = round(cm / hastighet * 1000)
    for riktning in ("upp", "ner"):
        for cm10 in range(10, 801):
            cm = cm10 / 10
            ms = lg.cm_till_ms(k, riktning, cm)
            vantat = round(cm / k.hastighet(riktning) * 1000)
            if not isinstance(ms, int):
                fail(f"cm_till_ms gav {type(ms).__name__}, inte int")
                return
            if ms != vantat:
                fail(f"cm_till_ms({riktning}, {cm}) = {ms}, väntat {vantat}")
    if lg.cm_till_ms(k, "ner", 10) != 3333:
        fail(f"10 cm ner vid 3 cm/s ska bli 3333 ms, blev {lg.cm_till_ms(k, 'ner', 10)}")


def check_okalibrerat():
    lg = _logik()
    ogiltiga = [
        None,
        lg.Kalibrering(125.0, 65.0, 22.0, 20.0),   # max < min
        lg.Kalibrering(80.0, 80.0, 22.0, 20.0),    # max = min
        lg.Kalibrering(65.0, 125.0, 0.0, 20.0),    # restid upp 0
        lg.Kalibrering(65.0, 125.0, 22.0, -1.0),   # restid ner negativ
        lg.Kalibrering(None, 125.0, 22.0, 20.0),   # saknat värde
    ]
    for k in ogiltiga:
        if k is not None and k.giltig():
            fail(f"{k} ska vara ogiltig")
        if k is not None and k.hastighet("upp") is not None:
            fail(f"{k}: hastighet ska vara None när ogiltig")
        for riktning in ("upp", "ner"):
            r = lg.planera_relativ(k, 90.0, riktning, 10)
            if not isinstance(r, lg.Nekande):
                fail(f"okalibrerat {k}: relativ {riktning} ska nekas, fick {r}")
            elif "kalibrer" not in r.sag:
                fail(f"okalibrerat: nekandet ska förklara kalibrering, sa '{r.sag}'")
        for lage in ("stå", "sitt"):
            r = lg.planera_lage(k, lage)
            if not isinstance(r, lg.Nekande):
                fail(f"okalibrerat {k}: {lage} ska nekas, fick {r}")


def check_okand_position():
    lg = _logik()
    for k in _kalibreringar(lg):
        for riktning in ("upp", "ner"):
            r = lg.planera_relativ(k, None, riktning, 10)
            if not isinstance(r, lg.Nekande):
                fail(f"okänd position: {riktning} ska nekas, fick {r}")
            elif "stå" not in r.sag or "sitt" not in r.sag:
                fail(f"okänd position: ska be om stå eller sitt, sa '{r.sag}'")
        # Stå/sitt går alltid att köra från okänd position
        for lage in ("stå", "sitt"):
            if not isinstance(lg.planera_lage(k, lage), lg.Uppdrag):
                fail(f"{lage} ska gå att köra från okänd position")


def check_ogiltig_indata():
    lg = _logik()
    k = _kalibreringar(lg)[0]
    for cm in (0, 0.5, 80.5, 81, -5, 1000, "tio", None, float("nan"), float("inf"), True):
        r = lg.planera_relativ(k, 90.0, "ner", cm)
        if not isinstance(r, lg.Nekande):
            fail(f"cm={cm!r} ska nekas, fick {r}")
    for cm in (1, 80, "10", 12.5):
        r = lg.planera_relativ(k, 90.0, "upp", cm)
        if not isinstance(r, lg.Uppdrag):
            fail(f"cm={cm!r} ska godtas från 90 cm uppåt, fick {r}")
    for riktning in ("UPP", "fram", "", None):
        r = lg.planera_relativ(k, 90.0, riktning, 10)
        if riktning == "UPP":
            if not isinstance(r, lg.Uppdrag):
                fail("riktning 'UPP' ska normaliseras till 'upp'")
        elif not isinstance(r, lg.Nekande):
            fail(f"riktning {riktning!r} ska nekas, fick {r}")
    for lage in ("ligg", "", None):
        if not isinstance(lg.planera_lage(k, lage), lg.Nekande):
            fail(f"läge {lage!r} ska nekas")
    for lage in ("Stå", "sta", "SITT"):
        if not isinstance(lg.planera_lage(k, lage), lg.Uppdrag):
            fail(f"läge {lage!r} ska normaliseras och godtas")


def check_svep_relativ():
    """Regel 1 + 2 svept: alla starter × alla cm × båda riktningar × två kalibreringar."""
    lg = _logik()
    antal = 0
    for k in _kalibreringar(lg):
        full_ms = {r: (getattr(k, f"restid_{r}_s") + lg.MARGINAL_S) * 1000 for r in ("upp", "ner")}
        for start in _halvsteg(k.min_cm, k.max_cm):
            for cm in range(1, 81):
                for riktning in ("upp", "ner"):
                    antal += 1
                    r = lg.planera_relativ(k, start, riktning, cm)
                    kvar = (k.max_cm - start) if riktning == "upp" else (start - k.min_cm)
                    if isinstance(r, lg.Nekande):
                        # Enda tillåtna skälet: bordet står (nästan) i ändläget åt det hållet.
                        if kvar >= lg.MINSTA_CM - 1e-9:
                            fail(f"{k} start {start} {riktning} {cm}: nekad fast {kvar:.2f} cm återstår ('{r.sag}')")
                        elif r.sag != ("Det är i topp." if riktning == "upp" else "Det är i botten."):
                            fail(f"ändläge {riktning} från {start}: ska säga 'Det är i topp/botten.', sa '{r.sag}'")
                        continue
                    if kvar < lg.MINSTA_CM - 1e-9:
                        fail(f"{k} start {start} {riktning}: borde nekas i ändläget, fick {r}")
                    if r.rela != riktning:
                        fail(f"rela {r.rela} ≠ riktning {riktning}")
                    if not (k.min_cm - TOL <= r.ny_position <= k.max_cm + TOL):
                        fail(f"{k} start {start} {riktning} {cm}: slut {r.ny_position} utanför [min, max]")
                    if r.ms < 0:
                        fail(f"negativ ms {r.ms}")
                    if r.ms > full_ms[riktning]:
                        fail(f"ms {r.ms} > full restid + marginal {full_ms[riktning]}")
                    if abs(r.faktisk_cm - abs(r.ny_position - start)) > TOL:
                        fail(f"faktisk {r.faktisk_cm} ≠ |{r.ny_position} − {start}|")
                    tillbaka = lg.ms_till_cm(k, riktning, r.ms)
                    if abs(tillbaka - r.faktisk_cm) >= 0.1:
                        fail(f"ms→cm {tillbaka:.4f} skiljer ≥ 0,1 från faktisk {r.faktisk_cm:.4f}")
                    if r.ms != lg.cm_till_ms(k, riktning, r.faktisk_cm):
                        fail(f"ms {r.ms} ≠ cm_till_ms(faktisk)")
                    ska_klippas = cm > kvar + TOL
                    if ska_klippas != r.klippt:
                        fail(f"start {start} {riktning} {cm}: klippt={r.klippt}, väntat {ska_klippas}")
                    if not ska_klippas and abs(r.faktisk_cm - cm) > TOL:
                        fail(f"oklippt men faktisk {r.faktisk_cm} ≠ begärd {cm}")
                    if ska_klippas:
                        mal = k.max_cm if riktning == "upp" else k.min_cm
                        if abs(r.ny_position - mal) > TOL:
                            fail(f"klippt men slut {r.ny_position} ≠ ändläge {mal}")
                    if not r.sag:
                        fail("uppdrag saknar sag-mening")
                    # Position efter körning ska stämma med planen
                    efter = lg.position_efter(k, start, riktning, r.ms)
                    if abs(efter - r.ny_position) >= 0.1:
                        fail(f"position_efter {efter:.3f} ≠ planerad {r.ny_position:.3f}")
    if antal < 10000:
        fail(f"svepet körde bara {antal} fall — något är fel med svepet")


def check_andlage_exakt():
    lg = _logik()
    k = _kalibreringar(lg)[0]
    r = lg.planera_relativ(k, k.max_cm, "upp", 10)
    if not isinstance(r, lg.Nekande) or r.sag != "Det är i topp.":
        fail(f"högst upp + upp ska nekas med 'Det är i topp.', fick {r}")
    r = lg.planera_relativ(k, k.min_cm, "ner", 10)
    if not isinstance(r, lg.Nekande) or r.sag != "Det är i botten.":
        fail(f"längst ner + ner ska nekas med 'Det är i botten.', fick {r}")
    # Position utanför intervallet (t.ex. glidning i uppskattningen) behandlas som ändläget.
    r = lg.planera_relativ(k, k.max_cm + 3, "upp", 10)
    if not isinstance(r, lg.Nekande):
        fail("position över max + upp ska nekas")
    r = lg.planera_relativ(k, k.max_cm + 3, "ner", 10)
    if not isinstance(r, lg.Uppdrag) or abs(r.ny_position - (k.max_cm - 10)) > TOL:
        fail(f"position över max + ner 10 ska utgå från max, fick {r}")


def check_lage():
    """Regel 3: stå/sitt = full restid + 1,5 s, position exakt max/min."""
    lg = _logik()
    if lg.MARGINAL_S != 1.5:
        fail(f"MARGINAL_S ska vara 1,5, är {lg.MARGINAL_S}")
    for k in _kalibreringar(lg):
        for lage, rela, restid, mal in (("stå", "upp", k.restid_upp_s, k.max_cm),
                                        ("sitt", "ner", k.restid_ner_s, k.min_cm)):
            for pos in (None, k.min_cm, (k.min_cm + k.max_cm) / 2, k.max_cm):
                r = lg.planera_lage(k, lage, pos)
                if not isinstance(r, lg.Uppdrag):
                    fail(f"{lage} från {pos} ska ge uppdrag, fick {r}")
                    continue
                if r.rela != rela:
                    fail(f"{lage}: rela {r.rela} ≠ {rela}")
                if r.ms != round((restid + 1.5) * 1000):
                    fail(f"{lage}: ms {r.ms} ≠ {round((restid + 1.5) * 1000)}")
                if r.ny_position != mal:
                    fail(f"{lage}: ny_position {r.ny_position} ≠ exakt {mal}")
                if pos is None and r.faktisk_cm is not None:
                    fail(f"{lage} från okänd: faktisk_cm ska vara None")
                if pos is not None and abs(r.faktisk_cm - abs(mal - pos)) > TOL:
                    fail(f"{lage} från {pos}: faktisk {r.faktisk_cm} fel")
                if lg.position_efter(k, pos, rela, r.ms, lage=lage) != mal:
                    fail(f"position_efter med läge {lage} ska bli exakt {mal}")


def check_position_efter():
    lg = _logik()
    k = _kalibreringar(lg)[0]
    if lg.position_efter(k, None, "upp", 1000) is not None:
        fail("position_efter från okänd ska ge None")
    if lg.position_efter(None, 90.0, "upp", 1000) is not None:
        fail("position_efter utan kalibrering ska ge None")
    p = lg.position_efter(k, 90.0, "ner", 3333)
    if abs(p - (90 - 9.999)) > 1e-3:
        fail(f"90 cm ner 3333 ms vid 3 cm/s ska bli ≈80,001, blev {p}")
    if lg.position_efter(k, 120.0, "upp", 60000) != k.max_cm:
        fail("lång körning upp ska klippas till max")
    if lg.position_efter(k, 70.0, "ner", 60000) != k.min_cm:
        fail("lång körning ner ska klippas till min")


def check_vitlista():
    """Regel 6 (2026-10-14): alla svenska nummer får styra, utom våra egna; svenska format normaliseras."""
    lg = _logik()
    if hasattr(lg, "VITLISTA") or hasattr(lg, "pa_vitlistan"):
        fail("den gamla tvånummers-vitlistan ska vara borta")
    norm = {
        "+46701740605": "+46701740605",
        "0701740605": "+46701740605",
        "070-1740605": "+46701740605",
        "070-174 06 05": "+46701740605",
        "0046701740605": "+46701740605",
        "+46 (0)70 174 06 05": "+46701740605",
        " +46701740606 ": "+46701740606",
        "070-174 06 06": "+46701740606",
        "+4721234567": "+4721234567",
    }
    for indata, vantat in norm.items():
        if lg.normalisera_nummer(indata) != vantat:
            fail(f"normalisera_nummer({indata!r}) = {lg.normalisera_nummer(indata)!r}, väntat {vantat!r}")
    for ogiltigt in ("", None, "abc", "07017", "+46", "1234567890123456789", "+46701740605x", 46701740605):
        if lg.normalisera_nummer(ogiltigt) is not None:
            fail(f"normalisera_nummer({ogiltigt!r}) ska ge None")
    ja = ["+46701740605", "070-1740605", "0046701740605", "0701740606", "+46 70 174 06 06",
          "+46701740608", "0701740609", "08-465 004 00", "+46 8 465 004 01"]
    nej = ["+4721234567", "+4915112345678", "+12025550123", "", None, "anonymous", "701740605",
           "+460701740605x", "+46701740698", "070-174 06 98"]
    for n in ja:
        if not lg.far_styra(n):
            fail(f"{n!r} ska få styra bordet")
    for n in nej:
        if lg.far_styra(n):
            fail(f"{n!r} ska inte få styra bordet")
    # Svep: alla svenska mobilnummer 070–079 med godtyckliga slutsiffror släpps in.
    for prefix in range(70, 80):
        for slut in range(0, 10**7, 999_983):
            n = f"+46{prefix}{slut:07d}"
            if n not in lg.EGNA_NUMMER and not lg.far_styra(n):
                fail(f"{n} ska få styra bordet")




def check_texter():
    lg = _logik()
    t = lg.text_klar(98.0)
    if t != "Klart. 98 centimeter.":
        fail(f"text_klar(98) = {t!r}")
    # Avrundning till heltal, halva uppåt (inte bankiravrundning)
    for pos, tal in ((97.5, "98"), (98.5, "99"), (98.49, "98"), (124.99, "125")):
        if lg.text_klar(pos) != f"Klart. {tal} centimeter.":
            fail(f"text_klar({pos}) ska säga {tal}, sa {lg.text_klar(pos)!r}")
    t = lg.text_klar(125.0, begard_cm=10, faktisk_cm=6.6)
    if t != "Klart. 125 centimeter, längre går det inte.":
        fail(f"klippt text ska säga att det inte går längre: {t!r}")
    t = lg.text_klar(100.0, begard_cm=10, faktisk_cm=10.0)
    if "längre" in t:
        fail(f"oklippt ska inte säga 'längre': {t!r}")
    for namn in ("TEXT_SVARAR_INTE", "TEXT_ROR_SIG_REDAN"):
        v = getattr(lg, namn, "")
        if not v or not v.endswith("."):
            fail(f"{namn} saknas eller är inte en mening")
    # Alla meningar agenten kan säga ska vara svenska och avslutade.
    k = _kalibreringar(lg)[0]
    meningar = [
        lg.planera_relativ(None, 90, "upp", 10).sag,
        lg.planera_relativ(k, None, "upp", 10).sag,
        lg.planera_relativ(k, k.max_cm, "upp", 10).sag,
        lg.planera_relativ(k, k.min_cm, "ner", 10).sag,
        lg.planera_relativ(k, 90, "upp", 0).sag,
        lg.planera_relativ(k, 90, "fram", 10).sag,
        lg.planera_relativ(k, 90, "upp", 10).sag,
        lg.planera_relativ(k, 120, "upp", 10).sag,
        lg.planera_lage(k, "stå").sag,
        lg.planera_lage(k, "sitt").sag,
        lg.planera_lage(k, "ligg").sag,
    ]
    for m in meningar:
        if not m or not m[-1] in ".?!":
            fail(f"mening utan avslut: {m!r}")
        for engelska in ("table", "error", "None", "up ", "down"):
            if engelska in m:
                fail(f"mening innehåller '{engelska}': {m!r}")
    # Förhandstexten läses normalt inte upp (svaret kommer när bordet stannat) och
    # får aldrig lova en återuppringning.
    for r in (lg.planera_relativ(k, 90, "upp", 10), lg.planera_relativ(k, 90, "ner", 10),
              lg.planera_relativ(k, 120, "upp", 10), lg.planera_lage(k, "stå"), lg.planera_lage(k, "sitt")):
        if r.sag != "Jo.":
            fail(f"förhandstext ska vara 'Jo.', är {r.sag!r}")


# --- Steg 6: absolut höjd ("ställ bordet på 100 centimeter") ---

def _halvsteg_mal(fran=50.0, till=140.0):
    return [fran + i * 0.5 for i in range(int(round((till - fran) / 0.5)) + 1)]


def check_svep_hojd():
    """Alla starter min..max × mål 50..140 cm (0,5-steg) × två kalibreringar.
    Mål utanför [min, max] klipps; mål i ändläget körs som stå/sitt (full restid +
    marginal, positionen nollställs); mål inom MINSTA_CM nekas; annars tidsstyrt."""
    lg = _logik()
    if not hasattr(lg, "planera_hojd"):
        fail("logik.planera_hojd saknas")
        return
    kals = [lg.Kalibrering(min_cm=62.0, max_cm=127.0, restid_upp_s=19.0, restid_ner_s=19.2),
            _kalibreringar(lg)[1]]
    antal = 0
    for k in kals:
        full_ms = {r: avr for r, avr in (("upp", lg.avrunda((k.restid_upp_s + lg.MARGINAL_S) * 1000)),
                                         ("ner", lg.avrunda((k.restid_ner_s + lg.MARGINAL_S) * 1000)))}
        for start in _halvsteg(k.min_cm, k.max_cm):
            for mal in _halvsteg_mal():
                antal += 1
                r = lg.planera_hojd(k, start, mal)
                klippt_mal = min(max(mal, k.min_cm), k.max_cm)
                klipptes = abs(klippt_mal - mal) > 1e-9
                andlage = "upp" if mal > k.max_cm else "ner"
                klipptext = f"Går bara till {lg.avrunda(klippt_mal)}."
                fall = f"{k.min_cm}–{k.max_cm} start {start} mål {mal}"
                if abs(klippt_mal - start) < lg.MINSTA_CM - 1e-9:
                    if not isinstance(r, lg.Nekande):
                        fail(f"{fall}: står redan där, ska nekas, fick {r}")
                    elif klipptes and r.sag != f"{klipptext} Står redan där.":
                        fail(f"{fall}: klippt mål ska säga '{klipptext} Står redan där.', sa {r.sag!r}")
                    elif not klipptes and r.sag != f"Står redan på {lg.avrunda(start)}.":
                        fail(f"{fall}: ska säga 'Står redan på {lg.avrunda(start)}.', sa {r.sag!r}")
                    continue
                if not isinstance(r, lg.Uppdrag):
                    fail(f"{fall}: ska ge uppdrag, fick {r}")
                    continue
                riktning = "upp" if klippt_mal > start else "ner"
                if r.rela != riktning:
                    fail(f"{fall}: rela {r.rela} ≠ {riktning}")
                if abs(r.faktisk_cm - abs(klippt_mal - start)) > TOL:
                    fail(f"{fall}: faktisk {r.faktisk_cm} ≠ |{klippt_mal} − {start}|")
                if r.klippt != klipptes:
                    fail(f"{fall}: klippt={r.klippt}, väntat {klipptes}")
                if not (0 <= r.ms <= full_ms[riktning]):
                    fail(f"{fall}: ms {r.ms} utanför [0, full restid + marginal {full_ms[riktning]}]")
                if klippt_mal in (k.min_cm, k.max_cm):
                    # Ändläget: kör som stå/sitt, positionen nollställs exakt.
                    lage = "stå" if klippt_mal == k.max_cm else "sitt"
                    if r.ny_position != klippt_mal:
                        fail(f"{fall}: ny_position {r.ny_position} ≠ exakt ändläge {klippt_mal}")
                    if r.ms != full_ms[riktning]:
                        fail(f"{fall}: ändläge ska köra full restid + marginal {full_ms[riktning]}, ms {r.ms}")
                    if getattr(r, "lage", None) != lage:
                        fail(f"{fall}: ändläge ska bära lage={lage!r}, har {getattr(r, 'lage', None)!r}")
                    if lg.position_efter(k, start, r.rela, r.ms, lage=getattr(r, "lage", None)) != klippt_mal:
                        fail(f"{fall}: position_efter ska nollställa till exakt {klippt_mal}")
                else:
                    if getattr(r, "lage", None) is not None:
                        fail(f"{fall}: mål inne i intervallet får inte bära lage, har {r.lage!r}")
                    if abs(r.ny_position - klippt_mal) > TOL:
                        fail(f"{fall}: ny_position {r.ny_position} ≠ {klippt_mal}")
                    if r.ms != lg.cm_till_ms(k, riktning, r.faktisk_cm):
                        fail(f"{fall}: ms {r.ms} ≠ cm_till_ms(faktisk)")
                    efter = lg.position_efter(k, start, r.rela, r.ms)
                    if abs(efter - klippt_mal) >= 0.1:
                        fail(f"{fall}: position_efter {efter:.3f} ≠ mål {klippt_mal}")
                if klipptes and r.sag != klipptext:
                    fail(f"{fall}: klippt mål ska säga {klipptext!r}, sa {r.sag!r}")
                if not klipptes and r.sag != "Jo.":
                    fail(f"{fall}: oklippt mål ska säga 'Jo.', sa {r.sag!r}")
                if not r.sag.endswith("."):
                    fail(f"{fall}: sag utan avslut {r.sag!r}")
    if antal < 40000:
        fail(f"höjdsvepet körde bara {antal} fall — något är fel med svepet")


def check_hojd_nekanden():
    lg = _logik()
    if not hasattr(lg, "planera_hojd"):
        return  # redan rapporterat av check_svep_hojd
    k = lg.Kalibrering(62.0, 127.0, 19.0, 19.2)
    # Okalibrerat ⇒ TEXT_OKALIBRERAT, även med okänd position
    for kal in (None, lg.Kalibrering(127.0, 62.0, 19.0, 19.2), lg.Kalibrering(None, 127.0, 19.0, 19.2)):
        for pos in (90.0, None):
            r = lg.planera_hojd(kal, pos, 100)
            if not isinstance(r, lg.Nekande) or r.sag != lg.TEXT_OKALIBRERAT:
                fail(f"okalibrerat {kal} pos {pos}: ska nekas med TEXT_OKALIBRERAT, fick {r}")
    # Okänd position ⇒ TEXT_OKAND_POSITION, även för mål i ändläget
    for mal in (62, 100, 127, 140, 50):
        for pos in (None, float("nan"), "nittio"):
            r = lg.planera_hojd(k, pos, mal)
            if not isinstance(r, lg.Nekande) or r.sag != lg.TEXT_OKAND_POSITION:
                fail(f"okänd position {pos!r} mål {mal}: ska nekas med TEXT_OKAND_POSITION, fick {r}")
    # Ogiltigt mål
    for mal in (None, "hundra", float("nan"), float("inf"), True, 0, -5, ""):
        r = lg.planera_hojd(k, 90.0, mal)
        if not isinstance(r, lg.Nekande):
            fail(f"mål {mal!r} ska nekas, fick {r}")
        elif r.sag != "Hur högt?":
            fail(f"mål {mal!r}: nekandet ska fråga 'Hur högt?', sa {r.sag!r}")
    # Strängar och decimalkomma godtas
    for mal, vantat in (("100", 100.0), ("95,5", 95.5), (" 110 ", 110.0)):
        r = lg.planera_hojd(k, 90.0, mal)
        if not isinstance(r, lg.Uppdrag) or abs(r.ny_position - vantat) > TOL:
            fail(f"mål {mal!r} ska tolkas som {vantat}, fick {r}")
    # Position som glidit utanför intervallet behandlas som ändläget
    r = lg.planera_hojd(k, 130.0, 100)
    if not isinstance(r, lg.Uppdrag) or abs(r.faktisk_cm - 27) > TOL:
        fail(f"position 130 (över max) mål 100 ska utgå från 127, fick {r}")
    r = lg.planera_hojd(k, 130.0, 127)
    if not isinstance(r, lg.Nekande) or r.sag != "Står redan på 127.":
        fail(f"position 130 mål 127 ska nekas 'Står redan på 127.', fick {r}")
    # Exempelmeningar från användaren
    r = lg.planera_hojd(k, 100.0, 50)
    if not isinstance(r, lg.Uppdrag) or r.sag != "Går bara till 62.":
        fail(f"mål 50 ska säga 'Går bara till 62.', sa {getattr(r, 'sag', r)!r}")
    r = lg.planera_hojd(k, 100.0, 100)
    if not isinstance(r, lg.Nekande) or r.sag != "Står redan på 100.":
        fail(f"mål 100 från 100 ska ge 'Står redan på 100.', fick {r}")
    # Stå/sitt och relativa uppdrag bär inget lage-fält som ändrats av misstag
    if getattr(lg.planera_lage(k, "stå"), "lage", None) != "stå" or \
            getattr(lg.planera_lage(k, "sitt"), "lage", None) != "sitt":
        fail("planera_lage ska sätta lage 'stå'/'sitt' på uppdraget")
    if getattr(lg.planera_relativ(k, 90.0, "upp", 10), "lage", "saknas") is not None:
        fail("planera_relativ ska ge lage None")


# --- Steg 2: server (Flask + SQLite) och ESP32-firmware ---

_ESP32 = os.path.join(_ROT, "esp32")
ENHET = "test-enhet-token"
AGENT = "test-agent-hemlighet"
LOSEN = "test-admin-losen"
VITT = "+46701740605"


def _har_flask():
    try:
        import flask  # noqa: F401
        return True
    except ImportError:
        return False


class _Klocka:
    """Falsk väggklocka för servern: tid står still tills testet flyttar den."""

    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _ny_app(klocka=None, **extra):
    import tempfile
    import app as appmod
    katalog = tempfile.mkdtemp(prefix="skrivbord-verify-")
    cfg = {
        "TESTING": True,
        "DB": os.path.join(katalog, "test.db"),
        "ENHET_TOKEN": ENHET,
        "AGENT_HEMLIGHET": AGENT,
        "ADMIN_LOSEN": LOSEN,
        "SECRET_KEY": "test-secret",
        "KLOCKA": klocka or _Klocka(),
        "POLL_S": 0.02,
    }
    cfg.update(extra)
    a = appmod.skapa_app(cfg)
    return a, a.extensions["bord"]


def _enhet(token=ENHET):
    return {"Authorization": f"Bearer {token}"}


def _agent(hemlighet=AGENT):
    return {"X-Agent-Hemlighet": hemlighet}


def _admin(losen=LOSEN, anv="admin"):
    import base64
    return {"Authorization": "Basic " + base64.b64encode(f"{anv}:{losen}".encode()).decode()}


def _kalibrera(bord, position=95.0):
    import logik
    bord.spara_kalibrering(logik.Kalibrering(65.0, 125.0, 22.0, 20.0))
    bord.satt_position(position)


def _slutfor(c):
    """Låt "enheten" hämta och rapportera klart det pågående uppdraget."""
    u = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).get_json()
    c.post("/api/enhet/klar", json={"id": u["id"], "ms_kord": u["ms"]}, headers=_enhet())


def _krav_flask(namn):
    if not _har_flask():
        fail(f"{namn}: HOPPADES ÖVER — flask kan inte importeras. Kör i venv: "
             ".venv/bin/python checks/verify.py")
        return False
    return True


def check_requirements():
    sokvag = os.path.join(_SERVER, "requirements.txt")
    if not os.path.exists(sokvag):
        fail("server/requirements.txt saknas")
        return
    rader = [r.strip() for r in open(sokvag, encoding="utf-8") if r.strip() and not r.startswith("#")]
    namn = {r.split("==")[0].lower() for r in rader}
    for paket in ("flask", "gunicorn"):
        if paket not in namn:
            fail(f"requirements.txt saknar {paket}")
    for r in rader:
        if "==" not in r:
            fail(f"requirements.txt: '{r}' är inte pinnad med ==")


def check_db_schema():
    import sqlite3
    import tempfile
    try:
        import db
    except Exception as e:
        fail(f"server/db.py går inte att importera: {e}")
        return
    sokvag = os.path.join(tempfile.mkdtemp(prefix="skrivbord-db-"), "s.db")
    conn = db.anslut(sokvag)
    tabeller = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("kalibrering", "position", "uppdrag", "handelse", "dygn", "enhet"):
        if t not in tabeller:
            fail(f"db: tabellen {t} saknas")
    kol = {r[1] for r in conn.execute("PRAGMA table_info(uppdrag)")}
    for k in ("id", "rela", "ms", "status", "skapad", "utskickat_tid", "klar_tid", "uppringare",
              "begard_cm", "faktisk_cm", "ny_position", "lage"):
        if k not in kol:
            fail(f"db: uppdrag saknar kolumnen {k}")
    if db.hamta_position(conn) is not None:
        fail("db: ny databas ska ha okänd position")
    if db.hamta_kalibrering(conn).giltig():
        fail("db: ny databas ska vara okalibrerad")
    # Standardsökväg och env
    gammal = os.environ.pop("SKRIVBORD_DB", None)
    try:
        if db.standard_sokvag() != os.path.join(_ROT, "data", "skrivbord.db"):
            fail(f"db: standardsökväg ska vara skrivbord/data/skrivbord.db, är {db.standard_sokvag()}")
        os.environ["SKRIVBORD_DB"] = "/tmp/x.db"
        if db.standard_sokvag() != "/tmp/x.db":
            fail("db: SKRIVBORD_DB ska styra sökvägen")
    finally:
        os.environ.pop("SKRIVBORD_DB", None)
        if gammal is not None:
            os.environ["SKRIVBORD_DB"] = gammal
    try:
        conn.execute("INSERT INTO uppdrag (rela, ms, status, skapad) VALUES ('fram', 1, 'väntar', 0)")
        fail("db: rela utanför upp/ner ska stoppas av CHECK")
    except sqlite3.IntegrityError:
        pass


def check_vagrar_starta_utan_hemligheter():
    if not _krav_flask("check_vagrar_starta_utan_hemligheter"):
        return
    import app as appmod
    import tempfile
    bas = {"DB": os.path.join(tempfile.mkdtemp(), "p.db"), "ENHET_TOKEN": "a",
           "AGENT_HEMLIGHET": "b", "ADMIN_LOSEN": "c", "SECRET_KEY": "d"}
    for saknas in ("ENHET_TOKEN", "AGENT_HEMLIGHET", "ADMIN_LOSEN", "SECRET_KEY"):
        for varde in (None, ""):
            cfg = dict(bas)
            if varde is None:
                cfg.pop(saknas)
            else:
                cfg[saknas] = varde
            sparat = os.environ.pop(saknas, None)
            try:
                appmod.skapa_app(cfg)
                fail(f"skapa_app ska vägra starta när {saknas} saknas ({varde!r})")
            except RuntimeError:
                pass
            finally:
                if sparat is not None:
                    os.environ[saknas] = sparat
    try:
        appmod.skapa_app(bas)
    except Exception as e:
        fail(f"skapa_app med alla hemligheter ska starta: {e}")


def check_enhet_floede():
    """Long-poll 204 → uppdrag → klar → position uppdaterad; dubbel klar ändrar inget."""
    if not _krav_flask("check_enhet_floede"):
        return
    import logik
    a, bord = _ny_app()
    c = a.test_client()
    _kalibrera(bord, 95.0)
    r = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet())
    if r.status_code != 204:
        fail(f"long-poll utan uppdrag ska ge 204, gav {r.status_code}")
    svar = bord.begar_rorelse("admin", riktning="ner", cm=10)
    if not svar.ok:
        fail(f"begar_rorelse ner 10 från 95 ska lyckas: {svar.sag}")
        return
    r = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet())
    if r.status_code != 200:
        fail(f"long-poll med uppdrag ska ge 200, gav {r.status_code}")
        return
    j = r.get_json()
    if set(j) - {"tidtagning"} != {"id", "rela", "ms"} or j["rela"] != "ner" or j["ms"] != 3333:
        fail(f"uppdragssvaret ska vara {{id, rela:'ner', ms:3333}}, var {j}")
    if bord.uppdrag(j["id"])["status"] != "utskickat":
        fail("hämtat uppdrag ska markeras utskickat")
    if c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).status_code != 204:
        fail("ett utskickat uppdrag får inte skickas igen")
    if bord.position() != 95.0:
        fail("positionen ska inte ändras förrän klar rapporterats")
    r = c.post("/api/enhet/klar", json={"id": j["id"], "ms_kord": 3333}, headers=_enhet())
    if r.status_code != 200:
        fail(f"klar ska ge 200, gav {r.status_code}")
    p1 = bord.position()
    if p1 is None or abs(p1 - 85.0) > 0.01:
        fail(f"position efter 10 cm ner från 95 ska vara ≈85, är {p1}")
    if bord.uppdrag(j["id"])["status"] != "klar":
        fail("uppdraget ska ha status klar")
    # Dubbel klar (t.ex. omsänt efter tappat svar) — idempotent
    r = c.post("/api/enhet/klar", json={"id": j["id"], "ms_kord": 9999}, headers=_enhet())
    if r.status_code != 200:
        fail(f"andra klar med samma id ska ge 200, gav {r.status_code}")
    if bord.position() != p1:
        fail(f"andra klar ändrade positionen {p1} → {bord.position()}")
    # Okänt id och trasig kropp
    if c.post("/api/enhet/klar", json={"id": 99999, "ms_kord": 1}, headers=_enhet()).status_code != 404:
        fail("klar med okänt id ska ge 404")
    if c.post("/api/enhet/klar", data="skräp", headers=_enhet()).status_code != 400:
        fail("klar med trasig kropp ska ge 400")
    # Stå från okänd position nollställer till exakt max
    bord.satt_position(None)
    svar = bord.begar_rorelse("admin", lage="stå")
    j = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).get_json()
    c.post("/api/enhet/klar", json={"id": j["id"], "ms_kord": j["ms"]}, headers=_enhet())
    if bord.position() != 125.0:
        fail(f"stå från okänd ska ge exakt 125, gav {bord.position()}")


def check_long_poll_vantar():
    """Long-pollen ska verkligen vänta och leverera ett uppdrag som dyker upp under väntan."""
    if not _krav_flask("check_long_poll_vantar"):
        return
    import threading
    import time
    import app as appmod
    a, bord = _ny_app()
    _kalibrera(bord, 95.0)
    t0 = time.monotonic()
    r = a.test_client().get("/api/enhet/uppdrag?vanta=0.4", headers=_enhet())
    dt = time.monotonic() - t0
    if r.status_code != 204 or not (0.35 <= dt < 2.0):
        fail(f"long-poll vanta=0,4 utan uppdrag: {r.status_code} efter {dt:.2f} s (väntat 204 efter ≈0,4 s)")
    utfall = {}

    def poll():
        t = time.monotonic()
        rr = a.test_client().get("/api/enhet/uppdrag?vanta=5", headers=_enhet())
        utfall["status"], utfall["dt"] = rr.status_code, time.monotonic() - t

    tr = threading.Thread(target=poll)
    tr.start()
    time.sleep(0.3)
    bord.begar_rorelse("admin", riktning="upp", cm=5)
    tr.join(10)
    if utfall.get("status") != 200 or utfall.get("dt", 99) > 2.0:
        fail(f"uppdrag skapat under long-poll ska levereras direkt, fick {utfall}")
    for arg, vantat in (("999", 25.0), ("-3", 0.0), ("abc", 25.0), (None, 25.0), ("10", 10.0)):
        if appmod.vanta_s(arg) != vantat:
            fail(f"vanta_s({arg!r}) = {appmod.vanta_s(arg)}, väntat {vantat}")
    if appmod.POLL_S_STANDARD != 0.2:
        fail(f"standardintervall för long-poll ska vara 0,2 s, är {appmod.POLL_S_STANDARD}")


def check_enhet_start_under_pagaende():
    if not _krav_flask("check_enhet_start_under_pagaende"):
        return
    a, bord = _ny_app()
    c = a.test_client()
    _kalibrera(bord, 95.0)
    bord.begar_rorelse("admin", riktning="upp", cm=10)
    j = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).get_json()
    r = c.post("/api/enhet/start", json={"version": "test-1"}, headers=_enhet())
    if r.status_code != 200:
        fail(f"start ska ge 200, gav {r.status_code}")
    if bord.position() is not None:
        fail(f"start under utskickat uppdrag ska göra positionen okänd, är {bord.position()}")
    if bord.uppdrag(j["id"])["status"] != "avbrutet":
        fail(f"avbrutet uppdrag ska ha status avbrutet, har {bord.uppdrag(j['id'])['status']}")
    # Sen klar på ett avbrutet uppdrag får inte återskapa en position
    c.post("/api/enhet/klar", json={"id": j["id"], "ms_kord": 1000}, headers=_enhet())
    if bord.position() is not None:
        fail("sen klar på avbrutet uppdrag ska lämna positionen okänd")
    # Start utan pågående uppdrag lämnar känd position orörd
    bord.satt_position(90.0)
    c.post("/api/enhet/start", json={"version": "test-1"}, headers=_enhet())
    if bord.position() != 90.0:
        fail("start utan pågående uppdrag ska inte röra positionen")
    if bord.enhet().get("version") != "test-1":
        fail("start ska spara firmwareversionen")


def check_enhet_auth():
    if not _krav_flask("check_enhet_auth"):
        return
    a, bord = _ny_app()
    c = a.test_client()
    for hdr in ({}, _enhet("fel"), {"Authorization": "Basic " + ENHET}, {"Authorization": "Bearer "}):
        for metod, url, kropp in (("get", "/api/enhet/uppdrag?vanta=0", None),
                                  ("post", "/api/enhet/klar", {"id": 1, "ms_kord": 1}),
                                  ("post", "/api/enhet/start", {"version": "x"}),
                                  ("get", "/api/enhet/fortsatt?id=1", None)):
            r = getattr(c, metod)(url, json=kropp, headers=hdr)
            if r.status_code != 401:
                fail(f"{metod.upper()} {url} med {hdr or 'ingen header'} ska ge 401, gav {r.status_code}")


def check_admin_auth_och_sida():
    if not _krav_flask("check_admin_auth_och_sida"):
        return
    import re
    a, bord = _ny_app()
    c = a.test_client()
    for hdr in ({}, _admin("fel"), _admin(LOSEN, "root")):
        r = c.get("/admin", headers=hdr)
        if r.status_code != 401:
            fail(f"/admin med {hdr or 'ingen auth'} ska ge 401, gav {r.status_code}")
        elif "Basic" not in r.headers.get("WWW-Authenticate", ""):
            fail("401 från /admin ska be om basic auth")
    r = c.get("/admin", headers=_admin())
    if r.status_code != 200:
        fail(f"/admin med rätt lösen ska ge 200, gav {r.status_code}")
        return
    html = r.get_data(as_text=True)
    for text in ("Skrivbordet", "okänd", "Kalibrering", "Stå", "Sitt", "Händelser", 'lang="sv"',
                 "viewport", "Kör till botten"):
        if text not in html:
            fail(f"/admin saknar '{text}'")
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    if not m:
        fail("/admin saknar csrf-fält i formulären")
        return
    csrf = m.group(1)
    # POST utan eller med fel CSRF-token nekas, även med rätt lösen
    for token in (None, "fel"):
        data = {"min_cm": "65", "max_cm": "125", "restid_upp_s": "22", "restid_ner_s": "20"}
        if token:
            data["csrf"] = token
        r = c.post("/admin/kalibrering", data=data, headers=_admin())
        if r.status_code != 400:
            fail(f"POST utan giltig csrf ska ge 400, gav {r.status_code}")
    if bord.kalibrering().giltig():
        fail("kalibrering ska inte sparas utan giltig csrf")
    # POST utan lösen nekas
    r = c.post("/admin/kalibrering", data={"csrf": csrf}, headers={})
    if r.status_code != 401:
        fail(f"POST /admin/kalibrering utan lösen ska ge 401, gav {r.status_code}")
    # Giltig kalibrering, decimalkomma godtas
    r = c.post("/admin/kalibrering", headers=_admin(), data={
        "csrf": csrf, "min_cm": "65,5", "max_cm": "125", "restid_upp_s": "22", "restid_ner_s": "20,5"})
    if r.status_code != 302:
        fail(f"giltig kalibrering ska ge 302, gav {r.status_code}")
    k = bord.kalibrering()
    if not (k.giltig() and k.min_cm == 65.5 and k.restid_ner_s == 20.5):
        fail(f"kalibreringen sparades fel: {k}")
    # Ogiltig kalibrering (max < min) sparas inte
    c.post("/admin/kalibrering", headers=_admin(), data={
        "csrf": csrf, "min_cm": "130", "max_cm": "125", "restid_upp_s": "22", "restid_ner_s": "20"})
    if bord.kalibrering().min_cm != 65.5:
        fail("ogiltig kalibrering (max < min) får inte skriva över den giltiga")
    # Manuella knappar
    r = c.post("/admin/lage", headers=_admin(), data={"csrf": csrf, "lage": "sitt"})
    p = bord.pagaende()
    if r.status_code != 302 or not p or p["lage"] != "sitt" or p["rela"] != "ner":
        fail(f"admin sitt ska skapa ett ner-uppdrag med läge sitt, fick {r.status_code} {p}")
    html = c.get("/admin", headers=_admin()).get_data(as_text=True)
    if "Pågående" not in html:
        fail("/admin ska visa pågående uppdrag")
    _slutfor(c)
    bord.satt_position(95.0)
    r = c.post("/admin/flytta", headers=_admin(), data={"csrf": csrf, "riktning": "upp", "cm": "7"})
    p = bord.pagaende()
    if not p or p["rela"] != "upp" or abs(p["begard_cm"] - 7) > 1e-9:
        fail(f"admin upp 7 cm ska skapa uppdrag, fick {p}")
    html = c.get("/admin", headers=_admin()).get_data(as_text=True)
    if "95" not in html:
        fail("/admin ska visa positionen")


def check_ett_uppdrag_i_taget():
    if not _krav_flask("check_ett_uppdrag_i_taget"):
        return
    import logik
    a, bord = _ny_app()
    c = a.test_client()
    _kalibrera(bord, 95.0)
    # Första flytten väntar i en egen tråd tills enheten rapporterat klart.
    import threading
    import time as _t
    svar1 = {}
    t1 = threading.Thread(target=lambda: svar1.update(j=a.test_client().post(
        "/api/agent/flytta", json={"uppringare": VITT, "riktning": "upp", "cm": 5}, headers=_agent()).get_json()),
        daemon=True)
    t1.start()
    for _ in range(200):
        if bord.pagaende():
            break
        _t.sleep(0.01)
    if not bord.pagaende():
        fail("första flytten ska skapa ett pågående uppdrag")
        return
    r2 = c.post("/api/agent/flytta", json={"uppringare": VITT, "riktning": "ner", "cm": 5}, headers=_agent())
    r3 = c.post("/api/agent/lage", json={"uppringare": VITT, "lage": "stå"}, headers=_agent())
    r4 = c.post("/api/agent/hojd", json={"uppringare": VITT, "cm": 80}, headers=_agent())
    for r in (r2, r3, r4):
        j = r.get_json()
        if r.status_code != 200 or j.get("ok") or j.get("sag") != logik.TEXT_ROR_SIG_REDAN:
            fail(f"ny flytt under pågående ska nekas med 'rör sig redan', fick {r.status_code} {j}")
    if bord.begar_rorelse("admin", riktning="upp", cm=5).ok:
        fail("admin ska också nekas under pågående uppdrag")
    # Även utskickat (inte bara väntande) räknas som pågående
    c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet())
    if bord.begar_rorelse("admin", riktning="upp", cm=5).ok:
        fail("nytt uppdrag under utskickat ska nekas")
    antal = bord.antal_uppdrag()
    if antal != 1:
        fail(f"exakt ett uppdrag ska finnas, finns {antal}")
    u = bord.pagaende()
    c.post("/api/enhet/klar", json={"id": u["id"], "ms_kord": u["ms"]}, headers=_enhet())
    t1.join(5)
    if svar1.get("j") != {"ok": True, "sag": logik.text_klar(100.0, 5.0, 5.0)}:
        fail(f"första flytten ska till sist svara med klar-texten, fick {svar1.get('j')}")


def check_timeout():
    """Regel 8: ingen klar inom ms + 30 s ⇒ timeout, position okänd, krok anropas."""
    if not _krav_flask("check_timeout"):
        return
    import logik
    klocka = _Klocka()
    a, bord = _ny_app(klocka)
    c = a.test_client()
    _kalibrera(bord, 95.0)
    timeouter, klara = [], []
    bord.vid_timeout = lambda u: timeouter.append(u["id"])
    bord.vid_klar = lambda u: klara.append(u["id"])
    bord.begar_rorelse("admin", riktning="ner", cm=10)       # 3333 ms
    j = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).get_json()
    klocka.t += 3.333 + 29.9
    c.get("/admin", headers=_admin())
    if bord.uppdrag(j["id"])["status"] != "utskickat" or bord.position() != 95.0:
        fail("före ms + 30 s ska uppdraget fortfarande vara utskickat")
    klocka.t += 0.2
    c.get("/admin", headers=_admin())
    if bord.uppdrag(j["id"])["status"] != "timeout":
        fail(f"efter ms + 30 s ska status vara timeout, är {bord.uppdrag(j['id'])['status']}")
    if bord.position() is not None:
        fail("timeout ska göra positionen okänd")
    if timeouter != [j["id"]]:
        fail(f"vid_timeout ska anropas exakt en gång, anropades {timeouter}")
    # Ny rörelse går nu bara via stå/sitt
    s = bord.begar_rorelse("admin", riktning="upp", cm=5)
    if s.ok or s.sag != logik.TEXT_OKAND_POSITION:
        fail(f"efter timeout ska relativ flytt nekas med okänd position, fick {s}")
    # Sen klar efter timeout återskapar inte positionen och anropar inte vid_klar
    c.post("/api/enhet/klar", json={"id": j["id"], "ms_kord": 3333}, headers=_enhet())
    if bord.position() is not None or klara:
        fail("sen klar efter timeout ska ignoreras")
    # Timeout kontrolleras också i long-pollen
    _kalibrera(bord, 95.0)
    bord.begar_rorelse("admin", riktning="ner", cm=10)
    j = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).get_json()
    klocka.t += 40
    c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet())
    if bord.uppdrag(j["id"])["status"] != "timeout":
        fail("long-pollen ska också upptäcka timeout")
    # Ett väntande uppdrag som enheten aldrig hämtar går ut, utan att positionen påverkas
    _kalibrera(bord, 95.0)
    s = bord.begar_rorelse("admin", riktning="ner", cm=10)
    klocka.t += bord.VANTAR_MAX_S + 1
    if c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).status_code != 204:
        fail("ett uppdrag som väntat för länge får inte skickas ut")
    if bord.uppdrag(s.id)["status"] != "timeout" or bord.position() != 95.0:
        fail("ohämtat uppdrag ska få timeout men lämna positionen (bordet rörde sig aldrig)")
    if len(timeouter) != 3:
        fail(f"vid_timeout ska ha anropats tre gånger totalt, {len(timeouter)}")
    # vid_klar anropas vid klar
    s = bord.begar_rorelse("admin", riktning="ner", cm=10)
    j = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).get_json()
    c.post("/api/enhet/klar", json={"id": j["id"], "ms_kord": j["ms"]}, headers=_enhet())
    c.post("/api/enhet/klar", json={"id": j["id"], "ms_kord": j["ms"]}, headers=_enhet())
    if klara != [j["id"]]:
        fail(f"vid_klar ska anropas exakt en gång per uppdrag, {klara}")


def check_agent():
    if not _krav_flask("check_agent"):
        return
    import logik
    a, bord = _ny_app()
    c = a.test_client()
    for hdr in ({}, _agent("fel"), _agent("")):
        for url, kropp in (("/api/agent/flytta", {"uppringare": VITT, "riktning": "upp", "cm": 5}),
                           ("/api/agent/lage", {"uppringare": VITT, "lage": "stå"})):
            r = c.post(url, json=kropp, headers=hdr)
            if r.status_code != 401:
                fail(f"{url} med {hdr or 'ingen hemlighet'} ska ge 401, gav {r.status_code}")
    # Okalibrerat
    j = c.post("/api/agent/lage", json={"uppringare": VITT, "lage": "stå"}, headers=_agent()).get_json()
    if j != {"ok": False, "sag": logik.TEXT_OKALIBRERAT}:
        fail(f"okalibrerat ska nekas med förklaring, fick {j}")
    _kalibrera(bord, 95.0)
    # Nummer utanför vitlistan
    for nummer in ("+4721234567", "+46701740698", "", None, "anonymous"):
        for url, kropp in (("/api/agent/flytta", {"uppringare": nummer, "riktning": "upp", "cm": 5}),
                           ("/api/agent/lage", {"uppringare": nummer, "lage": "stå"})):
            r = c.post(url, json=kropp, headers=_agent())
            j = r.get_json() or {}
            if r.status_code != 200 or j.get("ok") is not False or j.get("sag") != logik.TEXT_EJ_TILLATET:
                fail(f"{url} från {nummer!r} ska nekas med 200 och vitlistetext, fick {r.status_code} {j}")
    if bord.antal_uppdrag() != 0:
        fail("nekade nummer får inte skapa uppdrag")
    # Trasig kropp ⇒ fortfarande 200 med ok false
    r = c.post("/api/agent/flytta", data="skräp", headers=_agent())
    if r.status_code != 200 or (r.get_json() or {}).get("ok") is not False:
        fail(f"trasig kropp ska ge 200 ok false, fick {r.status_code}")
    # Ogiltig begäran från godkänt nummer ⇒ logikens nekande
    j = c.post("/api/agent/flytta", json={"uppringare": "070-1740605", "riktning": "upp", "cm": 500},
               headers=_agent()).get_json()
    if j.get("ok") is not False or "centimeter" not in j.get("sag", ""):
        fail(f"500 cm ska nekas med förklaring, fick {j}")
    # Godkänd flytt; normaliserat nummer sparas
    with _FejkEnhet(a):
        j = c.post("/api/agent/flytta", json={"uppringare": "070-1740605", "riktning": "ner", "cm": 10},
                   headers=_agent()).get_json()
    if j != {"ok": True, "sag": logik.text_klar(85.0, 10.0, 10.0)}:
        fail(f"godkänd flytt ska ge ok och klar-texten, fick {j}")
    p = bord.uppdrag(1)
    if not p or p["uppringare"] != VITT or p["begard_cm"] != 10 or abs(p["faktisk_cm"] - 10) > 1e-9:
        fail(f"uppdraget ska bära normaliserad uppringare och sträckor, {p}")
    if set(j) != {"ok", "sag"}:
        fail(f"agentsvaret ska bara ha ok och sag, har {sorted(j)}")


def check_inget_dygnstak():
    """Beslut 2026-10-14: inget tak — 100 rörelser i rad samma dygn godtas och räknas."""
    if not _krav_flask("check_inget_dygnstak"):
        return
    import logik
    if hasattr(logik, "DYGNSTAK") or hasattr(logik, "inom_dygnstak") or hasattr(logik, "TEXT_DYGNSTAK"):
        fail("logik ska inte längre ha något dygnstak")
    klocka = _Klocka()
    a, bord = _ny_app(klocka)
    c = a.test_client()
    _kalibrera(bord, 95.0)
    with _FejkEnhet(a):
        for i in range(100):
            riktning = "upp" if i % 2 == 0 else "ner"
            j = c.post("/api/agent/flytta", json={"uppringare": VITT, "riktning": riktning, "cm": 2},
                       headers=_agent()).get_json()
            if not j.get("ok"):
                fail(f"rörelse {i + 1} ska godtas utan tak, fick {j}")
                return
            klocka.t += 30
    if bord.dygn()["rorelser"] != 100:
        fail(f"dygnsräknaren ska stå på 100, står på {bord.dygn()['rorelser']}")



def check_tidtagning():
    """Kalibrering: kör till botten, start upp, stopp, uppmätt restid, spara."""
    if not _krav_flask("check_tidtagning"):
        return
    import re
    klocka = _Klocka()
    a, bord = _ny_app(klocka)
    c = a.test_client()
    html = c.get("/admin", headers=_admin()).get_data(as_text=True)
    csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
    # Kör till botten fungerar okalibrerat
    r = c.post("/admin/botten", headers=_admin(), data={"csrf": csrf})
    u = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).get_json() or {}
    if r.status_code != 302 or u.get("rela") != "ner" or u.get("ms") != bord.MAX_TIDTAGNING_MS:
        fail(f"kör till botten ska ge ner-uppdrag med {bord.MAX_TIDTAGNING_MS} ms, fick {u}")
    c.post("/api/enhet/klar", json={"id": u["id"], "ms_kord": 40000}, headers=_enhet())
    # Start upp
    c.post("/admin/tidtagning/start", headers=_admin(), data={"csrf": csrf, "riktning": "upp"})
    u = c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet()).get_json() or {}
    if u.get("rela") != "upp" or u.get("tidtagning") is not True or u.get("ms") != bord.MAX_TIDTAGNING_MS:
        fail(f"start upp ska ge tidtagningsuppdrag upp, fick {u}")
        return
    klocka.t += 5
    f = c.get(f"/api/enhet/fortsatt?id={u['id']}&gatt=5000", headers=_enhet()).get_json()
    if f != {"fortsatt": True}:
        fail(f"fortsatt före stopp ska vara true, var {f}")
    klocka.t += 16.0
    r = c.post("/admin/tidtagning/stopp", headers=_admin(), data={"csrf": csrf})
    if r.status_code != 302:
        fail(f"stopp ska ge 302, gav {r.status_code}")
    klocka.t += 0.4   # enheten frågar 0,4 s efter klicket
    f = c.get(f"/api/enhet/fortsatt?id={u['id']}&gatt=21400", headers=_enhet()).get_json()
    if f != {"fortsatt": False}:
        fail(f"fortsatt efter stopp ska vara false, var {f}")
    c.post("/api/enhet/klar", json={"id": u["id"], "ms_kord": 21450}, headers=_enhet())
    matt = bord.uppdrag(u["id"])["uppmatt_ms"]
    # Restiden = reläets tid på enheten minus tiden mellan klick och att enheten fick beskedet
    if matt is None or abs(matt - 21050) > 1:
        fail(f"uppmätt restid ska vara 21450 − 400 = 21050 ms, är {matt}")
    html = c.get("/admin", headers=_admin()).get_data(as_text=True)
    if "21,1 s" not in html and "21,05 s" not in html:
        fail("adminsidan ska visa uppmätt restid med decimalkomma (21,1 s)")
    if bord.position() is not None:
        fail("efter tidtagning ska positionen vara okänd tills stå/sitt körts")
    c.post("/admin/tidtagning/spara", headers=_admin(), data={"csrf": csrf, "id": str(u["id"])})
    if bord.kalibrering().restid_upp_s != 21.05:
        fail(f"sparad restid upp ska vara 21,05 s, är {bord.kalibrering().restid_upp_s}")
    # Fortsatt för okänt eller avslutat id ⇒ false (enheten släpper reläet)
    for id_ in (u["id"], 99999, "abc"):
        f = c.get(f"/api/enhet/fortsatt?id={id_}", headers=_enhet()).get_json()
        if f != {"fortsatt": False}:
            fail(f"fortsatt för id {id_} ska vara false, var {f}")
    # Stopp utan pågående tidtagning gör inget farligt
    if c.post("/admin/tidtagning/stopp", headers=_admin(), data={"csrf": csrf}).status_code != 302:
        fail("stopp utan pågående tidtagning ska ge 302")


# --- ESP32 (körs på host med Pin-mock och falsk klocka) ---

def _rela_modul():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rela_esp32", os.path.join(_ESP32, "rela.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Logg:
    def __init__(self):
        self.andringar = []
        self.pinnar = {}
        self.brott = []

    def kontroll(self, nar):
        if all(p.v for p in self.pinnar.values()) and len(self.pinnar) == 2:
            self.brott.append(nar)


class _FalskPin:
    def __init__(self, logg, namn, start=1):
        self.logg, self.namn, self.v = logg, namn, start   # startar HÖG för att bevisa att init sänker
        logg.pinnar[namn] = self

    def value(self, v=None):
        if v is None:
            return self.v
        self.v = 1 if v else 0
        self.logg.andringar.append((self.namn, self.v))
        self.logg.kontroll(f"{self.namn}={self.v}")

    on = lambda self: self.value(1)
    off = lambda self: self.value(0)


class _FalskTid:
    def __init__(self, logg=None, kasta_efter=None):
        self.t = 0
        self.sovt = []
        self.kasta_efter = kasta_efter

    def tid_ms(self):
        return self.t

    def sov_ms(self, ms):
        self.sovt.append(ms)
        if self.kasta_efter is not None and sum(self.sovt) > self.kasta_efter:
            raise OSError("simulerat fel mitt i körningen")
        self.t += ms


def _ny_rela(m, max_ms=40000, kasta_efter=None):
    logg = _Logg()
    upp, ner = _FalskPin(logg, "upp"), _FalskPin(logg, "ner")
    tid = _FalskTid(kasta_efter=kasta_efter)
    r = m.Rela(upp, ner, max_ms=max_ms, tid_ms=tid.tid_ms, sov_ms=tid.sov_ms)
    return r, logg, upp, ner, tid


def check_esp32_rela():
    try:
        m = _rela_modul()
    except Exception as e:
        fail(f"esp32/rela.py går inte att ladda på host: {e}")
        return
    # Låga efter init
    r, logg, upp, ner, tid = _ny_rela(m)
    if upp.v or ner.v:
        fail("pinnarna ska vara låga efter init")
    # Vanlig körning
    ms = r.kor(1, "upp", 1500)
    if ms != 1500 or sum(tid.sovt) != 1500:
        fail(f"kor 1500 ms ska köra 1500 ms, körde {ms} (sov {sum(tid.sovt)})")
    if ("upp", 1) not in logg.andringar or upp.v or ner.v:
        fail(f"upp ska ha dragits och släppts, logg {logg.andringar}")
    # Den andra släpps alltid först
    logg.andringar.clear()
    r.kor(2, "ner", 100)
    if logg.andringar[:2] != [("upp", 0), ("ner", 1)]:
        fail(f"kor ner ska släppa upp innan ner dras, logg {logg.andringar}")
    # Dubbel-id körs inte
    logg.andringar.clear()
    fore = sum(tid.sovt)
    if r.kor(2, "ner", 100) is not None or logg.andringar or sum(tid.sovt) != fore:
        fail("samma id får inte köras två gånger")
    # Kapning
    r, logg, upp, ner, tid = _ny_rela(m, max_ms=40000)
    ms = r.kor(3, "ner", 999999)
    if ms != 40000 or sum(tid.sovt) > 40000:
        fail(f"ms > MAX_MS ska kapas till 40000, körde {ms} (sov {sum(tid.sovt)})")
    # Ogiltigt
    for rela, ms in (("fram", 100), ("upp", -5), ("upp", "x")):
        r, logg, upp, ner, tid = _ny_rela(m)
        try:
            res = r.kor(10, rela, ms)
        except (ValueError, TypeError):
            res = None
        if upp.v or ner.v or sum(tid.sovt) > 0 and rela == "fram":
            fail(f"kor({rela!r}, {ms!r}) får inte lämna ett relä draget")
        if rela == "upp" and ms == -5 and res not in (0, None):
            fail(f"negativ ms ska inte köra, körde {res}")
    # Undantag mitt i körningen ⇒ båda låga, id förbrukat
    r, logg, upp, ner, tid = _ny_rela(m, kasta_efter=200)
    try:
        r.kor(5, "upp", 5000)
        fail("simulerat fel ska propageras ut ur kor")
    except OSError:
        pass
    if upp.v or ner.v:
        fail("efter undantag mitt i körningen ska båda reläerna vara släppta")
    tid.kasta_efter = None
    if r.kor(5, "upp", 5000) is not None:
        fail("ett id som avbröts av ett fel får inte köras om (bordet har redan rört sig)")
    # Tidtagning med stopp
    r, logg, upp, ner, tid = _ny_rela(m)
    svar = iter([True, True, True, False])
    ms = r.kor_med_stopp(6, "upp", lambda gatt: next(svar), bit_ms=500)
    if ms != 2000 or upp.v or ner.v:
        fail(f"kor_med_stopp ska stanna efter fyra frågor (2000 ms), körde {ms}")
    r, logg, upp, ner, tid = _ny_rela(m, max_ms=3000)
    ms = r.kor_med_stopp(7, "ner", lambda gatt: True, bit_ms=500)
    if ms != 3000 or upp.v or ner.v:
        fail(f"kor_med_stopp ska kapas vid MAX_MS 3000, körde {ms}")

    def kastar(gatt):
        raise OSError("nätet borta")
    r, logg, upp, ner, tid = _ny_rela(m)
    ms = r.kor_med_stopp(8, "upp", kastar, bit_ms=500)
    if upp.v or ner.v or ms != 500:
        fail(f"nätfel under tidtagning ska släppa reläet direkt (efter 500 ms), körde {ms}")
    if r.kor_med_stopp(8, "upp", lambda g: True) is not None:
        fail("kor_med_stopp ska också ignorera dubbel-id")
    # Slumpsvep: aldrig båda höga, alltid låga efteråt
    import random
    rnd = random.Random(1)
    r, logg, upp, ner, tid = _ny_rela(m, max_ms=5000)
    for i in range(500):
        val = rnd.random()
        try:
            if val < 0.6:
                r.kor(rnd.choice([i, i, i - 1]), rnd.choice(["upp", "ner", "x"]), rnd.randint(-100, 9000))
            else:
                n = rnd.randint(0, 12)
                r.kor_med_stopp(rnd.choice([i, i - 1]), rnd.choice(["upp", "ner"]),
                                lambda g, n=[n]: (n.__setitem__(0, n[0] - 1) or n[0] > 0), bit_ms=500)
        except (ValueError, TypeError):
            pass
        if upp.v or ner.v:
            fail(f"slumpsvep steg {i}: relä kvar draget efter operation")
            break
    if logg.brott:
        fail(f"båda reläerna höga samtidigt: {logg.brott[:5]}")


def check_esp32_filer():
    for namn in ("main.py", "rela.py", "config.example.py"):
        sokvag = os.path.join(_ESP32, namn)
        if not os.path.exists(sokvag):
            fail(f"esp32/{namn} saknas")
            continue
        try:
            compile(open(sokvag, encoding="utf-8").read(), sokvag, "exec")
        except SyntaxError as e:
            fail(f"esp32/{namn} kompilerar inte: {e}")
    try:
        ex = open(os.path.join(_ESP32, "config.example.py"), encoding="utf-8").read()
        for nyckel in ("WIFI_SSID", "WIFI_LOSEN", "SERVER", "ENHET_TOKEN", "MAX_MS", "AKTIVT_HOG"):
            if nyckel not in ex:
                fail(f"config.example.py saknar {nyckel}")
        if 'SERVER = "https://bord.example.com"' not in ex or "MAX_MS = 40000" not in ex \
                or "AKTIVT_HOG = True" not in ex:
            fail("config.example.py ska ha SERVER https://bord.example.com, MAX_MS 40000 och AKTIVT_HOG True")
        if os.path.exists(os.path.join(_ESP32, "config.py")) and _ar_sparad("esp32/config.py"):
            fail("esp32/config.py är incheckad — den innehåller WiFi-uppgifter och token")
        main = open(os.path.join(_ESP32, "main.py"), encoding="utf-8").read()
        for bit in ("Pin(5", "Pin(6", "machine.reset", "/api/enhet/start", "/api/enhet/uppdrag",
                    "/api/enhet/klar", "/api/enhet/fortsatt"):
            if bit not in main:
                fail(f"esp32/main.py saknar '{bit}'")
        if "Pin(5, Pin.OUT, value=SLAPPT)" not in main or "Pin(6, Pin.OUT, value=SLAPPT)" not in main \
                or "SLAPPT = 0 if AKTIVT_HOG else 1" not in main:
            fail("esp32/main.py ska skapa pinnarna i släppt läge från första stund (value=SLAPPT)")
        if "aktivt_hog=AKTIVT_HOG" not in main:
            fail("esp32/main.py ska skicka AKTIVT_HOG vidare till Rela")
        for strap in ("Pin(8", "Pin(9", "Pin(2,"):
            if strap in main:
                fail(f"esp32/main.py använder strapping-pinne {strap})")
    except OSError:
        pass
    ign = open(os.path.join(_ROT, ".gitignore"), encoding="utf-8").read().split()
    for rad in ("esp32/config.py", "data/", ".venv/", "__pycache__/", ".env", "*.db",
                "brygga/agent_id.txt", "deploy/deploy.env"):
        if rad not in ign:
            fail(f".gitignore saknar {rad}")


def _kontrast(a, b):
    def lum(h):
        h = h.lstrip("#")
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    l1, l2 = sorted((lum(a), lum(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def check_admin_kontrast():
    """Adminsidans färgpar, ljust och mörkt tema, ska klara WCAG AA (4,5:1)."""
    import re
    html = open(os.path.join(_SERVER, "templates", "admin.html"), encoding="utf-8").read()
    block = re.findall(r":root\s*\{([^}]*)\}", html)
    if len(block) < 2:
        fail("admin.html ska definiera färgtoken för ljust och mörkt tema i :root")
        return
    par = (("text", "bg"), ("text", "kort"), ("dampad", "kort"), ("dampad", "bg"),
           ("knapp-text", "knapp"), ("knapp-text", "stopp"), ("fel", "bg"), ("ok", "bg"),
           ("text", "bg"))
    for namn, b in zip(("ljust", "mörkt"), block[:2]):
        t = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", b))
        for fg, bg in par:
            if fg not in t or bg not in t:
                fail(f"{namn} tema saknar --{fg} eller --{bg}")
                continue
            k = _kontrast(t[fg], t[bg])
            if k < 4.5:
                fail(f"{namn} tema: --{fg} på --{bg} har kontrast {k:.2f}:1 (< 4,5)")


# --- Steg 7: svaret ges i samtalet — servern ringer aldrig ut ---

ANNAT_VITT = "+46701740606"


class _FejkEnhet:
    """Enheten i en egen tråd: hämtar uppdrag och rapporterar klart medan
    agentanropet väntar. ms_kord(u) bestämmer rapporterad körtid (None ⇒ rapportera
    aldrig, som en enhet som tappat nätet). hamta=False ⇒ hämtar aldrig uppdrag."""

    def __init__(self, a, ms_kord=lambda u: u["ms"], hamta=True):
        import threading
        self.c = a.test_client()
        self.ms_kord = ms_kord
        self.hamta = hamta
        self.stopp = threading.Event()
        self.hamtade = []
        self.t = threading.Thread(target=self._kor, daemon=True)

    def _kor(self):
        import time as _t
        while not self.stopp.is_set():
            if self.hamta:
                r = self.c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet())
                if r.status_code == 200:
                    u = r.get_json()
                    self.hamtade.append(u)
                    ms = self.ms_kord(u)
                    if ms is not None:
                        self.c.post("/api/enhet/klar", json={"id": u["id"], "ms_kord": ms}, headers=_enhet())
            _t.sleep(0.01)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.stopp.set()
        self.t.join(5)


def _handelsetext(bord, n=50):
    return "\n".join(h["text"] for h in bord.handelser(n))


class _Natspion:
    """Ersätter socket-anslutningar under en kontroll: varje försök att ansluta
    utåt registreras och nekas. Bevisar att inget i flödet ringer ut."""

    def __enter__(self):
        import socket
        self.forsok = []
        self._orig = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
        spion = self

        def connect(sock, adr, *a, **kw):
            spion.forsok.append(adr)
            raise OSError("nätet avstängt i verify")

        def create_connection(adr, *a, **kw):
            spion.forsok.append(adr)
            raise OSError("nätet avstängt i verify")
        socket.socket.connect = connect
        socket.socket.connect_ex = connect
        socket.create_connection = create_connection
        return self

    def __exit__(self, *exc):
        import socket
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = self._orig


def check_ingen_utringning_i_koden():
    """Servern får inte kunna ringa ut: inga 46elks-/TTS-moduler, inga utgående
    HTTP-klienter, ingen ljud- eller testsamtalsväg."""
    import ast
    for namn in ("elks.py", "tts.py"):
        if os.path.exists(os.path.join(_SERVER, namn)):
            fail(f"server/{namn} ska vara borttagen — servern ringer aldrig ut")
    forbjudna_moduler = {"elks", "tts", "urllib.request", "http.client", "requests", "httpx",
                         "aiohttp", "socket", "smtplib"}
    forbjuden_text = ("46elks.com", "/a1/calls", "ring_upp", "text-to-speech", "ateruppring(",
                      "/ljud/", "urlopen")
    for rot, mappar, filer in os.walk(_SERVER):
        mappar[:] = [m for m in mappar if m not in ("__pycache__", ".venv")]
        for f in filer:
            if not f.endswith((".py", ".html")):
                continue
            sokvag = os.path.join(rot, f)
            text = open(sokvag, encoding="utf-8").read()
            rel = os.path.relpath(sokvag, _ROT)
            for bit in forbjuden_text:
                if bit in text:
                    fail(f"{rel} innehåller '{bit}' — ingen kodväg får kunna ringa ut")
            if f.endswith(".html") and "återuppringning" in text.lower():
                fail(f"{rel} nämner återuppringning — den finns inte längre")
            if f.endswith(".py"):
                for nod in ast.walk(ast.parse(text)):
                    moduler = []
                    if isinstance(nod, ast.Import):
                        moduler = [x.name for x in nod.names]
                    elif isinstance(nod, ast.ImportFrom) and nod.module:
                        moduler = [nod.module] + [f"{nod.module}.{x.name}" for x in nod.names]
                    for m in moduler:
                        if m in forbjudna_moduler or m.split(".")[0] in ("elks", "tts", "requests", "httpx"):
                            fail(f"{rel} importerar {m} — servern ska inte ha någon utgående klient")
    if not _krav_flask("check_ingen_utringning_i_koden"):
        return
    a, bord = _ny_app()
    for modul in ("elks", "tts"):
        if modul in sys.modules:
            fail(f"modulen {modul} är laddad efter att appen skapats")
    regler = {r.rule for r in a.url_map.iter_rules()}
    for r in regler:
        if r.startswith("/ljud") or "ateruppringning" in r:
            fail(f"routen {r} ska vara borttagen")
    for attr in ("ateruppring", "ateruppring_konfigurerad", "http_post"):
        if hasattr(bord, attr):
            fail(f"Bord.{attr} ska vara borttagen")
    html = a.test_client().get("/admin", headers=_admin()).get_data(as_text=True)
    if "återuppringning" in html.lower():
        fail("/admin nämner återuppringning")


def check_agent_svar_i_samtalet():
    """Agentanropet väntar tills enheten rapporterat klart och svarar med resultatet."""
    if not _krav_flask("check_agent_svar_i_samtalet"):
        return
    import logik
    a, bord = _ny_app()
    c = a.test_client()
    _kalibrera(bord, 95.0)
    with _Natspion() as nat:
        with _FejkEnhet(a):
            j = c.post("/api/agent/flytta", json={"uppringare": VITT, "riktning": "ner", "cm": 10},
                       headers=_agent()).get_json()
            if j != {"ok": True, "sag": logik.text_klar(85.0, 10.0, 10.0)}:
                fail(f"flytta ner 10 ska svara med klar-texten för 85 cm, fick {j}")
            # Klippt relativ flytt nämner att det bara gick en bit
            j = c.post("/api/agent/flytta", json={"uppringare": VITT, "riktning": "ner", "cm": 30},
                       headers=_agent()).get_json()
            if j != {"ok": True, "sag": "Klart. 65 centimeter, längre går det inte."}:
                fail(f"ner 30 från 85 ska svara 'Klart. 65 centimeter, längre går det inte.', fick {j}")
            # Absolut höjd
            j = c.post("/api/agent/hojd", json={"uppringare": ANNAT_VITT, "cm": 100}, headers=_agent()).get_json()
            if j != {"ok": True, "sag": logik.text_klar(100.0)}:
                fail(f"höjd 100 ska svara {logik.text_klar(100.0)!r}, fick {j}")
            # Stå från okänd position ⇒ exakt max
            bord.satt_position(None)
            j = c.post("/api/agent/lage", json={"uppringare": VITT, "lage": "stå"}, headers=_agent()).get_json()
            if j != {"ok": True, "sag": logik.text_klar(125.0)}:
                fail(f"stå från okänd ska svara {logik.text_klar(125.0)!r}, fick {j}")
        # Sitt som körde för kort ⇒ okänd position, inget påhittat cm-tal
        with _FejkEnhet(a, ms_kord=lambda u: 1000):
            j = c.post("/api/agent/lage", json={"uppringare": VITT, "lage": "sitt"}, headers=_agent()).get_json()
            if j != {"ok": True, "sag": logik.TEXT_KLAR_OKAND_POSITION}:
                fail(f"för kort sitt ska svara TEXT_KLAR_OKAND_POSITION, fick {j}")
    if nat.forsok:
        fail(f"agentflödet försökte ansluta utåt: {nat.forsok}")
    if bord.dygn()["ateruppringningar"] != 0:
        fail("inga återuppringningar får räknas")
    if "svar i samtalet" not in _handelsetext(bord):
        fail("klart agentuppdrag ska loggas med 'svar i samtalet'")


def check_agent_vantar_pa_enheten():
    """Svaret kommer först när enheten rapporterat klart, inte när uppdraget skapats."""
    if not _krav_flask("check_agent_vantar_pa_enheten"):
        return
    import time as _t
    import logik
    a, bord = _ny_app()
    c = a.test_client()
    _kalibrera(bord, 95.0)

    def sent(u):
        _t.sleep(0.5)
        return u["ms"]
    with _FejkEnhet(a, ms_kord=sent):
        t0 = _t.monotonic()
        j = c.post("/api/agent/flytta", json={"uppringare": VITT, "riktning": "upp", "cm": 5},
                   headers=_agent()).get_json()
        tid = _t.monotonic() - t0
    if tid < 0.45:
        fail(f"agentanropet svarade efter {tid:.2f} s — innan enheten rapporterat klart")
    if j.get("sag") != logik.text_klar(100.0, 5.0, 5.0):
        fail(f"svaret ska vara klar-texten för 100 cm, var {j}")


def check_agent_timeout_i_samtalet():
    """Ingen klar ⇒ 'svarar inte' i samtalet, inom ms + extra; aldrig ett utgående samtal."""
    if not _krav_flask("check_agent_timeout_i_samtalet"):
        return
    import threading
    import time as _t
    import logik
    # 1. Enheten hämtar men rapporterar aldrig: väggklockans gräns ms + extra slår till
    a, bord = _ny_app(AGENT_VANTA_EXTRA_S=0.3)
    c = a.test_client()
    _kalibrera(bord, 95.0)
    with _Natspion() as nat:
        with _FejkEnhet(a, ms_kord=lambda u: None) as enh:
            t0 = _t.monotonic()
            j = c.post("/api/agent/flytta", json={"uppringare": VITT, "riktning": "upp", "cm": 1},
                       headers=_agent()).get_json()
            tid = _t.monotonic() - t0
    ms = enh.hamtade[0]["ms"] if enh.hamtade else 0
    if j != {"ok": True, "sag": logik.TEXT_SVARAR_INTE}:
        fail(f"ingen klar ska ge 'svarar inte' i samtalet, fick {j}")
    if not (ms / 1000 + 0.3 - 0.05 <= tid <= ms / 1000 + 0.3 + 1.0):
        fail(f"väntan ska vara ms + extra ≈ {ms / 1000 + 0.3:.2f} s, var {tid:.2f} s")
    # 2. Regel 8 via KLOCKA: timeout upptäcks under väntan, långt före väggklockans gräns
    klocka = _Klocka()
    a, bord = _ny_app(klocka, AGENT_VANTA_EXTRA_S=10)
    c = a.test_client()
    _kalibrera(bord, 95.0)
    stopp = threading.Event()

    def skjut_klockan():
        while not stopp.is_set():
            klocka.t += 5
            _t.sleep(0.02)
    t = threading.Thread(target=skjut_klockan, daemon=True)
    with _Natspion() as nat2:
        with _FejkEnhet(a, hamta=False):
            t.start()
            t0 = _t.monotonic()
            j = c.post("/api/agent/lage", json={"uppringare": VITT, "lage": "stå"}, headers=_agent()).get_json()
            tid = _t.monotonic() - t0
            stopp.set()
            t.join(2)
    if j != {"ok": True, "sag": logik.TEXT_SVARAR_INTE} or tid > 5:
        fail(f"timeout enligt regel 8 ska ge 'svarar inte' utan att vänta ut extra, fick {j} efter {tid:.2f} s")
    if nat.forsok or nat2.forsok:
        fail(f"timeout försökte ansluta utåt: {nat.forsok + nat2.forsok}")
    if "svar i samtalet" not in _handelsetext(bord):
        fail("timeout för agentuppdrag ska loggas med 'svar i samtalet'")


def check_admin_ingen_utringning():
    """Admin-, kalibrerings- och timeoutflöden gör inga nätanrop."""
    if not _krav_flask("check_admin_ingen_utringning"):
        return
    klocka = _Klocka()
    a, bord = _ny_app(klocka)
    c = a.test_client()
    _kalibrera(bord, 95.0)
    with _Natspion() as nat:
        bord.begar_rorelse("admin", riktning="ner", cm=10)
        _slutfor(c)
        bord.till_botten()
        _slutfor(c)
        bord.begar_rorelse("admin", lage="stå")
        c.get("/api/enhet/uppdrag?vanta=0", headers=_enhet())
        klocka.t += 120
        bord.stada()
    if nat.forsok:
        fail(f"adminflödet försökte ansluta utåt: {nat.forsok}")


# --- Steg 6: absolut höjd i servern ---

def check_agent_hojd():
    """POST /api/agent/hojd {uppringare, cm}: samma auth, vitlista och svarsform som /flytta."""
    if not _krav_flask("check_agent_hojd"):
        return
    import logik
    a, bord = _ny_app()
    c = a.test_client()
    url = "/api/agent/hojd"
    for hdr in ({}, _agent("fel"), _agent("")):
        r = c.post(url, json={"uppringare": VITT, "cm": 100}, headers=hdr)
        if r.status_code != 401:
            fail(f"{url} med {hdr or 'ingen hemlighet'} ska ge 401, gav {r.status_code}")
    j = c.post(url, json={"uppringare": VITT, "cm": 100}, headers=_agent()).get_json() or {}
    if j != {"ok": False, "sag": logik.TEXT_OKALIBRERAT}:
        fail(f"{url} okalibrerat ska nekas med förklaring, fick {j}")
    _kalibrera(bord, 95.0)                       # 65–125 cm, 22/20 s
    for nummer in ("+4721234567", "+46701740698", "", None, "anonymous"):
        r = c.post(url, json={"uppringare": nummer, "cm": 100}, headers=_agent())
        j = r.get_json() or {}
        if r.status_code != 200 or j != {"ok": False, "sag": logik.TEXT_EJ_TILLATET}:
            fail(f"{url} från {nummer!r} ska nekas med vitlistetext, fick {r.status_code} {j}")
    r = c.post(url, data="skräp", headers=_agent())
    if r.status_code != 200 or (r.get_json() or {}).get("ok") is not False:
        fail(f"{url} med trasig kropp ska ge 200 ok false, fick {r.status_code}")
    if bord.antal_uppdrag() != 0:
        fail("nekade höjdanrop får inte skapa uppdrag")
    # Tidsstyrt mål inne i intervallet. begar_rorelse blockerar inte; endpointens
    # väntan på enheten prövas i check_agent_svar_i_samtalet.
    s = bord.begar_rorelse("agent", uppringare="070-1740605", hojd=100)
    if not s.ok or s.sag != "Jo.":
        fail(f"höjd 100 från 95 ska godtas med 'Jo.', fick {s}")
    p = bord.pagaende()
    k = bord.kalibrering()
    if not p or p["rela"] != "upp" or p["lage"] or p["uppringare"] != VITT \
            or abs(p["faktisk_cm"] - 5) > 1e-9 or p["ms"] != logik.cm_till_ms(k, "upp", 5):
        fail(f"höjd 100 från 95 ska ge upp-uppdrag 5 cm utan läge, fick {p}")
    # Under pågående ⇒ rör sig redan
    j = c.post(url, json={"uppringare": VITT, "cm": 90}, headers=_agent()).get_json() or {}
    if j != {"ok": False, "sag": logik.TEXT_ROR_SIG_REDAN}:
        fail(f"höjd under pågående ska nekas med 'rör sig redan', fick {j}")
    _slutfor(c)
    if bord.position() is None or abs(bord.position() - 100) > 0.1:
        fail(f"efter höjd 100 ska positionen vara ≈100, är {bord.position()}")
    # Redan där
    j = c.post(url, json={"uppringare": VITT, "cm": "100"}, headers=_agent()).get_json() or {}
    if j != {"ok": False, "sag": "Står redan på 100."}:
        fail(f"höjd 100 när bordet står på 100 ska nekas, fick {j}")
    # Över max ⇒ klipps, körs som stå och nollställer exakt
    s = bord.begar_rorelse("agent", uppringare=VITT, hojd=140)
    if not s.ok or s.sag != "Går bara till 125.":
        fail(f"höjd 140 ska klippas och säga 'Går bara till 125.', fick {s}")
    p = bord.pagaende()
    if not p or p["lage"] != "stå" or p["rela"] != "upp" or p["ms"] != logik.avrunda((22 + logik.MARGINAL_S) * 1000):
        fail(f"höjd 140 ska bli ett stå-uppdrag med full restid + marginal, fick {p}")
    else:
        _slutfor(c)
        if bord.position() != 125.0:
            fail(f"efter klippt höjd mot max ska positionen vara exakt 125, är {bord.position()}")
    # Okänd position ⇒ nekas
    bord.satt_position(None)
    j = c.post(url, json={"uppringare": VITT, "cm": 100}, headers=_agent()).get_json() or {}
    if j != {"ok": False, "sag": logik.TEXT_OKAND_POSITION}:
        fail(f"höjd från okänd position ska nekas med TEXT_OKAND_POSITION, fick {j}")


def check_admin_hojd():
    if not _krav_flask("check_admin_hojd"):
        return
    import re
    a, bord = _ny_app()
    c = a.test_client()
    _kalibrera(bord, 95.0)
    html = c.get("/admin", headers=_admin()).get_data(as_text=True)
    if "Ställ på höjd (cm)" not in html:
        fail("/admin saknar fältet 'Ställ på höjd (cm)'")
    form = re.search(r'<form method="post" action="/admin/hojd">(.*?)</form>', html, re.S)
    if not form:
        fail("/admin saknar formulär mot /admin/hojd")
        return
    if 'name="csrf"' not in form.group(1) or 'name="cm"' not in form.group(1):
        fail("höjdformuläret ska ha csrf- och cm-fält")
    csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
    r = c.post("/admin/hojd", data={"cm": "100"}, headers=_admin())
    if r.status_code != 400:
        fail(f"POST /admin/hojd utan csrf ska ge 400, gav {r.status_code}")
    r = c.post("/admin/hojd", data={"cm": "100", "csrf": csrf}, headers={})
    if r.status_code != 401:
        fail(f"POST /admin/hojd utan lösen ska ge 401, gav {r.status_code}")
    if bord.antal_uppdrag() != 0:
        fail("nekade adminhöjder får inte skapa uppdrag")
    r = c.post("/admin/hojd", data={"cm": "105,5", "csrf": csrf}, headers=_admin())
    p = bord.pagaende()
    if r.status_code != 302 or not p or p["rela"] != "upp" or abs(p["faktisk_cm"] - 10.5) > 1e-9:
        fail(f"admin höjd 105,5 från 95 ska ge upp-uppdrag 10,5 cm, fick {r.status_code} {p}")


# --- Steg 8: profiler ("spara min höjd som Björn") ---

def _profil(nyckel, namn, cm):
    return {"nyckel": nyckel, "namn": namn, "cm": cm}


def check_profil_normalisering():
    lg = _logik()
    for f in ("profil_visningsnamn", "profilnyckel", "hitta_profilnyckel", "halv_cm", "genitiv",
              "planera_spara_profil", "valj_profil", "planera_profil", "text_profil_klar", "text_profiler"):
        if not hasattr(lg, f):
            fail(f"logik.{f} saknas")
            return
    if lg.MAX_PROFILNAMN != 40 or lg.MAX_PROFILER != 50:
        fail(f"MAX_PROFILNAMN/MAX_PROFILER ska vara 40/50, är {lg.MAX_PROFILNAMN}/{lg.MAX_PROFILER}")
    # Samma nyckel oavsett blanksteg och skiftläge
    for namn in ("  BJÖRN ", "björn", "Björn", "bJöRn", "Björn.", "\tBjörn\n"):
        if lg.profilnyckel(namn) != "björn":
            fail(f"profilnyckel({namn!r}) = {lg.profilnyckel(namn)!r}, väntat 'björn'")
    if lg.profilnyckel("  Anna   Karin ") != "anna karin":
        fail(f"blanksteg ska kollapsas: {lg.profilnyckel('  Anna   Karin ')!r}")
    if lg.profil_visningsnamn("  Anna   Karin ") != "Anna Karin":
        fail(f"visningsnamnet ska trimmas och kollapsas: {lg.profil_visningsnamn('  Anna   Karin ')!r}")
    # Genitiv-s tas bort bara när profilen utan s finns
    fall = [
        ("Björns", {"björn"}, "björn"),
        ("BJÖRNS", {"björn"}, "björn"),
        ("Björns höjd", {"björn"}, "björn"),
        ("björn", {"björn"}, "björn"),
        ("  BJÖRN ", {"björn"}, "björn"),
        ("Björns", set(), None),
        ("Björns", {"björns"}, "björns"),
        ("Hans", {"hans", "han"}, "hans"),          # exakt träff går före genitiv
        ("Hans", {"han"}, "han"),
        ("Anna-Karin", {"anna"}, None),
        ("Anna", {"anna-karin"}, None),
        ("Anna-Karins", {"anna-karin", "anna"}, "anna-karin"),
        ("Ann", {"anna"}, None),
        ("s", {""}, None),
        ("", {"björn"}, None),
        (None, {"björn"}, None),
    ]
    for namn, nycklar, vantat in fall:
        fick = lg.hitta_profilnyckel(namn, nycklar)
        if fick != vantat:
            fail(f"hitta_profilnyckel({namn!r}, {sorted(nycklar)}) = {fick!r}, väntat {vantat!r}")
    if lg.profilnyckel("Björns") != "björns":
        fail("profilnyckel får inte själv ta bort genitiv-s — bara uppslaget mot befintliga")
    # Längd och bokstäver
    if lg.profil_visningsnamn("A" * 40) != "A" * 40:
        fail("40 tecken ska godtas")
    for ogiltigt in ("A" * 41, "", "   ", "123", "1-2", ".", None, 5, True, ["Björn"]):
        if lg.profil_visningsnamn(ogiltigt) is not None or lg.profilnyckel(ogiltigt) is not None:
            fail(f"profilnamnet {ogiltigt!r} ska nekas")
    for giltigt in ("Ö", "Rum 3", "Anna-Karin", "Åsa"):
        if lg.profil_visningsnamn(giltigt) != giltigt:
            fail(f"profilnamnet {giltigt!r} ska godtas oförändrat")
    # Halvcentimeter, halva uppåt
    for x, v in ((98.0, 98.0), (98.24, 98.0), (98.25, 98.5), (98.74, 98.5), (98.75, 99.0), (97.6, 97.5)):
        if lg.halv_cm(x) != v:
            fail(f"halv_cm({x}) = {lg.halv_cm(x)}, väntat {v}")
    for namn, v in (("Björn", "Björns"), ("Hans", "Hans"), ("Max", "Max"), ("Liz", "Liz"), ("Anna", "Annas")):
        if lg.genitiv(namn) != v:
            fail(f"genitiv({namn!r}) = {lg.genitiv(namn)!r}, väntat {v!r}")


def check_profil_svep():
    """Profilhöjd ger exakt samma plan som till_hojd(cm): sparade höjder 62..127 i
    0,5-steg × flera startpositioner × två kalibreringar. Och sparad höjd = aktuell
    position avrundad till 0,5 cm, över hela intervallet."""
    lg = _logik()
    if not hasattr(lg, "planera_profil"):
        return  # redan rapporterat
    kals = [lg.Kalibrering(62.0, 127.0, 19.0, 19.2), _kalibreringar(lg)[0]]
    falt = ("rela", "ms", "faktisk_cm", "ny_position", "klippt", "lage")
    antal = 0
    for k in kals:
        starter = [k.min_cm, k.min_cm + 0.3, 80.5, 95.0, 100.25, k.max_cm - 0.2, k.max_cm]
        for cm in _halvsteg_mal(62.0, 127.0):
            for start in starter:
                antal += 1
                p = lg.planera_profil(k, start, _profil("björn", "Björn", cm))
                h = lg.planera_hojd(k, start, cm)
                fall = f"{k.min_cm}–{k.max_cm} start {start} profil {cm}"
                if type(p) is not type(h):
                    fail(f"{fall}: profil gav {type(p).__name__}, till_hojd {type(h).__name__}")
                    continue
                if isinstance(h, lg.Uppdrag):
                    for f in falt:
                        if getattr(p, f) != getattr(h, f):
                            fail(f"{fall}: {f} {getattr(p, f)!r} ≠ till_hojd {getattr(h, f)!r}")
                if not p.sag.endswith("."):
                    fail(f"{fall}: sag utan avslut {p.sag!r}")
        # Okänd position och okalibrerat nekas lika
        for pos in (None, float("nan")):
            p = lg.planera_profil(k, pos, _profil("björn", "Björn", 98.0))
            if not isinstance(p, lg.Nekande) or p.sag != lg.TEXT_OKAND_POSITION:
                fail(f"profil från okänd position ska nekas med TEXT_OKAND_POSITION, fick {p}")
        # Sparad höjd = position avrundad till 0,5 (inom intervallet)
        for pos in _halvsteg(k.min_cm, k.max_cm) + [k.min_cm + 0.26, 99.74, 99.76]:
            s = lg.planera_spara_profil(k, pos, "Björn", [])
            if not isinstance(s, lg.ProfilSparning):
                fail(f"spara från {pos} ska godtas, fick {s}")
                continue
            vantat = lg.halv_cm(min(max(pos, k.min_cm), k.max_cm))
            if s.cm != vantat:
                fail(f"spara från {pos}: cm {s.cm}, väntat {vantat}")
    if antal < 1500:
        fail(f"profilsvepet körde bara {antal} fall")
    # Svar när bordet redan står där
    k = kals[0]
    p = lg.planera_profil(k, 98.0, _profil("björn", "Björn", 98.0))
    if not isinstance(p, lg.Nekande) or p.sag != "Står redan på Björns.":
        fail(f"profil 98 från 98 ska ge 'Står redan på Björns.', fick {p}")


def check_profil_texter():
    lg = _logik()
    if not hasattr(lg, "planera_spara_profil"):
        return
    k = lg.Kalibrering(62.0, 127.0, 19.0, 19.2)
    s = lg.planera_spara_profil(k, 98.0, "  björn ", [])
    if not isinstance(s, lg.ProfilSparning) or s.sag != "Sparat. björn, 98." \
            or s.nyckel != "björn" or s.namn != "björn" or s.gammal_cm is not None:
        fail(f"ny profil ska ge 'Sparat. björn, 98.', fick {s}")
    s = lg.planera_spara_profil(k, 98.0, "Björn", [])
    if getattr(s, "sag", None) != "Sparat. Björn, 98.":
        fail(f"ny profil Björn: {s}")
    # Överskrivning behåller visningsnamnet, även via genitiv och annat skiftläge
    befintliga = [_profil("björn", "Björn", 104.0), _profil("anna", "Anna", 82.0)]
    for namn in ("Björn", "BJÖRN", "björns", "Björns höjd"):
        s = lg.planera_spara_profil(k, 98.0, namn, befintliga)
        if not isinstance(s, lg.ProfilSparning) or s.nyckel != "björn" or s.namn != "Björn" \
                or s.gammal_cm != 104.0 or s.sag != "Ändrat. Björn, 98, var 104.":
            fail(f"spara {namn!r} över Björn 104 ska ge 'Ändrat. Björn, 98, var 104.', fick {s}")
    # Nekanden
    for kal in (None, lg.Kalibrering(None, 127.0, 19.0, 19.2)):
        s = lg.planera_spara_profil(kal, 98.0, "Björn", [])
        if not isinstance(s, lg.Nekande) or "kalibrer" not in s.sag:
            fail(f"spara okalibrerat ska nekas med förklaring, fick {s}")
    for pos in (None, float("nan"), "nittio"):
        s = lg.planera_spara_profil(k, pos, "Björn", [])
        if not isinstance(s, lg.Nekande) or s.sag != lg.TEXT_SPARA_OKAND_POSITION:
            fail(f"spara från okänd position {pos!r} ska nekas med TEXT_SPARA_OKAND_POSITION, fick {s}")
    if "stå eller sitt" not in lg.TEXT_SPARA_OKAND_POSITION:
        fail("TEXT_SPARA_OKAND_POSITION ska be om stå eller sitt")
    for namn in ("", "   ", "A" * 41, "42", None):
        s = lg.planera_spara_profil(k, 98.0, namn, [])
        if not isinstance(s, lg.Nekande) or s.sag != lg.TEXT_PROFILNAMN_OGILTIGT:
            fail(f"spara med namnet {namn!r} ska nekas med TEXT_PROFILNAMN_OGILTIGT, fick {s}")
    # Tak 50: ny nekas, överskrivning godtas
    femtio = [_profil(f"p{i}", f"P{i}", 70.0 + i / 2) for i in range(50)]
    s = lg.planera_spara_profil(k, 98.0, "Björn", femtio)
    if not isinstance(s, lg.Nekande) or s.sag != lg.TEXT_PROFILTAK or lg.MAX_PROFILER != 50:
        fail(f"profil nummer 51 ska nekas med TEXT_PROFILTAK, fick {s}")
    s = lg.planera_spara_profil(k, 98.0, "p7", femtio)
    if not isinstance(s, lg.ProfilSparning) or s.nyckel != "p7" or s.namn != "P7":
        fail(f"överskrivning vid taket ska godtas, fick {s}")
    s = lg.planera_spara_profil(k, 98.0, "Björn", femtio[:49])
    if not isinstance(s, lg.ProfilSparning):
        fail(f"profil nummer 50 ska godtas, fick {s}")
    # Uppslag
    tre = [_profil("anna", "Anna", 82.0), _profil("björn", "Björn", 98.0), _profil("cecilia", "Cecilia", 110.0)]
    v = lg.valj_profil("Björns", tre)
    if not isinstance(v, dict) or v["nyckel"] != "björn":
        fail(f"valj_profil('Björns') ska hitta Björn, fick {v}")
    v = lg.valj_profil("David", tre)
    if not isinstance(v, lg.Nekande) or \
            v.sag != "Ingen David. Finns: Anna, Björn och Cecilia.":
        fail(f"okänt namn ska lista befintliga, fick {v}")
    v = lg.valj_profil("David", tre[:1])
    if not isinstance(v, lg.Nekande) or v.sag != "Ingen David. Finns: Anna.":
        fail(f"okänt namn med en profil, fick {v}")
    v = lg.valj_profil("David", [])
    if not isinstance(v, lg.Nekande) or v.sag != "Ingen David. Inga sparade.":
        fail(f"okänt namn utan profiler, fick {v}")
    v = lg.valj_profil("", tre)
    if not isinstance(v, lg.Nekande) or v.sag != "Vilket namn?":
        fail(f"tomt namn ska nekas med 'Vilket namn?', fick {v}")
    # Lista, sorterad på svenska (Å Ä Ö sist)
    t = lg.text_profiler([_profil("björn", "Björn", 98.0), _profil("anna", "Anna", 82.0)])
    if t != "Anna 82, Björn 98.":
        fail(f"text_profiler = {t!r}")
    t = lg.text_profiler([_profil("örjan", "Örjan", 90.0), _profil("åsa", "Åsa", 80.0),
                          _profil("ärla", "Ärla", 85.0), _profil("zack", "Zack", 70.0)])
    if t != "Zack 70, Åsa 80, Ärla 85, Örjan 90.":
        fail(f"svensk sortering: {t!r}")
    if lg.text_profiler([]) != "Inga sparade.":
        fail(f"tom lista: {lg.text_profiler([])!r}")
    # Klar-texten
    t = lg.text_profil_klar("Björn", 98.0, 98.03)
    if t != "Klart. Björns, 98 centimeter.":
        fail(f"text_profil_klar = {t!r}")
    t = lg.text_profil_klar("Hans", 98.5, 98.5)
    if t != "Klart. Hans, 99 centimeter.":
        fail(f"text_profil_klar Hans = {t!r}")
    t = lg.text_profil_klar("Björn", 130.0, 127.0)
    if t != "Klart. 127, närmare Björns går inte.":
        fail(f"profil utanför intervallet ska säga var bordet faktiskt står: {t!r}")


def check_profil_db():
    import tempfile
    import db
    conn = db.anslut(os.path.join(tempfile.mkdtemp(prefix="skrivbord-db-"), "p.db"))
    kol = {r[1]: r for r in conn.execute("PRAGMA table_info(profil)")}
    for k in ("nyckel", "namn", "cm", "skapad", "andrad"):
        if k not in kol:
            fail(f"db: profil saknar kolumnen {k}")
    if "nyckel" in kol and kol["nyckel"][5] != 1:
        fail("db: profil.nyckel ska vara primärnyckel")


def _skapa_profiler(bord, n, start=0):
    for i in range(start, start + n):
        bord.satt_position(70.0 + (i % 100) / 2)
        s = bord.spara_profil("admin", f"Profil {i}")
        if not s.ok:
            fail(f"kunde inte skapa profil {i}: {s}")
            return


def check_agent_profil():
    """De tre agent-endpointerna: hemlighet, vitlista, svarsform, synkron väntan, tak,
    samma uppdrag som till_hojd — och inga utgående anslutningar."""
    if not _krav_flask("check_agent_profil"):
        return
    import logik
    a, bord = _ny_app()
    c = a.test_client()
    url = {"spara": "/api/agent/profil/spara", "till": "/api/agent/profil/till",
           "lista": "/api/agent/profil/lista"}
    for hdr in ({}, _agent("fel"), _agent("")):
        for u in url.values():
            r = c.post(u, json={"uppringare": VITT, "namn": "Björn"}, headers=hdr)
            if r.status_code != 401:
                fail(f"{u} med {hdr or 'ingen hemlighet'} ska ge 401, gav {r.status_code}")
    regler = {r.rule for r in a.url_map.iter_rules()}
    if any("profil" in r and r.startswith("/api/agent") and ("bort" in r or "radera" in r) for r in regler):
        fail("agenten får inte kunna ta bort profiler")

    def post(namn, kropp):
        r = c.post(url[namn], json=kropp, headers=_agent())
        j = r.get_json() or {}
        if r.status_code != 200 or set(j) != {"ok", "sag"}:
            fail(f"{url[namn]} ska svara 200 med bara ok och sag, fick {r.status_code} {j}")
        return j

    with _Natspion() as nat:
        # Okalibrerat
        j = post("spara", {"uppringare": VITT, "namn": "Björn"})
        if j.get("ok") is not False or "kalibrer" not in j.get("sag", ""):
            fail(f"spara okalibrerat ska nekas, fick {j}")
        _kalibrera(bord, 95.0)                       # 65–125 cm
        # Vitlistan, alla tre
        for nummer in ("+4721234567", "+46701740698", "", None, "anonymous"):
            for namn in url:
                j = post(namn, {"uppringare": nummer, "namn": "Björn"})
                if j != {"ok": False, "sag": logik.TEXT_EJ_TILLATET}:
                    fail(f"{url[namn]} från {nummer!r} ska nekas med vitlistetext, fick {j}")
        if bord.profiler():
            fail("nekade nummer får inte spara profiler")
        # Trasig kropp
        for u in url.values():
            r = c.post(u, data="skräp", headers=_agent())
            if r.status_code != 200 or (r.get_json() or {}).get("ok") is not False:
                fail(f"{u} med trasig kropp ska ge 200 ok false, fick {r.status_code}")
        # Okänd position ⇒ spara nekas
        bord.satt_position(None)
        j = post("spara", {"uppringare": VITT, "namn": "Björn"})
        if j != {"ok": False, "sag": logik.TEXT_SPARA_OKAND_POSITION}:
            fail(f"spara från okänd position ska nekas, fick {j}")
        # Lista utan profiler
        j = post("lista", {"uppringare": VITT})
        if j != {"ok": True, "sag": "Inga sparade."}:
            fail(f"tom lista, fick {j}")
        # Spara, skriv över via annat skiftläge och genitiv
        bord.satt_position(104.2)
        j = post("spara", {"uppringare": "070-1740605", "namn": " Björn "})
        if j != {"ok": True, "sag": "Sparat. Björn, 104."}:
            fail(f"spara Björn på 104,2 ska ge 'Sparat. Björn, 104.', fick {j}")
        bord.satt_position(97.8)
        j = post("spara", {"uppringare": VITT, "namn": "BJÖRNS"})
        if j != {"ok": True, "sag": "Ändrat. Björn, 98, var 104."}:
            fail(f"överskrivning ska ge 'Ändrat. Björn, 98, var 104.', fick {j}")
        bord.satt_position(82.0)
        post("spara", {"uppringare": ANNAT_VITT, "namn": "Anna"})
        ps = bord.profiler()
        if [(p["nyckel"], p["namn"], p["cm"]) for p in ps] != [("anna", "Anna", 82.0), ("björn", "Björn", 98.0)]:
            fail(f"profilerna ska vara Anna 82 och Björn 98 (visningsnamn bevarat), är {ps}")
        j = post("lista", {"uppringare": VITT})
        if j != {"ok": True, "sag": "Anna 82, Björn 98."}:
            fail(f"lista, fick {j}")
        # Okänt namn listar befintliga och skapar inget uppdrag
        fore = bord.antal_uppdrag()
        j = post("till", {"uppringare": VITT, "namn": "Cecilia"})
        if j != {"ok": False, "sag": "Ingen Cecilia. Finns: Anna och Björn."}:
            fail(f"okänt namn ska lista befintliga, fick {j}")
        if bord.antal_uppdrag() != fore:
            fail("okänt profilnamn får inte skapa uppdrag")
        # Till profil: synkront, svaret är profiltexten
        with _FejkEnhet(a):
            j = post("till", {"uppringare": VITT, "namn": "björns höjd"})
        if j != {"ok": True, "sag": "Klart. Björns, 98 centimeter."}:
            fail(f"till Björn ska svara 'Klart. Björns, 98 centimeter.', fick {j}")
        if bord.position() is None or abs(bord.position() - 98.0) >= 0.1:
            fail(f"efter till Björn ska positionen vara ≈98, är {bord.position()}")
        j = post("till", {"uppringare": VITT, "namn": "Björn"})
        if j != {"ok": False, "sag": "Står redan på Björns."}:
            fail(f"till Björn när bordet står där, fick {j}")
        # Under pågående ⇒ rör sig redan, även för spara
        bord.begar_rorelse("admin", riktning="upp", cm=5)
        for namn in ("till", "spara"):
            j = post(namn, {"uppringare": VITT, "namn": "Anna"})
            if j != {"ok": False, "sag": logik.TEXT_ROR_SIG_REDAN}:
                fail(f"{namn} under pågående ska nekas med 'rör sig redan', fick {j}")
        _slutfor(c)
        # Okänd position ⇒ till nekas
        bord.satt_position(None)
        j = post("till", {"uppringare": VITT, "namn": "Anna"})
        if j != {"ok": False, "sag": logik.TEXT_OKAND_POSITION}:
            fail(f"till profil från okänd position ska nekas, fick {j}")
        # Tak 50 genom endpointen
        _skapa_profiler(bord, 48)
        bord.satt_position(90.0)
        if len(bord.profiler()) != 50:
            fail(f"ska finnas 50 profiler, finns {len(bord.profiler())}")
        j = post("spara", {"uppringare": VITT, "namn": "Ny"})
        if j != {"ok": False, "sag": logik.TEXT_PROFILTAK}:
            fail(f"profil 51 ska nekas med TEXT_PROFILTAK, fick {j}")
        j = post("spara", {"uppringare": VITT, "namn": "Anna"})
        if j.get("ok") is not True or not j.get("sag", "").startswith("Ändrat. Anna,"):
            fail(f"överskrivning vid taket ska godtas, fick {j}")
        for namn in ("A" * 41, "", "17"):
            j = post("spara", {"uppringare": VITT, "namn": namn})
            if j != {"ok": False, "sag": logik.TEXT_PROFILNAMN_OGILTIGT}:
                fail(f"namnet {namn!r} ska nekas, fick {j}")
    if nat.forsok:
        fail(f"profilflödet försökte ansluta utåt: {nat.forsok}")

    # Profil kör exakt samma uppdrag som till_hojd
    a, bord = _ny_app()
    c = a.test_client()
    _kalibrera(bord, 95.0)
    for i, cm in enumerate((65.0, 70.5, 94.5, 95.5, 110.0, 124.5, 125.0)):
        bord.satt_position(cm)
        bord.spara_profil("admin", f"P{i}")
        rader = []
        for vag in ("profil", "hojd"):
            bord.satt_position(95.0)
            s = (bord.begar_rorelse("agent", uppringare=VITT, profil=f"p{i}") if vag == "profil"
                 else bord.begar_rorelse("agent", uppringare=VITT, hojd=cm))
            if not s.ok:
                fail(f"{vag} {cm} från 95 ska godtas, fick {s}")
                continue
            rader.append(bord.uppdrag(s.id))
            _slutfor(c)
        if len(rader) == 2:
            for f in ("rela", "ms", "faktisk_cm", "ny_position", "lage", "start_position", "uppringare"):
                if rader[0][f] != rader[1][f]:
                    fail(f"profil {cm}: uppdragets {f} {rader[0][f]!r} ≠ till_hojd {rader[1][f]!r}")


def check_admin_profil():
    if not _krav_flask("check_admin_profil"):
        return
    import re
    a, bord = _ny_app()
    c = a.test_client()
    _kalibrera(bord, 98.0)
    html = c.get("/admin", headers=_admin()).get_data(as_text=True)
    for text in ("Profiler", "Spara nuvarande höjd som"):
        if text not in html:
            fail(f"/admin saknar '{text}'")
    csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
    # Utan csrf ⇒ 400, utan lösen ⇒ 401, för alla tre
    for u, data in (("/admin/profil/spara", {"namn": "Björn"}), ("/admin/profil/till", {"nyckel": "björn"}),
                    ("/admin/profil/ta-bort", {"nyckel": "björn"})):
        r = c.post(u, data=data, headers=_admin())
        if r.status_code != 400:
            fail(f"POST {u} utan csrf ska ge 400, gav {r.status_code}")
        r = c.post(u, data=dict(data, csrf="fel"), headers=_admin())
        if r.status_code != 400:
            fail(f"POST {u} med fel csrf ska ge 400, gav {r.status_code}")
        r = c.post(u, data=dict(data, csrf=csrf), headers={})
        if r.status_code != 401:
            fail(f"POST {u} utan lösen ska ge 401, gav {r.status_code}")
    if bord.profiler() or bord.antal_uppdrag():
        fail("nekade adminanrop får inte spara profiler eller skapa uppdrag")
    r = c.post("/admin/profil/spara", data={"namn": "Björn", "csrf": csrf}, headers=_admin())
    if r.status_code != 302 or [(p["namn"], p["cm"]) for p in bord.profiler()] != [("Björn", 98.0)]:
        fail(f"admin spara Björn på 98 ska spara, fick {r.status_code} {bord.profiler()}")
    html = c.get("/admin", headers=_admin()).get_data(as_text=True)
    if "Sparat. Björn, 98." not in html:
        fail("admin ska visa sparbekräftelsen")
    sek = re.search(r"<h2>Profiler</h2>(.*?)</section>", html, re.S)
    if not sek or "Björn" not in sek.group(1) or "98" not in sek.group(1):
        fail("profilsektionen ska lista Björn och 98 cm")
    else:
        for knapp in ("Kör hit", "Ta bort"):
            if knapp not in sek.group(1):
                fail(f"profilsektionen saknar knappen '{knapp}'")
        for form in re.findall(r"<form[^>]*>(.*?)</form>", sek.group(1), re.S):
            if 'name="csrf"' not in form:
                fail("varje formulär i profilsektionen ska ha csrf")
    # Kör hit
    bord.satt_position(80.0)
    r = c.post("/admin/profil/till", data={"nyckel": "björn", "csrf": csrf}, headers=_admin())
    p = bord.pagaende()
    if r.status_code != 302 or not p or p["rela"] != "upp" or abs(p["faktisk_cm"] - 18) > 1e-9:
        fail(f"admin Kör hit Björn från 80 ska ge upp-uppdrag 18 cm, fick {r.status_code} {p}")
    _slutfor(c)
    # Ta bort
    r = c.post("/admin/profil/ta-bort", data={"nyckel": "björn", "csrf": csrf}, headers=_admin())
    if r.status_code != 302 or bord.profiler():
        fail(f"admin Ta bort ska ta bort profilen, fick {r.status_code} {bord.profiler()}")
    r = c.post("/admin/profil/ta-bort", data={"nyckel": "björn", "csrf": csrf}, headers=_admin())
    if r.status_code != 302:
        fail(f"ta bort okänd profil ska ge 302 med felmeddelande, gav {r.status_code}")


# --- Steg 9: norrländskt kort — allt agenten läser upp ---

MAX_ORD = 8
FORBJUDNA = ("ringer", "jag ", "okej", "gärna", "tyvärr")


def _alla_sag_texter(lg):
    """Varje text agenten kan läsa upp: konstanterna plus ett svep över all
    planering och alla klar-texter för kalibreringen 62–127. Korta namn (Bo, Al)
    så att namn och listor inte äter upp ordgränsen."""
    texter = {}

    def ta(kalla, t):
        if isinstance(t, (lg.Uppdrag, lg.Nekande, lg.ProfilSparning)):
            t = t.sag
        if isinstance(t, dict):
            return
        texter.setdefault(t, kalla)

    for namn in dir(lg):
        if namn.startswith("TEXT_"):
            ta(namn, getattr(lg, namn))
    k = lg.Kalibrering(62.0, 127.0, 22.0, 20.0)
    positioner = _halvsteg(62.0, 127.0) + [None, 50.0, 140.0]
    for pos in positioner:
        for r in ("upp", "ner", "snett", None):
            for cm in (0, 0.4, 1, 5, 10, 40, 80, 81, "x", None):
                ta(f"relativ {pos} {r} {cm}", lg.planera_relativ(k, pos, r, cm))
        for l in ("stå", "sitt", "ligg", None):
            ta(f"läge {pos} {l}", lg.planera_lage(k, l, pos))
        for mal in (-1, 0, "x", None, 50, 62, 62.2, 80, 100, 126.8, 127, 140):
            ta(f"höjd {pos} {mal}", lg.planera_hojd(k, pos, mal))
        for pcm in (50.0, 62.0, 80.0, 98.0, 127.0, 140.0):
            profil = {"nyckel": "bo", "namn": "Bo", "cm": pcm}
            ta(f"profil {pos} {pcm}", lg.planera_profil(k, pos, profil))
            if pos is not None:
                ta(f"profil klar {pos} {pcm}", lg.text_profil_klar("Bo", pcm, pos))
                ta(f"profil klar Max {pos} {pcm}", lg.text_profil_klar("Max", pcm, pos))
        if pos is not None:
            ta(f"klar {pos}", lg.text_klar(pos))
            for begard in (5, 40, 80):
                ta(f"klar {pos} {begard}", lg.text_klar(pos, begard, begard))
                ta(f"klar klippt {pos} {begard}", lg.text_klar(pos, begard, begard / 2))
    for kal in (None, lg.Kalibrering(None, None, None, None)):
        ta("okalibrerat relativ", lg.planera_relativ(kal, 90.0, "upp", 5))
        ta("okalibrerat läge", lg.planera_lage(kal, "stå"))
        ta("okalibrerat höjd", lg.planera_hojd(kal, 90.0, 100))
        ta("okalibrerat profil", lg.planera_profil(kal, 90.0, {"nyckel": "bo", "namn": "Bo", "cm": 90.0}))
        ta("okalibrerat spara", lg.planera_spara_profil(kal, 90.0, "Bo", []))
    p = lambda n, cm: {"nyckel": n.casefold(), "namn": n, "cm": cm}
    listor = [[], [p("Bo", 98.0)], [p("Al", 82.0), p("Bo", 98.0)], [p("Al", 82.0), p("Bo", 98.0), p("Ek", 110.0)]]
    for lista in listor:
        ta(f"lista {len(lista)}", lg.text_profiler(lista))
        for namn in ("Bo", "Bos", "Ek", "Ut", "", "123", "x" * 41, None):
            ta(f"välj {namn!r} bland {len(lista)}", lg.valj_profil(namn, lista))
            for pos in (None, 62.0, 97.6, 98.2, 127.0):
                ta(f"spara {namn!r} {pos} bland {len(lista)}", lg.planera_spara_profil(k, pos, namn, lista))
    fullt = [p(f"P{i}", 80.0) for i in range(lg.MAX_PROFILER)]
    ta("spara i full lista", lg.planera_spara_profil(k, 90.0, "Ny", fullt))
    return texter


def check_korta_texter():
    lg = _logik()
    texter = _alla_sag_texter(lg)
    if len(texter) < 40:
        fail(f"svepet hittade bara {len(texter)} olika texter — täcker det verkligen allt?")
    for t, kalla in sorted(texter.items(), key=lambda x: x[1]):
        if not isinstance(t, str) or not t.strip():
            fail(f"{kalla}: tom eller icke-sträng sag {t!r}")
            continue
        if len(t.split()) > MAX_ORD:
            fail(f"{kalla}: {len(t.split())} ord (högst {MAX_ORD}): {t!r}")
        for f in FORBJUDNA:
            if f in t.casefold():
                fail(f"{kalla}: innehåller {f.strip()!r}: {t!r}")
    # Felsvaret i appen läses också upp.
    with open(os.path.join(_SERVER, "app.py"), encoding="utf-8") as fh:
        app_src = fh.read()
    import re
    for t in re.findall(r'Svar\(False, "(Något gick fel[^"]*)"', app_src):
        if len(t.split()) > MAX_ORD or any(f in t.casefold() for f in FORBJUDNA):
            fail(f"app.py felsvar för långt eller artigt: {t!r}")


def check_exakta_korta_texter():
    """De exakta formuleringarna användaren valt (2026-10-14)."""
    lg = _logik()
    k = lg.Kalibrering(62.0, 127.0, 22.0, 20.0)
    p = lambda n, cm: {"nyckel": n.casefold(), "namn": n, "cm": cm}
    vantat = {
        "klar": (lg.text_klar(92.0), "Klart. 92 centimeter."),
        "klar klippt": (lg.text_klar(125.0, 10, 3), "Klart. 125 centimeter, längre går det inte."),
        "svarar inte": (lg.TEXT_SVARAR_INTE, "Bordet svarar inte."),
        "okänd efter": (lg.TEXT_KLAR_OKAND_POSITION, "Stannat. Vet inte var."),
        "rör sig": (lg.TEXT_ROR_SIG_REDAN, "Vänta, det rör sig."),
        "okalibrerat": (lg.TEXT_OKALIBRERAT, "Inte kalibrerat."),
        "spara okalibrerat": (lg.TEXT_SPARA_OKALIBRERAT, "Inte kalibrerat."),
        "okänd position": (lg.TEXT_OKAND_POSITION, "Vet inte var. Säg stå eller sitt."),
        "ej tillåtet": (lg.TEXT_EJ_TILLATET, "Bara svenska nummer."),
        "riktning": (lg.planera_relativ(k, 90.0, "snett", 5).sag, "Upp eller ner?"),
        "intervall": (lg.planera_relativ(k, 90.0, "upp", 81).sag, "Mellan 1 och 80 centimeter."),
        "i topp": (lg.planera_relativ(k, 127.0, "upp", 5).sag, "Det är i topp."),
        "i botten": (lg.planera_relativ(k, 62.0, "ner", 5).sag, "Det är i botten."),
        "relativ": (lg.planera_relativ(k, 90.0, "ner", 5).sag, "Jo."),
        "relativ klippt": (lg.planera_relativ(k, 125.0, "upp", 5).sag, "Jo."),
        "läge": (lg.planera_lage(k, "stå", 90.0).sag, "Jo."),
        "läge okänt": (lg.planera_lage(k, "ligg", 90.0).sag, "Stå eller sitt?"),
        "höjd okänd": (lg.planera_hojd(k, 90.0, "x").sag, "Hur högt?"),
        "höjd": (lg.planera_hojd(k, 90.0, 100).sag, "Jo."),
        "höjd klippt": (lg.planera_hojd(k, 90.0, 140).sag, "Går bara till 127."),
        "höjd står redan": (lg.planera_hojd(k, 100.0, 100).sag, "Står redan på 100."),
        "sparat": (lg.planera_spara_profil(k, 98.0, "Björn", []).sag, "Sparat. Björn, 98."),
        "ändrat": (lg.planera_spara_profil(k, 98.0, "Björn", [p("Björn", 104.0)]).sag, "Ändrat. Björn, 98, var 104."),
        "vilket namn": (lg.valj_profil("", [p("Björn", 98.0)]).sag, "Vilket namn?"),
        "ingen": (lg.valj_profil("Kalle", [p("Björn", 98.0), p("Anna", 82.0)]).sag,
                  "Ingen Kalle. Finns: Anna och Björn."),
        "ingen en": (lg.valj_profil("Kalle", [p("Anna", 82.0)]).sag, "Ingen Kalle. Finns: Anna."),
        "ingen tom": (lg.valj_profil("Kalle", []).sag, "Ingen Kalle. Inga sparade."),
        "profil klar": (lg.text_profil_klar("Björn", 98.0, 98.0), "Klart. Björns, 98 centimeter."),
        "profil klar klippt": (lg.text_profil_klar("Björn", 140.0, 125.0), "Klart. 125, närmare Björns går inte."),
        "profil står redan": (lg.planera_profil(k, 98.0, p("Björn", 98.0)).sag, "Står redan på Björns."),
        "profil": (lg.planera_profil(k, 90.0, p("Björn", 98.0)).sag, "Jo."),
        "lista": (lg.text_profiler([p("Björn", 98.0), p("Anna", 82.0)]), "Anna 82, Björn 98."),
        "lista tom": (lg.text_profiler([]), "Inga sparade."),
        "ogiltigt namn": (lg.TEXT_PROFILNAMN_OGILTIGT, "Ogiltigt namn."),
        "tak": (lg.TEXT_PROFILTAK, "Fullt. Ta bort någon först."),
    }
    for fall, (fick, ska) in vantat.items():
        if fick != ska:
            fail(f"text {fall}: {fick!r}, väntade {ska!r}")


# --- Publikt repo: inga platsspecifika uppgifter, mallar, hemligheter, bilder ---

def _git_filer():
    """Filer som är spårade eller skulle bli det (ej gitignorerade). None utan git."""
    import subprocess
    try:
        p = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           cwd=_ROT, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return sorted({f for f in p.stdout.decode("utf-8", "replace").split("\0")
                   if f and os.path.exists(os.path.join(_ROT, f))})


def _ar_sparad(rel):
    filer = _git_filer()
    return filer is not None and rel in filer


def _textfiler():
    filer = _git_filer()
    if filer is None:   # utan git: gå igenom katalogen och hoppa över det som är ignorerat
        filer = []
        for rot, mappar, namn in os.walk(_ROT):
            mappar[:] = [m for m in mappar if m not in (".git", ".venv", "venv", "__pycache__", "data")]
            filer += [os.path.relpath(os.path.join(rot, n), _ROT) for n in namn]
    ut = []
    for rel in filer:
        try:
            with open(os.path.join(_ROT, rel), "rb") as f:
                data = f.read()
            ut.append((rel, data.decode("utf-8")))
        except (OSError, UnicodeDecodeError):
            continue   # binärfil
    return ut


def check_egna_nummer_ur_miljon():
    """Inga telefonnummer i serverkoden: de egna numren kommer ur SKRIVBORD_EGNA_NUMMER."""
    import re
    lg = _logik()
    if lg.las_egna_nummer("") != frozenset() or lg.las_egna_nummer(None) != frozenset():
        fail("utan SKRIVBORD_EGNA_NUMMER ska listan över egna nummer vara tom")
    if lg.las_egna_nummer(" 070-174 06 98 ,+46701740699, skräp") != frozenset({"+46701740698", "+46701740699"}):
        fail(f"las_egna_nummer tolkar inte en kommaseparerad lista: {lg.las_egna_nummer('070-174 06 98')}")
    if EGET_NUMMER not in lg.EGNA_NUMMER or lg.far_styra(EGET_NUMMER):
        fail("ett nummer i SKRIVBORD_EGNA_NUMMER ska aldrig få styra bordet")
    for namn in ("logik.py", "app.py", "db.py"):
        text = open(os.path.join(_SERVER, namn), encoding="utf-8").read()
        for m in re.finditer(r"\+46\d{7,}", text):
            if m.group(0).startswith("+467017406"):   # PTS serie för fiktiv användning
                continue
            fail(f"server/{namn} innehåller telefonnumret {m.group(0)} — egna nummer ska komma ur miljön")


def check_esp32_rela_aktivt_lag():
    """Med aktivt_hog=False är "draget" = låg nivå: samma spärrar, omvända nivåer."""
    m = _rela_modul()
    logg = _Logg()
    logg.kontroll = lambda nar: (logg.brott.append(nar)
                                 if len(logg.pinnar) == 2 and not any(p.v for p in logg.pinnar.values())
                                 else None)
    upp, ner = _FalskPin(logg, "upp", start=0), _FalskPin(logg, "ner", start=0)   # startar DRAGNA
    tid = _FalskTid()
    r = m.Rela(upp, ner, max_ms=5000, tid_ms=tid.tid_ms, sov_ms=tid.sov_ms, aktivt_hog=False)
    if (upp.v, ner.v) != (1, 1):
        fail(f"aktivt låg: båda ska vara höga (släppta) efter init, är {(upp.v, ner.v)}")
    logg.andringar.clear()
    if r.kor(1, "upp", 1500) != 1500 or ("upp", 0) not in logg.andringar or (upp.v, ner.v) != (1, 1):
        fail(f"aktivt låg: upp ska dras (0) och släppas (1), logg {logg.andringar}")
    logg.andringar.clear()
    r.kor(2, "ner", 100)
    if logg.andringar[:2] != [("upp", 1), ("ner", 0)]:
        fail(f"aktivt låg: kor ner ska släppa upp innan ner dras, logg {logg.andringar}")
    if r.kor(3, "ner", 99999) != 5000 or (upp.v, ner.v) != (1, 1):
        fail("aktivt låg: MAX_MS ska kapa och båda ska släppas efteråt")
    if logg.brott:
        fail(f"aktivt låg: båda reläerna dragna samtidigt: {logg.brott[:5]}")


def check_deploy_mallar():
    """Deploy och tjänstefiler är mallar: ingen server, användare, domän eller e-post i koden."""
    import re
    dep = os.path.join(_ROT, "deploy")
    sh = open(os.path.join(dep, "deploy.sh"), encoding="utf-8").read()
    for krav in ("saknas SKRIVBORD_SERVER", "saknas SKRIVBORD_DOMAN", "saknas CERTBOT_EPOST",
                 'checks/verify.py', "verify_brygga.py", '-m "$EPOST"'):
        if krav not in sh:
            fail(f"deploy.sh saknar {krav!r}")
    if re.search(r"^\s*(SERVER|DOMAN|MAL)=[^$]", sh, re.M):
        fail("deploy.sh har hårdkodad SERVER/DOMAN/MAL — de ska komma ur miljön")
    if re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", sh):
        fail("deploy.sh innehåller en e-postadress eller ssh-mål")
    mallar = {
        "skrivbord.service.mall": ("User=@ANVANDARE@", "@MAL@/venv/bin/gunicorn", "--bind 127.0.0.1:@PORT@",
                                   "--workers 1", "--worker-class gthread"),
        "skrivbord-brygga.service.mall": ("User=@ANVANDARE@", "BRYGGA_PORT=@BRYGGA_PORT@"),
        "nginx-skrivbord.conf.mall": ("server_name @DOMAN@", "location = /elks"),
    }
    for namn, krav in mallar.items():
        sokvag = os.path.join(dep, namn)
        if not os.path.exists(sokvag):
            fail(f"deploy/{namn} saknas")
            continue
        text = open(sokvag, encoding="utf-8").read()
        for k in krav:
            if k not in text:
                fail(f"deploy/{namn} saknar {k!r}")
        if f"deploy/{namn}" not in sh:
            fail(f"deploy.sh fyller inte i deploy/{namn}")
    for namn in os.listdir(dep):
        if namn.endswith((".service", ".conf")):
            fail(f"deploy/{namn}: färdiga tjänstefiler hör inte hemma i repot — bara mallar")


# Platsspecifika strängar som inte får finnas i ett publikt repo. De lagras som
# sha256-prefix så att kontrollen själv inte publicerar dem: fyra telefonnummer
# (jämförs som "+46" + nationellt nummer) och fyra ord (värd-, användar- och projektnamn).
_FORBJUDNA_NUMMER = {"ca770796dcbe74cd", "c06b5046b9549dc0", "6a18effe67665636", "0ad9bea838cc2927"}
_FORBJUDNA_ORD = {9: {"b7cf7ffc3d96d7a0"}, 7: {"881df12b68cd344e"}, 8: {"cd4fbbe9d2f9644c"},
                  13: {"bde8d9239fa401bf"}}
_HEMLIGA_MONSTER = (
    (r"\bsk_[0-9a-f]{16,}", "ElevenLabs-nyckel (sk_…)"),
    (r"\bagent_[A-Za-z0-9]{20,}", "ElevenLabs agent-id"),
    (r"\btool_[A-Za-z0-9]{20,}", "ElevenLabs verktygs-id"),
    (r"\bu[0-9a-f]{32}\b", "46elks API-användarnamn"),
    (r"\b[0-9a-fA-F]{32,}\b", "lång hexsträng (nyckel/token?)"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "privat nyckel"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS-nyckel"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}", "GitHub-token"),
    (r"\bxox[abprs]-[A-Za-z0-9-]{10,}", "Slack-token"),
    (r"\bsk-[A-Za-z0-9_-]{20,}", "API-nyckel (sk-…)"),
    (r"(?i)\b\w*(api_?key|secret|token|losen|password|hemlighet)\w*\s*[:=]\s*[\"']"
     r"(?!test-|fejk|\[FYLL I|<|[a-z_]+[\"'])[^\"'\s]{12,}[\"']", "hårdkodat hemligt värde"),
)


def _h(s):
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _hitta_forbjudet(text):
    """Returnerar en lista med beskrivningar av förbjudna träffar i text."""
    import re
    ut = []
    # Telefonnummer: alla sifferföljder (med mellanslag/bindestreck/parenteser inne i)
    # prövas som "+46" + varje delsträng på 7–10 siffror.
    for m in re.finditer(r"[+\d][\d \-()]{5,}\d", text):
        siffror = re.sub(r"\D", "", m.group(0))
        for langd in range(7, 11):
            for i in range(len(siffror) - langd + 1):
                if _h("+46" + siffror[i:i + langd]) in _FORBJUDNA_NUMMER:
                    ut.append(f"ett av de riktiga telefonnumren ({m.group(0).strip()[:4]}…)")
                    break
    lag = text.lower()
    for langd, hashar in _FORBJUDNA_ORD.items():
        for i in range(len(lag) - langd + 1):
            if lag[i] in "skc" and _h(lag[i:i + langd]) in hashar:
                ut.append(f"ett platsspecifikt namn ({lag[i:i + 2]}…, {langd} tecken)")
    for monster, vad in _HEMLIGA_MONSTER:
        for m in re.finditer(monster, text):
            ut.append(f"{vad}: {m.group(0)[:12]}…")
    return ut


def check_inga_hemligheter():
    """Svep över alla spårade textfiler: inga riktiga nummer, värdnamn, id:n eller nycklar."""
    filer = _textfiler()
    if len(filer) < 10:
        fail(f"hemlighetssvepet hittade bara {len(filer)} textfiler — fel katalog?")
    for rel, text in filer:
        for traff in sorted(set(_hitta_forbjudet(text))):
            fail(f"{rel}: {traff}")
    # Svepet måste fungera: pröva det mot syntetiska träffar som byggs här och aldrig sparas.
    prov = ["sk_" + "ab12" * 8, "agent_" + "x" * 24, "tool_" + "Y1" * 12, 'API_KEY = "' + "q7Z" * 7 + '"']
    for p in prov:
        if not _hitta_forbjudet(p):
            fail(f"hemlighetssvepet missar ett syntetiskt prov ({p[:8]}…)")
    for ofarligt in ("agent_torrkorning", "tool_ids", 'ENHET_TOKEN = "[FYLL I: x]"', "+46701740605"):
        if _hitta_forbjudet(ofarligt):
            fail(f"hemlighetssvepet larmar på det ofarliga {ofarligt!r}")


def _svg_langd(v):
    import re
    m = re.fullmatch(r"\s*(-?[\d.]+)\s*(px)?\s*", v or "")
    return float(m.group(1)) if m else None


def _svg_text_rutor(svg_rot):
    """(text, x0, y0, x1, y1, storlek) per <text>, med ärvd font-size och text-anchor.
    Bredden uppskattas som 0,6 × storlek per tecken (generös för sans-serif)."""
    ns = "{http://www.w3.org/2000/svg}"
    ut = []

    def ga(el, arv):
        arv = dict(arv)
        for attr in ("font-size", "text-anchor", "font-weight"):
            if el.get(attr):
                arv[attr] = el.get(attr)
        if el.get("transform"):
            arv["transform"] = el.get("transform")
        if el.tag == ns + "text":
            text = "".join(el.itertext())
            storlek = _svg_langd(arv.get("font-size"))
            x, y = _svg_langd(el.get("x", "0")), _svg_langd(el.get("y", "0"))
            bredd = len(text) * (storlek or 0) * (0.64 if arv.get("font-weight") == "bold" else 0.6)
            ankare = arv.get("text-anchor", "start")
            x0 = x - bredd / 2 if ankare == "middle" else x - bredd if ankare == "end" else x
            ut.append((text, x0, y - (storlek or 0) * 0.8, x0 + bredd, y + (storlek or 0) * 0.25,
                       storlek, arv.get("transform")))
        for barn in el:
            ga(barn, arv)
    ga(svg_rot, {})
    return ut


def check_svg_bilder():
    """docs/*.svg: självständiga, vit bakgrund, all text inom viewBox, ≥ 12 px och utan överlapp."""
    import xml.etree.ElementTree as ET
    ns = "{http://www.w3.org/2000/svg}"
    etiketter = {"koppling.svg": ("GPIO5", "GPIO6", "IN1", "IN2", "COM", "NO", "5V", "GND", "UPP", "NER"),
                 "arkitektur.svg": ("46elks", "ElevenLabs", "brygga", "server", "ESP32", "long-poll", "relä")}
    for namn, krav in etiketter.items():
        sokvag = os.path.join(_ROT, "docs", namn)
        if not os.path.exists(sokvag):
            fail(f"docs/{namn} saknas")
            continue
        ra_text = open(sokvag, encoding="utf-8").read()
        try:
            rot = ET.fromstring(ra_text)
        except ET.ParseError as e:
            fail(f"docs/{namn} är inte giltig XML: {e}")
            continue
        for forbjudet in ("<image", "@import", "@font-face", "href=\"http", "xlink:href=\"http", "url(http"):
            if forbjudet in ra_text:
                fail(f"docs/{namn} laddar något utifrån ({forbjudet})")
        vb = (rot.get("viewBox") or "").replace(",", " ").split()
        if len(vb) != 4:
            fail(f"docs/{namn} saknar viewBox")
            continue
        vx, vy, vw, vh = map(float, vb)
        forsta = next((el for el in rot if el.tag in (ns + "rect",)), None)
        if forsta is None or (forsta.get("fill") or "").lower() not in ("#fff", "#ffffff", "white") \
                or _svg_langd(forsta.get("width")) is None and forsta.get("width") != "100%":
            fail(f"docs/{namn}: första elementet ska vara en vit bakgrundsrect")
        elif forsta.get("width") != "100%" and (_svg_langd(forsta.get("width")) < vw
                                                or _svg_langd(forsta.get("height")) < vh):
            fail(f"docs/{namn}: bakgrundsrecten täcker inte hela viewBox")
        rutor = _svg_text_rutor(rot)
        alltext = " ".join(r[0] for r in rutor)
        for e in krav:
            if e not in alltext:
                fail(f"docs/{namn} saknar etiketten {e!r}")
        for text, x0, y0, x1, y1, storlek, transform in rutor:
            if storlek is None or storlek < 12:
                fail(f"docs/{namn}: texten {text!r} har font-size {storlek} (< 12 px)")
            if transform:
                fail(f"docs/{namn}: texten {text!r} har transform — kontrollen kan inte mäta den")
                continue
            if x0 < vx or y0 < vy or x1 > vx + vw or y1 > vy + vh:
                fail(f"docs/{namn}: texten {text!r} ({x0:.0f},{y0:.0f})–({x1:.0f},{y1:.0f}) går utanför viewBox")
        for i, a in enumerate(rutor):
            for b in rutor[i + 1:]:
                if a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]:
                    fail(f"docs/{namn}: texterna {a[0]!r} och {b[0]!r} överlappar")


def check_readme_och_licens():
    sokvag = os.path.join(_ROT, "README.md")
    if not os.path.exists(sokvag):
        fail("README.md saknas")
        return
    rd = open(sokvag, encoding="utf-8").read()
    for krav in ("docs/koppling.svg", "docs/arkitektur.svg", "The Unlicense", "SKRIVBORD_OFFENTLIG_URL",
                 "SKRIVBORD_EGNA_NUMMER", "GPIO8", "GPIO9", "AKTIVT_HOG", "--workers 1"):
        if krav not in rd:
            fail(f"README.md nämner inte {krav!r}")
    lic = os.path.join(_ROT, "LICENSE")
    if not os.path.exists(lic):
        fail("LICENSE saknas")
        return
    t = open(lic, encoding="utf-8").read()
    if not t.startswith("This is free and unencumbered software released into the public domain.") \
            or "<https://unlicense.org>" not in t:
        fail("LICENSE ska vara The Unlicense, ordagrant")
    if "copyright (c)" in t.lower() or "copyright (c)" in rd.lower():
        fail("ingen copyright-rad ska finnas (The Unlicense)")



CHECKS = [
    check_logik_importerbar,
    check_hastighet_och_cm_till_ms,
    check_okalibrerat,
    check_okand_position,
    check_ogiltig_indata,
    check_svep_relativ,
    check_andlage_exakt,
    check_lage,
    check_position_efter,
    check_vitlista,
    check_texter,
    # Steg 2
    check_requirements,
    check_db_schema,
    check_vagrar_starta_utan_hemligheter,
    check_enhet_floede,
    check_long_poll_vantar,
    check_enhet_start_under_pagaende,
    check_enhet_auth,
    check_admin_auth_och_sida,
    check_ett_uppdrag_i_taget,
    check_timeout,
    check_agent,
    check_inget_dygnstak,
    check_tidtagning,
    check_esp32_rela,
    check_esp32_filer,
    check_admin_kontrast,
    # Steg 7: svar i samtalet, ingen utringning
    check_ingen_utringning_i_koden,
    check_agent_svar_i_samtalet,
    check_agent_vantar_pa_enheten,
    check_agent_timeout_i_samtalet,
    check_admin_ingen_utringning,
    # Steg 6: absolut höjd
    check_svep_hojd,
    check_hojd_nekanden,
    check_agent_hojd,
    check_admin_hojd,
    # Steg 8: profiler
    check_profil_normalisering,
    check_profil_svep,
    check_profil_texter,
    check_profil_db,
    check_agent_profil,
    check_admin_profil,
    # Steg 9: norrländskt kort
    check_korta_texter,
    check_exakta_korta_texter,
    # Publikt repo
    check_egna_nummer_ur_miljon,
    check_esp32_rela_aktivt_lag,
    check_deploy_mallar,
    check_inga_hemligheter,
]


def main():
    for kontroll in CHECKS:
        try:
            kontroll()
        except Exception as e:  # en trasig kontroll ska inte döda hela sviten
            fail(f"{kontroll.__name__} kastade: {type(e).__name__}: {e}")

    if _fel:
        print(f"VERIFIERING FÖLL — {len(_fel)} problem:\n")
        for i, msg in enumerate(_fel[:200], 1):
            print(f"  {i}. {msg}")
        if len(_fel) > 200:
            print(f"  … och {len(_fel) - 200} till")
        sys.exit(1)

    print(f"VERIFIERING OK — {len(CHECKS)} kontroller godkända.")
    sys.exit(0)


if __name__ == "__main__":
    main()
