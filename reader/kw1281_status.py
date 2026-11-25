"""Momentopname van een KW1281-ECU: identiteit, foutgeheugen en alle lezingen uit het voertuigbestand. Alleen lezen.

Gebruik: kw1281_status.py <label>        -> zelftest_<label>.json

selftest_snapshot.py schakelt hierheen zodra het voertuigbestand TRANSPORT = "kw1281" heeft. Het bestand heeft dezelfde
sleutels als de momentopname van een KWP2000-auto, zodat selftest_diff.py twee momentopnames kan vergelijken. Deze ECU
heeft geen readiness-monitors, geen freeze frame en geen modus 06: die delen blijven leeg.
"""
import json
import sys
import time

import kw1281
import signalen
from kline import KLineError
from kw1281_opname import controleer_plan
from paden import in_datamap

label = sys.argv[1]
lezingen, _elke, _roulerend = controleer_plan()
snap = {"label": label, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "transport": "kw1281", "mil": None, "pending": [],
        "freeze_dtc": None, "dtc18": None, "monitors": {}, "mode06": {}, "live_raw": {}, "blocks": {}, "fouten": []}
k = kw1281.KW1281(adres=getattr(signalen, "ADRES", kw1281.ADRES), baud=getattr(signalen, "BAUD", kw1281.BAUD))
try:
    snap["ecu"] = signalen.identiteit(k.wek())
    codes = k.fouten()
    namen = getattr(signalen, "FOUTCODES", {})
    snap["stored"] = [f"{code:05d} {namen.get(code, 'onbekende code')} (status {status:02X})" for code, status in codes]
    snap["dtc_count"] = len(codes)
    for _ in range(3):  # drie keer, zodat te zien is welke bytes binnen één momentopname al bewegen
        for sleutel, lezing in lezingen.items():
            try:
                data = k.lees(*lezing)
            except kw1281.VerbodenTitel:
                raise
            except KLineError as e:
                snap["fouten"].append(f"{sleutel}: {e}")
                if not k.verbonden:
                    k.wek()
                continue
            snap["blocks"].setdefault(sleutel, []).append(data.hex())
    k.stop()
finally:
    k.close()

out = in_datamap(f"zelftest_{label}.json")
json.dump(snap, open(out, "w"), indent=1)
print(f"=== momentopname '{label}'  {snap['time']}  (KW1281) ===")
print("ECU: " + ", ".join(f"{key} {val}" for key, val in snap["ecu"].items()))
print("foutgeheugen: " + ("; ".join(snap["stored"]) or "geen fout opgeslagen"))
laatste = {sleutel: bytes.fromhex(reeks[-1]) for sleutel, reeks in snap["blocks"].items()}
for naam, waarde in signalen.decode(laatste).items():
    _blok, _fn, eenheid, tekst, zeker, *_ = signalen.SIGNALEN[naam]
    print(f"  {naam:22s} {waarde:9.2f} {eenheid:5s} {tekst} [{zeker}]")
if snap["fouten"]:
    print("mislukte lezingen: " + "; ".join(snap["fouten"]))
print("opgeslagen in", out)
