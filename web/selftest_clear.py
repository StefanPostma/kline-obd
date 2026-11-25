#!/usr/bin/env python3
"""Test van foutcodes wissen (README.md, "Foutcodes wissen"). Alleen de simulator en tijdelijke bestanden: de echte kabel wordt niet geopend
en er wordt nooit iets naar een echte ECU gestuurd."""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

import analyzer
import feed
import hub
import server
import sim

fails = 0
PORT = 8792
quiet = lambda *a: None


def check(name, ok, extra=""):
    global fails
    fails += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FOUT'} {name}{('  ' + str(extra)) if extra and not ok else ''}")


def wait(cond, secs=40.0):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def make(tmp, running=False, batt=None, codes=True, **ecu_attrs):
    eng = sim.Engine()
    eng.running = running
    eng.batt = batt
    if not codes:
        eng.dtcs = []                                # MIL uit, geen codes (de stand van de echte auto)
    ecu = sim.SimECU("kwp-fast", eng)
    for k, v in ecu_attrs.items():
        setattr(ecu, k, v)
    mon = analyzer.Monitor(lambda: (sim.FakeSerial(ecu), "DEMO"), os.path.join(tmp, "logs"), demo=True, log=quiet)
    mon.start()
    return mon, ecu, eng


def ready(mon, quiet_secs=4.0):
    """Verbonden en alle waarden voor de voorwaarden binnen; bij motor uit ook ≥ quiet_secs toerental nul."""
    return wait(lambda: mon.snapshot()["status"]["state"] == "verbonden"
                and all(k in mon.snapshot()["values"] for k in ("rpm", "speed", "batt")), 30) and \
        (time.sleep(quiet_secs) or True)


def phase(mon):
    return mon.snapshot()["clear"]["phase"]


print("Mode 04 zit op één plek")
here = os.path.dirname(os.path.abspath(__file__))
users = sorted(f for f in os.listdir(here) if f.endswith(".py") and not f.startswith(("selftest", "sim"))   # sim.py: de nep-ECU zelf
               and "clear_dtcs(" in open(os.path.join(here, f), encoding="utf-8").read())
check("alleen obd.py (definitie) en analyzer.py (na de voorwaarden) noemen clear_dtcs", users == ["analyzer.py", "obd.py"], users)
raw04 = sorted(f for f in os.listdir(here) if f.endswith(".py") and not f.startswith(("selftest", "sim"))
               and ("[0x04]" in open(os.path.join(here, f), encoding="utf-8").read().replace(" ", "")))
check("geen ander bestand stelt zelf een 04-verzoek samen", raw04 == ["obd.py"], raw04)

