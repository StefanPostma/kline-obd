"""Signaaldefinities voor de Matiz 0.8 (Siemens Sirius D32, 5WY5407A): KWP service 21, blok + byte -> grootheid.

Voertuigbestand: de gedeelde bron voor de uitlezer (leesplan), de analyse en de webpagina (betekenis van de bytes).

  from signalen import decode, vensters
  waarden = decode({"01": bytes..., "0C": bytes..., ...})   # alleen blokken die je gelezen hebt
  vensters(waarden)                                          # zelftest-vensters van de ECU volgens het handboek

Zekerheid per signaal:
  "pid"      vergeleken met de standaard OBD-PID op meerdere punten
  "handboek" gedrag past bij een ingang/parameter uit het werkplaatshandboek (M-150, 2002, sectie 1F)
  "gedrag"   alleen afgeleid uit hoe de waarde beweegt; nergens gedocumenteerd
Er is niets van deze indeling door de fabrikant bevestigd.
"""

import re

ADC = 5 / 1023  # blok 01 bevat 10-bit ADC-woorden op 5 V

NAAM = "Chevrolet/Daewoo Matiz M200 0.8 (Siemens Sirius D32, 5WY5407A)"
AANKNOPEN = ("fast",)  # ISO 14230 fast init op 0x33; de ECU antwoordt vanaf 0x11

# identiteit van de ECU: het verzoek en hoe het antwoord gelezen wordt (voor het zijbestand <opname>.meta.json)
IDENTITEIT_VERZOEK = [0x1A, 0x80]

# -- Leesplan: welke blokken/PID's je opvraagt om de signalen hieronder te kunnen vullen -------------------------
# Fabrikantblokken via KWP service 21 (readDataByLocalId): verzoek [0x21, id], het antwoord begint met 61 <id> en
# daarna 32 databytes (strip de eerste 2 bytes). Standaard-PID's via service 01: verzoek [0x01, pid], antwoord 41 <pid>..
# De verbinding (egr_capture.py) gebruikt dit; de webpagina kan het importeren om zélf rechtstreeks alles te lezen.
KERN_BLOKKEN      = [0x01, 0x0C, 0x0D, 0x03, 0x02]   # elke ronde: ADC's, toerental/MAP, toestandsvlaggen, onrust, EGR-stuur+snelheid
ROULEREND_BLOKKEN = [0x04, 0x05, 0x07, 0x0B, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x14, 0x15]  # overige blokken met inhoud, 1 per ronde
PID_ELKE_RONDE    = [0x06]                            # STFT elke ronde (om naast de sonde na de kat te leggen)
PID_ROULEREND     = [0x0D, 0x03, 0x07, 0x05, 0x04, 0x0E]  # snelheid, brandstofsysteem, LTFT, koelwater, belasting, voorontsteking
SNEL_BLOKKEN      = [0x03, 0x0C, 0x02]                # alleen onrust per cilinder + toerental/snelheid, zo snel mogelijk
ALLE_BLOKKEN      = list(range(0x01, 0x18))           # alle lokale ID's die deze ECU kent, voor een volledige momentopname
# Blokken zonder nut: 06/08/09/0A/16/17 zijn altijd 0, 13 is statisch. ECU-identiteit: service 1A (zie IDENTITEIT_VERZOEK).
# Een volledige ronde = KERN_BLOKKEN + PID_ELKE_RONDE + 1 uit ROULEREND_BLOKKEN + 1 uit PID_ROULEREND (op de echte lijn ~0,8/s).


def identiteit(antwoord):
    """antwoord: payload van 1A 80 zonder de kop 5A 80 -> {"onderdeelnummer", "software", "kalibratie", "productiedatum"}.

    De velden staan als leesbare tekst in het antwoord; wat niet gevonden wordt, ontbreekt in het resultaat.
    """
    tekst = "".join(chr(c) if 32 <= c < 127 else " " for c in antwoord)
    uit = {}
    for sleutel, patroon in (("onderdeelnummer", r"5WY\w{5}"), ("software", r"KR\d{10}"), ("kalibratie", r"\d[A-Z]\d{2}[A-Z]\d{3}")):
        m = re.search(patroon, tekst)
        if m:
            uit[sleutel] = m.group(0)
    m = re.search(r"(?<!\d)(\d{2})(\d{2})(\d{2})\d{8}(?!\d)", tekst)  # 14 cijfers: jjmmdd + tijdstempel
    if m:
        uit["productiedatum"] = f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return uit


