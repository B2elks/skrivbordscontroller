"""Ren logik för Skrivbordet — ingen I/O, inga beroenden utanför stdlib.

Allt som avgör hur långt och hur länge bordet ska köras bor här, så att
verify.py kan svepa reglerna utan databas, nätverk eller hårdvara.

Positionen uppskattas av tid × hastighet; hastigheten per riktning kommer ur
kalibreringen: (max − min) / restid.
"""

import math
import os
import re
from dataclasses import dataclass, replace
from typing import Optional, Union

# Regel 3: stå/sitt kör full restid plus marginal så att bordet säkert når ändläget.
MARGINAL_S = 1.5

# Tillåten begärd sträcka för relativa kommandon (cm).
MIN_BEGARD_CM = 1
MAX_BEGARD_CM = 80

# Mindre än så här kvar till ändläget räknas som "står redan i ändläget".
# Lika med halva avrundningssteget i uppläsningen, så agenten aldrig säger "0 centimeter".
MINSTA_CM = 0.5


# Regel 6: alla svenska nummer får styra bordet, utom de egna numren i kedjan
# (numret man ringer och websocket-numret). De är platsspecifika och kommer ur
# miljön: SKRIVBORD_EGNA_NUMMER, kommaseparerad, tom som standard.
def las_egna_nummer(text) -> frozenset:
    """'+46701740698, 0701740699' → frozenset av E.164-nummer. Ogiltiga delar hoppas över."""
    if not isinstance(text, str):
        return frozenset()
    return frozenset(n for n in (normalisera_nummer(d.strip()) for d in text.split(",")) if n)

# Agenten läser upp texterna ordagrant i norrländsk stil: så få ord som möjligt,
# inga artighetsfraser, inget "jag". verify.py kräver högst 8 ord per text.
TEXT_SVARAR_INTE = "Bordet svarar inte."
TEXT_ROR_SIG_REDAN = "Vänta, det rör sig."
TEXT_OKALIBRERAT = "Inte kalibrerat."
TEXT_OKAND_POSITION = "Vet inte var. Säg stå eller sitt."
TEXT_EJ_TILLATET = "Bara svenska nummer."
TEXT_KLAR_OKAND_POSITION = "Stannat. Vet inte var."
# Bekräftelse före körning. Agentens endpoints svarar först när bordet stannat,
# så den läses normalt aldrig upp — men den får inte lova något som inte sker.
TEXT_JO = "Jo."


@dataclass(frozen=True)
class Kalibrering:
    min_cm: Optional[float]
    max_cm: Optional[float]
    restid_upp_s: Optional[float]
    restid_ner_s: Optional[float]

    def giltig(self) -> bool:
        varden = (self.min_cm, self.max_cm, self.restid_upp_s, self.restid_ner_s)
        if any(not _ar_tal(v) for v in varden):
            return False
        return self.max_cm > self.min_cm and self.restid_upp_s > 0 and self.restid_ner_s > 0

    def restid(self, riktning: str) -> Optional[float]:
        if not self.giltig():
            return None
        return self.restid_upp_s if riktning == "upp" else self.restid_ner_s if riktning == "ner" else None

    def hastighet(self, riktning: str) -> Optional[float]:
        """cm per sekund i given riktning, eller None om okalibrerat/okänd riktning."""
        t = self.restid(riktning)
        if t is None:
            return None
        return (self.max_cm - self.min_cm) / t


@dataclass(frozen=True)
class Uppdrag:
    rela: str                      # "upp" eller "ner"
    ms: int                        # hur länge reläet ska vara draget
    faktisk_cm: Optional[float]    # verklig sträcka efter klippning (None om startläget är okänt)
    ny_position: float             # uppskattad position efter körning
    klippt: bool                   # True om ändläget kortade sträckan
    sag: str                       # bekräftelse som agenten läser upp
    lage: Optional[str] = None     # "stå"/"sitt" när uppdraget kör till ändläget och nollställer positionen