with tempfile.TemporaryDirectory() as tmp:
    print("Voorwaarden voor wissen")
    mon, ecu, eng = make(tmp, running=True)
    check("simulator verbonden met draaiende motor", ready(mon, 1.0))
    reasons = mon.clear_gates()
    check("draaiende motor: geweigerd", any("draait" in r for r in reasons), reasons)
    code, body = mon.request_clear_prepare()
    check("momentopname maken wordt geweigerd met redenen", code == 409 and body["reasons"], (code, body))
    eng.running, eng.batt = False, 11.5
    wait(lambda: any("te laag" in r for r in mon.clear_gates()), 25)       # de accu wordt maar eens per 5 s gelezen
    reasons = mon.clear_gates()
    check("motor uit maar accu 11,5 V: geweigerd om de accu", any("te laag" in r for r in reasons) and not any("draait" in r for r in reasons), reasons)
    eng.batt = 11.8
    wait(lambda: any("11.80 V" in r for r in mon.clear_gates()), 25)
    check("accu precies op de grens (11,8 V) is nog te laag", any("te laag" in r for r in mon.clear_gates()), mon.clear_gates())
    eng.batt = 12.4
    wait(lambda: mon.clear_gates() == [], 25)
    check("motor uit, accu 12,4 V: alle voorwaarden kloppen", mon.clear_gates() == [], mon.clear_gates())
    check("tot nu toe is er nooit mode 04 verstuurd", ecu.seen.get(0x04, 0) == 0, ecu.seen)
    mon.request_dtc_read()
    wait(lambda: mon.snapshot()["dtc"], 15)
    check("foutcodes lezen verstuurt ook geen mode 04", ecu.seen.get(0x04, 0) == 0, ecu.seen)

    print("Momentopname vooraf")
    code, body = mon.request_clear_prepare()
    check("prepare wordt geaccepteerd", code == 202, (code, body))
    check("momentopname klaar", wait(lambda: phase(mon) == "ready", 60), mon.snapshot()["clear"])
    c = mon.snapshot()["clear"]
    prep = c["prepared"]
    path = os.path.join(tmp, "logs", prep["file"])
    check("bestand van de momentopname bestaat", os.path.exists(path), prep)
    snap = json.load(open(path))
    schema = {"label", "time", "pid01", "mil", "dtc_count", "monitors", "stored", "pending", "freeze_dtc", "mode06",
              "live_raw", "dtc18", "blocks"}
    check("schema van zelftest_<label>.json aanwezig", schema <= set(snap), schema - set(snap))
    check("opgeslagen code in de momentopname", snap["stored"] == ["P0171"] and snap["mil"] is True and snap["dtc_count"] == 1, snap)
    check("modus 06 gelezen", "01" in snap["mode06"] and len(snap["mode06"]["01"]) == 2, snap["mode06"])
    check("monitors uit PID 01 01", snap["monitors"]["katalysator"] == "voltooid", snap["monitors"])
    check("live-waarden (LTFT) vastgelegd", "ltft" in snap["live_raw"] and "rpm" in snap["live_raw"], snap["live_raw"])
    check("tot nu toe nog steeds geen mode 04", ecu.seen.get(0x04, 0) == 0)
    check("samenvatting voor de pagina", prep["summary"]["stored"] == ["P0171"] and prep["id"] and prep["expires_in"] > 200, prep)
    code, _ = mon.request_clear_prepare()
    check("tweede prepare tijdens 'ready' is toegestaan en maakt een nieuw id", code == 202)
    wait(lambda: phase(mon) == "ready", 60)
    prep = mon.snapshot()["clear"]["prepared"]

    print("Bevestiging en id")
    check("zonder bevestiging geweigerd", mon.request_clear(prep["id"], None)[0] == 400)
    check("bevestiging moet echt True zijn", mon.request_clear(prep["id"], "ja")[0] == 400)
    check("verkeerd id geweigerd", mon.request_clear("0000000000000000", True)[0] == 409)
    check("zonder id geweigerd", mon.request_clear(None, True)[0] == 409)
    check("nog niets verstuurd na al die weigeringen", ecu.seen.get(0x04, 0) == 0, ecu.seen)
    real = mon._clear_token["expires"]
    mon._clear_token["expires"] = time.monotonic() - 1
    code, body = mon.request_clear(prep["id"], True)
    check("verlopen momentopname geweigerd", code == 409 and "verlopen" in body["error"], (code, body))
    mon._clear_token["expires"] = real

    print("Wissen")
    code, body = mon.request_clear(prep["id"], True)
    check("wissen wordt geaccepteerd", code == 202, (code, body))
    check("klaar", wait(lambda: phase(mon) == "done", 90), mon.snapshot()["clear"])
    res = mon.snapshot()["clear"]["result"]
    check("uitkomst: gewist", res["outcome"] == "gewist", res)
    check("mode 04 is precies één keer verstuurd", ecu.seen.get(0x04, 0) == 1, ecu.seen)
    check("de simulator-ECU is gewist", eng.dtcs == [], eng.dtcs)
    check("momentopname achteraf bestaat", res["na_bestand"] and os.path.exists(os.path.join(tmp, "logs", res["na_bestand"])), res)
    check("verschil: code weg en MIL uit", res["verschil"]["stored"] == {"voor": ["P0171"], "na": []}
          and res["verschil"]["mil"] == {"voor": True, "na": False}, res.get("verschil"))
    check("verschil: monitors terug op niet voltooid", "monitors" in res["verschil"], res.get("verschil"))
    check("geleerde waarde (LTFT) bleef staan en staat dus niet in het verschil", "ltft" not in res["verschil"], res.get("verschil"))
    acties = [json.loads(l) for l in open(os.path.join(tmp, "logs", "acties.log"))]
    check("één regel in acties.log", len(acties) == 1 and acties[0]["uitkomst"] == "gewist" and acties[0]["voor"] == res["voor_bestand"], acties)
    code, _ = mon.request_clear(prep["id"], True)
    check("hetzelfde id kan niet nog een keer", code == 409)
    check("na afloop: voorwaarden-lijst en toestand zijn JSON-baar", json.dumps(mon.snapshot(), allow_nan=False) is not None)
    mon.cancel_clear()
    check("opruimen zet de toestand terug", phase(mon) == "idle")
    mon.stop()

    print("De ECU weigert")
    mon, ecu, eng = make(tmp, nrc04=0x22)
    ready(mon)
    mon.request_clear_prepare(); wait(lambda: phase(mon) == "ready", 60)
    mon.request_clear(mon.snapshot()["clear"]["prepared"]["id"], True)
    check("klaar", wait(lambda: phase(mon) == "done", 60), mon.snapshot()["clear"])
    res = mon.snapshot()["clear"]["result"]
    check("uitkomst: geweigerd, met uitleg", res["outcome"] == "geweigerd" and "voorwaarden" in res["message"], res)
    check("niet opnieuw geprobeerd", ecu.seen.get(0x04, 0) == 1, ecu.seen)
    check("codes zijn er nog", eng.dtcs == ["P0171"], eng.dtcs)
    check("geen momentopname achteraf bij een weigering", res["na_bestand"] is None)
    mon.stop()

    print("Geen antwoord op mode 04")
    mon, ecu, eng = make(tmp, drop04=True)
    ready(mon)
    mon.request_clear_prepare(); wait(lambda: phase(mon) == "ready", 60)
    mon.request_clear(mon.snapshot()["clear"]["prepared"]["id"], True)
    check("klaar", wait(lambda: phase(mon) == "done", 90), mon.snapshot()["clear"])
    res = mon.snapshot()["clear"]["result"]
    check("er is niet opnieuw gestuurd", ecu.seen.get(0x04, 0) == 1, ecu.seen)
    check("uitkomst volgt uit teruglezen: waarschijnlijk gewist", res["outcome"] == "waarschijnlijk gewist"
          and "teruggelezen" in res["message"], res)
    mon.stop()

    print("Geen antwoord op mode 04, en vooraf geen codes (zoals de echte auto nu)")
    mon, ecu, eng = make(tmp, codes=False, drop04=True)             # verzoek komt aan, antwoord niet
    ready(mon)
    mon.request_clear_prepare(); wait(lambda: phase(mon) == "ready", 60)
    check("zonder codes staat dat in de momentopname", mon.snapshot()["clear"]["prepared"]["summary"]["stored"] == [])
    mon.request_clear(mon.snapshot()["clear"]["prepared"]["id"], True)
    check("klaar", wait(lambda: phase(mon) == "done", 90), mon.snapshot()["clear"])
    res = mon.snapshot()["clear"]["result"]
    check("lege codelijst alleen is geen bewijs; teruggezette readiness wel: waarschijnlijk gewist",
          res["outcome"] == "waarschijnlijk gewist" and "readiness" in res["message"], res)
    check("niet opnieuw gestuurd", ecu.seen.get(0x04, 0) == 1, ecu.seen)
    mon.stop()
    mon, ecu, eng = make(tmp, codes=False, lose04=True)             # verzoek komt nooit aan
    ready(mon)
    mon.request_clear_prepare(); wait(lambda: phase(mon) == "ready", 60)
    mon.request_clear(mon.snapshot()["clear"]["prepared"]["id"], True)
    check("klaar", wait(lambda: phase(mon) == "done", 90), mon.snapshot()["clear"])
    res = mon.snapshot()["clear"]["result"]
    check("geen codes vooraf en niets veranderd: 'onbekend', niet 'gewist'", res["outcome"] == "onbekend" and "niet zien" in res["message"], res)
    check("niet opnieuw gestuurd", ecu.seen.get(0x04, 0) == 1, ecu.seen)
    mon.stop()
    mon, ecu, eng = make(tmp, lose04=True)                          # wel codes vooraf, verzoek komt nooit aan
    ready(mon)
    mon.request_clear_prepare(); wait(lambda: phase(mon) == "ready", 60)
    mon.request_clear(mon.snapshot()["clear"]["prepared"]["id"], True)
    check("klaar", wait(lambda: phase(mon) == "done", 90), mon.snapshot()["clear"])
    check("codes staan er nog: niet gewist", mon.snapshot()["clear"]["result"]["outcome"] == "niet gewist", mon.snapshot()["clear"]["result"])
    mon.stop()

    print("Momentopname mislukt of onvolledig")
    mon, ecu, eng = make(tmp, fail={0x03: 0x12})
    ready(mon)
    mon.request_clear_prepare()
    check("essentieel deel (mode 03) mislukt: fout, geen id", wait(lambda: phase(mon) == "error", 60)
          and "niets gewist" in mon.snapshot()["clear"]["message"], mon.snapshot()["clear"])
    check("zonder momentopname is wissen niet mogelijk", mon.request_clear("x", True)[0] == 409)
    check("en er is niets verstuurd", ecu.seen.get(0x04, 0) == 0)
    mon.stop()
    mon, ecu, eng = make(tmp, fail={0x06: 0x12, 0x02: 0x12, 0x18: 0x12})
    ready(mon)
    mon.request_clear_prepare()
    check("aanvullende delen ontbreken (06, 02, 18): momentopname lukt toch", wait(lambda: phase(mon) == "ready", 60), mon.snapshot()["clear"])
    fouten = mon.snapshot()["clear"]["prepared"]["summary"]["fouten"]
    check("wat ontbreekt staat onder 'fouten'", any("modus 06" in f for f in fouten) and any("freeze" in f for f in fouten), fouten)
    mon.stop()

    print("Toestand verandert na de momentopname")
    mon, ecu, eng = make(tmp)
    ready(mon)
    mon.request_clear_prepare(); wait(lambda: phase(mon) == "ready", 60)
    eng.running = True                                   # iemand start de motor
    wait(lambda: any("draait" in r for r in mon.clear_gates()), 25)
    code, body = mon.request_clear(mon.snapshot()["clear"]["prepared"]["id"], True)
    check("motor gestart: wissen geweigerd", code == 409 and any("draait" in r for r in body["reasons"]), (code, body))
    check("er is niets verstuurd", ecu.seen.get(0x04, 0) == 0)
    mon.stop()

    print("Meekijken en schakelen")
    fm = feed.FeedMonitor(tmp, log=quiet)
    check("meekijken: wissen geweigerd", "Alleen in kabelmodus" in fm.clear_gates()[0] and fm.request_clear_prepare()[0] == 409)
    mon, ecu, eng = make(tmp)
    h = hub.Hub(lambda: feed.FeedMonitor(tmp, log=quiet), lambda: mon, "cable", holder=lambda: None, log=quiet)
    h.mon = mon
    mon.clear = {"phase": "clearing"}
    check("schakelen tijdens wissen wordt geweigerd", h.switch("feed")[0] == 409)
    mon.clear = {"phase": "idle"}
    mon.stop()

    print("HTTP")
    mon, ecu, eng = make(tmp)
    h = hub.Hub(lambda: feed.FeedMonitor(tmp, log=quiet), lambda: mon, "cable", holder=lambda: None, log=quiet)
    h.mon = mon
    server.Handler.monitor, server.Handler.port = h, PORT
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ORIGIN = f"http://127.0.0.1:{PORT}"

    def req(path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=8)
        c.request("POST", path, body=body, headers={"Host": f"127.0.0.1:{PORT}", **(headers or {})})
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, (json.loads(data) if data else None)

    JSON = {"Content-Type": "application/json", "Origin": ORIGIN}
    ready(mon)
    check("zonder Origin geweigerd", req("/api/dtc/prepare", "{}", {"Content-Type": "application/json"})[0] == 403)
    check("vreemde Origin geweigerd", req("/api/dtc/prepare", "{}", {"Content-Type": "application/json", "Origin": "http://evil.example"})[0] == 403)
    check("zonder JSON geweigerd", req("/api/dtc/prepare", "{}", {"Content-Type": "text/plain", "Origin": ORIGIN})[0] == 415)
    check("ongeldige body geweigerd", req("/api/dtc/clear", "geen json", JSON)[0] == 400)
    check("wissen zonder momentopname geweigerd", req("/api/dtc/clear", json.dumps({"snapshot": "x", "confirm": True}), JSON)[0] == 409)
    code, body = req("/api/dtc/prepare", "{}", JSON)
    check("prepare via HTTP", code == 202, (code, body))
    check("klaar", wait(lambda: phase(mon) == "ready", 60))
    sid = mon.snapshot()["clear"]["prepared"]["id"]
    check("zonder confirm geweigerd", req("/api/dtc/clear", json.dumps({"snapshot": sid}), JSON)[0] == 400)
    check("nog niets verstuurd", ecu.seen.get(0x04, 0) == 0)
    code, body = req("/api/dtc/clear", json.dumps({"snapshot": sid, "confirm": True}), JSON)
    check("wissen via HTTP", code == 202, (code, body))
    check("klaar", wait(lambda: phase(mon) == "done", 90))
    check("uitkomst gewist, één keer verstuurd", mon.snapshot()["clear"]["result"]["outcome"] == "gewist" and ecu.seen.get(0x04, 0) == 1)
    check("annuleren geeft de toestand vrij", req("/api/dtc/cancel", "{}", JSON)[0] == 200 and phase(mon) == "idle")
    srv.shutdown()
    mon.stop()

print()
print("ALLES OK" if not fails else f"{fails} FOUT(EN)")
sys.exit(1 if fails else 0)
