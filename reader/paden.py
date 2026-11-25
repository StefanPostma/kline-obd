"""Waar de scripts hun opnames en momentopnames neerzetten.

Volgorde: de omgevingsvariabele KLINE_DATA, anders de map `recordings/` naast deze map, anders de map van de scripts.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def datamap():
    env = os.environ.get("KLINE_DATA")
    if env:
        return os.path.expanduser(env)
    rec = os.path.normpath(os.path.join(HERE, "..", "recordings"))
    return rec if os.path.isdir(rec) else HERE


def in_datamap(naam):
    """Een kale bestandsnaam komt in de datamap; een pad met een map erin blijft zoals het is."""
    return naam if os.path.dirname(naam) else os.path.join(datamap(), naam)


def alleen_kwp(wat):
    """Stop als het gekozen voertuig een ander transport dan OBD2/KWP2000 heeft.

    Een scan of probe is bij KWP2000 onschuldig, maar niet overal: de Digifant van de VW 1600i (KW1281) controleert
    onbekende verzoeken niet en kan daardoor in de basisinstelling of bij schrijven naar het EEPROM uitkomen."""
    try:
        import signalen
    except Exception as e:
        if os.environ.get("KLINE_VOERTUIG"):  # een gevraagd voertuig dat niet laadt: niet op de gok doorgaan
            sys.exit(f"voertuigbestand laden mislukt: {e}")
        return
    transport = getattr(signalen, "TRANSPORT", "kwp2000")
    if transport != "kwp2000":
        sys.exit(f"{wat} bestaat alleen voor OBD2/KWP2000; dit voertuig spreekt {transport}. "
                 "Gebruik daarvoor 'opname' of 'status': die sturen alleen de benoemde leesverzoeken.")
