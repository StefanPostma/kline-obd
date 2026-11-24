"""Generieke EOBD-leesscan over KWP2000: PID-support, DTC's, monitors, live data.

Alleen leesmodi (01, 02, 03, 06, 07, 09). Er wordt niets gewist of aangestuurd.
"""
import json
import sys
import time

from kline import KLine, KLineError, NegativeResponse, hx

PIDS = {
    0x04: ("motorbelasting", "%", lambda d: d[0] * 100 / 255),
    0x05: ("koelvloeistoftemp", "C", lambda d: d[0] - 40),
    0x06: ("korte-termijn fuel trim", "%", lambda d: (d[0] - 128) * 100 / 128),
    0x07: ("lange-termijn fuel trim", "%", lambda d: (d[0] - 128) * 100 / 128),
    0x0B: ("inlaatspruitstukdruk (MAP)", "kPa", lambda d: d[0]),
    0x0C: ("toerental", "rpm", lambda d: (d[0] * 256 + d[1]) / 4),
    0x0D: ("snelheid", "km/h", lambda d: d[0]),
    0x0E: ("voorontsteking", "gr", lambda d: d[0] / 2 - 64),
    0x0F: ("inlaatluchttemp", "C", lambda d: d[0] - 40),
    0x10: ("luchtmassa (MAF)", "g/s", lambda d: (d[0] * 256 + d[1]) / 100),
    0x11: ("gasklepstand", "%", lambda d: d[0] * 100 / 255),
    0x14: ("lambdasonde B1S1", "V", lambda d: d[0] / 200),
    0x15: ("lambdasonde B1S2", "V", lambda d: d[0] / 200),
    0x1F: ("looptijd sinds start", "s", lambda d: d[0] * 256 + d[1]),
    0x21: ("afstand met MIL aan", "km", lambda d: d[0] * 256 + d[1]),
    0x2C: ("EGR aangestuurd", "%", lambda d: d[0] * 100 / 255),
    0x2D: ("EGR fout", "%", lambda d: (d[0] - 128) * 100 / 128),
    0x2E: ("EVAP purge aangestuurd", "%", lambda d: d[0] * 100 / 255),
    0x2F: ("brandstofniveau", "%", lambda d: d[0] * 100 / 255),
    0x30: ("warm-ups sinds wissen", "", lambda d: d[0]),
    0x31: ("afstand sinds wissen", "km", lambda d: d[0] * 256 + d[1]),
    0x33: ("barometerdruk", "kPa", lambda d: d[0]),
    0x42: ("boordspanning ECU", "V", lambda d: (d[0] * 256 + d[1]) / 1000),
    0x43: ("absolute belasting", "%", lambda d: (d[0] * 256 + d[1]) * 100 / 255),
    0x45: ("relatieve gasklepstand", "%", lambda d: d[0] * 100 / 255),
    0x46: ("buitentemp", "C", lambda d: d[0] - 40),
}

MONITORS = ["katalysator", "verwarmde kat", "EVAP", "secundaire lucht", "A/C", "lambdasonde",
            "lambdaverwarming", "EGR"]


def dtc_text(a, b):
    return "PCBU"[a >> 6] + f"{(a >> 4) & 3}{a & 0xF:X}{b:02X}"


def parse_dtcs(payloads):
    out = []
    for p in payloads:
        body = p[1:]
        for i in range(0, len(body) - 1, 2):
            if body[i] or body[i + 1]:
                out.append(dtc_text(body[i], body[i + 1]))
    return out


def ask(k, data):
    try:
        return k.request(data)
    except NegativeResponse as e:
        print(f"    {hx(bytes(data))}: negatief antwoord {e}")
    except KLineError as e:
        print(f"    {hx(bytes(data))}: {e}")
    return []


def supported(k, mode):
    pids = set()
    base = 0x00
    while True:
        r = ask(k, [mode, base])
        if not r or len(r[0]) < 6:
            break
        bits = int.from_bytes(r[0][2:6], "big")
        for i in range(32):
            if bits & (1 << (31 - i)):
                pids.add(base + i + 1)
        if base + 0x20 not in pids or base >= 0xE0:
            break
        base += 0x20
    return sorted(pids)


result = {}
k = KLine()
try:
    k.aanknopen(("fast", "slow"))  # eerst ISO 14230 fast init, anders 5-baud (ISO 9141-2 / KWP2000)

    print("\n=== Ondersteunde PID's (modus 01) ===")
    pids = supported(k, 0x01)
    result["pids"] = pids
    print("    " + " ".join(f"{p:02X}" for p in pids))
    print(f"    PID 2C (EGR aangestuurd): {'JA' if 0x2C in pids else 'nee'}"
          f"   PID 2D (EGR fout): {'JA' if 0x2D in pids else 'nee'}")

    print("\n=== Monitorstatus (PID 01) ===")
    r = ask(k, [0x01, 0x01])
    if r:
        a, b, c, d = r[0][2:6]
        print(f"    MIL: {'AAN' if a & 0x80 else 'uit'}   opgeslagen DTC's: {a & 0x7F}")
        result["mil"] = bool(a & 0x80)
        result["dtc_count"] = a & 0x7F
        mon = {}
        for i, name in enumerate(MONITORS):
            if c & (1 << i):
                state = "NIET voltooid" if d & (1 << i) else "voltooid"
            else:
                state = "niet aanwezig"
            mon[name] = state
            print(f"    monitor {name:<18} {state}")
        result["monitors"] = mon

    print("\n=== Storingscodes ===")
    stored = parse_dtcs(ask(k, [0x03]))
    pending = parse_dtcs(ask(k, [0x07]))
    result["stored"], result["pending"] = stored, pending
    print(f"    opgeslagen (modus 03): {', '.join(stored) or 'geen'}")
    print(f"    in behandeling (modus 07): {', '.join(pending) or 'geen'}")

    r = ask(k, [0x02, 0x02, 0x00])
    if r and len(r[0]) >= 5:
        ff = dtc_text(r[0][3], r[0][4]) if (r[0][3] or r[0][4]) else "geen"
        print(f"    freeze frame veroorzaakt door: {ff}")
        result["freeze_dtc"] = ff

    print("\n=== Live data (modus 01) ===")
    live = {}
    for p in pids:
        if p in (0x01, 0x20, 0x40, 0x60) or p in (0x03, 0x13, 0x1C):
            r = ask(k, [0x01, p])
            if r and p in (0x03, 0x13, 0x1C):
                print(f"    PID {p:02X}: ruw {hx(r[0][2:])}")
                live[f"{p:02X}"] = r[0][2:].hex()
            continue
        r = ask(k, [0x01, p])
        if not r:
            continue
        raw = r[0][2:]
        if p in PIDS:
            name, unit, fn = PIDS[p]
            val = fn(raw)
            print(f"    PID {p:02X} {name:<28} {val:8.1f} {unit}   (ruw {hx(raw)})")
            live[f"{p:02X}"] = val
        else:
            print(f"    PID {p:02X} ruw {hx(raw)}")
            live[f"{p:02X}"] = raw.hex()
    result["live"] = live

    print("\n=== Modus 06 (monitor-testresultaten) / 09 (voertuiginfo) ===")
    ask(k, [0x06, 0x00])
    ask(k, [0x09, 0x00])

    k.stop()
finally:
    k.close()

out = sys.argv[1] if len(sys.argv) > 1 else None
if out:
    result["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
