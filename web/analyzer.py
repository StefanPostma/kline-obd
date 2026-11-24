"""Poller + analyse: verbindt met de ECU, leest live waarden, beoordeelt afwijkingen, legt events vast.

Alle drempels zijn indicatief voor een benzinemotor (Matiz 0.8) en bedoeld als aanwijzing,
niet als diagnose. Ze staan bovenaan zodat je ze makkelijk kunt bijstellen.
"""
import csv
import json
import os
import queue
import statistics
import threading
import time
from collections import deque
from datetime import datetime

import obd
import signalset
import vehicle
from kwp import BadFrame, ChecksumError, KLine, KLineError, Negative, NoEcho, NoResponse

HISTORY_SECS = 900          # hoeveel seconden meetgeschiedenis in het geheugen
EVENT_PRE, EVENT_POST = 8.0, 3.0
EVENT_COOLDOWN = 4.0
MAX_FAILS = 6               # opeenvolgende mislukte verzoeken voordat we opnieuw aanknopen

LTFT_WARN, LTFT_BAD = 10, 20
STFT_MEAN_WARN = 15
ECT_WARM, ECT_HOT, ECT_BAD = 70, 100, 108
MAP_IDLE_WARN, MAP_IDLE_BAD = 55, 70
RPM_DIP, RPM_SURGE = 250, 300
ENGINE_SETTLE = 10.0        # s na het starten van de motor: opstarten (toerentalpiek, MAP-val) is geen stotter
COARSE_GAP = 0.8            # s tussen toerentalmetingen vanaf wanneer we het ritme 'grof' noemen (opname: 1,2 s)
LEVELS = {"ok": 0, "warn": 1, "bad": 2}

# Een checksum van 1 byte laat bij veel ruis af en toe een foute waarde door (gezien: 15360 rpm, STFT -60%).
# Daarom: onmogelijke waarden weggooien en verdachte sprongen meteen opnieuw meten.
LIMITS = {"rpm": (0, 8000), "speed": (0, 250), "ect": (-40, 150), "iat": (-40, 120), "tps": (0, 100),
          "map": (5, 255), "stft": (-50, 50), "ltft": (-50, 50), "load": (0, 100), "timing": (-40, 60),
          "batt": (6, 18), "o2_b1s1": (0, 1.3), "o2_b1s2": (0, 1.3), "maf": (0, 400)}
JUMP = {"rpm": 1800, "map": 60, "tps": 40, "speed": 30, "ect": 15, "iat": 15, "stft": 30, "load": 60, "batt": 3}


class LinkLost(Exception):
    pass


