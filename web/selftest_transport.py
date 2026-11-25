#!/usr/bin/env python3
"""Test van het transport in het signalenbestand (TRANSPORT) en de zekerheidslabels van de pagina.

De kabelmodus (kwp.py) spreekt alleen KWP2000/OBD2. Een voertuigbestand met een ander transport (bv. "kw1281" voor de
Digifant-ECU van een VW 1600i) mag meekijken met een opname, maar de kabel mag er niet voor open. Alleen simulator en
tijdelijke bestanden: er wordt nooit een seriële poort geopend."""
import os
import subprocess
import sys
import tempfile

import analyzer
import feed
import hub
import signalset

fails = 0
HERE = os.path.dirname(os.path.abspath(__file__))


def check(name, ok, extra=""):
    global fails
    fails += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FOUT'} {name}{('  ' + str(extra)) if extra and not ok else ''}")


def signals_file(tmp, name, transport):
    path = os.path.join(tmp, name)
    head = "" if transport is None else f"TRANSPORT = {transport!r}\n"
    open(path, "w").write(
        head + "BLOKLENGTE = 4\n"
        "SIGNALEN = {'toerental': ('12', lambda b: b[0] * 25, 'rpm', 'toerental', 'firmware', ''),\n"
        "            'inlaatdruk': ('12', lambda b: b[1], 'kPa', 'inlaatdruk', 'aanname', '')}\n"
        "CANONICAL = {'toerental': 'rpm'}\n")
    return path


class NoPort:
    """open_serial voor de kabelmodus: als dit ooit wordt aangeroepen, is de kabel open gegaan."""
    calls = 0

    def __call__(self, *a, **k):
        NoPort.calls += 1
        raise AssertionError("seriële poort geopend")


print("transport in het signalenbestand")
with tempfile.TemporaryDirectory() as tmp:
    plain = signalset.SignalSet({}, signals_file(tmp, "geen.py", None))
    check("zonder TRANSPORT geldt kwp2000", plain.transport == "kwp2000" and plain.cable_refusal() is None)
    check("het standaard-SignalSet (geen bestand) mag de kabel", signalset.SignalSet().cable_refusal() is None)
    upper = signalset.SignalSet({}, signals_file(tmp, "hoofd.py", " KWP2000 "))
    check("hoofdletters en spaties maken niet uit", upper.cable_refusal() is None, upper.transport)
    kw = signalset.SignalSet({}, signals_file(tmp, "vw.py", "kw1281"))
    why = kw.cable_refusal() or ""
    check("kw1281 wordt geweigerd, met uitleg", "kw1281" in why and "--replay" in why, why)
    check("het bestand zelf laadt wel (meekijken kan)", kw.error is None and set(kw.meta) >= {"inlaatdruk"}, kw.error)
    check("andere labels komen mee", kw.meta["inlaatdruk"]["certainty"] == "aanname")
    empty = signalset.SignalSet({}, signals_file(tmp, "leeg.py", ""))
    check("een leeg TRANSPORT valt terug op kwp2000", empty.cable_refusal() is None, empty.transport)

    print("kabelmodus")
    logs = os.path.join(tmp, "logs")
    try:
        analyzer.Monitor(NoPort(), logs, signals=kw)
        refused = False
    except ValueError as e:
        refused = "kw1281" in str(e)
    check("analyzer.Monitor weigert te starten", refused)
    check("er is geen poort geopend en geen log aangemaakt", NoPort.calls == 0 and not os.path.exists(logs))
    ok_mon = analyzer.Monitor(NoPort(), os.path.join(tmp, "logs2"), signals=plain)
    ok_mon._csv_file.close()
    check("een kwp2000-bestand mag wel (Monitor wordt gemaakt)", ok_mon.sigs is plain)

    print("schakelen in de pagina")
    made = {"feed": 0, "cable": 0}

    class Dummy:
        def __init__(self, kind):
            self.kind, self.stopped = kind, False
            self.thread = None
            self.profile_name = "auto"
            self._csv_file = open(os.devnull, "w")

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        def snapshot(self):
            return {"status": {"state": "verbonden"}}

    def make(kind):
        def f():
            made[kind] += 1
            return Dummy(kind)
        return f

    h = hub.Hub(make("feed"), make("cable"), "feed", holder=lambda: None, log=lambda *a: None,
                cable_check=lambda: kw.cable_refusal())
    h.start()
    feed_mon = h.mon
    code, body = h.switch("cable")
    check("schakelen naar de kabel geeft 409 met de reden", code == 409 and "kw1281" in body.get("error", ""), (code, body))
    check("het meekijken blijft draaien (niet gestopt, niet vervangen)",
          h.mon is feed_mon and not feed_mon.stopped and h.mode == "feed" and h.switching is None)
    check("er is geen kabelbron gemaakt", made["cable"] == 0)
    check("de pagina krijgt de reden in source.error", "kw1281" in (h.error or ""))
    code, _ = h.switch("feed")
    check("naar dezelfde bron schakelen blijft kunnen", code == 200)
    ok_hub = hub.Hub(make("feed"), make("cable"), "feed", holder=lambda: None, log=lambda *a: None,
                     cable_check=lambda: plain.cable_refusal())
    ok_hub.start()
    code, _ = ok_hub.switch("cable")
    check("zonder weigering schakelt hij gewoon (202)", code == 202, code)
    ok_hub.mode = None
    try:
        hub.Hub(make("feed"), make("cable"), "cable", holder=lambda: None, log=lambda *a: None,
                cable_check=lambda: kw.cable_refusal()).start()
        started = True
    except RuntimeError as e:
        started = "kw1281" not in str(e)
    check("starten in kabelmodus met kw1281 faalt luid", not started)

    print("meekijken met een opname")
    rec = os.path.join(tmp, "rit_vw.jsonl")
    open(rec, "w").write('{"t": 0.0, "e": %.1f, "12": "0a14ff00"}\n' % 1700000000.0)
    fm = feed.FeedMonitor(rec, replay=False, signals=signals_file(tmp, "vw2.py", "kw1281"), log=lambda *a: None)
    check("FeedMonitor accepteert een kw1281-bestand", fm.sigs.transport == "kw1281" and fm.sigs.error is None)
    fm.stop()

    print("server.py")
    sig = signals_file(tmp, "vw3.py", "kw1281")
    p = subprocess.run([sys.executable, os.path.join(HERE, "server.py"), "--signals", sig, "--http-port", "8794"],
                       capture_output=True, text=True, timeout=30, cwd=HERE)
    check("zonder --follow/--replay stopt de server met een melding (code 2)",
          p.returncode == 2 and "kw1281" in p.stderr and "--replay" in p.stderr, (p.returncode, p.stderr[-200:]))

print("pagina: zekerheidslabels")
html = open(os.path.join(HERE, "static", "index.html"), encoding="utf-8").read()
check("de pagina kent 'firmware' en 'aanname'", "firmware:" in html and "aanname:" in html)
check("een onbekend label komt nooit leeg op het scherm", "onbekend zekerheidslabel" in html and "certText(v.certainty)" in html)
check("er staat geen CERT[...] || '' meer die labels stil weglaat", "CERT[v.certainty] || ''" not in html)

print(f"\n{'ALLES GOED' if not fails else str(fails) + ' FOUT'}")
sys.exit(1 if fails else 0)
