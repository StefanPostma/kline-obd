"""ECU-identificatie en leesscan van fabrikant-specifieke data.

Alleen leesservices: modus 06/09, 1A (ReadEcuIdentification), 18 (ReadDTCByStatus)
en 21 (ReadDataByLocalIdentifier). Geen schrijf-, wis- of aanstuurservices.
"""
import json
import sys

from paden import alleen_kwp
from kline import KLine, KLineError, NegativeResponse, hx


def ascii_of(b):
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


alleen_kwp("De identificatie- en blokkenscan")
result = {"mode06": {}, "mode09": {}, "ident": {}, "local": {}, "neg": {}}
k = KLine()
try:
    k.fast_init(0x33, True)

    print("\n=== Modus 09: voertuiginfo ===")
    for it in (0x02, 0x04, 0x06):
        try:
            parts = k.request([0x09, it], quiet=True)
        except KLineError as e:
            print(f"    09 {it:02X}: {e}")
            continue
        body = b"".join(p[3:] for p in parts)
        result["mode09"][f"{it:02X}"] = body.hex()
        print(f"    09 {it:02X}: {hx(body)}   '{ascii_of(body)}'")

    print("\n=== Modus 06: testresultaten niet-continue monitors ===")
    tids = []
    base = 0x00
    while True:
        try:
            r = k.request([0x06, base], quiet=True)[0]
        except KLineError as e:
            print(f"    06 {base:02X}: {e}")
            break
        bits = int.from_bytes(r[3:7], "big")
        for i in range(32):
            if bits & (1 << (31 - i)):
                tids.append(base + i + 1)
        if base + 0x20 not in tids:
            break
        base += 0x20
    print("    ondersteunde TID's: " + (" ".join(f"{t:02X}" for t in tids) or "geen"))
    for t in tids:
        if t % 0x20 == 0:
            continue
        try:
            for r in k.request([0x06, t], quiet=True):
                result["mode06"].setdefault(f"{t:02X}", []).append(r.hex())
                print(f"    06 {t:02X}: {hx(r)}")
        except KLineError as e:
            print(f"    06 {t:02X}: {e}")

    print("\n=== Fysiek adres 0x11: identificatie (1A xx) ===")
    k.use_physical()
    for ident in (0x80, 0x81, 0x87, 0x88, 0x8A, 0x90, 0x91, 0x92, 0x94, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0x9C):
        try:
            d = k.query([0x1A, ident])
        except NegativeResponse as e:
            result["neg"][f"1A{ident:02X}"] = e.code
            continue
        except KLineError as e:
            print(f"    1A {ident:02X}: {e}")
            continue
        result["ident"][f"{ident:02X}"] = d.hex()
        print(f"    1A {ident:02X}: {hx(d[2:])}   '{ascii_of(d[2:])}'")
    negs = sorted({v for key, v in result["neg"].items() if key.startswith("1A")})
    print("    overige 1A-id's: negatief " + ", ".join(f"{c:02X}" for c in negs))

    print("\n=== Fysiek adres 0x11: DTC's op status (18 00 FF 00) ===")
    try:
        d = k.query([0x18, 0x00, 0xFF, 0x00])
        result["dtc18"] = d.hex()
        print(f"    {hx(d)}")
    except KLineError as e:
        print(f"    {e}")

    print("\n=== Fysiek adres 0x11: scan lokale identifiers (21 xx) ===")
    for lid in range(0x00, 0x100):
        try:
            d = k.query([0x21, lid])
        except NegativeResponse as e:
            result["neg"][f"21{lid:02X}"] = e.code
            continue
        except KLineError as e:
            print(f"    21 {lid:02X}: {e}")
            continue
        result["local"][f"{lid:02X}"] = d[2:].hex()
        print(f"    21 {lid:02X} ({len(d) - 2:3d} bytes): {hx(d[2:])}")
    codes = {}
    for key, v in result["neg"].items():
        if key.startswith("21"):
            codes[v] = codes.get(v, 0) + 1
    print("    niet beschikbaar: " + ", ".join(f"{n}x code {c:02X}" for c, n in codes.items()))

    k.stop()
finally:
    k.close()

if len(sys.argv) > 1:
    with open(sys.argv[1], "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nOpgeslagen in {sys.argv[1]}. Let op: het identiteitsantwoord (1A 80) kan het serienummer van de ECU en een "
          "VIN bevatten; kijk het na voordat je het bestand deelt.")
