#!/usr/bin/env python3
"""Test van de kabelmodus met een leesplan uit het signalenbestand (service 21). Alleen de simulator en tijdelijke
bestanden: de echte kabel wordt niet geopend. Het signalenbestand en de blokken hier zijn verzonnen (geen Matiz-bytes)."""
import os
import sys
import tempfile
import time

import analyzer
import signalset
import sim

fails = 0


def check(name, ok, extra=""):
    global fails
    fails += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FOUT'} {name}{('  ' + str(extra)) if extra and not ok else ''}")


def wait(cond, secs=25.0):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


FAKE = '''
SIGNALEN = {
    "toerental": ("0C", lambda b: int.from_bytes(b[0:2], "big") / 4, "rpm", "toerental", "pid", ""),
    "snelheid_blok": ("0C", lambda b: 400, "km/u", "onmogelijke snelheid", "gedrag", ""),
    "gasklep_v": ("01", lambda b: b[0] * 5 / 255, "V", "gasklepspanning", "gedrag", ""),
    "extra_a": ("0F", lambda b: b[1], "stappen", "extra waarde A", "handboek", "alleen een proef"),
    "kort": ("10", lambda b: b[0], "", "kort blok", "gedrag", ""),
    "kapot": ("14", lambda b: b[0], "", "blok dat de ECU niet kent", "gedrag", ""),
}
BLOKLENGTE = 16
CANONICAL = {"toerental": "rpm", "snelheid_blok": "speed"}
TPS = {"signal": "gasklep_v", "rest": 0.5, "full": 4.5}
KERN_BLOKKEN = [0x01, 0x0C, 0x30]            # 0x30 heeft geen signaal: wordt niet opgevraagd
ROULEREND_BLOKKEN = [0x0F, 0x10, 0x14]
PID_ELKE_RONDE = [0x0D]
PID_ROULEREND = [0x05, 0xEE]                 # 0xEE bestaat niet: de ECU ondersteunt hem niet
VENSTER_LABELS = {"proef": "Proefvenster"}
def vensters(w):
    return {"proef": w.get("extra_a", 0) > 40}
'''

BLOCKS = {
    0x01: bytes([102]) + bytes(15),                        # gasklep_v = 2,0 V -> tps 37,5 %
    0x0C: (4800).to_bytes(2, "big") + bytes(14),           # toerental 1200
    0x0F: bytes([0, 42]) + bytes(14),
    0x10: b"\x07",                                         # te kort: wordt overgeslagen
    # 0x14 ontbreekt: de ECU antwoordt 7F 21 31
}


