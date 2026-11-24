#!/usr/bin/env python3
"""Test van feed.py en signalset.py (meekijken met een opname, voor elke auto). Geen kabel, geen auto: alleen
tijdelijke bestanden. De voertuigkennis in de tests is een verzonnen auto, om te laten zien dat de pagina niets van de
Matiz in zich heeft."""
import json
import os
import sys
import tempfile
import time

import feed
import signalset
import vehicle

fails = 0


def check(name, ok, extra=""):
    global fails
    fails += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FOUT'} {name}{('  ' + str(extra)) if extra and not ok else ''}")


# een verzonnen auto: andere blokken, andere bytes, andere schalen dan de Matiz
FAKE = '''
def _w(b, i): return int.from_bytes(b[i:i + 2], "big")
BLOKLENGTE = 16
SIGNALEN = {
    "motortoeren":  ("AA", lambda b: _w(b, 2) / 4, "rpm", "toerental", "pid", ""),
    "koelvloeistof": ("AA", lambda b: b[5] - 40, "C", "koelwater", "pid", ""),
    "pedaal_v":     ("BB", lambda b: _w(b, 0) * 0.001, "V", "gaspedaalsensor", "pid", "rust 0,5 V"),
    "turbo_druk":   ("BB", lambda b: _w(b, 4) / 10, "kPa", "turbodruk", "gedrag", "alleen een vermoeden"),
    "wielsnelheid": ("BB", lambda b: b[8], "km/u", "snelheid", "handboek", ""),
}
def vensters(w):
    return {"warm_genoeg": w.get("koelvloeistof", 0) > 80}
VENSTER_LABELS = {"warm_genoeg": "Motor warm genoeg"}
VENSTER_OPMERKINGEN = {"warm_genoeg": "iets ruimer dan het venster van de ECU"}
CANONICAL = {"motortoeren": "rpm", "koelvloeistof": "ect", "wielsnelheid": "speed"}
TPS = {"signal": "pedaal_v", "rest": 0.5, "full": 4.5}
HIGHLIGHT = ["turbo_druk"]
'''
CFG = {}          # alles voor deze auto staat in het signalenbestand; er is geen profielregel nodig


def block(size, **at):
    b = bytearray(size)
    for off, val in at.items():
        off = int(off[1:])
        v = val if isinstance(val, (tuple, list)) else (val,)
        b[off:off + len(v)] = bytes(v)
    return bytes(b).hex()


def row(t, rpm=910, ect=90, pedal_mv=500, **extra):
    r = {"t": t, "e": round(time.time() - 0.01, 1),
         "AA": block(16, b2=((rpm * 4) >> 8, (rpm * 4) & 255), b5=ect + 40),
         "BB": block(16, b0=(pedal_mv >> 8, pedal_mv & 255), b4=(0, 12), b8=50)}
    r.update(extra)
    return r