def _w(b, off):
    return int.from_bytes(b[off:off + 2], "big")


# sleutel: (blok, functie(bytes) -> waarde, eenheid, omschrijving, zekerheid, grens/opmerking)
SIGNALEN = {
    "toerental":        ("0C", lambda b: _w(b, 5), "rpm", "toerental", "pid", ""),
    "map":              ("0C", lambda b: _w(b, 19) / 550.8, "kPa", "inlaatdruk", "pid", ""),
    "koelwater":        ("0C", lambda b: b[14] * 0.75 - 48, "C", "koelwatertemperatuur", "pid", ""),
    "inlaatlucht":      ("0C", lambda b: b[15] * 0.75 - 48, "C", "inlaatluchttemperatuur", "pid", ""),
    "closed_loop":      ("0C", lambda b: b[3], "0/1", "closed loop actief", "pid", "gelijk aan PID 03 = closed loop"),
    "gasklep_rel":      ("0C", lambda b: b[12], "", "gasklepopening boven de leergrens", "gedrag", "telt pas vanaf ADC ~123-127"),
    "accuspanning":     ("0C", lambda b: b[18] * 0.1123, "V", "boordspanning", "gedrag", "schaal uit een referentiepunt; handboek: fout < 11,5 V of > 16 V"),
    "gas_dicht":        ("0D", lambda b: b[4], "0/1", "ECU ziet gasklep als dicht (stationairregeling)", "gedrag", "ook actief tot gasklep-ADC ~140"),
    "stationairregelaar": ("0D", lambda b: b[28], "stappen", "stand stationairregelaar", "handboek", "hoger = meer lucht; ~80 warm stationair, 172 met gas"),
    "snelheid":         ("02", lambda b: b[4], "km/u", "rijsnelheid", "pid", ""),
    "luchtdruk":        ("02", lambda b: _w(b, 7) / 550.8, "kPa", "barometerdruk", "gedrag", "102 kPa bij motor uit"),
    "lucht_per_slag":   ("02", lambda b: _w(b, 11), "ruw", "luchtmassa per slag / inspuittijd (ruwe eenheid)", "gedrag", "~4300 bij warm stationair"),
    "egr_stuur":        ("02", lambda b: b[24], "ruw", "EGR-stuursignaal (handboek: Desired EEGR position)", "handboek", "0 = dicht; 70-138 gezien"),
    "gasklep_v":        ("01", lambda b: _w(b, 4) * ADC, "V", "gasklepsensor", "pid", "rust 0,51 V"),
    "klopsensor_v":     ("01", lambda b: _w(b, 6) * ADC, "V", "klopsensor, ruiskanaal", "handboek", "fout onder 0,2 V bij warm, > 2500 rpm"),
    "lambda_voor_v":    ("01", lambda b: _w(b, 10) * ADC, "V", "lambdasonde voor katalysator", "pid", "0,1 = arm, 0,9 = rijk"),
    "lambda_na_v":      ("01", lambda b: _w(b, 14) * ADC, "V", "lambdasonde na katalysator", "pid", "fout onder 0,07 V gedurende 40 s"),
    "egr_stand_v":      ("01", lambda b: _w(b, 20) * ADC, "V", "EGR-klepstand (handboek: actual EEGR position)", "handboek", "dicht 0,61 V; rust moet 0,4-1,75 V zijn"),
    "map_v":            ("01", lambda b: _w(b, 22) * ADC, "V", "MAP-sensor", "pid", ""),
    "tankvlotter_v":    ("01", lambda b: _w(b, 26) * ADC, "V", "brandstofniveausensor", "handboek", "fout onder 0,05 V of boven 4,9 V"),
    "onrust_1":         ("03", lambda b: b[28], "", "krukas-onrust cilinderwaarde 1", "gedrag", ""),
    "onrust_2":         ("03", lambda b: b[29], "", "krukas-onrust cilinderwaarde 2", "gedrag", ""),
    "onrust_3":         ("03", lambda b: b[30], "", "krukas-onrust cilinderwaarde 3", "gedrag", ""),
    "boven_drempel_1":  ("03", lambda b: b[24], "", "onrust boven de drempel, cilinderwaarde 1", "gedrag", "niet 0 zodra onrust > ~52"),
    "boven_drempel_2":  ("03", lambda b: b[25], "", "onrust boven de drempel, cilinderwaarde 2", "gedrag", ""),
    "boven_drempel_3":  ("03", lambda b: b[26], "", "onrust boven de drempel, cilinderwaarde 3", "gedrag", ""),
    "kilometerstand":   ("05", lambda b: int.from_bytes(b[29:32], "big") / 10, "km", "kilometerstand volgens de ECU", "gedrag", "liep 11,5 op tijdens een rit van 11,4 km"),
}


