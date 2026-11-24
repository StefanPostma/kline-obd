#!/usr/bin/env python3
"""Test van het schakelen tussen opname en kabel (hub.py + /api/source). Alleen simulator en tijdelijke bestanden:
de echte kabel wordt niet geopend."""
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
PORT = 8791


def check(name, ok, extra=""):
    global fails
    fails += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FOUT'} {name}{('  ' + str(extra)) if extra and not ok else ''}")


def wait(cond, secs=15.0):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def row(t, rpm=910):
    # alleen standaard-PID's: dat werkt voor elke auto, ook zonder signalenbestand
    return {"t": t, "e": round(time.time() - 0.01, 1), "pid0C": (rpm * 4).to_bytes(2, "big").hex(), "pid0D": "00"}


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    h = {"Host": f"127.0.0.1:{PORT}", **(headers or {})}
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data else None)


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "rit_test.jsonl")
    open(path, "w").close()
    stop_feeding = threading.Event()

    def feeder():                                   # laat de "opname" groeien zolang de test loopt
        t = 0.0
        with open(path, "a") as f:
            while not stop_feeding.is_set():
                f.write(json.dumps(row(t)) + "\n")
                f.flush()
                t += 0.3
                time.sleep(0.3)

    threading.Thread(target=feeder, daemon=True).start()
    quiet = lambda *a: None
    busy = {"who": None}

    def make_feed():
        return feed.FeedMonitor(tmp, log=quiet)

    def make_cable():
        ser = sim.FakeSerial(sim.SimECU("kwp-fast", sim.Engine()))
        return analyzer.Monitor(lambda: (ser, "DEMO"), os.path.join(tmp, "logs"), demo=True, log=quiet)

    h = hub.Hub(make_feed, make_cable, "feed", holder=lambda: busy["who"], log=quiet)
    h.start()
    server.Handler.monitor, server.Handler.port = h, PORT
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print("Start met meekijken")
    check("begint met de opname", wait(lambda: h.snapshot()["status"]["state"] == "meekijken"))
    s = h.snapshot()
    check("bron in de snapshot", s["source"]["mode"] == "feed" and set(s["source"]["modes"]) == {"feed", "cable"})

    print("Schakelen naar de kabel")
    code, body = h.switch("cable")
    check("verzoek wordt geaccepteerd", code == 202, (code, body))
    check("tijdens het schakelen meldt de pagina dat", h.snapshot()["status"]["state"] in ("schakelen", "verbonden", "init"))
    check("kabelbron werkt (simulator verbonden)",
          wait(lambda: h.snapshot()["status"]["state"] == "verbonden" and h.snapshot()["values"].get("rpm")))
    s = h.snapshot()
    check("modus is kabel en de opnamebron is gestopt", s["source"]["mode"] == "cable" and not s["source"]["switching"])
    check("kabelmodus heeft geen 'feed'-vlag (foutcodes-knop blijft aan)", not s["status"].get("feed"))

    print("Terug naar de opname")
    code, _ = h.switch("feed")
    check("verzoek wordt geaccepteerd", code == 202, code)
    check("opname wordt weer gevolgd", wait(lambda: h.snapshot()["source"]["mode"] == "feed"
                                            and h.snapshot()["status"]["state"] == "meekijken"
                                            and h.snapshot()["values"].get("rpm")))
    check("zelfde bron nogmaals kiezen is een no-op", h.switch("feed")[0] == 200)
    check("onbekende bron wordt geweigerd", h.switch("raket")[0] == 400)

    print("Kabel in gebruik door een ander programma")
    busy["who"] = "egr_capture.py rit_koud.jsonl 7200 (pid 4242)"
    code, body = h.switch("cable")
    check("wordt geweigerd", code == 409 and "egr_capture.py" in body["error"], (code, body))
    time.sleep(0.2)
    s = h.snapshot()
    check("blijft bij de opname en meldt waarom", s["source"]["mode"] == "feed" and "4242" in (s["source"]["error"] or ""))
    busy["who"] = None
    code, _ = h.switch("cable")
    check("daarna kan het weer", code == 202)
    wait(lambda: h.snapshot()["status"]["state"] == "verbonden")
    check("en de fout is weg", h.snapshot()["source"]["error"] is None)

    print("HTTP: /api/source")
    code, body = req("POST", "/api/source", json.dumps({"mode": "feed"}),
                     {"Content-Type": "application/json", "Origin": "http://evil.example"})
    check("vreemde Origin wordt geweigerd", code == 403, (code, body))
    code, body = req("POST", "/api/source", json.dumps({"mode": "feed"}), {"Content-Type": "text/plain"})
    check("zonder JSON-Content-Type wordt geweigerd", code == 415, (code, body))
    code, body = req("POST", "/api/source", "geen json", {"Content-Type": "application/json"})
    check("ongeldige body wordt geweigerd", code == 400, (code, body))
    check("er is niets omgeschakeld door de geweigerde verzoeken", h.mode == "cable")
    code, body = req("POST", "/api/source", json.dumps({"mode": "feed"}),
                     {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    check("geldig verzoek van de eigen pagina werkt", code == 202, (code, body))
    check("state laat de bron zien", wait(lambda: req("GET", "/api/state")[1]["source"]["mode"] == "feed"))

    print("Wie heeft de kabel?")
    who = hub.cable_holder("/dev/does-not-exist-*")
    check("zonder kabel: niemand", who is None, who)
    try:
        r = hub.cable_holder()
        check("met de echte kabel (alleen lsof, niet geopend) geeft tekst of None", r is None or isinstance(r, str))
    except Exception as e:
        check("cable_holder gooit geen fout", False, e)

    stop_feeding.set()
    h.stop()
    srv.shutdown()

print()
print("ALLES OK" if not fails else f"{fails} FOUT(EN)")
sys.exit(1 if fails else 0)
