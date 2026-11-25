"""Meekijken: de webinterface voedt zich met een opname van het uitleesscript in plaats van de kabel te openen.

Het uitleesscript (bij de Matiz: reader/egr_capture.py, gestart via rit.py) houdt de kabel vast en schrijft elke
ronde één regel: {"t": seconden, "e": epoch, "<blok>": "<hex>", "pid<XX>": "<hex>"}. Deze module leest die regels (live of
achteraf) en zet ze om naar dezelfde waarden als de directe lezer, zodat pagina, afwijkingsregels en vastgelegde momenten
hetzelfde werken. De kabel wordt niet geopend en er gaat niets naar de auto.

Voertuigkennis zit hier niet: welke bytes wat betekenen komt uit het signalenbestand en het profiel (signalset.py,
vehicle.PROFILES[..]["feed"]). Zonder die gegevens werkt het meekijken nog steeds met de standaard-PID's in de opname.
"""
import glob
import json
import os
import tempfile
import time

import analyzer
import obd
import signalset
import vehicle

HERE = os.path.dirname(os.path.abspath(__file__))
MARKS_DIR = os.path.join(HERE, "logs")     # annotaties van de gebruiker (knop "Annotatie: probleem") komen hier, nooit in de map van de opname
LIVE_MAX_AGE = 20.0        # een bestand dat langer niet groeit is geen lopende opname
STALL_AFTER = 15.0         # zo lang zonder nieuwe regels = de opname krijgt geen antwoord van de ECU
DEFAULT_PATTERNS = ["*.jsonl"]


class _NullCsv:
    def writerow(self, row):
        pass


class _ScaledClock:
    """Klok voor afspelen: loopt `speed` keer zo snel, zodat een rit van 17 minuten in een minuut te bekijken is.
    Vervangt `analyzer.time`, dus alle tijden in de analyse lopen mee."""

    def __init__(self, speed):
        self.speed, self._m0, self._w0 = speed, time.monotonic(), time.time()

    def monotonic(self):
        return self._m0 + (time.monotonic() - self._m0) * self.speed

    def time(self):
        return self._w0 + (time.time() - self._w0) * self.speed

    def __getattr__(self, name):
        return getattr(time, name)


class _Tail:
    """Leest alleen volledige regels die erbij gekomen zijn sinds de vorige keer."""

    def __init__(self, path):
        self.path, self.pos, self.partial = path, 0, b""

    def skip_to_end(self):
        self.pos = os.path.getsize(self.path)

    def poll(self):
        size = os.path.getsize(self.path)
        if size < self.pos:                         # afgekapt of vervangen: opnieuw beginnen
            self.pos, self.partial = 0, b""
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read()
        self.pos += len(data)
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()                  # laatste stuk is nog niet af
        rows = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("t"), (int, float)):
                rows.append(row)
        return rows