with tempfile.TemporaryDirectory() as tmp:
    sigfile = os.path.join(tmp, "signalen_fake.py")
    open(sigfile, "w").write(FAKE)

    print("Signaalset van een verzonnen auto")
    ss = signalset.SignalSet(CFG, sigfile)
    check("signalenbestand geladen, met mapping, tps en aandachtssignalen uit het bestand zelf",
          ss.error is None and len(ss.defs) == 5 and ss.canonical["motortoeren"] == "rpm" and ss.highlight == ["turbo_druk"], ss.error)
    d = ss.decode(row(0))
    check("toerental krijgt de sleutel die de regels kennen", d.get("rpm") == 910, d)
    check("koelwater -> ect, snelheid -> speed", d.get("ect") == 90 and d.get("speed") == 50, d)
    check("overige signalen houden hun eigen naam", "pedaal_v" in d and "turbo_druk" in d and "motortoeren" not in d)
    check("gaskleppercentage geschat uit sensorspanning (rust = 0%)", abs(d["tps"]) < 1e-9, d.get("tps"))
    check("vol gas is 100%", signalset.SignalSet(CFG, sigfile).decode(row(0, pedal_mv=4500))["tps"] == 100.0)
    meta = ss.meta
    check("extra signalen krijgen label, eenheid, groep en zekerheid",
          meta["turbo_druk"]["group"] == "mfr" and meta["turbo_druk"]["certainty"] == "gedrag"
          and meta["turbo_druk"]["unit"] == "kPa" and meta["turbo_druk"]["label"] == "Turbodruk")
    check("bekende grootheden krijgen geen eigen label (komt uit obd.META)", "rpm" not in meta and "ect" not in meta)
    check("testvensters uit het bestand, met label en opmerking",
          ss.windows() == [{"name": "warm_genoeg", "label": "Motor warm genoeg", "active": True,
                            "note": "iets ruimer dan het venster van de ECU"}], ss.windows())
    check("korter blok dan BLOKLENGTE wordt overgeslagen", "pedaal_v" not in ss.decode({"t": 0, "BB": "0102"}))
    check("kapotte hex geeft geen fout", signalset.SignalSet(CFG, sigfile).decode({"t": 0, "AA": "zz"}) == {})
    check("een standaard-PID gaat voor het blok", ss.decode(row(0, pid0D="14"))["speed"] == 20)

    print("Zonder signalenbestand")
    plain = signalset.SignalSet({}, None)
    d = plain.decode(row(0, pid0D="0A", pid05="7B", pid06="80"))
    check("alleen standaard-PID's", d == {"speed": 10, "ect": 83, "stft": 0.0}, d)
    check("geen testvensters", plain.windows() == [])
    broken = signalset.SignalSet({}, os.path.join(tmp, "bestaat-niet.py"), log=lambda *a: None)
    check("ontbrekend bestand: foutmelding, geen crash", broken.error and broken.decode(row(0, pid0D="0A")) == {"speed": 10})

    print("Profielen")
    check("matiz08 verwijst naar zijn signalenbestand", "signals" in vehicle.PROFILES["matiz08"]["feed"])
    check("het generieke profiel heeft geen voertuigkennis", "feed" not in vehicle.PROFILES["generic-petrol"])
    matiz = os.path.expanduser(vehicle.PROFILES["matiz08"]["feed"]["signals"])
    here = os.path.dirname(os.path.abspath(__file__))
    # een echte opname: de voorbeeldrit (naast deze map, of in ../examples in de publieke indeling)
    rit = next((p for p in (os.path.join(here, "voorbeeld_log", "rit_voorbeeld.jsonl"),
                            os.path.join(here, "..", "examples", "rit_voorbeeld.jsonl")) if os.path.exists(p)), None)
    if os.path.exists(matiz) and rit:
        ms = signalset.SignalSet(vehicle.PROFILES["matiz08"]["feed"])
        rows = [json.loads(l) for l in open(rit) if l.strip()]
        d = {}
        for r in rows[len(rows) // 2:]:             # de voorbeeldrit begint met de motor uit: neem een regel uit het midden
            d = ms.decode(r)
            if "rpm" in d and "map" in d and "ect" in d:
                break
        check("Matiz: echte opnameregel geeft toerental, MAP, koelwater",
              ms.error is None and 400 < d.get("rpm", 0) < 6500 and 15 < d.get("map", 0) < 110 and -40 < d.get("ect", -99) < 110, d)
        check("Matiz: extra signalen met zekerheid", ms.meta["egr_stand_v"]["certainty"] == "handboek")
    else:
        print("  --   Matiz-signalen overgeslagen (bestanden niet aanwezig)")

    print("Regels volgen")
    path = os.path.join(tmp, "rit_test.jsonl")
    tail = feed._Tail(path)
    with open(path, "w") as f:
        f.write(json.dumps(row(0)) + "\n" + json.dumps(row(1.2))[:40])     # tweede regel is nog niet af
    got = tail.poll()
    check("alleen volledige regels", len(got) == 1, str(len(got)))
    with open(path, "a") as f:
        f.write(json.dumps(row(1.2))[40:] + "\nkapotte regel\n" + json.dumps(row(2.4)) + "\n")
    got = tail.poll()
    check("rest van de halve regel erbij, rommel genegeerd", [r["t"] for r in got] == [1.2, 2.4], str([r["t"] for r in got]))

    print("Meekijken met een lopende opname (verzonnen auto, via --signals)")
    feed.MARKS_DIR = os.path.join(tmp, "logs")
    with open(os.path.join(tmp, "rit_test.meta.json"), "w") as f:
        json.dump({"ecu": {"onderdeelnummer": "ABC123", "kalibratie": "CAL-9", "software": "SW 1.2"}}, f)
    mon = feed.FeedMonitor(tmp, signals=sigfile, log=lambda *a: None)
    mon.start()

    def wait(cond, secs=4.0):
        end = time.time() + secs
        while time.time() < end:
            try:
                if cond():
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    check("verse opname wordt gevolgd",
          wait(lambda: mon.snapshot()["status"]["state"] == "meekijken") and mon.snapshot()["values"]["rpm"]["v"] == 910)
    with open(path, "a") as f:
        f.write(json.dumps(row(3.6, rpm=1500)) + "\n" + json.dumps(row(4.8, rpm=15360)) + "\n")
    check("nieuwe regel komt live binnen", wait(lambda: mon.snapshot()["values"]["rpm"]["v"] == 1500))
    time.sleep(0.6)
    check("onmogelijk toerental (15360) wordt weggegooid", mon.snapshot()["values"]["rpm"]["v"] == 1500)
    snap = mon.snapshot()
    check("fabrikantsignaal staat als kaart in de groep met zekerheid",
          snap["values"]["turbo_druk"]["card"] and snap["values"]["turbo_druk"]["group"] == "mfr"
          and snap["values"]["turbo_druk"]["certainty"] == "gedrag")
    check("fabrikantkaarten staan achter de standaardkaarten", snap["order"].index("turbo_druk") > snap["order"].index("rpm"))
    check("testvenster en aandachtssignalen gaan mee naar de pagina",
          snap["windows"][0]["active"] is True and snap["highlight"] == ["turbo_druk"])
    check("gaskleppercentage heeft het geschatte label", "geschat" in snap["values"]["tps"]["label"])
    check("identiteit zegt dat het meekijken is", snap["identity"]["protocol"] == "meekijken met opname")
    check("status wijst op meekijken (UI schakelt foutcodes uit)", snap["status"].get("feed") is True)
    mon.request_dtc_read()
    check("foutcodes lezen doet niets en gaat niet stuk", mon.snapshot()["dtc"] is None)
    ident = snap["identity"]
    check("identiteit uit het zijbestand naast de opname", ident["ecu_name"] == "ABC123" and ident["cal_id"] == "CAL-9"
          and any("SW 1.2" in n for n in ident["notes"]), ident)
    # het script vult het zijbestand aan na het aanknopen: eerst een lege "ecu", later gevuld
    metapath = os.path.join(tmp, "rit_test.meta.json")
    json.dump({"ecu": {}, "leesplan": {"kern": ["01"]}}, open(metapath, "w"))
    os.utime(metapath, (time.time() + 2, time.time() + 2))
    check("lege ecu in het zijbestand: identiteit leeg, geen fout",
          wait(lambda: mon.snapshot()["identity"]["ecu_name"] is None and not any("SW" in n for n in mon.snapshot()["identity"]["notes"])))
    json.dump({"ecu": {"onderdeelnummer": "XYZ9", "kalibratie": "K1", "software": "SW 2.0", "productiedatum": "08072917"},
               "leesplan": {"kern": ["01"]}, "ecu_ruw": {"80": "00"}}, open(metapath, "w"))
    os.utime(metapath, (time.time() + 4, time.time() + 4))
    check("aangevuld zijbestand wordt opnieuw gelezen (zonder dubbele notities)",
          wait(lambda: mon.snapshot()["identity"]["ecu_name"] == "XYZ9")
          and sum("ECU-software" in n for n in mon.snapshot()["identity"]["notes"]) == 1
          and any("productiedatum 08072917" in n for n in mon.snapshot()["identity"]["notes"]), mon.snapshot()["identity"])
    mon.mark()
    marks = os.path.join(tmp, "logs", "rit_test.markeringen.log")
    mline = json.loads(open(marks).readline()) if os.path.exists(marks) else {}
    check("markering wordt naast de opname-naam bewaard, niet in de opname",
          mline.get("opname") == "rit_test.jsonl" and isinstance(mline.get("e"), float) and mline.get("t") is not None, mline)
    check("de opname zelf is niet aangepast", all("tekst" not in json.loads(l) for l in open(path) if l.strip().startswith("{")))
    check("de markering zit ook in de vastgelegde momenten", any(e["kind"] == "handmatig" for e in mon.snapshot()["events"]))
    mon.stop()

    print("Verouderd-drempel per kanaal")
    mon4 = feed.FeedMonitor(tmp, log=lambda *a: None)
    for i in range(5):
        mon4._record("ltft", -7.0, i * 22.0)           # eens per 22 s gelezen
        mon4._record("rpm", 900, i * 1.3)
    check("roulerend kanaal is pas na ~55 s verouderd", 54 < mon4._stale_after("ltft") < 56, mon4._stale_after("ltft"))
    check("kanaal dat elke ronde komt blijft op 10 s", mon4._stale_after("rpm") == 10.0, mon4._stale_after("rpm"))
    check("onbekend kanaal: 10 s", mon4._stale_after("bestaat_niet") == 10.0)

    print("Oude opname en ontbrekende map")
    old = time.time() - 600
    os.utime(path, (old, old))
    mon2 = feed.FeedMonitor(tmp, log=lambda *a: None)
    mon2.start()
    check("oude opname wordt niet als live getoond",
          wait(lambda: mon2.snapshot()["status"]["state"] == "wacht_opname") and not mon2.snapshot()["values"])
    mon2.stop()
    mon3 = feed.FeedMonitor(None, profile="generic-petrol", log=lambda *a: None)
    mon3.start()
    check("zonder map in het profiel: duidelijke melding",
          wait(lambda: "geen map ingesteld" in mon3.snapshot()["status"]["text"]), mon3.snapshot()["status"])
    mon3.stop()

print()
print("ALLES OK" if not fails else f"{fails} FOUT(EN)")
sys.exit(1 if fails else 0)
