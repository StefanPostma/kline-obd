"""Waar de scripts hun opnames en momentopnames neerzetten.

Volgorde: de omgevingsvariabele KLINE_DATA, anders de map `recordings/` naast deze map, anders de map van de scripts.
"""
import os

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
