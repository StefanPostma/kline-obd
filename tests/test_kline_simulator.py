#!/usr/bin/env python3
"""De K-line-bibliotheek van de uitlezer (reader/kline.py) op draadniveau tegen de simulator van de webpagina (web/sim.py).

    python tests/test_kline_simulator.py

De simulator bootst de seriële poort na, met echo, wekpuls, 5-baud init en een ECU. Zo zijn ook de init-methoden en
frames getest die de Matiz niet gebruikt: KWP2000 met 5-baud init en ISO 9141-2. Dit is geen test op een echte auto.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "reader"))

import kline  # noqa: E402
import sim  # noqa: E402

fouten = []


def check(naam, ok, detail=""):
    print(f"  {'ok ' if ok else 'FOUT'}  {naam}" + (f"   <- {detail}" if not ok and detail != "" else ""))
    if not ok:
        fouten.append(naam)


def lijn(proto, blocks=None):
    """Een KLine zonder echte poort: de simulator op de plek van pyserial."""
    k = object.__new__(kline.KLine)
    k.verbose = False
    k.t0 = time.perf_counter()
    k.s = sim.FakeSerial(sim.SimECU(proto, sim.Engine(), blocks=blocks))
    k.s.timeout = 0.02
    k.mode = k.target = k.addr_mode = k.ecu = None
    k.last_rx = time.perf_counter()
    return k


def probeer(k, verzoek):
    try:
        return k.query(verzoek), None
    except kline.KLineError as e:
        return None, e


VERWACHT = {  # protocol van de nagebootste ECU -> (gelukte init-methode, mode, antwoordt de ECU met een eigen adres)
    "kwp-fast": ("fast", "kwp", True),
    "kwp-slow": ("slow", "kwp", False),
    "iso9141-slow": ("slow", "iso9141", False),
}

for proto, (methode, mode, heeft_adres) in VERWACHT.items():
    print(f"Nagebootste ECU: {proto}")
    blok = bytes(range(32))
    k = lijn(proto, blocks={0x01: blok} if mode == "kwp" else None)
    try:
        gelukt = k.aanknopen(("fast", "slow"), tries=1)
    except kline.KLineError as e:
        check("aanknopen", False, e)
        continue
    check(f"aangeknoopt via {methode} init, protocol {mode}", gelukt == methode and k.mode == mode, (gelukt, k.mode))
    check("ECU-adres bekend na fast init, onbekend na 5-baud init", (k.ecu is not None) == heeft_adres, k.ecu)
    if k.mode == "kwp" and k.ecu is not None:
        k.use_physical()
    d, e = probeer(k, [0x01, 0x00])
    check("01 00: bitkaart van ondersteunde PID's", d is not None and d[:2] == b"\x41\x00" and len(d) == 6, e or (d and d.hex()))
    d, e = probeer(k, [0x01, 0x0C])
    check("01 0C: toerental, twee databytes", d is not None and d[:2] == b"\x41\x0C" and len(d) == 4, e or (d and d.hex()))
    if mode == "kwp":
        d, e = probeer(k, [0x21, 0x01])
        check("21 01: fabrikantblok van 32 bytes", d is not None and d[:2] == b"\x61\x01" and d[2:] == blok, e or (d and d.hex()))
    try:
        r = k.request([0x03], quiet=True)
        check("03: foutcodes via request()", bool(r) and r[0][:1] == b"\x43", [x.hex() for x in r])
    except kline.KLineError as e:
        check("03: foutcodes via request()", False, e)
    k.s.corrupt_every = 2  # de simulator beschadigt nu elk tweede antwoord, zoals ruis op de lijn
    uitkomsten = [probeer(k, [0x01, 0x0D]) for _ in range(2)]
    geweigerd = [e for d, e in uitkomsten if e is not None]
    gelezen = [d for d, e in uitkomsten if d is not None]
    check("van twee antwoorden is er een beschadigd: dat wordt geweigerd, het andere gelezen",
          len(geweigerd) == 1 and "checksum" in str(geweigerd[0]) and len(gelezen) == 1 and gelezen[0][:2] == b"\x41\x0D",
          [str(e)[:60] for e in geweigerd] + [d.hex() for d in gelezen])
    k.s.corrupt_every = 0
    d, e = probeer(k, [0x01, 0x0D])
    check("daarna leest de lijn weer goed", d is not None and d[:2] == b"\x41\x0D", e)

print()
if fouten:
    print(f"{len(fouten)} FOUT: " + "; ".join(fouten))
    sys.exit(1)
print("ALLES OK")
