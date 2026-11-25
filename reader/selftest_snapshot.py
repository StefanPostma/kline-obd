"""Leg de zelfteststatus van de ECU vast, om voor en na een rit te vergelijken. Alleen lezen.

Gebruik: selftest_snapshot.py <label>        -> zelftest_<label>.json

Vastgelegd: MIL/DTC-telling en monitorstatus (PID 01), opgeslagen en pending codes (03/07/18), freeze frame,
testresultaten van de niet-continue monitors (modus 06), LTFT en alle fabrikantblokken 21 01..17 (3x gelezen,
zodat bytes die binnen een momentopname al bewegen als 'live' herkend worden).
"""
import json
import os
import sys
import time

from kline import KLine, KLineError, NegativeResponse, hx
from paden import in_datamap

try:
    import signalen
except Exception as e:
    if os.environ.get("KLINE_VOERTUIG"):
        sys.exit(f"voertuigbestand laden mislukt: {e}")
    signalen = None
if getattr(signalen, "TRANSPORT", "kwp2000") == "kw1281":  # ander transport: een eigen momentopname, zelfde bestandsvorm
    import runpy
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kw1281_status.py"), run_name="__main__")
    sys.exit(0)
AANKNOPEN = tuple(getattr(signalen, "AANKNOPEN", None) or ("fast",))
# lokale ID's voor de momentopname; zonder voertuigbestand die van de Matiz, een auto zonder blokken geeft een lege lijst
ALLE_BLOKKEN = list(getattr(signalen, "ALLE_BLOKKEN", range(0x01, 0x18)) if signalen else range(0x01, 0x18))

MONITORS = ["katalysator", "verwarmde kat", "EVAP", "secundaire lucht", "A/C", "lambdasonde",
            "lambdaverwarming", "EGR"]
FUELSYS = {1: "open loop (koud)", 2: "closed loop", 4: "open loop (belasting)", 8: "open loop (storing)",
           16: "closed loop met sondestoring"}


def dtc_text(a, b):
    return "PCBU"[a >> 6] + f"{(a >> 4) & 3}{a & 0xF:X}{b:02X}"


def dtcs(payloads):
    out = []
    for p in payloads:
        body = p[1:]
        out += [dtc_text(body[i], body[i + 1]) for i in range(0, len(body) - 1, 2) if body[i] or body[i + 1]]
    return out


def ask(k, data):
    try:
        return k.request(data, quiet=True)
    except KLineError:
        return []


label = sys.argv[1]
snap = {"label": label, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
k = KLine(verbose=False)
try:
    k.aanknopen(AANKNOPEN, tries=5)

    r = ask(k, [0x01, 0x01])
    if r:
        a, b, c, d = r[0][2:6]
        snap["pid01"] = r[0][2:6].hex()
        snap["mil"] = bool(a & 0x80)
        snap["dtc_count"] = a & 0x7F
        snap["monitors"] = {name: ("niet aanwezig" if not c & (1 << i) else "NIET voltooid" if d & (1 << i) else "voltooid")
                            for i, name in enumerate(MONITORS)}
    snap["stored"] = dtcs(ask(k, [0x03]))
    snap["pending"] = dtcs(ask(k, [0x07]))
    r = ask(k, [0x02, 0x02, 0x00])
    snap["freeze_dtc"] = dtc_text(r[0][3], r[0][4]) if r and len(r[0]) >= 5 and (r[0][3] or r[0][4]) else None

    # modus 06: TID's opvragen en per TID alle testwaarden met limiet
    tids, base = [], 0x00
    while True:
        r = ask(k, [0x06, base])
        if not r:
            break
        bits = int.from_bytes(r[0][3:7], "big")
        tids += [base + i + 1 for i in range(32) if bits & (1 << (31 - i))]
        if base + 0x20 not in tids:
            break
        base += 0x20
    snap["mode06"] = {}
    for t in tids:
        if t % 0x20:
            snap["mode06"][f"{t:02X}"] = [p[2:].hex() for p in ask(k, [0x06, t])]

    live = {}
    for pid, key in ((0x03, "fuelsys"), (0x05, "ect"), (0x07, "ltft"), (0x06, "stft"), (0x0C, "rpm"), (0x0B, "map"), (0x21, "mil_km")):
        r = ask(k, [0x01, pid])
        if r:
            live[key] = r[0][2:].hex()
    snap["live_raw"] = live

    # fabrikantdeel (KWP2000 op het fysieke adres): alleen als de ECU zijn adres liet zien en het voertuig blokken heeft
    snap["dtc18"] = None
    snap["blocks"] = {}
    if k.mode == "kwp" and getattr(k, "ecu", None) is not None and ALLE_BLOKKEN:
        k.use_physical()
        try:
            snap["dtc18"] = k.query([0x18, 0x00, 0xFF, 0x00]).hex()
        except KLineError as e:
            snap["dtc18"] = f"fout: {e}"
        for _ in range(3):
            for lid in ALLE_BLOKKEN:
                try:
                    blok = k.query([0x21, lid])[2:].hex()
                except KLineError:
                    continue
                snap["blocks"].setdefault(f"{lid:02X}", []).append(blok)
    k.stop()
finally:
    k.close()

out = in_datamap(f"zelftest_{label}.json")
json.dump(snap, open(out, "w"), indent=1)

lv = snap["live_raw"]
print(f"=== zelfteststatus '{label}'  {snap['time']} ===")
print(f"MIL {'AAN' if snap.get('mil') else 'uit'}, {snap.get('dtc_count')} codes | opgeslagen: {', '.join(snap['stored']) or 'geen'}"
      f" | pending: {', '.join(snap['pending']) or 'geen'} | freeze frame: {snap['freeze_dtc'] or 'geen'} | 18: {snap['dtc18']}")
for name, state in snap.get("monitors", {}).items():
    if state != "niet aanwezig":
        print(f"  monitor {name:12s} {state}")
if all(key in lv for key in ("rpm", "ect", "map", "fuelsys", "ltft")):  # een andere auto kan PID's missen
    print(f"toestand: {int(lv['rpm'], 16) / 4:.0f} rpm, {int(lv['ect'], 16) - 40} C, MAP {int(lv['map'], 16)} kPa,"
          f" {FUELSYS.get(int(lv['fuelsys'][:2], 16), lv['fuelsys'])}, LTFT {(int(lv['ltft'], 16) - 128) * 100 / 128:.1f} %")
print("modus 06 testresultaten (test-ID, onderdeel, waarde, limiet):")
for tid, entries in snap["mode06"].items():
    for e in entries:
        b = bytes.fromhex(e)
        kind = "min" if b[0] & 0x80 else "max"
        print(f"  TID {tid} onderdeel {b[0] & 0x7F:02X}: waarde {int.from_bytes(b[1:3], 'big'):5d}  {kind}-limiet {int.from_bytes(b[3:5], 'big'):5d}   (ruw {hx(b)})")
if snap["blocks"]:
    print(f"blokken: {len(snap['blocks'])} ID's, elk {min(len(v) for v in snap['blocks'].values())}-{max(len(v) for v in snap['blocks'].values())}x gelezen")
else:
    print("blokken: geen (dit voertuig heeft geen fabrikantblokken in zijn voertuigbestand)")
print("opgeslagen in", out)
