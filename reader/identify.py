"""Probeer verbinding te maken met de motor-ECU en identificeer protocol + ECU.

Alleen init + leesverzoeken. Volgorde: KWP fast init functioneel (OBD, 0x33),
KWP fast init fysiek (Daewoo motor-ECU 0x11), 5-baud init 0x33.
"""
import sys
import time

from paden import alleen_kwp
from kline import KLine, KLineError, hx

ATTEMPTS = [
    ("fast init, functioneel 0x33 (EOBD/KWP2000)", lambda k: k.fast_init(0x33, True)),
    ("fast init, fysiek 0x11 (Daewoo motor-ECU)", lambda k: k.fast_init(0x11, False)),
    ("5-baud init 0x33 (ISO 9141-2)", lambda k: k.slow_init(0x33)),
]

alleen_kwp("Het proberen van init-methoden")
only = int(sys.argv[1]) if len(sys.argv) > 1 else None

k = KLine()
try:
    for i, (name, init) in enumerate(ATTEMPTS):
        if only is not None and i != only:
            continue
        print(f"\n=== poging {i}: {name} ===")
        try:
            init(k)
        except KLineError as e:
            print(f"    mislukt: {e}")
            time.sleep(3.0)  # ECU terug naar rust voor de volgende poging
            continue
        print(f"    VERBONDEN via {name}")
        probes = [[0x01, 0x00]] if k.target == 0x33 or k.mode == "iso9141" else [[0x1A, 0x80], [0x21, 0x01]]
        for p in probes:
            try:
                for d in k.request(p):
                    print(f"    antwoord op {hx(bytes(p))}: {hx(d)}")
            except KLineError as e:
                print(f"    {hx(bytes(p))}: {e}")
        k.stop()
        break
    else:
        print("\nGeen enkele init gaf antwoord.")
finally:
    k.close()
