"""Opdrachtenkanaal: het verbindingsproces neemt hiermee aansturingen aan, toetst ze en voert ze bewaakt uit.

Een opdracht is een klein JSON-bestand in opdrachten/nieuw/. Het verbindingsproces (en niets anders) pakt het tussen
twee leesvragen op, toetst het tegen stuurbaar.py én de actuele meetwaarden, voert het uit of weigert het, en schrijft
het resultaat naar opdrachten/klaar/. De kabel blijft zo van één proces en elke actie staat in dezelfde tijdbasis als de
meting.

Veiligheid zit in lagen: (1) het proces neemt alleen opdrachten aan als het met --klasse C of D is gestart; (2) alleen
acties uit stuurbaar.py met bekende, bevestigde bytes gaan de lijn op; (3) voorwaarden worden vóór én tijdens de actie
getoetst; (4) na afloop laat het de ECU weer zelf regelen en leest het terug. Voor de Matiz is nog niets verzendbaar,
dus elke opdracht wordt nu geweigerd met reden "bytes onbekend" — precies de bedoeling tot we het protocol kennen.

Gebruik als module vanuit het verbindingsproces:
    kanaal = Kanaal(basis_dir, klasse="C", actielog=log)   # klasse=None => neemt niets aan
    kanaal.verwerk_wachtende(context)                       # elke ronde aanroepen
Een Context levert de actuele signalen en de guarded lees/stuur-verzoeken (zie DrogeContext onderaan).

Droge zelftest (nep-ECU, geen kabel):  python -I opdracht.py --droog
"""
import json
import os
import time

import stuurbaar


def _nu():
    return round(time.time(), 2)