@dataclass(frozen=True)
class Nekande:
    sag: str                       # förklaring som agenten läser upp


Plan = Union[Uppdrag, Nekande]


def _ar_tal(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def avrunda(x: float) -> int:
    """Avrunda halva uppåt (Pythons round() avrundar 98,5 till 98)."""
    return int(math.floor(x + 0.5))


def _riktning(r) -> Optional[str]:
    if not isinstance(r, str):
        return None
    r = r.strip().lower()
    return r if r in ("upp", "ner") else None


def _lage(lage) -> Optional[str]:
    if not isinstance(lage, str):
        return None
    lage = lage.strip().lower()
    if lage in ("stå", "sta"):
        return "stå"
    if lage == "sitt":
        return "sitt"
    return None


def _cm(cm) -> Optional[float]:
    """Tolka begärd sträcka; None om den inte är ett ändligt tal."""
    if isinstance(cm, bool):
        return None
    if isinstance(cm, str):
        try:
            cm = float(cm.strip().replace(",", "."))
        except ValueError:
            return None
    if not _ar_tal(cm):
        return None
    return float(cm)


def cm_till_ms(kal: Kalibrering, riktning: str, cm: float) -> int:
    """Regel 1: ms = round(cm / hastighet[riktning] * 1000)."""
    return round(cm / kal.hastighet(riktning) * 1000)


def ms_till_cm(kal: Kalibrering, riktning: str, ms: float) -> float:
    return ms / 1000 * kal.hastighet(riktning)


def _talord(cm: float) -> str:
    return f"{avrunda(cm)} centimeter"


def planera_relativ(kal: Optional[Kalibrering], position_cm: Optional[float], riktning, cm) -> Plan:
    """Planera "höj/sänk N cm". Klipper mot ändläget (regel 2), nekar vid okänd
    position (regel 4) och okalibrerat bord (regel 10)."""
    if kal is None or not kal.giltig():
        return Nekande(TEXT_OKALIBRERAT)
    r = _riktning(riktning)
    if r is None:
        return Nekande("Upp eller ner?")
    begard = _cm(cm)
    if begard is None or not (MIN_BEGARD_CM <= begard <= MAX_BEGARD_CM):
        return Nekande(f"Mellan {MIN_BEGARD_CM} och {MAX_BEGARD_CM} centimeter.")
    if position_cm is None or not _ar_tal(position_cm):
        return Nekande(TEXT_OKAND_POSITION)

    start = _klipp(kal, position_cm)
    kvar = (kal.max_cm - start) if r == "upp" else (start - kal.min_cm)
    if kvar < MINSTA_CM - 1e-9:
        return Nekande("Det är i topp." if r == "upp" else "Det är i botten.")
    return replace(_tidsstyrt(kal, start, r, begard), sag=TEXT_JO)


def _klipp(kal: Kalibrering, cm: float) -> float:
    """En uppskattning eller ett mål utanför intervallet behandlas som ändläget."""
    return min(max(float(cm), kal.min_cm), kal.max_cm)


def _tidsstyrt(kal: Kalibrering, start: float, r: str, stracka: float) -> Uppdrag:
    """Regel 1 + 2: kör stracka cm åt r från start, klippt mot ändläget. Utan sag —
    anroparen formulerar bekräftelsen."""
    if r == "upp":
        ny = min(start + stracka, kal.max_cm)
    else:
        ny = max(start - stracka, kal.min_cm)
    faktisk = abs(ny - start)
    kvar = (kal.max_cm - start) if r == "upp" else (start - kal.min_cm)
    return Uppdrag(rela=r, ms=cm_till_ms(kal, r, faktisk), faktisk_cm=faktisk,
                   ny_position=ny, klippt=stracka > kvar + 1e-9, sag="")


def planera_lage(kal: Optional[Kalibrering], lage, position_cm: Optional[float] = None) -> Plan:
    """Regel 3: stå = upp till max, sitt = ner till min, full restid + marginal.
    Fungerar även från okänd position — det är så positionen återställs."""
    if kal is None or not kal.giltig():
        return Nekande(TEXT_OKALIBRERAT)
    l = _lage(lage)
    if l is None:
        return Nekande("Stå eller sitt?")
    r = "upp" if l == "stå" else "ner"
    mal = kal.max_cm if l == "stå" else kal.min_cm
    ms = avrunda((kal.restid(r) + MARGINAL_S) * 1000)
    faktisk = None
    if position_cm is not None and _ar_tal(position_cm):
        faktisk = abs(mal - _klipp(kal, position_cm))
    return Uppdrag(rela=r, ms=ms, faktisk_cm=faktisk, ny_position=mal, klippt=False, sag=TEXT_JO, lage=l)


def planera_hojd(kal: Optional[Kalibrering], position_cm: Optional[float], mal_cm) -> Plan:
    """Planera "ställ bordet på N centimeter". Målet klipps till [min, max] (regel 2).
    Mål i ändläget körs som stå/sitt (regel 3): full restid + marginal nollställer
    positionen, vilket är säkrare än att tidsstyra hela vägen dit. Annars tidsstyrt
    som en relativ flytt. Okänd position nekas (regel 4), okalibrerat likaså (regel 10)."""
    if kal is None or not kal.giltig():
        return Nekande(TEXT_OKALIBRERAT)
    mal = _cm(mal_cm)
    if mal is None or mal <= 0:
        return Nekande("Hur högt?")
    if position_cm is None or not _ar_tal(position_cm):
        return Nekande(TEXT_OKAND_POSITION)

    start = _klipp(kal, position_cm)
    klippt_mal = _klipp(kal, mal)
    klippt = klippt_mal != mal
    klipptext = f"Går bara till {avrunda(klippt_mal)}." if klippt else ""
    if abs(klippt_mal - start) < MINSTA_CM - 1e-9:
        if klippt:
            return Nekande(f"{klipptext} Står redan där.")
        return Nekande(f"Står redan på {avrunda(start)}.")

    r = "upp" if klippt_mal > start else "ner"
    if klippt_mal in (kal.min_cm, kal.max_cm):
        u = planera_lage(kal, "stå" if r == "upp" else "sitt", start)
    else:
        u = _tidsstyrt(kal, start, r, abs(klippt_mal - start))
    return replace(u, sag=klipptext or TEXT_JO, klippt=klippt)


def position_efter(kal: Optional[Kalibrering], start_cm: Optional[float], rela: str,
                   ms_kord: int, lage: Optional[str] = None) -> Optional[float]:
    """Uppskattad position efter att reläet varit draget ms_kord ms.
    Stå/sitt nollställer till exakt ändläge oavsett startläge."""
    if kal is None or not kal.giltig():
        return None
    l = _lage(lage) if lage is not None else None
    if l == "stå":
        return kal.max_cm
    if l == "sitt":
        return kal.min_cm
    r = _riktning(rela)
    if start_cm is None or not _ar_tal(start_cm) or r is None:
        return None
    stracka = ms_till_cm(kal, r, max(0, ms_kord))
    ny = start_cm + stracka if r == "upp" else start_cm - stracka
    return min(max(ny, kal.min_cm), kal.max_cm)


_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalisera_nummer(nummer) -> Optional[str]:
    """Svenska nummerformat → E.164 ('070-174 06 05' → '+46701740605'). None om ogiltigt."""
    if not isinstance(nummer, str):
        return None
    n = re.sub(r"[\s\-().]", "", nummer)
    if n.startswith("00"):
        n = "+" + n[2:]
    elif n.startswith("0"):
        n = "+46" + n[1:]
    if not _E164.match(n):
        return None
    if n.startswith("+46"):
        if n.startswith("+460"):          # "+46 (0)738…" skrivs ibland med inledande nolla
            n = "+46" + n[4:]
        if not re.fullmatch(r"\+46[1-9]\d{6,8}", n):
            return None
    return n


# Läses en gång vid import — det enda miljöberoendet i modulen.
EGNA_NUMMER = las_egna_nummer(os.environ.get("SKRIVBORD_EGNA_NUMMER", ""))


def far_styra(nummer) -> bool:
    """Svenskt nummer (+46) som inte är ett av de egna. Dolda och utländska nummer nekas."""
    n = normalisera_nummer(nummer)
    return n is not None and n.startswith("+46") and n not in EGNA_NUMMER



def text_klar(position_cm: float, begard_cm: Optional[float] = None,
              faktisk_cm: Optional[float] = None) -> str:
    """Svaret agenten läser upp när bordet är klart. Nämner om ändläget kortade sträckan."""
    if begard_cm is not None and faktisk_cm is not None and faktisk_cm < begard_cm - 1e-9:
        return f"Klart. {_talord(position_cm)}, längre går det inte."
    return f"Klart. {_talord(position_cm)}."


# --- Profiler: "spara min höjd som Björn", "ställ på Björns höjd" ---

MAX_PROFILNAMN = 40
MAX_PROFILER = 50

TEXT_SPARA_OKAND_POSITION = TEXT_OKAND_POSITION
TEXT_SPARA_OKALIBRERAT = TEXT_OKALIBRERAT
TEXT_PROFILNAMN_OGILTIGT = "Ogiltigt namn."
TEXT_PROFILTAK = "Fullt. Ta bort någon först."
TEXT_INGA_PROFILER = "Inga sparade."

# Skiljetecken som taligenkänningen kan lägga runt ett namn.
_KANTTECKEN = " .,!?;:\"'«»“”‘’"
# Ord efter namnet som betyder samma profil: "Björns höjd" = "Björn".
_EFTERLED = (" höjd", " profil")


@dataclass(frozen=True)
class ProfilSparning:
    nyckel: str                    # normaliserat namn, primärnyckel
    namn: str                      # visningsnamn (befintlig profils namn behålls)
    cm: float                      # sparad höjd, avrundad till 0,5 cm
    gammal_cm: Optional[float]     # tidigare höjd vid överskrivning, annars None
    sag: str


def profil_visningsnamn(namn) -> Optional[str]:
    """Trimmat namn med kollapsade blanksteg, eller None om ogiltigt
    (inte sträng, längre än MAX_PROFILNAMN eller utan bokstav)."""
    if not isinstance(namn, str):
        return None
    n = " ".join(" ".join(namn.split()).strip(_KANTTECKEN).split())
    if not n or len(n) > MAX_PROFILNAMN or not any(c.isalpha() for c in n):
        return None
    return n


def profilnyckel(namn) -> Optional[str]:
    """Nyckeln ett namn sparas under: visningsnamnet casefoldat. Tar inte bort
    genitiv-s — det gör bara uppslaget, och bara mot en profil som finns."""
    v = profil_visningsnamn(namn)
    return v.casefold() if v else None


def hitta_profilnyckel(namn, nycklar) -> Optional[str]:
    """Befintlig nyckel som namnet syftar på, eller None. Exakt träff går först;
    sedan utan genitiv-s ("Björns" → "björn") och utan efterled ("Björns höjd")."""
    k = profilnyckel(namn)
    if k is None:
        return None
    kandidater = [k]
    for e in _EFTERLED:
        if k.endswith(e) and len(k) > len(e):
            kandidater.append(k[:-len(e)].strip())
    for c in list(kandidater):
        if len(c) > 1 and c.endswith("s"):
            kandidater.append(c[:-1])
    for c in kandidater:
        if c in nycklar:
            return c
    return None


def halv_cm(x: float) -> float:
    """Avrunda till närmaste halva centimeter, halva uppåt."""
    return math.floor(x * 2 + 0.5) / 2


def genitiv(namn: str) -> str:
    """Svensk genitiv: Björn → Björns, men Hans/Max/Liz får inget extra s."""
    return namn if namn[-1:].lower() in ("s", "x", "z") else namn + "s"


def _sv_sortering(namn: str) -> str:
    # Å, Ä, Ö sist och i den ordningen, som i svenska alfabetet.
    return namn.casefold().translate(str.maketrans({"å": "{", "ä": "|", "ö": "}"}))


def sortera_profiler(profiler) -> list:
    return sorted(profiler, key=lambda p: (_sv_sortering(p["namn"]), p["nyckel"]))


def _och(delar) -> str:
    delar = list(delar)
    return delar[0] if len(delar) == 1 else ", ".join(delar[:-1]) + " och " + delar[-1]


def planera_spara_profil(kal: Optional[Kalibrering], position_cm, namn, profiler):
    """Spara aktuell position under namn. profiler = befintliga {nyckel, namn, cm}.
    Returnerar ProfilSparning eller Nekande."""
    if kal is None or not kal.giltig():
        return Nekande(TEXT_SPARA_OKALIBRERAT)
    visning = profil_visningsnamn(namn)
    if visning is None:
        return Nekande(TEXT_PROFILNAMN_OGILTIGT)
    if position_cm is None or not _ar_tal(position_cm):
        return Nekande(TEXT_SPARA_OKAND_POSITION)
    profiler = list(profiler)
    per_nyckel = {p["nyckel"]: p for p in profiler}
    nyckel = hitta_profilnyckel(namn, per_nyckel) or profilnyckel(namn)
    cm = halv_cm(_klipp(kal, position_cm))
    befintlig = per_nyckel.get(nyckel)
    if befintlig is None:
        if len(profiler) >= MAX_PROFILER:
            return Nekande(TEXT_PROFILTAK)
        return ProfilSparning(nyckel, visning, cm, None, f"Sparat. {visning}, {avrunda(cm)}.")
    gammal = befintlig["cm"]
    return ProfilSparning(nyckel, befintlig["namn"], cm, gammal,
                          f"Ändrat. {befintlig['namn']}, {avrunda(cm)}, var {avrunda(gammal)}.")


def valj_profil(namn, profiler):
    """Profilen namnet syftar på (dict), eller Nekande som räknar upp de som finns."""
    profiler = sortera_profiler(profiler)
    visning = profil_visningsnamn(namn)
    if visning is None:
        return Nekande("Vilket namn?")
    nyckel = hitta_profilnyckel(namn, {p["nyckel"] for p in profiler})
    for p in profiler:
        if p["nyckel"] == nyckel:
            return p
    finns = f"Finns: {_och(p['namn'] for p in profiler)}." if profiler else TEXT_INGA_PROFILER
    return Nekande(f"Ingen {visning}. {finns}")


def planera_profil(kal: Optional[Kalibrering], position_cm, profil) -> Plan:
    """Kör till en sparad höjd: exakt samma plan som planera_hojd(profil cm), bara
    med profilens namn i meningarna."""
    plan = planera_hojd(kal, position_cm, profil["cm"])
    if plan.sag in (TEXT_OKALIBRERAT, TEXT_OKAND_POSITION):
        return plan
    g = genitiv(profil["namn"])
    klippt = _klipp(kal, profil["cm"]) != profil["cm"]
    if isinstance(plan, Nekande):
        if klippt:
            return plan
        return Nekande(f"Står redan på {g}.")
    if plan.klippt:
        return plan
    return replace(plan, sag=TEXT_JO)


def text_profil_klar(namn: str, profil_cm: float, position_cm: float) -> str:
    """Svaret när bordet stannat efter till_profil."""
    g = genitiv(namn)
    if abs(position_cm - profil_cm) < MINSTA_CM:
        return f"Klart. {g}, {_talord(profil_cm)}."
    return f"Klart. {avrunda(position_cm)}, närmare {g} går inte."


def text_profiler(profiler) -> str:
    profiler = sortera_profiler(profiler)
    if not profiler:
        return TEXT_INGA_PROFILER
    return ", ".join(f"{p['namn']} {avrunda(p['cm'])}" for p in profiler) + "."
