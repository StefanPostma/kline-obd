"""Wat er op een voertuig aangestuurd of gewist mag worden: de witte lijst naast signalen.py.

Eén regel per actie. Wat hier niet in staat, verstuurt het verbindingsproces nooit. Een actie is pas echt
verzendbaar als `verzoek` bekend is (de bytes) EN `bron` niet "vermoed" is. Voor de Matiz kennen we de bytes nog
niet (nergens gepubliceerd), dus alle acties staan op bron "vermoed" met `verzoek=None`: het proces weigert ze en
zegt waarom. Zo staat precies vast WAT we willen kunnen, zonder dat er iets de lijn op gaat voordat we het HOE weten.

De bytes leren we zonder risico voor de auto: meeluisteren met een tester (tweede kabel) of een losse ECU op tafel.
Pas als een actie daar is bevestigd, zet je `bron="op_tafel_bevestigd"` of `"afgeluisterd"` en vul je `verzoek` in.
"""

# -- voorwaarden per toestand: functies over de laatst gedecodeerde signalen (signalen.decode) ------------------
# Een voorwaarde geeft (ok, reden). Ontbreekt een signaal, dan is de voorwaarde niet vervuld (veilige kant).

def _heeft(sig, *namen):
    ontbreekt = [n for n in namen if sig.get(n) is None]
    return (False, "signaal ontbreekt: " + ", ".join(ontbreekt)) if ontbreekt else (True, "")


def contact_aan(sig):
    """Motor uit, contact aan: toerental 0, accu op peil."""
    ok, reden = _heeft(sig, "toerental", "accuspanning")
    if not ok:
        return ok, reden
    if sig["toerental"] > 50:
        return False, f"motor draait ({sig['toerental']} tpm); deze actie wil motor uit"
    if sig["accuspanning"] < 11.8:
        return False, f"accu te laag ({sig['accuspanning']:.1f} V)"
    return True, ""


def stationair(sig):
    """Warme, stabiele stationairloop en stilstand."""
    ok, reden = _heeft(sig, "toerental", "snelheid", "koelwater", "accuspanning", "gas_dicht")
    if not ok:
        return ok, reden
    if sig["snelheid"] > 0:
        return False, f"auto rijdt ({sig['snelheid']} km/u)"
    if not (700 <= sig["toerental"] <= 1200):
        return False, f"toerental buiten stationair ({sig['toerental']} tpm)"
    if sig["koelwater"] < 70:
        return False, f"motor niet warm ({sig['koelwater']:.0f} C)"
    if sig["accuspanning"] < 13.0:
        return False, f"accu te laag voor draaiende motor ({sig['accuspanning']:.1f} V)"
    if not sig["gas_dicht"]:
        return False, "gas staat niet dicht"
    return True, ""


# Tijdens rijden sturen we niets: leesfouten lopen dan op (4,6%) en de oorzaak is onbekend.
VOORWAARDEN = {"contact_aan": contact_aan, "stationair": stationair}


# -- de witte lijst --------------------------------------------------------------------------------------------
# verzoek: functie(waarde)->list[int] met de te verzenden bytes, of None zolang onbekend.
# loslaten: bytes om de ECU zelf weer te laten regelen, of None.
# lees_tijdens: blokken die tijdens de actie elke ronde snel gelezen worden om het effect te zien.
# bron: "afgeluisterd" | "op_tafel_bevestigd" | "vermoed". Alleen de eerste twee mogen de lijn op.

ACTIES = {
    "storingslamp": dict(
        omschrijving="storingslamp (MIL) aan/uit — onschuldige test van het hele stuurpad",
        klasse="C", toestand="contact_aan", waarde_bereik=(0, 1), max_duur_s=5.0,
        verzoek=None, loslaten=None, lees_tijdens=[], bron="vermoed",
        opmerking="eerste test: raakt de motor niet, laat alleen zien of aansturen aankomt"),
    "koelventilator": dict(
        omschrijving="koelventilatorrelais aan",
        klasse="C", toestand="contact_aan", waarde_bereik=(0, 1), max_duur_s=10.0,
        verzoek=None, loslaten=None, lees_tijdens=[], bron="vermoed"),
    "egr_klep": dict(
        omschrijving="EGR-klep op een percentage zetten (0–100), motor uit; stand teruglezen uit blok 01",
        klasse="C", toestand="contact_aan", waarde_bereik=(0, 100), max_duur_s=8.0,
        verzoek=None, loslaten=None, lees_tijdens=["01"], bron="vermoed",
        opmerking="beantwoordt of de klep goed sluit (rust 0,62 of 0,65 V) en of de kanalen vrij zijn"),
    "egr_klep_stationair": dict(
        omschrijving="EGR-klep openen bij stationair — de motor hoort te gaan hikken",
        klasse="C", toestand="stationair", waarde_bereik=(0, 100), max_duur_s=6.0,
        verzoek=None, loslaten=None, lees_tijdens=["01", "0C", "03"], bron="vermoed",
        opmerking="bewijst dat er uitlaatgas door de kanalen stroomt; direct afbreken onder 500 tpm"),
    "stationairregelaar": dict(
        omschrijving="stationairregelaar (IAC) op een aantal stappen zetten",
        klasse="C", toestand="stationair", waarde_bereik=(0, 255), max_duur_s=6.0,
        verzoek=None, loslaten=None, lees_tijdens=["0D", "0C"], bron="vermoed"),
    "injector_uit": dict(
        omschrijving="één injector uitschakelen (cilindernummer 1–3) — directe test voor het stotteren",
        klasse="C", toestand="stationair", waarde_bereik=(1, 3), max_duur_s=4.0,
        verzoek=None, loslaten=None, lees_tijdens=["03", "0C"], bron="vermoed",
        opmerking="laat zien welke cilinder het minst bijdraagt; alleen als de ECU cilinderuitschakeling kent"),
    "adaptatie_wissen": dict(
        omschrijving="geleerde waarden wissen (brandstofcorrectie, gasklep/IAC) — pas na een reparatie",
        klasse="D", toestand="contact_aan", waarde_bereik=None, max_duur_s=3.0,
        verzoek=None, loslaten=None, lees_tijdens=[], bron="vermoed",
        opmerking="klasse D: eerst een momentopname vooraf; de ECU leert daarna opnieuw"),
}


def beoordeel(sleutel, waarde, sig):
    """(mag, reden). Controleert bestaan, verzendbaarheid, waardebereik en de voorwaarden van de toestand."""
    actie = ACTIES.get(sleutel)
    if actie is None:
        return False, f"onbekende actie: {sleutel!r}"
    if actie["bron"] == "vermoed" or actie["verzoek"] is None:
        return False, "bytes onbekend: eerst afluisteren of op een losse ECU bevestigen"
    bereik = actie["waarde_bereik"]
    if bereik is not None:
        if waarde is None:
            return False, "waarde vereist"
        if not (bereik[0] <= waarde <= bereik[1]):
            return False, f"waarde {waarde} buiten {bereik}"
    toets = VOORWAARDEN.get(actie["toestand"])
    if toets is None:
        return False, f"onbekende toestand: {actie['toestand']}"
    return toets(sig)


def verzendbaar(sleutel):
    """Of de bytes van deze actie bekend en toegestaan zijn om te versturen (los van de huidige toestand)."""
    actie = ACTIES.get(sleutel)
    return bool(actie) and actie["verzoek"] is not None and actie["bron"] != "vermoed"
