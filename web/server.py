#!/usr/bin/env python3
"""Webinterface voor live OBD-data van de Matiz.

  python server.py            # met kabel
  python server.py --demo     # gesimuleerde ECU, zonder auto of kabel
  python server.py --follow   # meekijken met de opname van het uitleesscript (kabel blijft van dat script)
  python server.py --replay ../examples/rit_voorbeeld.jsonl --speedup 20
Open daarna http://127.0.0.1:8777
"""
import argparse
import glob
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import analyzer
import feed
import hub
import signalset
import kwp
import sim
import vehicle

HERE = os.path.dirname(os.path.abspath(__file__))


def make_opener(args):
    if args.demo:
        return lambda: (sim.FakeSerial(sim.SimECU(args.demo_proto, sim.Engine(speedup=args.speedup))),
                        "DEMO (gesimuleerd)")

    def open_real():
        path = args.serial or next(iter(sorted(glob.glob("/dev/cu.usbserial-*"))), None)
        if not path:
            raise FileNotFoundError("geen /dev/cu.usbserial-* (kabel niet aangesloten of niet herkend)")
        return kwp.open_port(path, args.dtr, args.rts), path
    return open_real


class Handler(BaseHTTPRequestHandler):
    monitor = None
    port = 8777

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        # tegen DNS-rebinding: alleen verzoeken die echt naar localhost gaan
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "host niet toegestaan"})
        url = urlparse(self.path)
        if url.path == "/":
            with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if url.path == "/api/state":
            return self._send(200, self.monitor.snapshot())
        if url.path == "/api/history":
            secs = float(parse_qs(url.query).get("secs", ["120"])[0])
            return self._send(200, {"series": self.monitor.history(min(max(secs, 5), 900))})
        if url.path.startswith("/api/event/"):
            try:
                ev = self.monitor.event_detail(int(url.path.rsplit("/", 1)[1]))
            except ValueError:
                ev = None
            return self._send(200 if ev else 404, ev or {"error": "onbekend event"})
        self._send(404, {"error": "niet gevonden"})

    def _origin_ok(self):
        # tegen verzoeken vanaf andere websites: een POST vanuit een andere pagina draagt een vreemde Origin
        origin = self.headers.get("Origin")
        return origin is None or origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}")

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._send(403, {"error": "host niet toegestaan"})
        path = urlparse(self.path).path
        if path == "/api/source":
            # JSON als Content-Type: een andere website kan dat niet zonder preflight versturen
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(415, {"error": "application/json verwacht"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                mode = json.loads(self.rfile.read(min(n, 1024))).get("mode")
            except (ValueError, AttributeError):
                return self._send(400, {"error": "ongeldig verzoek"})
            code, body = self.monitor.switch(mode)
            return self._send(code, body)
        if path == "/api/dtc":
            self.monitor.request_dtc_read()
            return self._send(202, {"queued": True})
        if path == "/api/mark":
            self.monitor.mark()
            return self._send(202, {"marked": True})
        if path == "/api/identify":
            self.monitor.request_ident_ext()
            return self._send(202, {"queued": True})
        self._send(404, {"error": "niet gevonden"})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="gesimuleerde ECU (nep-data, duidelijk gemarkeerd)")
    ap.add_argument("--demo-proto", default="kwp-fast", choices=["kwp-fast", "kwp-slow", "iso9141-slow"])
    ap.add_argument("--speedup", type=float, default=1.0,
                    help="demo/replay: tijd sneller laten lopen (opwarmen, of een rit in een minuut bekijken)")
    ap.add_argument("--follow", nargs="?", const="auto", metavar="BESTAND_OF_MAP",
                    help="kijk mee met een lopende opname van het uitleesscript (JSONL) i.p.v. de kabel te openen; "
                         "zonder waarde: nieuwste rit*.jsonl in de map van het profiel (recordings/)")
    ap.add_argument("--replay", metavar="BESTAND", help="speel een opname (JSONL) af; zie ook --speedup")
    ap.add_argument("--signals", metavar="BESTAND",
                    help="signalenbestand (SIGNALEN per blok/byte) voor het meekijken; standaard dat van het profiel")
    ap.add_argument("--serial", help="seriële poort, standaard automatisch /dev/cu.usbserial-*")
    ap.add_argument("--dtr", type=int, choices=[0, 1], help="DTR-lijn zetten (alleen als je kabel dat nodig heeft)")
    ap.add_argument("--rts", type=int, choices=[0, 1], help="RTS-lijn zetten (idem)")
    ap.add_argument("--profile", default="auto", choices=["auto", *vehicle.PROFILES],
                    help="voertuigprofiel (drempels per auto); auto = generiek. Matiz: matiz08")
    ap.add_argument("--probe-ident", action="store_true",
                    help="bij het opstarten ook de uitgebreide KWP-identificatie (service 1A) proberen")
    ap.add_argument("--p4-ms", type=float, default=5.0,
                    help="pauze tussen bytes van verzoeken (ISO: 5-20 ms). Lager = sneller, maar niet elke ECU verdraagt dat")
    ap.add_argument("--http-port", type=int, default=8777)
    ap.add_argument("--log-dir", default=os.path.join(HERE, "logs"))
    args = ap.parse_args()

    log = lambda *a: print(*a, file=sys.stderr, flush=True)

    def make_feed():
        return feed.FeedMonitor(args.replay or args.follow, replay=bool(args.replay), speedup=args.speedup,
                                profile=args.profile, signals=args.signals, log=log)

    def make_cable():
        # het signalenbestand van het profiel (of --signals) bepaalt welke fabrikantblokken de kabelmodus naast de
        # standaard-PID's leest; zonder bestand of leesplan blijft het bij de standaard-PID's. De simulator kent geen blokken.
        cfg = vehicle.PROFILES[vehicle.pick_profile(args.profile)].get("feed", {})
        sigs = None if args.demo else signalset.SignalSet(cfg, args.signals, log=log)
        return analyzer.Monitor(make_opener(args), args.log_dir, demo=args.demo, profile=args.profile,
                                probe_ident=args.probe_ident, p4_ms=args.p4_ms, signals=sigs, log=log)

    # begin met meekijken als --follow/--replay is opgegeven, anders met de kabel; de pagina kan wisselen
    mon = hub.Hub(make_feed, make_cable, "feed" if args.follow or args.replay else "cable",
                  holder=(lambda: None) if args.demo else hub.cable_holder, log=log)
    Handler.monitor, Handler.port = mon, args.http_port
    mon.start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.http_port), Handler)
    print(f"Matiz OBD live: http://127.0.0.1:{args.http_port}  (log: {mon.log_path})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mon.stop()


if __name__ == "__main__":
    main()
