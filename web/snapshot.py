"""Momentopname van de ECU vóór en na het wissen van foutcodes (alleen leesverzoeken).

Het resultaat heeft het schema van `zelftest_<label>.json` van de uitleessessie (reader/selftest_snapshot.py), zodat
`selftest_diff.py` er twee naast elkaar kan leggen. Sleutels: label, time, pid01, mil, dtc_count, monitors, stored,
pending, freeze_dtc, mode06 {tid: [hex]}, live_raw {fuelsys, ect, ltft, stft, rpm, map, mil_km}, dtc18, blocks
{"01": [hex, hex, hex], ...}. Toegevoegd mag altijd, hernoemen meldt die sessie eerst op het bord: wij voegen toe
`fouten` (wat niet gelezen kon worden) en `bron`.

Essentieel is wat een wissing onbeslist zou laten: PID 01 01 (MIL/readiness) en mode 03 (opgeslagen codes). Lukt dat niet,
dan is er geen momentopname en wordt er niet gewist. Mode 07, freeze frame, mode 06, live-waarden, service 18 en de
fabrikantblokken zijn aanvullend: ontbreken ze (andere auto, andere ECU), dan staat dat onder `fouten`."""
import json
import time

import obd
from kwp import KLineError

MONITORS = ["katalysator", "verwarmde kat", "EVAP", "secundaire lucht", "A/C", "lambdasonde",
            "lambdaverwarming", "EGR"]
LIVE_PIDS = ((0x03, "fuelsys"), (0x05, "ect"), (0x07, "ltft"), (0x06, "stft"), (0x0C, "rpm"), (0x0B, "map"),
             (0x21, "mil_km"))
BLOCK_ROUNDS = 3                                    # elk blok 3× lezen: bytes die zelf bewegen zijn dan herkenbaar


class SnapshotError(Exception):
    pass


def _codes(frames):
    out = []
    for p in frames:
        out += obd.decode_dtcs(p[1:])
    return out


def take(kl, label, sigs=None, stop=lambda: False, progress=lambda text: None):
    """Leest de ECU en geeft het schema hierboven terug. Gooit SnapshotError als het essentiële deel mislukt of als
    `stop()` waar wordt (de verbinding wordt afgesloten)."""
    snap = {"label": label, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "bron": "pagina (kabelmodus)", "fouten": []}
    errs = snap["fouten"]

    def note(what, e):
        errs.append(f"{what}: {e}")

    def check_stop():
        if stop():
            raise SnapshotError("onderbroken: de verbinding wordt afgesloten")

    progress("MIL en readiness")
    try:
        p = kl.request(bytes([0x01, 0x01]), expect=6)
        if len(p) < 6 or p[:2] != bytes([0x41, 0x01]):
            raise SnapshotError(f"onverwacht antwoord op PID 01: {p.hex(' ')}")
    except KLineError as e:
        raise SnapshotError(f"MIL en readiness (01 01) niet gelezen: {e}")
    a, _b, c, d = p[2:6]
    snap["pid01"] = p[2:6].hex()
    snap["mil"] = bool(a & 0x80)
    snap["dtc_count"] = a & 0x7F
    snap["monitors"] = {name: ("niet aanwezig" if not c & (1 << i) else "NIET voltooid" if d & (1 << i) else "voltooid")
                        for i, name in enumerate(MONITORS)}

    progress("opgeslagen codes")
    try:
        snap["stored"] = _codes(kl.request_all(bytes([0x03]), expect=None))
    except KLineError as e:
        raise SnapshotError(f"opgeslagen codes (mode 03) niet gelezen: {e}")
    try:
        snap["pending"] = _codes(kl.request_all(bytes([0x07])))
    except KLineError as e:
        snap["pending"] = []
        note("pending codes (mode 07)", e)
    snap["freeze_dtc"] = None
    try:
        r = kl.request(bytes([0x02, 0x02, 0x00]))
        if len(r) >= 5 and r[0] == 0x42 and (r[3] or r[4]):
            snap["freeze_dtc"] = obd.decode_dtcs(r[3:5])[0]
    except KLineError as e:
        note("freeze frame", e)
    check_stop()

    progress("modus 06")
    snap["mode06"] = {}
    try:
        tids, base = [], 0x00
        while base <= 0xE0:
            r = kl.request(bytes([0x06, base]))
            bits = int.from_bytes(r[3:7], "big")
            tids += [base + i + 1 for i in range(32) if bits & (1 << (31 - i))]
            if base + 0x20 not in tids:
                break
            base += 0x20
        for t in tids:
            if t % 0x20:
                snap["mode06"][f"{t:02X}"] = [f[2:].hex() for f in kl.request_all(bytes([0x06, t]))]
    except KLineError as e:
        note("modus 06", e)
    check_stop()

    progress("live-waarden")
    snap["live_raw"] = {}
    for pid, key in LIVE_PIDS:
        try:
            r = kl.request(bytes([0x01, pid]))
            if r[:2] == bytes([0x41, pid]):
                snap["live_raw"][key] = r[2:].hex()
        except KLineError as e:
            note(f"PID {pid:02X}", e)

    snap["dtc18"] = None
    snap["blocks"] = {}
    blocks = list(getattr(sigs, "all_blocks", []) or [])
    if blocks:
        try:
            snap["dtc18"] = kl.request(bytes([0x18, 0x00, 0xFF, 0x00])).hex()
        except KLineError as e:
            snap["dtc18"] = f"fout: {e}"
            note("service 18 (codes via fabrikantdeel)", e)
        for rnd in range(BLOCK_ROUNDS):
            for i, lid in enumerate(blocks):
                check_stop()
                progress(f"blokken, ronde {rnd + 1} van {BLOCK_ROUNDS} ({i + 1}/{len(blocks)})")
                try:
                    r = kl.request(bytes([0x21, lid]))
                    if r[:2] == bytes([0x61, lid]):
                        snap["blocks"].setdefault(f"{lid:02X}", []).append(r[2:].hex())
                except KLineError as e:
                    if rnd == 0:
                        note(f"blok {lid:02X}", e)
    return snap


