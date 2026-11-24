"""Het gekozen voertuigbestand, onder de naam die de scripts importeren (`import signalen`).

Welke auto: de omgevingsvariabele KLINE_VOERTUIG. Dat is de naam van een bestand in ../vehicles (zonder .py) of het pad
naar een eigen bestand. Zonder die variabele geldt STANDAARD. `rit.py --voertuig <naam>` en `kline-obd --voertuig <naam>`
zetten de variabele voor je.
"""
import importlib.util
import os

STANDAARD = "matiz_m200_08"

_hier = os.path.dirname(os.path.abspath(__file__))
_keuze = os.environ.get("KLINE_VOERTUIG") or STANDAARD
_pad = _keuze if _keuze.endswith(".py") else os.path.join(_hier, "..", "vehicles", _keuze + ".py")
VOERTUIGBESTAND = os.path.abspath(os.path.expanduser(_pad))
if not os.path.exists(VOERTUIGBESTAND):
    raise ImportError(f"voertuigbestand niet gevonden: {VOERTUIGBESTAND} (KLINE_VOERTUIG={_keuze!r})")

_spec = importlib.util.spec_from_file_location("voertuigbestand", VOERTUIGBESTAND)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
globals().update({naam: waarde for naam, waarde in vars(_mod).items() if not naam.startswith("__")})