# standaard OBD-PID's (modus 01) die de verbinding meeleest: sleutel in de opname -> (naam, functie, eenheid, toelichting)
PIDS = {
    "pid03": ("brandstofsysteem", lambda d: d[0], "", "1 = open loop koud, 2 = closed loop, 4 = open loop belasting/afsnijding"),
    "pid04": ("belasting", lambda d: d[0] * 100 / 255, "%", ""),
    "pid05": ("koelwater_pid", lambda d: d[0] - 40, "C", ""),
    "pid06": ("stft", lambda d: (d[0] - 128) * 100 / 128, "%", "korte brandstofcorrectie; negatief = ECU haalt brandstof weg"),
    "pid07": ("ltft", lambda d: (d[0] - 128) * 100 / 128, "%", "lange brandstofcorrectie"),
    "pid0D": ("snelheid_pid", lambda d: d[0], "km/u", ""),
    "pid0E": ("voorontsteking", lambda d: d[0] / 2 - 64, "gr", ""),
}


def decode_pids(pids):
    """pids: {"pid06": bytes, ...} -> {naam: waarde}."""
    return {PIDS[key][0]: PIDS[key][1](val) for key, val in pids.items() if key in PIDS and val}


def decode(blokken):
    """blokken: {"01": bytes, "0C": bytes, ...} -> {sleutel: waarde} voor alle signalen waarvan het blok aanwezig is."""
    uit = {}
    for sleutel, (blok, fn, *_rest) in SIGNALEN.items():
        if blok in blokken and len(blokken[blok]) >= 32:
            uit[sleutel] = fn(blokken[blok])
    return uit


def vensters(w):
    """Zit de motor nu in een zelftest-venster van de ECU? Voorwaarden uit het werkplaatshandboek (P0400, P0420, P0172).

    De luchtmassa-voorwaarde (92-157 mg per slag) ontbreekt: de schaal van dat signaal is nog onbekend.
    """
    if not all(k in w for k in ("toerental", "map", "koelwater")):
        return {}
    uit = {
        "egr_test": 2500 <= w["toerental"] <= 2900 and w["map"] > 75 and w["koelwater"] > 80 and w.get("inlaatlucht", 20) > 15,
        "mengsel_te_rijk_diagnose": w.get("closed_loop") == 1 and 80 <= w["koelwater"] <= 115 and w["map"] > 70,
    }
    if "snelheid" in w:
        uit["katalysatortest"] = (64 <= w["snelheid"] <= 80 and 2400 <= w["toerental"] <= 3000
                                  and w.get("closed_loop") == 1 and w["koelwater"] > 70)
    return uit


# teksten voor de vensters (door de webpagina getoond als chips)
VENSTER_LABELS = {
    "egr_test": "EGR-test",
    "katalysatortest": "Katalysatortest",
    "mengsel_te_rijk_diagnose": "Mengseldiagnose (te rijk)",
}


# uitleg per venster (door de webpagina getoond als tooltip): de voorwaarden komen uit het M-150 handboek
VENSTER_OPMERKINGEN = {
    "egr_test": "2500-2900 rpm, MAP > 75 kPa, koelwater > 80 C, inlaatlucht > 15 C. De luchtmassa-eis van de ECU "
                "(92-157 mg per slag) ontbreekt, dus dit venster is ruimer dan dat van de ECU.",
    "katalysatortest": "64-80 km/u, 2400-3000 rpm, closed loop, koelwater > 70 C. De ECU eist ook een stabiel toerental "
                       "(binnen 80 rpm) en een luchtstroom van 25-50 kg/u; die zitten er niet in.",
    "mengsel_te_rijk_diagnose": "Closed loop, koelwater 80-115 C, MAP > 70 kPa: alleen hier beoordeelt de ECU of het "
                                "mengsel te rijk is. Stationair valt erbuiten.",
}


if __name__ == "__main__":
    print(f"{'sleutel':20s} {'blok':5s} {'eenheid':8s} {'zekerheid':9s} omschrijving")
    for sleutel, (blok, _fn, eenheid, tekst, zeker, opm) in SIGNALEN.items():
        print(f"{sleutel:20s} {blok:5s} {eenheid:8s} {zeker:9s} {tekst}{' (' + opm + ')' if opm else ''}")