def write(snap, path):
    with open(path, "w") as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)


def summary(snap):
    """Korte samenvatting voor de pagina: wat er in de momentopname staat."""
    lv = snap.get("live_raw", {})
    ltft = None
    if lv.get("ltft"):
        ltft = round((int(lv["ltft"], 16) - 128) * 100 / 128, 1)
    return {"mil": snap.get("mil"), "dtc_count": snap.get("dtc_count"), "stored": snap.get("stored", []),
            "pending": snap.get("pending", []), "freeze_dtc": snap.get("freeze_dtc"),
            "monitors": {k: v for k, v in snap.get("monitors", {}).items() if v != "niet aanwezig"},
            "ltft": ltft, "blocks": len(snap.get("blocks", {})), "mode06": len(snap.get("mode06", {})),
            "fouten": list(snap.get("fouten", []))}


def diff(voor, na):
    """Wat veranderde er? Alleen wat een leesbare uitspraak oplevert; het volledige verschil is te zien met
    `selftest_diff.py` op de twee bestanden. Bytes die binnen één momentopname al bewegen (live) worden overgeslagen."""
    out = {}
    a, b = summary(voor), summary(na)
    for k in ("mil", "dtc_count", "stored", "pending", "freeze_dtc", "monitors", "ltft"):
        if a[k] != b[k]:
            out[k] = {"voor": a[k], "na": b[k]}
    moved = {}
    for lid, reads_a in voor.get("blocks", {}).items():
        reads_b = na.get("blocks", {}).get(lid)
        if not reads_b or len(set(map(len, reads_a + reads_b))) != 1:
            continue
        ba, bb = [bytes.fromhex(x) for x in reads_a], [bytes.fromhex(x) for x in reads_b]
        pos = [i for i in range(len(ba[0]))
               if len({x[i] for x in ba}) == 1 and len({x[i] for x in bb}) == 1 and ba[0][i] != bb[0][i]]
        if pos:
            moved[lid] = pos
    if moved:
        out["blokken_veranderd"] = moved
    return out
