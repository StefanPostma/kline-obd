"""Voertuigbestand voor een auto zonder eigen bestand: alleen standaard OBD2 (modus 01) over de K-line.

Geen fabrikantblokken en geen identiteit via KWP2000. De uitlezer vraagt na het aanknopen welke PID's de ECU kent
(01 00, 01 20, ...) en haalt de rest uit het plan, dus de lijsten hieronder mogen ruim zijn.

Niet op een echte auto getest: alleen tegen een nagebootste ECU (fast init, 5-baud KWP2000 en ISO 9141-2).
Kopieer dit bestand als begin voor een nieuwe auto; het schema staat in vehicles/README.md.
"""

NAAM = "Generiek OBD2 over K-line (ISO 14230 / ISO 9141-2)"
AANKNOPEN = ("fast", "slow")  # eerst ISO 14230 fast init, lukt dat niet dan 5-baud init

IDENTITEIT_VERZOEK = None  # geen fabrikantidentiteit


def identiteit(antwoord):
    return {}


# -- Leesplan -----------------------------------------------------------------------------------------------------
KERN_BLOKKEN = []
ROULEREND_BLOKKEN = []
SNEL_BLOKKEN = []
ALLE_BLOKKEN = []
PID_ELKE_RONDE = [0x0C, 0x0D, 0x11, 0x06]  # toerental, snelheid, gasklep, korte brandstofcorrectie
PID_ROULEREND = [0x05, 0x0F, 0x0B, 0x10, 0x04, 0x0E, 0x07, 0x03, 0x14, 0x15]

# -- Signalen ------------------------------------------------------------------------------------------------------
SIGNALEN = {}  # geen fabrikantblokken, dus geen blok/byte-signalen

# standaard OBD-PID's (modus 01): sleutel in de opname -> (naam, functie(databytes), eenheid, toelichting)
PIDS = {
    "pid03": ("brandstofsysteem", lambda d: d[0], "", "1 = open loop koud, 2 = closed loop, 4 = open loop belasting/afsnijding"),
    "pid04": ("belasting", lambda d: d[0] * 100 / 255, "%", ""),
    "pid05": ("koelwater", lambda d: d[0] - 40, "C", ""),
    "pid06": ("stft", lambda d: (d[0] - 128) * 100 / 128, "%", "korte brandstofcorrectie; negatief = ECU haalt brandstof weg"),
    "pid07": ("ltft", lambda d: (d[0] - 128) * 100 / 128, "%", "lange brandstofcorrectie"),
    "pid0B": ("map", lambda d: d[0], "kPa", "inlaatdruk"),
    "pid0C": ("toerental", lambda d: int.from_bytes(d[:2], "big") / 4, "rpm", ""),
    "pid0D": ("snelheid", lambda d: d[0], "km/u", ""),
    "pid0E": ("voorontsteking", lambda d: d[0] / 2 - 64, "gr", ""),
    "pid0F": ("inlaatlucht", lambda d: d[0] - 40, "C", ""),
    "pid10": ("luchtmassa", lambda d: int.from_bytes(d[:2], "big") / 100, "g/s", ""),
    "pid11": ("gasklep", lambda d: d[0] * 100 / 255, "%", ""),
    "pid14": ("lambda_voor_v", lambda d: d[0] / 200, "V", "sonde voor de katalysator"),
    "pid15": ("lambda_na_v", lambda d: d[0] / 200, "V", "sonde na de katalysator"),
}


def decode_pids(pids):
    """pids: {"pid0C": bytes, ...} -> {naam: waarde}."""
    return {PIDS[key][0]: PIDS[key][1](val) for key, val in pids.items() if key in PIDS and val}


def decode(blokken):
    return {}


def vensters(w):
    return {}


VENSTER_LABELS = {}
VENSTER_OPMERKINGEN = {}
