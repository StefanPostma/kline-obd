"""Schakelen tussen twee bronnen: meekijken met een opname (feed.py) of rechtstreeks de kabel gebruiken (analyzer.Monitor).

De pagina praat met één Hub. Die houdt de actieve monitor vast en wisselt op verzoek. Alleen de kabelbron opent de kabel,
en alleen als niemand anders hem vasthoudt (bv. de opname van het uitleesscript): twee programma's op één K-line
storen elkaar en kunnen de opname bederven.
"""
import glob
import os
import subprocess
import threading

import vehicle

MODES = {"feed": "opname (bestand)", "cable": "kabel (direct)"}
STOP_WAIT = 30.0        # zo lang mag de vorige bron nodig hebben om af te sluiten (aanknopen kan enkele seconden duren)


def cable_holder(pattern="/dev/cu.usbserial-*"):
    """Wie anders dan wij heeft de kabel open? Tekst voor de gebruiker, of None als hij vrij is (of lsof ontbreekt)."""
    for dev in sorted(glob.glob(pattern)):
        try:
            out = subprocess.run(["lsof", "-Fp", dev], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        pids = [int(x[1:]) for x in out.split() if x[:1] == "p" and x[1:].isdigit() and int(x[1:]) != os.getpid()]
        if pids:
            try:
                args = subprocess.run(["ps", "-p", str(pids[0]), "-o", "args="], capture_output=True, text=True,
                                      timeout=3).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                args = ""
            script = " ".join(t for t in args.split() if not t.startswith("/")) or "een ander programma"
            return f"{script[:80]} (pid {pids[0]})"
    return None


class Hub:
    """Zelfde methoden als analyzer.Monitor (snapshot, history, event_detail, ...), plus schakelen."""

    def __init__(self, make_feed, make_cable, mode, holder=cable_holder, log=print):
        self.make = {"feed": make_feed, "cable": make_cable}
        self.mode = mode
        self.holder = holder
        self.log = log
        self.mon = None
        self.switching = None
        self.error = None
        self._lock = threading.Lock()

    @property
    def log_path(self):
        return self.mon.log_path

    def start(self):
        if self.mode == "cable":
            who = self.holder()
            if who:
                self.log(f"LET OP: de kabel is al in gebruik door {who}")
        self.mon = self.make[self.mode]()
        self.mon.start()

    def stop(self):
        if self.mon:
            self.mon.stop()

    # ---------- schakelen ----------
    def switch(self, mode):
        """Geeft (http-code, antwoord). Het eigenlijke omschakelen gebeurt op de achtergrond."""
        if mode not in MODES:
            return 400, {"error": "onbekende bron"}
        with self._lock:
            if self.switching:
                return 409, {"error": "Er wordt al geschakeld."}
            if mode == self.mode:
                return 200, {"mode": mode}
            if mode == "cable":
                who = self.holder()
                if who:
                    self.error = (f"De kabel is in gebruik door {who}. Stop dat eerst, of blijf bij meekijken "
                                  "met de opname.")
                    return 409, {"error": self.error}
            self.error = None
            self.switching = mode
        threading.Thread(target=self._swap, args=(mode,), name="hub-switch", daemon=True).start()
        return 202, {"switching": mode}

    def _swap(self, mode):
        old = self.mon
        try:
            old.stop()
            thread = getattr(old, "thread", None)
            if thread:
                thread.join(STOP_WAIT)
                if thread.is_alive():
                    raise RuntimeError("de vorige bron sluit nog af")
            try:
                old._csv_file.close()
            except Exception:
                pass
            new = self.make[mode]()
            new.start()
            self.mon, self.mode = new, mode
            self.log(f"[bron] nu: {MODES[mode]}")
        except Exception as e:
            self.error = f"Schakelen naar {MODES[mode]} mislukt: {type(e).__name__}: {e}"
            self.log(f"[bron] {self.error}")
        finally:
            self.switching = None

    # ---------- doorgeven aan de actieve bron ----------
    def snapshot(self):
        snap = self.mon.snapshot()
        snap.setdefault("windows", [])               # alleen de opnamebron kent testvensters en aandachtssignalen
        snap.setdefault("highlight", [])
        snap["profile"] = {"name": self.mon.profile_name, "label": vehicle.PROFILES[self.mon.profile_name]["label"]}
        snap["source"] = {"mode": self.mode, "switching": self.switching, "error": self.error, "modes": MODES}
        if self.switching:
            snap["status"] = dict(snap["status"], state="schakelen", text=f"Schakelen naar {MODES[self.switching]}…")
        return snap

    def history(self, secs):
        return self.mon.history(secs)

    def event_detail(self, event_id):
        return self.mon.event_detail(event_id)

    def request_dtc_read(self):
        self.mon.request_dtc_read()

    def request_ident_ext(self):
        self.mon.request_ident_ext()

    def mark(self):
        self.mon.mark()
