#!/usr/bin/env python3
"""Verkent wat een ECU via KWP2000 beantwoordt, ALLEEN met lezende diensten. Stuurt nooit een actuator aan.

  python probe_ecu.py             # service 21 (ReadDataByLocalIdentifier), id 00..FF
  python probe_ecu.py --ids 0-3F  # een deel

Voor hergebruik bij andere auto's: laat zien welke fabrikant-specifieke data een ECU zelf vrijgeeft.
Stop eerst server.py: de kabel kan maar door één programma tegelijk worden gebruikt.
Resultaat ook als JSON in logs/.
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

from kwp import BadFrame, KLine, Negative, NoResponse, open_port

NEG_TEXT = {0x10: "algemeen geweigerd", 0x11: "dienst niet ondersteund", 0x12: "subfunctie niet ondersteund",
            0x21: "bezig", 0x22: "voorwaarden niet juist", 0x31: "buiten bereik / bestaat niet",
            0x33: "beveiliging (security access) nodig", 0x78: "antwoord volgt"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", default="00-FF", help="bereik hex, bv. 00-FF")
    ap.add_argument("--serial")
    args = ap.parse_args()
    lo, hi = (int(x, 16) for x in args.ids.split("-"))
    port = args.serial or sorted(glob.glob("/dev/cu.usbserial-*"))[0]
    ser = open_port(port)
    kl = KLine(ser, log=lambda m: print("  ", m))
    kl.connect()
    print(f"verbonden via {kl.init_method} ({kl.timing_used}), keybytes {kl.keybytes[0]:02X} {kl.keybytes[1]:02X}")

    positive, special, silent, other = {}, {}, [], {}
    t0 = time.monotonic()
    for n, i in enumerate(range(lo, hi + 1)):
        try:
            p = kl.request(bytes([0x21, i]), timeout=0.4)
        except Negative as e:
            other[e.code] = other.get(e.code, 0) + 1
            if e.code not in (0x11, 0x12, 0x31):          # iets anders dan 'bestaat niet': interessant
                special[i] = e.code
            if e.code == 0x11 and n == 0:
                print("De ECU ondersteunt service 21 niet (code 11). Stop.")
                break
            continue
        except (NoResponse, BadFrame):
            silent.append(i)
            continue
        if p[:2] == bytes([0x61, i]):
            positive[i] = p[2:].hex(" ")
            print(f"  ID {i:02X}: {len(p) - 2} byte(s): {positive[i]}")
        if n % 64 == 63:
            print(f"  ... {n + 1} van {hi - lo + 1} gedaan ({time.monotonic() - t0:.0f} s)")
    ser.close()

    print(f"\nKlaar in {time.monotonic() - t0:.0f} s. Beantwoord met data: {len(positive)}; "
          f"geen antwoord/ruis: {len(silent)}; negatieve antwoorden: "
          + ", ".join(f"{NEG_TEXT.get(c, 'code ' + format(c, '02X'))} x{n}" for c, n in sorted(other.items())))
    for i, c in special.items():
        print(f"  ID {i:02X}: {NEG_TEXT.get(c, 'code ' + format(c, '02X'))}")
    os.makedirs("logs", exist_ok=True)
    out = f"logs/probe21-{datetime.now():%Y%m%d-%H%M%S}.json"
    json.dump({"positive": {f"{k:02X}": v for k, v in positive.items()},
               "special": {f"{k:02X}": f"{v:02X}" for k, v in special.items()},
               "silent": [f"{i:02X}" for i in silent], "negative_counts": {f"{k:02X}": v for k, v in other.items()}},
              open(out, "w"), indent=1)
    print("opgeslagen:", out)


if __name__ == "__main__":
    main()
