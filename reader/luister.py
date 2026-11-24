#!/usr/bin/env python3
"""Passief meeluisteren op de K-line en de KWP2000-frames ontcijferen. Verzendt nooit iets.

Bedoeld voor een passieve OBD-splitter: onze kabel hangt als stil derde oor op dezelfde K-line (pin 7) als een tester.
Terwijl die tester bijvoorbeeld de EGR-test of "adaptatie wissen" draait, legt dit elk verzoek met zijn antwoord vast en
licht het de actuator-, beveiligings- en schrijfcommando's eruit. Zo weten we daarna welke bytes welk effect gaven.

Werkt alleen als onze kabel echt stil blijft (dat is hij hier: geen wekpuls, geen break, geen verzoek) en er kan maar
één programma tegelijk de poort open hebben, dus niet samen met het verbindingsproces. Start dit vóórdat de tester zijn
sessie opent.

    luister.py [label] [seconden]        live meeluisteren           -> luister_<label>.log   (standaard 120 s)
    luister.py --ontleed <bestand>       een eerdere opname herlezen -> <bestand>.ontleed.log
    luister.py --droog                   zelftest met een verzonnen bus-opname, zonder kabel
"""
import json
import sys
import time

import kwp_ontleed
from kwp_ontleed import Ontleder, Koppelaar, regel


def _schrijf(fh, soort, frame=None, paar=None):
    doc = {"soort": soort}
    if paar:
        doc.update(dt_ms=paar["dt_ms"], verzoek=paar["verzoek"], antwoord=paar["antwoord"])
    if frame:
        doc["frame"] = frame
    fh.write(json.dumps(doc) + "\n")
    fh.flush()


def _toon_paar(paar):
    v, a = paar["verzoek"], paar["antwoord"]
    merk = f" [{v['soort'].upper()}]" if v["soort"] else ""
    kop = f"  {v.get('naam','?')}{merk}"
    print(f"[{v['t'] % 100000:9.3f}]{kop}")
    print(f"             verzoek  {v['hex']}  data={v['data'] or '-'}")
    nrc = f"  nrc={a['nrc']}" if "nrc" in a else ""
    print(f"             antwoord {a['hex']}  {a.get('naam','?')}  data={a['data'] or '-'}{nrc}  (+{paar['dt_ms']} ms)")


def _verwerk(frames, koppel, fh, toon=True):
    aantal = 0
    for fr in frames:
        aantal += 1
        paar, los = koppel.voeg(fr)
        if paar:
            _schrijf(fh, "paar", paar=paar)
            if toon:
                _toon_paar(paar)
        if los:
            _schrijf(fh, "los", frame=los)
            if toon:
                print(f"[{los['t'] % 100000:9.3f}]  los: {regel(los)}")
    return aantal


def live(label, seconden):
    import serial
    from kline import PORT, BAUD
    from paden import in_datamap
    uit = in_datamap(f"luister_{label}.log")
    try:
        s = serial.Serial()
        s.port, s.baudrate = PORT, BAUD
        s.bytesize, s.parity, s.stopbits = serial.EIGHTBITS, serial.PARITY_NONE, serial.STOPBITS_ONE
        s.timeout = 0.02
        s.dtr = True
        s.rts = False  # nodig om op deze kabel te kunnen ontvangen; stuurt niets
        s.open()
    except serial.SerialException as e:
        sys.exit(f"kan de kabel niet openen (draait het verbindingsproces nog?): {e}")
    rauwpad = in_datamap(f"luister_{label}.raw")  # alle bytes ongewijzigd, als vangnet om later opnieuw te ontcijferen
    print(f"meeluisteren op {PORT} @ {BAUD}, {seconden:.0f} s, naar {uit} (+ {rauwpad}). Onze kabel verzendt niets.\n",
          flush=True)
    ontl, koppel = Ontleder(), Koppelaar()
    start = time.time()
    frames = 0
    with open(uit, "w") as fh, open(rauwpad, "w") as rf:
        try:
            while time.time() - start < seconden:
                chunk = s.read(256)
                if chunk:
                    rf.write(chunk.hex())
                    rf.flush()
                    ontl.voed(chunk, time.time())
                    frames += _verwerk(ontl.frames(), koppel, fh)
            frames += _verwerk(ontl.frames(flush=True), koppel, fh)  # staart legen
        except KeyboardInterrupt:
            print("gestopt", flush=True)
        finally:
            s.close()
    rest = len(ontl.buf)
    print(f"\n{frames} frames in {uit} ({time.time() - start:.0f} s)." + (f"  {rest} losse bytes over." if rest else ""))
    if frames == 0:
        print("niets gehoord. De lijn is stil tenzij iemand zendt: zonder tester die commando's stuurt valt er niets op "
              "te vangen, op de immobilizer-handshake bij contact aan na.")