class FeedMonitor(analyzer.Monitor):
    """Zelfde uitvoer als analyzer.Monitor (snapshot, history, events), maar met een opnamebestand als bron.

    source: bestand of map; None = de map uit het profiel. replay=True speelt een bestaand bestand af (speedup keer
    sneller dan echte tijd); anders wordt een lopende opname gevolgd. signals: eigen signalenbestand i.p.v. dat uit het
    profiel."""

    is_feed = True

    def __init__(self, source=None, replay=False, speedup=1.0, profile="auto", signals=None, log=print):
        pname = vehicle.pick_profile(profile)
        cfg = vehicle.PROFILES[pname].get("feed", {})
        self.patterns = cfg.get("patterns", DEFAULT_PATTERNS)
        src = source if source and source != "auto" else cfg.get("dir")
        self.source = os.path.expanduser(src) if src else None
        self.replay = replay
        self.speedup = max(speedup, 0.1)
        if replay:
            analyzer.time = _ScaledClock(self.speedup)
        scratch = tempfile.mkdtemp(prefix="matiz-feed-")
        super().__init__(None, scratch, log=log, profile=profile)
        self._csv_file.close()                      # deze modus logt niet zelf: de opname is het log
        os.remove(self.log_path)
        os.rmdir(scratch)
        self._csv = _NullCsv()
        self.log_path = self.source
        self.sigs = signalset.SignalSet(cfg, signals, log=log)
        self.windows = []
        self.feed_name = None
        self.last_row_at = None
        self.cur_path = None
        self.last_row_t = None                      # `t` van de laatste verwerkte regel (voor de markeringen)
        self._meta_mtime = None

    def stop(self):
        super().stop()
        if self.replay:
            analyzer.time = time                    # de versnelde klok niet laten doorlopen in een volgende bron

    # ---------- niet beschikbaar zonder kabel ----------
    def request_dtc_read(self):
        pass

    def request_ident_ext(self):
        pass

    def mark(self):
        """Als analyzer.Monitor.mark, en bewaar het moment ook naast de opname-naam in logs/ zodat een rapport het later
        kan terugvinden (kloktijd `e`, en `t` van de laatste opnameregel). De opname zelf blijft van het uitleesscript."""
        super().mark()
        if self.replay or not self.cur_path:
            return
        line = json.dumps({"e": round(time.time(), 3), "t": self.last_row_t, "opname": self.feed_name,
                           "tekst": "annotatie: probleem"})
        try:
            os.makedirs(MARKS_DIR, exist_ok=True)
            stem = os.path.splitext(self.feed_name)[0]
            with open(os.path.join(MARKS_DIR, f"{stem}.markeringen.log"), "a") as f:
                f.write(line + "\n")
        except OSError as e:
            self.log(f"markering niet bewaard: {e}")

    # ---------- bron ----------
    def _pick(self):
        if self.source is None:
            return None
        if os.path.isfile(self.source):
            return self.source
        files = [f for p in self.patterns for f in glob.glob(os.path.join(self.source, p))]
        return max(files, key=os.path.getmtime) if files else None

    def _status(self, state, text):
        if state != self.status.get("state"):       # alleen een wisseling loggen; de tekst loopt elke tik mee
            self._set_status(state, text, port=self.feed_name, feed=True, demo=False)
        else:
            with self.lock:
                self.status.update(text=text, port=self.feed_name)

    def _identity(self):
        p = vehicle.PROFILES[self.profile_name]
        notes = ["Deze pagina kijkt mee met de opname van het uitleesscript; de kabel is van dat script. "
                 "Foutcodes lezen en ECU-identificatie kunnen daarom hier niet."]
        if self.sigs.error:
            notes.append(self.sigs.error)
        elif not self.sigs.defs:
            notes.append("Geen signalenbestand voor dit profiel: alleen de standaard-PID's uit de opname worden getoond.")
        return {"protocol": "meekijken met opname", "init": "—", "keybytes": "—", "obd_std": None, "fuel": None,
                "diesel": False, "vin": None, "vin_info": None, "cal_id": None, "ecu_name": None, "mode09": [],
                "supported_pids": [], "profile": self.profile_name, "profile_label": p["label"],
                "notes": notes, "ecu_ext": None}

    def _load_meta(self):
        """Optioneel zijbestand van het uitleesscript naast de opname: <naam>.meta.json met de identiteit van de ECU.
        Verwacht (alles optioneel): {"ecu": {"onderdeelnummer", "kalibratie", "software", "productiedatum"}}. Het script
        vult het aan nadat het is aangeknoopt, dus het wordt opnieuw gelezen zodra het bestand verandert. Onbekende
        sleutels worden genegeerd; ontbreekt het bestand, dan blijft de identiteit leeg."""
        if not self.cur_path:
            return
        path = os.path.splitext(self.cur_path)[0] + ".meta.json"
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if mtime == self._meta_mtime:
            return
        self._meta_mtime = mtime
        try:
            ecu = json.load(open(path)).get("ecu") or {}
        except (OSError, ValueError, AttributeError):
            return                                  # half geschreven: bij de volgende wijziging opnieuw
        if not isinstance(ecu, dict):
            return
        ident = self._identity()
        ident["ecu_name"] = str(ecu["onderdeelnummer"]) if ecu.get("onderdeelnummer") else None
        ident["cal_id"] = str(ecu["kalibratie"]) if ecu.get("kalibratie") else None
        for key, label in (("software", "ECU-software"), ("productiedatum", "ECU-productiedatum")):
            if ecu.get(key):
                ident["notes"].append(f"{label} {ecu[key]}")
        with self.lock:
            self.identity = ident

    def _new_source(self, path):
        with self.lock:
            self._reset_session()
            self.events = []
            self._last_event = {}
            self.identity = self._identity()
            self.windows = []
        self.cur_path = path
        self.feed_name = os.path.basename(path)
        self.last_row_at = None
        self.last_row_t = None
        self._meta_mtime = None
        self._load_meta()

    # ---------- verwerken ----------
    def _ingest(self, row, now):
        vals = self.sigs.decode(row)
        self.last_row_t = row["t"]
        with self.lock:
            if self.session_start is None:
                self.session_start = now
        # gaspedaal en snelheid eerst: de stotterdetectie op het toerental kijkt daar naar
        for key in sorted(vals, key=lambda k: k not in ("tps", "speed")):
            v = vals[key]
            lim = analyzer.LIMITS.get(key)
            if lim and not lim[0] <= v <= lim[1]:
                continue                            # onmogelijke waarde (beschadigd frame): niet gebruiken
            self._record(key, v, now)
        windows = self.sigs.windows()
        with self.lock:
            self.windows = windows
        self._finalize_events(now)

    def _ingest_batch(self, rows):
        """Tijdstip per regel: de kloktijd uit de opname (e), anders achterwaarts vanaf nu via t."""
        now = time.monotonic()
        last_t = rows[-1]["t"]
        for row in rows:
            mono = row["e"] - self.wall0 if isinstance(row.get("e"), (int, float)) else now - (last_t - row["t"])
            mono = min(mono, now)
            if mono < now - analyzer.HISTORY_SECS:
                continue
            self._ingest(row, mono)
        self.last_row_at = now

    # ---------- hoofdlus ----------
    def _run(self):
        try:
            self._run_replay() if self.replay else self._run_follow()
        except Exception as e:                      # bv. bestand verdwenen tijdens het lezen
            self._status("fout", f"{type(e).__name__}: {e}")

    def _run_follow(self):
        tail = None
        while not self._stop.is_set():
            path = self._pick()
            if path is None:
                self.feed_name = None
                where = f"in {self.source}" if self.source else "(geen map ingesteld; geef een bestand of map mee met --follow)"
                self._status("wacht_opname", f"Geen opname gevonden {where}. Start de opname met het uitleesscript.")
                self._sleep(1.0)
                continue
            if tail is None or tail.path != path:
                tail = _Tail(path)
                self._new_source(path)
                if time.time() - os.path.getmtime(path) > LIVE_MAX_AGE:
                    tail.skip_to_end()              # oude opname: geen lopende rit, niet doen alsof
            rows = tail.poll()
            if rows:
                self._ingest_batch(rows)
            self._load_meta()
            now = time.monotonic()
            if self.last_row_at is None:
                self._status("wacht_opname", f"Laatste opname: {self.feed_name}, maar die groeit niet. "
                             "Wacht op een nieuwe opname of speel een oude af met --replay.")
            elif now - self.last_row_at > STALL_AFTER:
                self._status("opname_stil", f"Geen nieuwe metingen in {self.feed_name} sinds "
                             f"{now - self.last_row_at:.0f} s: de opname krijgt geen antwoord van de ECU "
                             "(contact uit?) of is gestopt.")
            else:
                self._status("meekijken", f"Meekijken met {self.feed_name}")
            self._sleep(0.25)

    def _run_replay(self):
        path = self._pick()
        if path is None:
            self._status("wacht_opname", "Geen opname gevonden om af te spelen.")
            return
        self._new_source(path)
        tail = _Tail(path)
        rows = tail.poll()
        if not rows:
            self._status("fout", f"{self.feed_name} bevat geen bruikbare regels.")
            return
        t0 = rows[0]["t"]
        clock = analyzer.time                       # de versnelde klok: de analyse leest zijn tijd daar ook van
        start = clock.monotonic()
        self.session_start = start
        i = 0
        while not self._stop.is_set():
            now = clock.monotonic()
            while i < len(rows) and rows[i]["t"] - t0 <= now - start:
                self._ingest(rows[i], start + rows[i]["t"] - t0)
                i += 1
            if i >= len(rows):
                self._status("opname_gestopt", f"Afspelen van {self.feed_name} klaar ({len(rows)} regels).")
                self._sleep(1.0)
                self._finalize_events(clock.monotonic())
                continue
            self._status("meekijken", f"Afspelen van {self.feed_name} ({self.speedup:g}×, "
                         f"{rows[min(i, len(rows) - 1)]['t'] - t0:.0f} van {rows[-1]['t'] - t0:.0f} s)")
            self._sleep(0.1)

    # snapshot(), _stale_after() en de signaalmeta zitten in analyzer.Monitor: de kabelmodus gebruikt ze ook