class Monitor:
    def __init__(self, open_serial, log_dir, demo=False, log=print, profile="auto", probe_ident=False,
                 p4_ms=5.0, signals=None):
        self.open_serial = open_serial
        # voertuigspecifieke signalen (SignalSet) en leesplan: zonder die leest de kabelmodus alleen standaard-PID's
        self.sigs = signals or signalset.SignalSet()
        self.windows = []
        self.p4 = p4_ms / 1000.0                # pauze tussen tester-bytes (verlagen = sneller, minder veilig)
        self.demo = demo
        self.log = log
        self.requested_profile = profile
        self.profile_name = vehicle.pick_profile(profile)
        self.probe_ident = probe_ident          # uitgebreide KWP-identificatie (1A) direct bij opstarten
        self.lock = threading.RLock()
        self.cmds = queue.Queue()
        self._stop = threading.Event()
        self.wall0 = time.time() - time.monotonic()

        self.status = {"state": "start", "text": "Starten…", "proto": None, "keybytes": None,
                       "method": None, "port": None, "demo": demo}
        self._reset_session()
        self.events = []
        self._last_event = {}
        self._next_id = 1
        self.dtc = None

        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_path = os.path.join(log_dir, f"{stamp}{'-demo' if demo else ''}.csv")
        self._csv_file = open(self.log_path, "w", newline="", buffering=1)
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow(["t_epoch", "key", "value"])

    # ---------- levenscyclus ----------
    def start(self):
        self.thread = threading.Thread(target=self._run, name="monitor", daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()

    def request_dtc_read(self):
        self.cmds.put("dtc")

    def request_ident_ext(self):
        self.cmds.put("ident_ext")

    def mark(self):
        """Handmatig moment ('nu stotterde hij'): bewaart de 8 s ervoor en 3 s erna van alle waarden."""
        self._record_event("handmatig", time.monotonic(), "Handmatig gemarkeerd: het stotterde nu")

    def _reset_session(self):
        self.identity = None
        self.latest = {}
        self.hist = {}
        self.stats = {}
        self.supported = []
        self.req_log = deque(maxlen=100)
        self.windows = []
        self.engine_since = None
        self._rpm_low_since = None
        self.session_start = None

    def _set_status(self, state, text, **kw):
        with self.lock:
            self.status.update(state=state, text=text, **kw)
        self.log(f"[{state}] {text}")

    def _sleep(self, secs):
        self._stop.wait(secs)

    def _run(self):
        while not self._stop.is_set():
            try:
                ser, port = self.open_serial()
            except Exception as e:
                self._set_status("geen_kabel", f"Geen kabel gevonden: {e}", port=None)
                self._sleep(2.0)
                continue
            try:
                self._set_status("init", "Kabel gevonden, verbinden met ECU…", port=port)
                self._session(ser)
            except NoEcho:
                self._set_status("geen_echo", "Kabel herkend, maar geen echo op de K-line. "
                                 "Zit de kabel in de OBD-poort (12 V op pin 16)? Zo niet, dan zit de "
                                 "K-line niet rechtstreeks op de FTDI en past deze kabel niet.")
                self._sleep(3.0)
            except LinkLost as e:
                self._record_event("comm", time.monotonic(), f"Verbinding met ECU verloren ({e})")
                self._set_status("geen_ecu", f"Verbinding verloren ({e}); opnieuw verbinden…")
                self._sleep(1.0)
            except NoResponse as e:
                self._set_status("geen_ecu", f"Echo OK, maar de ECU antwoordt niet "
                                 f"(contact aan? motor/ECU wakker?). Details: {e}")
                self._sleep(3.0)
            except (KLineError, OSError) as e:
                self._set_status("fout", f"{type(e).__name__}: {e}")
                self._sleep(3.0)
            finally:
                try:
                    ser.close()
                except Exception:
                    pass

    def _session(self, ser):
        kl = KLine(ser, log=self.log)
        kl.p4 = self.p4
        method = kl.connect()
        kb =f"{kl.keybytes[0]:02X} {kl.keybytes[1]:02X}"
        try:
            supported = obd.supported_pids(kl)
        except (NoResponse, BadFrame) as e:
            raise KLineError(f"init gelukt (keybytes {kb}), maar het eerste OBD-verzoek (01 00) mislukte: {e}. "
                             "Dat wijst op een framing-/protocolprobleem, niet op de kabel.")
        plan = {p: obd.POLL_PERIOD[p] for p in supported if p in obd.POLL_PERIOD and p in obd.PIDS}
        pick = None
        if self.sigs.plan:                          # leesplan van het voertuig: fabrikantblokken + gekozen PID's
            plan, pick = self._round_plan(supported)
        if not plan:
            raise KLineError(f"geen bruikbare PID's (ECU meldt: {[hex(p) for p in supported]})")
        with self.lock:
            self._reset_session()
            self.supported = supported
            self.session_start = time.monotonic()
        self._set_status("verbonden", "Verbonden", method=method, proto=kl.proto, keybytes=kb,
                         echo=kl.check_echo)
        self._identify(kl, supported)
        self._poll(kl, plan, pick)

    def _identify(self, kl, supported):
        """Welke auto/ECU is dit? Resultaat in self.identity en naast het CSV-log opgeslagen."""
        info = obd.identify(kl, supported)
        profile = vehicle.PROFILES[self.profile_name]
        ident = {
            "protocol": "KWP2000 (ISO 14230)" if kl.proto == "kwp" else "ISO 9141-2",
            "init": kl.init_method,
            "keybytes": f"{kl.keybytes[0]:02X} {kl.keybytes[1]:02X}",
            "obd_std": info["obd_std"],
            "fuel": None if info["diesel"] is None else ("diesel" if info["diesel"] else "benzine"),
            "diesel": bool(info["diesel"]),
            "vin": info["vin"], "vin_info": vehicle.decode_vin(info["vin"]) if info["vin"] else None,
            "cal_id": info["cal_id"], "ecu_name": info["ecu_name"],
            "mode09": [f"{i:02X}" for i in info["mode09"] if i % 0x20],
            "supported_pids": [f"{p:02X}" for p in supported],
            "profile": self.profile_name, "profile_label": profile["label"],
            "notes": list(info["notes"]), "ecu_ext": None,
        }
        if not kl.check_echo:
            ident["notes"].append("De kabel echoot niet; verbinding werkt in modus zonder echo-controle.")
        if self.sigs.error:
            ident["notes"].append(self.sigs.error + "; alleen standaard-PID's worden gelezen.")
        elif self.sigs.plan:
            ident["notes"].append("Naast de standaard-PID's worden fabrikantblokken (service 21) gelezen volgens het "
                                  "leesplan van het signalenbestand.")
        elif self.sigs.path:
            ident["notes"].append("Het signalenbestand heeft geen leesplan: alleen standaard-PID's worden gelezen.")
        if ident["diesel"]:
            ident["notes"].append("Dieselmotor: de analyseregels zijn voor benzine; de regels voor lambda, "
                                  "brandstofcorrectie en inlaatdruk staan uit.")
        if self.requested_profile not in vehicle.PROFILES:
            ident["notes"].append("Geen voertuigprofiel gekozen, dus generieke drempels. Voor de Matiz: "
                                  "start met --profile matiz08.")
        with self.lock:
            self.identity = ident
        self._save_identity()
        self.log("IDENTITEIT: " + json.dumps({k: v for k, v in ident.items() if k != "supported_pids"},
                                              ensure_ascii=False))
        if self.probe_ident:
            self._probe_ident_ext(kl)

    def _probe_ident_ext(self, kl):
        ext = obd.probe_kwp_ident(kl)
        with self.lock:
            if self.identity is not None:
                self.identity["ecu_ext"] = ext
        self._save_identity()
        self.log(f"KWP-identificatie (1A): {len(ext)} optie(s) beantwoord")

    def _save_identity(self):
        try:
            with open(self.log_path.replace(".csv", ".identity.json"), "w") as f:
                json.dump(self.identity, f, indent=1, ensure_ascii=False)
        except OSError as e:
            self.log(f"identiteit niet opgeslagen: {e}")

    # ---------- poller ----------
    def _round_plan(self, supported):
        """Leesplan uit het signalenbestand -> (plan, pick). plan: {PID of ("blk", id): periode}; pick() geeft het volgende
        verzoek: elke ronde de kernblokken en vaste PID's, en één roulerend blok en één roulerende PID. PID's die
        de ECU niet ondersteunt vallen af, net als blokken die niet bestaan (zie _poll)."""
        pl = self.sigs.plan
        ok = lambda p: p in supported and p in obd.PIDS
        kern, rot_b = list(pl["kern"]), list(pl["rot_blocks"])
        pids, rot_p = [p for p in pl["pids"] if ok(p)], [p for p in pl["rot_pids"] if ok(p)]
        plan = {("blk", b): 1.0 for b in kern + rot_b}
        plan.update({p: 1.0 for p in pids + rot_p})
        state = {"queue": [], "i": 0, "j": 0}

        def next_round():
            q = [("blk", b) for b in kern] + pids
            alive_b = [b for b in rot_b if ("blk", b) in plan]
            alive_p = [p for p in rot_p if p in plan]
            if alive_b:
                q.append(("blk", alive_b[state["i"] % len(alive_b)]))
                state["i"] += 1
            if alive_p:
                q.append(alive_p[state["j"] % len(alive_p)])
                state["j"] += 1
            return [k for k in q if k in plan]

        def pick():
            while not state["queue"]:
                state["queue"] = next_round()
                if not state["queue"]:
                    raise KLineError("geen enkel blok of PID uit het leesplan wordt door de ECU beantwoord")
            k = state["queue"].pop(0)
            return k if k in plan else pick()
        return plan, pick

    def _read_block(self, kl, blk):
        """Fabrikantblok via KWP service 21: verzoek [21, id], antwoord 61 <id> + databytes. Geeft de databytes."""
        p = kl.request(bytes([0x21, blk]))
        if p[:2] != bytes([0x61, blk]) or len(p) < 2 + self.sigs.block_len:
            raise BadFrame(f"onverwacht antwoord op blok {blk:02X}: {p.hex(' ')}")
        return p[2:]

    def _poll(self, kl, plan, pick=None):
        last = {p: 0.0 for p in plan}
        fails = 0
        bad = {}
        while not self._stop.is_set():
            self._run_cmds(kl)
            now = time.monotonic()
            pid = pick() if pick else max(plan, key=lambda p: (now - last[p]) / plan[p])
            errs0 = kl.checksum_errors
            try:
                if isinstance(pid, tuple):          # ("blk", id): fabrikantblok, gedecodeerd via het signalenbestand
                    blk = pid[1]
                    values = self.sigs.decode({f"{blk:02X}": self._read_block(kl, blk).hex()})
                else:
                    values = obd.read_pid(kl, pid)
            except NoEcho:
                raise
            except Negative as e:                    # ECU leeft, maar kent deze PID of dit blok niet
                last[pid] = time.monotonic()
                bad[pid] = bad.get(pid, 0) + 1
                if bad[pid] >= 3 and len(plan) > 1:
                    self.log(f"{self._what(pid)} genegeerd: {e}")
                    del plan[pid]
                continue
            except (NoResponse, BadFrame) as e:
                self.req_log.extend([False] * (kl.checksum_errors - errs0))
                if not isinstance(e, ChecksumError):
                    self.req_log.append(False)
                last[pid] = time.monotonic()
                fails += 1
                if fails >= MAX_FAILS:
                    raise LinkLost(str(e))
                continue
            fails = 0
            bad.pop(pid, None)
            self.req_log.extend([False] * (kl.checksum_errors - errs0))    # onderweg verloren antwoorden (ruis)
            if isinstance(pid, tuple):               # blok: een onmogelijke waarde kost alleen die waarde, niet het blok
                values = {k: v for k, v in values.items() if self._plausible(k, v)}
            else:
                values = self._validate(kl, pid, values, time.monotonic())
            if values is None:                       # onmogelijke waarde of niet-bevestigde sprong: weggooien
                self.req_log.append(False)
                last[pid] = time.monotonic()
                continue
            self.req_log.append(True)
            t = time.monotonic()
            last[pid] = t
            # gaspedaal en snelheid eerst: de stotterdetectie op het toerental kijkt daar naar
            for key in sorted(values, key=lambda k: k not in ("tps", "speed")):
                self._record(key, values[key], t)
            if isinstance(pid, tuple):
                windows = self.sigs.windows()
                with self.lock:
                    self.windows = windows
            self._finalize_events(t)

    @staticmethod
    def _what(pid):
        return f"blok {pid[1]:02X}" if isinstance(pid, tuple) else f"PID {pid:02X}"

    @staticmethod
    def _plausible(key, v):
        lo, hi = LIMITS.get(key, (-1e9, 1e9))
        return lo <= v <= hi

    def _validate(self, kl, pid, values, now):
        """Geeft de waarden terug als ze plausibel zijn, anders None (zie LIMITS/JUMP)."""
        for key, v in values.items():
            lo, hi = LIMITS.get(key, (-1e9, 1e9))
            if not lo <= v <= hi:
                self.log(f"onmogelijke waarde genegeerd: {key}={v:g}")
                return None
        for key, v in values.items():
            prev, lim = self.latest.get(key), JUMP.get(key)
            if not (prev and lim and now - prev[0] < 1.5 and abs(v - prev[1]) > lim):
                continue
            try:                                     # grote sprong: nog eens meten, alleen een bevestigde sprong telt
                v2 = obd.read_pid(kl, pid).get(key)
            except KLineError:
                v2 = None
            if v2 is not None and abs(v2 - v) <= lim / 3:
                continue
            self.log(f"sprong niet bevestigd, genegeerd: {key} {prev[1]:g} -> {v:g} (tweede meting {v2})")
            return None
        return values

    def _run_cmds(self, kl):
        while True:
            try:
                cmd = self.cmds.get_nowait()
            except queue.Empty:
                return
            if cmd == "dtc":
                self._read_dtcs(kl)
            elif cmd == "ident_ext":
                self._probe_ident_ext(kl)

    def _read_dtcs(self, kl):
        res = {"read_at": time.time(), "stored": [], "pending": [], "freeze": None, "error": None}
        try:
            res["stored"] = obd.read_dtcs(kl, 0x03)
        except KLineError as e:
            res["error"] = f"Opgeslagen codes lezen mislukt: {e}"
        try:
            res["pending"] = obd.read_dtcs(kl, 0x07)
        except KLineError:
            pass                                    # mode 07 is niet op elke ECU aanwezig
        if res["stored"] or res["pending"]:
            res["freeze"] = obd.read_freeze(kl) or None
        for k in ("stored", "pending"):
            res[k] = [{"code": c, "text": obd.dtc_text(c)} for c in res[k]]
        with self.lock:
            self.dtc = res

    # ---------- meetdata ----------
    def _record(self, key, v, now):
        with self.lock:
            self.latest[key] = (now, v)
            h = self.hist.setdefault(key, deque())
            h.append((now, v))
            cutoff = now - HISTORY_SECS
            while h and h[0][0] < cutoff:
                h.popleft()
            st = self.stats.get(key)
            if st is None:
                self.stats[key] = [v, v, v, 1]
            else:
                st[0], st[1], st[2], st[3] = min(st[0], v), max(st[1], v), st[2] + v, st[3] + 1
            self._csv.writerow([f"{self.wall0 + now:.3f}", key, v])
            if key == "rpm":
                self._track_engine(v, now)
        self._detect(key, now, v)

    def _track_engine(self, rpm, now):
        if rpm > 400:
            if self.engine_since is None:
                self.engine_since = now
            self._rpm_low_since = None
        elif rpm < 300:
            if self._rpm_low_since is None:
                self._rpm_low_since = now
            elif now - self._rpm_low_since > 3:
                self.engine_since = None

    def _window(self, key, secs, now):
        h = self.hist.get(key)
        if not h:
            return []
        return [(t, v) for t, v in h if t >= now - secs]

    def _fresh(self, key, now, maxage=15.0):
        x = self.latest.get(key)
        return x[1] if x and now - x[0] <= maxage else None

    # ---------- events (stotter-momenten vastleggen) ----------
    def _coarse(self, now):
        """Liggen de toerentalmetingen ruim een seconde uit elkaar (zoals in een opname met 1,2 s per ronde)?
        Dan zegt één sprong tussen twee metingen weinig: een gasstoot, optrekken en schakelen geven net zulke sprongen."""
        h = [t for t, _ in self._window("rpm", 10, now)]
        gaps = sorted(b - a for a, b in zip(h, h[1:]))
        return len(gaps) >= 4 and gaps[len(gaps) // 2] >= COARSE_GAP

    def _moving(self, now):
        """Reed de auto de laatste 4 s (snelheid > 1 km/h)? None als er geen snelheid is."""
        w = self._window("speed", 4.0, now)
        return max(x for _, x in w) > 1 if w else None

    def _settling(self, now):
        """True als het toerental de laatste ENGINE_SETTLE seconden nog op of onder startniveau (400 rpm) lag: de motor
        is net gestart of afgeslagen. None als er geen toerental is om dat aan te zien."""
        w = self._window("rpm", ENGINE_SETTLE, now)
        return any(x <= 400 for _, x in w) if w else None

    def _throttle_state(self, now):
        """Wat deed het gaspedaal de laatste 2,5 s? None als er te weinig TPS-metingen zijn om dat te zeggen
        (dan beoordelen we niets: het gaspedaal wordt maar ~1× per seconde gemeten)."""
        w = self._window("tps", 4.0 if self._coarse(now) else 2.5, now)
        if len(w) < 2:
            return None
        xs = [x for _, x in w]
        idle_tps = min(x for _, x in self.hist["tps"])          # laagste stand in deze sessie = gasklep dicht
        speed = self._fresh("speed", now, 10)
        return {"falling": xs[0] - xs[-1] > 3, "rising": xs[-1] - xs[0] > 3, "steady": max(xs) - min(xs) < 3,
                "closed": xs[-1] <= idle_tps + 1.5, "driving": speed is not None and speed > 3}

    def _detect(self, key, now, v):
        """Alleen momenten die op stotteren lijken: schakelen (gaspedaal los) en uitrollen (gasklep dicht
        tijdens het rijden) tellen niet mee, die geven altijd een toerentalverandering."""
        with self.lock:
            if key == "rpm":
                w = self._window("rpm", 1.5, now)
                ts = self._throttle_state(now)
                if len(w) >= 2 and ts is not None:
                    tp, peak = max(w, key=lambda x: x[1])
                    low = min(x for _, x in w)
                    coasting = ts["closed"] and ts["driving"]
                    # Bij een grof ritme kan rijdend (optrekken, schakelen, koppeling) een toerentalsprong niet van een
                    # stotter worden onderscheiden: de eerste 1,2 s-opname gaf zo 7 "pieken" bij gewoon optrekken. Dan
                    # beoordelen we alleen stilstaand, en niet in de eerste seconden na het starten van de motor.
                    blind = (self._coarse(now) and self._moving(now) is True) or self._settling(now) is True
                    if peak >= 600 and v < 450 and not coasting:
                        self._record_event("rpm_dip", now, f"Bijna afgeslagen: {peak:.0f} → {v:.0f} rpm")
                    elif blind:
                        pass
                    elif peak >= 600 and peak - v >= RPM_DIP and not ts["falling"] and not coasting:
                        self._record_event("rpm_dip", now, f"Toerental-dip: {peak:.0f} → {v:.0f} rpm in "
                                           f"{now - tp:.1f} s zonder dat het gas werd losgelaten")
                    elif low >= 500 and v - low >= RPM_SURGE and not ts["rising"] and not coasting:
                        self._record_event("rpm_surge", now, f"Toerental-piek: {low:.0f} → {v:.0f} rpm "
                                           "zonder dat het gas werd bijgegeven")
            elif key == "stft" and abs(v) > 25:
                self._record_event("trim", now, f"Brandstofcorrectie schiet uit: STFT {v:+.0f}%")
            elif key == "map":
                ts = self._throttle_state(now)
                w = [x for _, x in self._window("map", 1.5, now)]
                if (ts and ts["steady"] and not ts["driving"] and len(w) >= 2 and abs(v - w[0]) >= 30
                        and self._settling(now) is not True):       # bij het starten valt de MAP altijd weg
                    self._record_event("map", now, f"Inlaatdruk sprong {w[0]:.0f} → {v:.0f} kPa "
                                       "terwijl de auto stilstond en het gas niet bewoog")
            elif key == "mil" and v == 1:
                h = self.hist["mil"]
                if len(h) >= 2 and h[-2][1] == 0:
                    self._record_event("dtc", now, "Storingslamp (MIL) is aangegaan")

    def _record_event(self, kind, now, text):
        group = "rpm" if kind.startswith("rpm") else kind   # dip en herstel zijn één moment
        with self.lock:
            if now - self._last_event.get(group, -1e9) < EVENT_COOLDOWN:
                return
            self._last_event[group] = now
            snap = {k: x[1] for k, x in self.latest.items() if now - x[0] <= 5}
            ev = {"id": self._next_id, "mono": now, "t": self.wall0 + now, "kind": kind,
                  "text": text, "values": snap, "series": None}
            self._next_id += 1
            self.events.append(ev)
        self.log(f"EVENT {kind}: {text}")

    def _finalize_events(self, now):
        with self.lock:
            for ev in self.events:
                if ev["series"] is None and now >= ev["mono"] + EVENT_POST:
                    t0 = ev["mono"]
                    ev["series"] = {
                        k: [[round(t - t0, 2), v] for t, v in h if t0 - EVENT_PRE <= t <= t0 + EVENT_POST]
                        for k, h in self.hist.items()}
                    ev["series"] = {k: s for k, s in ev["series"].items() if s}

    # ---------- beoordeling ----------
    def evaluate(self, now):
        """Geeft de huidige afwijkingen als lijst van {level, title, detail, keys}."""
        out = []

        def add(level, title, detail, keys=()):
            out.append({"level": level, "title": title, "detail": detail, "keys": list(keys)})

        def fresh(k, age=15.0):
            return self._fresh(k, now, age)

        def vals(k, secs):
            return [x for _, x in self._window(k, secs, now)]

        rpm, speed, ect = fresh("rpm"), fresh("speed", 20), fresh("ect", 60)
        running = rpm is not None and rpm > 400
        idle = running and ((speed == 0) if speed is not None else rpm < 1100)
        warm = ect is not None and ect >= ECT_WARM
        run_secs = fresh("runtime", 60)
        if run_secs is None and self.engine_since is not None:
            run_secs = now - self.engine_since

        idle_rng = vehicle.PROFILES[self.profile_name]["idle_rpm"]
        petrol = not (self.identity or {}).get("diesel")      # lambda/trims/MAP-regels gelden voor benzine

        # Stationair toerental
        if idle:
            r = vals("rpm", 15)
            if len(r) >= 8:
                sd, mn, mx = statistics.pstdev(r), min(r), max(r)
                if sd > 60 or mx - mn > 250:
                    add("bad" if sd > 120 else "warn", "Onrustig stationair toerental",
                        f"Toerental schommelt tussen {mn:.0f} en {mx:.0f} rpm (σ {sd:.0f}) in 15 s.", ["rpm"])
                med = statistics.median(r)
                if idle_rng and warm and not idle_rng[0] <= med <= idle_rng[1]:
                    add("warn", "Stationair toerental buiten verwacht bereik",
                        f"Mediaan {med:.0f} rpm bij warme motor (verwacht {idle_rng[0]}–{idle_rng[1]}, "
                        "profiel " + self.profile_name + ").", ["rpm"])

        # Mengselcorrectie
        ltft = fresh("ltft", 60) if petrol else None
        if ltft is not None and abs(ltft) > LTFT_WARN:
            if ltft > 0:
                add("bad" if ltft > LTFT_BAD else "warn", f"Mengsel wordt te arm gecorrigeerd (LTFT {ltft:+.1f}%)",
                    "De ECU geeft structureel extra brandstof. Denk aan: valse lucht/vacuümlek, zwakke "
                    "brandstofdruk of vuile injectoren, MAP-sensor.", ["ltft"])
            else:
                add("bad" if ltft < -LTFT_BAD else "warn", f"Mengsel wordt te rijk gecorrigeerd (LTFT {ltft:+.1f}%)",
                    "De ECU haalt structureel brandstof weg. Denk aan: lekkende injector, te hoge "
                    "brandstofdruk, lambdasonde, vervuilde luchtfilter.", ["ltft"])
        s = vals("stft", 15) if petrol else []
        if len(s) >= 5:
            m = statistics.fmean(s)
            if abs(m) > STFT_MEAN_WARN:
                add("warn", f"Korte brandstofcorrectie wijkt af (gemiddeld {m:+.1f}%)",
                    "Gemiddelde STFT over 15 s ligt ver van 0%.", ["stft"])
            if max(s) - min(s) > 35:
                add("warn", "Brandstofcorrectie zeer onrustig",
                    f"STFT varieert {min(s):+.0f}% tot {max(s):+.0f}% in 15 s.", ["stft"])
            if ltft is not None and abs(m + ltft) > 15:        # korte + lange correctie samen = echte afwijking
                tot = m + ltft
                what = ("De ECU haalt veel brandstof weg: het mengsel is van zichzelf te rijk. Denk aan: EVAP-purgeklep "
                        "die blijft openstaan, lekkende injector, te hoge brandstofdruk, vuil luchtfilter, bougies."
                        if tot < 0 else
                        "De ECU geeft veel extra brandstof: het mengsel is van zichzelf te arm. Denk aan: valse lucht/"
                        "vacuümlek, zwakke brandstofpomp of -druk, vuile injectoren, lek in de uitlaat voor de sonde.")
                add("bad" if abs(tot) > 25 else "warn",
                    f"Totale brandstofcorrectie {tot:+.0f}% (STFT {m:+.0f}% + LTFT {ltft:+.0f}%)", what, ["stft", "ltft"])

        # Lambdasonde
        o2 = vals("o2_b1s1", 20) if petrol else []
        fs = fresh("fuelsys", 30)
        if len(o2) >= 8 and warm and (run_secs or 0) > 120 and fs not in (1, 4, 8):
            if max(o2) - min(o2) < 0.3:
                m = statistics.fmean(o2)
                what = "blijft laag (arm)" if m < 0.3 else "blijft hoog (rijk)" if m > 0.6 else "wisselt niet"
                add("warn", f"Lambdasonde voor de kat {what}",
                    f"Spanning {min(o2):.2f}–{max(o2):.2f} V in 20 s bij warme motor. Een gezonde sonde "
                    "schakelt ongeveer 0,1 ↔ 0,9 V; traag/dood of systeem staat in open lus.", ["o2_b1s1"])

        # Inlaatdruk op stationair
        mp = fresh("map") if petrol else None
        if idle and warm and mp is not None and mp > MAP_IDLE_WARN:
            add("bad" if mp > MAP_IDLE_BAD else "warn", f"Hoge inlaatdruk op stationair ({mp:.0f} kPa)",
                "Gezond stationair ligt meestal rond 30–45 kPa. Hoger past bij vacuümlek, "
                "klepslijtage of slechte sensor.", ["map"])

        # Gasklep
        t = vals("tps", 10)
        if idle and len(t) >= 8 and max(t) - min(t) > 6:
            add("warn", "Gasklepsignaal springt op stationair",
                f"TPS varieert {min(t):.1f}–{max(t):.1f}% terwijl de auto stilstaat "
                "(mogelijk slechte sensor/aansluiting, tenzij je gas gaf).", ["tps"])

        # Koelwater
        if ect is not None:
            if ect > ECT_BAD:
                add("bad", f"Koelwater erg heet ({ect:.0f} °C)", "Risico op oververhitting.", ["ect"])
            elif ect > ECT_HOT:
                add("warn", f"Koelwater warm ({ect:.0f} °C)", "Boven de normale werktemperatuur.", ["ect"])
            elif running and ect < ECT_WARM and (run_secs or 0) > 600:
                add("warn", f"Motor wordt niet warm ({ect:.0f} °C na {run_secs / 60:.0f} min)",
                    "Mogelijk thermostaat die open blijft hangen of sensorfout. Koude motor "
                    "geeft ook extra brandstofcorrectie en stotteren.", ["ect"])

        # Accuspanning
        b = fresh("batt")
        if b is not None:
            if running:
                if b < 12.4 or b > 15.3:
                    add("bad", f"Boordspanning {b:.1f} V", "Laadsysteem (dynamo/regelaar) buiten bereik.", ["batt"])
                elif b < 13.0 or b > 14.9:
                    add("warn", f"Boordspanning {b:.1f} V", "Normaal ca. 13,5–14,7 V bij draaiende motor.", ["batt"])
            elif b < 11.8:
                add("warn", f"Accuspanning laag ({b:.1f} V)", "Motor staat stil; accu is zwak.", ["batt"])

        # Foutcodes
        cnt, mil = fresh("dtc_count", 60), fresh("mil", 60)
        if (cnt or 0) > 0 or mil:
            add("bad" if mil else "warn", f"ECU meldt {int(cnt or 0)} foutcode(s)" + (" en MIL aan" if mil else ""),
                "Klik op 'Foutcodes lezen' voor de codes en de freeze frame.", [])

        # Stotter-events in de laatste 5 minuten
        recent = [e for e in self.events if e["kind"] in ("rpm_dip", "rpm_surge", "handmatig")
                  and now - e["mono"] <= 300]
        if len(recent) >= 3:
            add("bad", f"Stotteren vastgelegd: {len(recent)}× in 5 min",
                "Kijk bij 'Vastgelegde momenten' welke waarden er tegelijk afweken.", ["rpm"])

        # Verbindingskwaliteit
        if len(self.req_log) >= 30:
            bad_ratio = 1 - sum(self.req_log) / len(self.req_log)
            if bad_ratio > 0.2:
                add("warn", f"Veel mislukte verzoeken ({bad_ratio:.0%})",
                    "Slechte verbinding of ECU die niet goed antwoordt; metingen kunnen hiaten hebben.", [])
        return out

    # ---------- uitvoer voor de webinterface ----------
    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            values = {}
            for key, (t, v) in self.latest.items():
                mn, mx, sm, n = self.stats[key]
                values[key] = {"v": v, "age": round(now - t, 1), "min": mn, "max": mx, "avg": sm / n, "n": n}
            alerts = self.evaluate(now)
            for key, d in values.items():
                lvl = max((LEVELS[a["level"]] for a in alerts if key in a["keys"]), default=0)
                d["level"] = {0: "ok", 1: "warn", 2: "bad"}[lvl]
                label, unit, dec, card = obd.META.get(key, (key, "", 1, False))
                d.update(label=label, unit=unit, dec=dec, card=card)
                d.update(self.sigs.meta.get(key, {}))        # label, eenheid en zekerheid uit het signalenbestand
                d["stale_after"] = self._stale_after(key)
            fs = values.get("fuelsys")
            if fs:
                fs["text"] = obd.FUEL_STATUS.get(int(fs["v"]), f"Status {int(fs['v'])}")
            ok = sum(self.req_log)
            return {
                "status": dict(self.status),
                "order": ([k for k in obd.META if k in values]
                          + [k for k, m in self.sigs.meta.items() if m.get("group") and k in values]),
                "values": values,
                "windows": list(self.windows),
                "highlight": self.sigs.highlight,
                "alerts": sorted(alerts, key=lambda a: -LEVELS[a["level"]]),
                "events": [{"id": e["id"], "age": round(e["mono"] - now, 1), "t": e["t"], "kind": e["kind"],
                            "text": e["text"], "ready": e["series"] is not None}
                           for e in reversed(self.events)],
                "dtc": self.dtc,
                "identity": self.identity,
                "supported": [f"{p:02X}" for p in self.supported],
                "link": {"ok": ok, "total": len(self.req_log)},
                "session_secs": round(now - self.session_start, 0) if self.session_start else None,
                "log_path": self.log_path,
            }

    def _stale_after(self, key):
        """Na hoeveel seconden is een waarde verouderd? Kanalen die roulerend worden gelezen (bv. eens per 22 s) zijn
        niet verouderd na 10 s: 2,5x het gemeten interval, minimaal 10 s."""
        h = self.hist.get(key)
        pts = [t for t, _ in list(h)[-12:]] if h else []
        if len(pts) < 3:
            return 10.0
        gaps = sorted(b - a for a, b in zip(pts, pts[1:]))
        return max(10.0, 2.5 * gaps[len(gaps) // 2])

    def history(self, secs, max_points=400):
        now = time.monotonic()
        with self.lock:
            out = {}
            for key, h in self.hist.items():
                pts = [[round(t - now, 2), v] for t, v in h if t >= now - secs]
                if len(pts) > max_points:
                    step = len(pts) / max_points
                    pts = [pts[int(i * step)] for i in range(max_points)]
                if pts:
                    out[key] = pts
            return out

    def event_detail(self, event_id):
        with self.lock:
            for e in self.events:
                if e["id"] == event_id:
                    return {k: e[k] for k in ("id", "t", "kind", "text", "values", "series")}
        return None