class Kanaal:
    def __init__(self, basis_dir, klasse=None, actielog=None):
        self.klasse = klasse  # None, "C" of "D": welke acties dit proces mag uitvoeren
        self.log = actielog
        self.nieuw = os.path.join(basis_dir, "nieuw")
        self.klaar = os.path.join(basis_dir, "klaar")
        self.stopvlag = os.path.join(basis_dir, "stop")
        for d in (self.nieuw, self.klaar):
            os.makedirs(d, exist_ok=True)
        self.afbreken = False

    # -- queue ---------------------------------------------------------------
    def _wachtende(self):
        try:
            namen = sorted(n for n in os.listdir(self.nieuw) if n.endswith(".json"))
        except FileNotFoundError:
            return []
        return [os.path.join(self.nieuw, n) for n in namen]

    def _schrijf_resultaat(self, opdracht, uitkomst, reden, extra=None):
        doc = dict(opdracht, uitkomst=uitkomst, reden=reden, afgehandeld=_nu())
        if extra:
            doc.update(extra)
        pad = os.path.join(self.klaar, os.path.basename(opdracht.get("_bestand", f"{_nu()}.json")))
        tmp = pad + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=1)
        os.replace(tmp, pad)
        if self.log:
            self.log.write(json.dumps({"t": _nu(), "uitkomst": uitkomst, "sleutel": opdracht.get("sleutel"),
                                       "waarde": opdracht.get("waarde"), "reden": reden}) + "\n")
            self.log.flush()
        return doc

    def _opruimen(self, bestand):
        try:
            os.remove(bestand)
        except OSError:
            pass

    def verwerk_wachtende(self, context):
        """Handel de oudste wachtende opdracht af (één per aanroep). Geeft het resultaatdoc of None."""
        if os.path.exists(self.stopvlag):
            self.afbreken = True
        wacht = self._wachtende()
        if not wacht:
            return None
        bestand = wacht[0]
        try:
            opdracht = json.load(open(bestand))
        except (ValueError, OSError) as e:
            self._opruimen(bestand)
            return None
        opdracht["_bestand"] = os.path.basename(bestand)
        self._opruimen(bestand)
        return self._afhandelen(opdracht, context)

    # -- afhandeling ---------------------------------------------------------
    def _afhandelen(self, opdracht, context):
        sleutel = opdracht.get("sleutel")
        waarde = opdracht.get("waarde")
        if self.klasse is None:
            return self._schrijf_resultaat(opdracht, "geweigerd", "dit proces neemt geen aansturing aan "
                                           "(start met --klasse C of D)")
        actie = stuurbaar.ACTIES.get(sleutel)
        if actie is None:
            return self._schrijf_resultaat(opdracht, "geweigerd", f"onbekende actie: {sleutel!r}")
        if actie["klasse"] == "D" and self.klasse != "D":
            return self._schrijf_resultaat(opdracht, "geweigerd", "actie is klasse D; dit proces draait op klasse C")
        if actie["klasse"] == "C" and self.klasse not in ("C", "D"):
            return self._schrijf_resultaat(opdracht, "geweigerd", "geen toestemming voor klasse C")
        sig = context.sig()
        mag, reden = stuurbaar.beoordeel(sleutel, waarde, sig)
        if not mag:
            return self._schrijf_resultaat(opdracht, "geweigerd", reden, {"meetwaarden_vooraf": _kort(sig)})
        return self._uitvoeren(opdracht, actie, waarde, context, sig)

    def _uitvoeren(self, opdracht, actie, waarde, context, sig0):
        """Alleen bereikbaar voor een verzendbare actie (bekende bytes). Bewaakt uitvoeren en netjes loslaten."""
        if actie["loslaten"] is None and not actie.get("zelf_loslaten"):
            return self._schrijf_resultaat(opdracht, "geweigerd",
                                           "geen loslaat-verzoek bekend; actie niet veilig terug te zetten")
        tijdlijn = []
        voor = {b: _hex(context.query_blok(b)) for b in actie["lees_tijdens"]}
        try:
            verzoek = actie["verzoek"](waarde)
            antwoord = context.stuur(verzoek)
        except Exception as e:
            self._losmaken(actie, context)
            return self._schrijf_resultaat(opdracht, "mislukt", f"verzenden faalde: {e}",
                                            {"verzonden": _hex(bytes(verzoek)) if 'verzoek' in dir() else None})
        start = time.time()
        einde = "duur verstreken"
        while time.time() - start < actie["max_duur_s"]:
            if self.afbreken or os.path.exists(self.stopvlag):
                einde = "gestopt"
                break
            meting = {b: _hex(context.query_blok(b)) for b in actie["lees_tijdens"]}
            tijdlijn.append({"t": round(time.time() - start, 2), **meting})
            mag, reden = stuurbaar.beoordeel(opdracht["sleutel"], waarde, context.sig())
            if not mag:
                einde = f"voorwaarde geschonden: {reden}"
                break
        self._losmaken(actie, context)
        na = {b: _hex(context.query_blok(b)) for b in actie["lees_tijdens"]}
        return self._schrijf_resultaat(opdracht, "uitgevoerd", einde, {
            "verzonden": _hex(bytes(verzoek)), "antwoord": _hex(antwoord),
            "blokken_voor": voor, "blokken_na": na, "tijdlijn": tijdlijn,
            "meetwaarden_vooraf": _kort(sig0)})

    def _losmaken(self, actie, context):
        if actie["loslaten"] is not None:
            try:
                context.stuur(actie["loslaten"])
            except Exception:
                pass


def _hex(b):
    return b.hex() if isinstance(b, (bytes, bytearray)) else b


def _kort(sig):
    namen = ("toerental", "snelheid", "koelwater", "accuspanning", "gas_dicht", "egr_stand_v")
    return {n: sig.get(n) for n in namen if n in sig}