def run_monitor(tmp, ecu, signals, until, secs=25.0):
    logs = []
    mon = analyzer.Monitor(lambda: (sim.FakeSerial(ecu), "DEMO"), os.path.join(tmp, "logs"), demo=True,
                           log=lambda *a: logs.append(" ".join(map(str, a))), signals=signals)
    mon.start()
    ok = wait(lambda: until(mon.snapshot(), logs), secs)
    snap = mon.snapshot()
    mon.stop()
    time.sleep(0.3)
    return ok, snap, logs


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "fake_signalen.py")
    open(path, "w").write(FAKE)

    print("Leesplan uit het signalenbestand")
    sigs = signalset.SignalSet({}, path, log=print)
    check("plan gelezen", sigs.plan is not None and sigs.error is None, sigs.error)
    check("blok zonder signaal valt af", 0x30 not in sigs.plan["kern"] and sigs.plan["kern"] == [0x01, 0x0C], sigs.plan)
    check("roulerende blokken en PID's", sigs.plan["rot_blocks"] == [0x0F, 0x10, 0x14] and sigs.plan["pids"] == [0x0D])
    check("zonder bestand geen plan", signalset.SignalSet().plan is None)
    open(os.path.join(tmp, "geen_plan.py"), "w").write("SIGNALEN = {'a': ('0C', lambda b: b[0], '', 'a', 'pid', '')}\n")
    check("bestand zonder plan geeft geen plan", signalset.SignalSet({}, os.path.join(tmp, "geen_plan.py"), log=print).plan is None)

    print("Kabelmodus met leesplan (simulator)")
    ecu = sim.SimECU("kwp-fast", sim.Engine(), blocks=BLOCKS)
    ok, snap, logs = run_monitor(
        tmp, ecu, signalset.SignalSet({}, path, log=print),
        lambda s, lg: bool(s["values"].get("extra_a") and s["values"].get("tps") and s["values"].get("speed")
                           and any("blok 14 genegeerd" in l for l in lg)), 60)
    v = snap["values"]
    check("status verbonden", snap["status"]["state"] == "verbonden", snap["status"])
    check("fabrikantblok gelezen en gedecodeerd (extra_a = 42)", v.get("extra_a", {}).get("v") == 42, list(v))
    check("signaal uit het bestand heeft label en groep voor de pagina",
          v["extra_a"].get("group") == "mfr" and v["extra_a"].get("label") == "Extra waarde A"
          and v["extra_a"].get("certainty") == "handboek" and "extra_a" in snap["order"], v.get("extra_a"))
    check("toerental uit blok 0C (canoniek: rpm)", abs(v.get("rpm", {}).get("v", 0) - 1200) < 0.5, v.get("rpm"))
    check("gasklep geschat uit de sensorspanning (37,5 %)", abs(v.get("tps", {}).get("v", 0) - 37.5) < 0.5, v.get("tps"))
    check("standaard-PID uit het plan (snelheid) werkt mee", "speed" in v and v["speed"]["v"] < 250, v.get("speed"))
    check("onmogelijke waarde uit een blok kost alleen die waarde", v["speed"]["v"] != 400 and "rpm" in v, v.get("speed"))
    check("te kort blok wordt overgeslagen", "kort" not in v)
    check("blok dat de ECU niet kent levert niets", "kapot" not in v)
    check("blok dat de ECU niet kent wordt na 3 keer losgelaten",
          any("blok 14 genegeerd" in l for l in logs) and ecu.seen_blocks.get(0x14, 0) == 3, (ecu.seen_blocks, logs[-3:]))
    check("blok zonder signaal (0x30) is nooit gevraagd", 0x30 not in ecu.seen_blocks, ecu.seen_blocks)
    check("PID die de ECU niet ondersteunt (0xEE) is nooit gevraagd", ecu.seen.get(0x01, 0) > 0 and
          not any("PID EE" in l for l in logs))
    check("service 21 is gebruikt voor de kernblokken", ecu.seen_blocks.get(0x01, 0) > 3 and ecu.seen_blocks.get(0x0C, 0) > 3, ecu.seen_blocks)
    check("notitie over het leesplan", any("service 21" in n for n in snap["identity"]["notes"]), snap["identity"]["notes"])
    check("testvenster uit het signalenbestand", any(w["name"] == "proef" and w["active"] and w["label"] == "Proefvenster"
                                                   for w in snap["windows"]), snap["windows"])
    check("verbindingskwaliteit", snap["link"]["total"] > 0 and snap["link"]["ok"] >= snap["link"]["total"] * 0.5, snap["link"])
    check("toestand is JSON-baar", __import__("json").dumps(snap, allow_nan=False) is not None)

    print("Zonder leesplan: alleen standaard-PID's (geen service 21)")
    ecu = sim.SimECU("kwp-fast", sim.Engine(), blocks=BLOCKS)
    ok, snap, logs = run_monitor(tmp, ecu, None, lambda s, lg: bool(s["values"].get("rpm") and s["values"].get("stft")), 25)
    check("standaardwaarden komen binnen", ok and "rpm" in snap["values"], list(snap["values"]))
    check("geen enkel verzoek voor service 21", ecu.seen.get(0x21, 0) == 0, ecu.seen)
    check("snapshot heeft de nieuwe velden", snap["windows"] == [] and snap["highlight"] == [] and "order" in snap)

    print("Signalenbestand ontbreekt of is stuk: alleen standaard-PID's, met een notitie")
    ecu = sim.SimECU("kwp-fast", sim.Engine(), blocks=BLOCKS)
    kapot = os.path.join(tmp, "kapot.py")
    open(kapot, "w").write("dit is geen python (\n")
    ok, snap, logs = run_monitor(tmp, ecu, signalset.SignalSet({}, kapot, log=lambda *a: None),
                                 lambda s, lg: bool(s["values"].get("rpm") and s["identity"]), 25)
    check("blijft werken op standaard-PID's", ok and ecu.seen.get(0x21, 0) == 0, ecu.seen)
    check("notitie over het stuk bestand", any("niet geladen" in n for n in snap["identity"]["notes"]), snap["identity"]["notes"])

print()
print("ALLES OK" if not fails else f"{fails} FOUT(EN)")
sys.exit(1 if fails else 0)
