"""Relästyrning för Skrivbordet — MicroPython 1.29, testbar på host.

Standard är AKTIVT HÖGA reläer: pinne hög = relä draget = bordet rör sig.
Med aktivt_hog=False gäller det omvända (pinne låg = draget) för moduler
som triggar på låg nivå. Allt nedan talar om "draget"/"släppt", inte nivåer.
Spärrar (spec regel 9):
  - aldrig båda dragna samtidigt: den andra släpps alltid först
  - hårt tak per drag: MAX_MS
  - båda släppta direkt vid init, och alltid efter en körning (try/finally)
  - samma uppdrags-id körs aldrig två gånger

Pinnar, klocka och sömn injiceras så att verify.py kan köra klassen med
en Pin-mock och falsk klocka.
"""

import time

try:
    _tid_ms = time.ticks_ms
    _diff = time.ticks_diff
    _sov_ms = time.sleep_ms
except AttributeError:  # CPython (verify)
    def _tid_ms():
        return int(time.monotonic() * 1000)

    def _diff(a, b):
        return a - b

    def _sov_ms(ms):
        time.sleep(ms / 1000)

RIKTNINGAR = ("upp", "ner")


class Rela:
    def __init__(self, pin_upp, pin_ner, max_ms, tid_ms=None, sov_ms=None, aktivt_hog=True):
        self._pin = {"upp": pin_upp, "ner": pin_ner}
        self._dragen = 1 if aktivt_hog else 0
        self._slappt = 1 - self._dragen
        self.max_ms = int(max_ms)
        self._tid_ms = tid_ms or _tid_ms
        self._sov_ms = sov_ms or _sov_ms
        self.senaste_id = None
        self.slapp()

    def slapp(self):
        self._pin["upp"].value(self._slappt)
        self._pin["ner"].value(self._slappt)

    def _dra(self, rela):
        # Släpp den andra först, så att båda aldrig är dragna ens ett ögonblick.
        andra = "ner" if rela == "upp" else "upp"
        self._pin[andra].value(self._slappt)
        self._pin[rela].value(self._dragen)

    def _forbered(self, id_, rela, ms=0):
        """Kontrollera indata och förbruka id:t. None ⇒ ska inte köras."""
        if rela not in RIKTNINGAR:
            raise ValueError("okänt relä: %r" % (rela,))
        ms = int(ms)
        if id_ is not None and id_ == self.senaste_id:
            return None
        # id:t förbrukas före körningen: ett fel mitt i får inte leda till att
        # samma flytt körs igen (bordet har redan rört sig en bit).
        self.senaste_id = id_
        return max(0, min(ms, self.max_ms))

    def kor(self, id_, rela, ms):
        """Dra reläet i ms millisekunder (kapat till max_ms). Returnerar faktisk
        tid i ms, eller None om id:t redan körts."""
        ms = self._forbered(id_, rela, ms)
        if ms is None:
            return None
        if ms == 0:
            self.slapp()
            return 0
        start = self._tid_ms()
        try:
            self._dra(rela)
            self._sov_ms(ms)
        finally:
            self.slapp()
        return _diff(self._tid_ms(), start)

    def kor_med_stopp(self, id_, rela, fortsatt, bit_ms=500):
        """Tidtagning: håll reläet tills fortsatt(gatt_ms) svarar något annat än
        True, tills max_ms nåtts, eller tills fortsatt kastar (nätfel ⇒ stanna).
        Reläet är draget även medan fortsatt() väntar på nätet."""
        if self._forbered(id_, rela) is None:
            return None
        start = self._tid_ms()
        try:
            self._dra(rela)
            while True:
                gatt = _diff(self._tid_ms(), start)
                kvar = self.max_ms - gatt
                if kvar <= 0:
                    break
                self._sov_ms(min(bit_ms, kvar))
                gatt = _diff(self._tid_ms(), start)
                if gatt >= self.max_ms:
                    break
                try:
                    if fortsatt(gatt) is not True:
                        break
                except Exception:
                    break
        finally:
            self.slapp()
        return _diff(self._tid_ms(), start)