# -- schrijven van een opdracht (ook gebruikt door stuur.py) ---------------------------------------------------
def leg_neer(basis_dir, sleutel, waarde=None, duur_s=None, door="stuur.py"):
    nieuw = os.path.join(basis_dir, "nieuw")
    os.makedirs(nieuw, exist_ok=True)
    oid = f"{time.time():.3f}".replace(".", "_")
    doc = {"id": oid, "sleutel": sleutel, "waarde": waarde, "duur_s": duur_s, "door": door, "aangemaakt": _nu()}
    pad = os.path.join(nieuw, f"{oid}.json")
    with open(pad + ".tmp", "w") as fh:
        json.dump(doc, fh)
    os.replace(pad + ".tmp", pad)
    return pad


# -- droge zelftest ---------------------------------------------------------------------------------------------
class DrogeContext:
    """Nep-context voor de zelftest: vaste signalen en een nep-ECU die ook een verzonnen stuurcommando beantwoordt."""
    def __init__(self, signalen_dict, blok=b"\x00" * 32):
        self._sig = signalen_dict
        self._blok = blok
        self.verzonden = []

    def sig(self):
        return self._sig

    def query_blok(self, blok):
        return self._blok

    def stuur(self, data):
        self.verzonden.append(bytes(data))
        return bytes([0x70, data[1] if len(data) > 1 else 0])  # positief antwoord-achtig


def _droog():
    import tempfile
    basis = tempfile.mkdtemp(prefix="opdracht_droog_")
    import io
    log = io.StringIO()
    print(f"droge test in {basis}\n")

    # 1) klasse None: alles geweigerd
    k0 = Kanaal(basis, klasse=None, actielog=log)
    leg_neer(basis, "egr_klep", 50)
    r = k0.verwerk_wachtende(DrogeContext({}))
    print("klasse None ->", r["uitkomst"], "|", r["reden"])
    assert r["uitkomst"] == "geweigerd"

    # 2) klasse C, Matiz-actie: geweigerd want bytes onbekend
    kC = Kanaal(basis, klasse="C", actielog=log)
    goed = {"toerental": 0, "accuspanning": 12.4, "snelheid": 0, "koelwater": 85, "gas_dicht": True}
    leg_neer(basis, "egr_klep", 50)
    r = kC.verwerk_wachtende(DrogeContext(goed))
    print("Matiz egr_klep ->", r["uitkomst"], "|", r["reden"])
    assert r["uitkomst"] == "geweigerd" and "bytes onbekend" in r["reden"]

    # 3) klasse C, verzendbare proefactie, maar motor draait: geweigerd op voorwaarde
    stuurbaar.ACTIES["_proef"] = dict(
        omschrijving="alleen voor de zelftest", klasse="C", toestand="contact_aan",
        waarde_bereik=(0, 100), max_duur_s=0.2, verzoek=lambda w: [0x30, 0x99, int(w)],
        loslaten=[0x30, 0x99, 0x00], lees_tijdens=["01"], bron="op_tafel_bevestigd")
    leg_neer(basis, "_proef", 40)
    r = kC.verwerk_wachtende(DrogeContext({"toerental": 900, "accuspanning": 12.4}))
    print("proef, motor draait ->", r["uitkomst"], "|", r["reden"])
    assert r["uitkomst"] == "geweigerd"

    # 4) klasse C, verzendbare proefactie, voorwaarden goed: uitgevoerd, verzonden + losgelaten
    ctx = DrogeContext({"toerental": 0, "accuspanning": 12.4})
    leg_neer(basis, "_proef", 40)
    r = kC.verwerk_wachtende(ctx)
    print("proef, contact aan ->", r["uitkomst"], "|", r["reden"], "| verzonden", [b.hex() for b in ctx.verzonden])
    assert r["uitkomst"] == "uitgevoerd"
    assert ctx.verzonden[0].hex() == "309928" and ctx.verzonden[-1].hex() == "309900"  # commando en loslaten

    print("\nalle controles geslaagd")
    print("\nactielog:")
    print(log.getvalue())


if __name__ == "__main__":
    import sys
    if "--droog" in sys.argv:
        _droog()
    else:
        print(__doc__)