def _hexen(lijn):
    """De hex-reeks(en) uit één invoerregel: een JSON-regel uit onze eigen log, of gewoon losse hex."""
    try:
        d = json.loads(lijn)
    except ValueError:
        return ["".join(c for c in lijn if c in "0123456789abcdefABCDEF")]
    if "verzoek" in d and "antwoord" in d:  # een gekoppeld paar uit onze log
        return [d["verzoek"].get("hex", ""), d["antwoord"].get("hex", "")]
    if "frame" in d:
        return [d["frame"].get("hex", "")]
    return [d.get("hex", "")]


def ontleed_bestand(pad):
    uit = pad + ".ontleed.log"
    ontl, koppel = Ontleder(), Koppelaar()
    n = 0
    lijnen = [l.strip() for l in open(pad) if l.strip()]
    gestructureerd = bool(lijnen) and lijnen[0].startswith("{")  # onze eigen log vs. een ruwe hex-dump
    with open(uit, "w") as fh:
        if gestructureerd:
            for lijn in lijnen:  # elke regel is één of twee complete frames: begrensd ontleden
                for h in _hexen(lijn):
                    if h:
                        ontl.voed(bytes.fromhex(h), 0.0)
                        n += _verwerk(ontl.frames(flush=True), koppel, fh)
        else:  # ruwe dump: één doorlopende stroom (een frame kan over regels heen lopen)
            for lijn in lijnen:
                ontl.voed(bytes.fromhex("".join(c for c in lijn if c in "0123456789abcdefABCDEF")), 0.0)
                n += _verwerk(ontl.frames(), koppel, fh)
            n += _verwerk(ontl.frames(flush=True), koppel, fh)
    print(f"\n{n} frames ontcijferd uit {pad} -> {uit}")


def _mk(tgt, src, payload, functioneel=False):
    fmt = (0xC0 if functioneel else 0x80) | len(payload)
    frame = bytes([fmt, tgt, src]) + bytes(payload)
    return frame + bytes([sum(frame) & 0xFF])


def droog():
    T, E = 0xF1, 0x11  # tester, ECU
    stroom = b"".join([
        b"\xAA\x55",                               # ruis: moet de synchronisatie overleven
        _mk(E, T, [0x10, 0x85]), _mk(T, E, [0x50, 0x85]),          # sessie starten
        _mk(E, T, [0x27, 0x01]), _mk(T, E, [0x67, 0x01, 0xAA, 0xBB]),  # beveiliging: seed vragen
        _mk(E, T, [0x2F, 0x05, 0x07, 0x32]), _mk(T, E, [0x6F, 0x05, 0x07, 0x32]),  # EGR-klep aansturen
        _mk(E, T, [0x31, 0x01]), _mk(T, E, [0x7F, 0x31, 0x22]),    # routine: geweigerd (conditionsNotCorrect)
    ])
    ontl, koppel = Ontleder(), Koppelaar()
    ontl.voed(stroom, 0.0)
    import io
    fh = io.StringIO()
    paren, frames = [], list(ontl.frames(flush=True))
    for fr in frames:
        paar, _ = koppel.voeg(fr)
        if paar:
            paren.append(paar)
    print(f"ruwe bus-opname: {stroom.hex()}\n")
    for p in paren:
        _toon_paar(p)
    namen = [p["verzoek"]["naam"] for p in paren]
    soorten = {p["verzoek"]["naam"]: p["verzoek"]["soort"] for p in paren}
    assert len(frames) == 8, f"verwacht 8 frames, kreeg {len(frames)} (synchronisatie?)"
    assert len(paren) == 4, f"verwacht 4 paren, kreeg {len(paren)}"
    assert soorten["inputOutputControlByLocalId"] == "actuator"
    assert soorten["securityAccess"] == "beveiliging"
    assert paren[-1]["antwoord"]["nrc"] == "conditionsNotCorrect"
    assert "negatief op startRoutineByLocalId" in paren[-1]["antwoord"]["naam"]
    print("\nalle controles geslaagd:", ", ".join(namen))


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
    elif a[0] == "--droog":
        droog()
    elif a[0] == "--ontleed":
        if len(a) < 2:
            sys.exit("gebruik: luister.py --ontleed <bestand>")
        ontleed_bestand(a[1])
    else:
        label = a[0]
        seconden = float(a[1]) if len(a) > 1 else 120.0
        live(label, seconden)


if __name__ == "__main__":
    main()
