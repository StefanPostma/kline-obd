#!/usr/bin/env python3
"""Zelftest: draait kwp.py / obd.py / analyzer.py tegen de simulator (geen auto of kabel nodig).

  python selftest.py          # protocollen + analyse (~50 s)
  python selftest.py --quick  # alleen protocollen (~15 s)
"""
import json
import os
import sys
import tempfile
import time

import analyzer
import obd
import sim
import vehicle
from kwp import BadFrame, KLine, NoEcho

failures = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FOUT ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def test_decode():
    print("DTC-decodering")
    check("P0171", obd.decode_dtcs(bytes([0x01, 0x71])) == ["P0171"])
    check("U0123 + padding", obd.decode_dtcs(bytes([0xC1, 0x23, 0, 0, 0, 0])) == ["U0123"])
    check("aantalbyte vooraan wordt genegeerd", obd.decode_dtcs(bytes([1, 0x03, 0x00])) == ["P0300"])
    check("alleen nullen = geen codes", obd.decode_dtcs(bytes(6)) == [])


def test_protocol(proto):
    print(f"Protocol {proto}")
    ecu = sim.SimECU(proto)
    kl = KLine(sim.FakeSerial(ecu))
    kl.fast_attempts = 1                      # de simulator van de trage protocollen antwoordt nooit op een wekpuls
    t0 = time.monotonic()
    method = kl.connect()
    print(f"       init via {method} in {time.monotonic() - t0:.1f} s")
    check("verbonden", kl.init_method is not None)
    check("keybytes", kl.keybytes == ecu.kb, str(kl.keybytes))
    check("framing", kl.proto == ("iso9141" if ecu.iso else "kwp"), str(kl.proto))
    sup = obd.supported_pids(kl)
    check("ondersteunde PID's", set(sup) == {p for p in sim.SUPPORTED if p % 0x20}, str(sup))
    ref = ecu.engine.values()
    got = {}
    for pid in (0x0C, 0x05, 0x0B, 0x11, 0x06, 0x14, 0x42, 0x01, 0x03):
        got.update(obd.read_pid(kl, pid))
    check("rpm", abs(got["rpm"] - ref["rpm"]) < 80, f"{got['rpm']:.0f} vs {ref['rpm']:.0f}")
    check("koelwater", abs(got["ect"] - ref["ect"]) <= 2, f"{got['ect']} vs {ref['ect']:.1f}")
    check("MAP", abs(got["map"] - ref["map"]) < 12, f"{got['map']} vs {ref['map']:.1f}")
    check("TPS", abs(got["tps"] - ref["tps"]) < 1.5)
    check("accuspanning", abs(got["batt"] - ref["batt"]) < 0.3)
    check("lambda", 0 <= got["o2_b1s1"] <= 1 and "o2_b1s1_trim" not in got)
    check("MIL/dtc_count", got["mil"] == 1 and got["dtc_count"] == 1, f"{got['mil']} {got['dtc_count']}")
    ident = obd.identify(kl, sup)
    check("VIN uitgelezen", ident["vin"] == sim.SIM_VIN, str(ident["vin"]))
    check("kalibratie-ID", ident["cal_id"] == "SIM-CAL-0001", str(ident["cal_id"]))
    check("ECU-naam", ident["ecu_name"] == "SIMULATED-ECU", str(ident["ecu_name"]))
    check("OBD-norm", ident["obd_std"] == "EOBD (Europa)", str(ident["obd_std"]))
    check("benzine herkend", ident["diesel"] is False, str(ident["diesel"]))
    v = vehicle.decode_vin(ident["vin"]) or {}
    check("VIN-decodering", v.get("maker", "").startswith("GM Korea") and v.get("year") == 2009, str(v))
    if not ecu.iso:
        ext = obd.probe_kwp_ident(kl)
        check("KWP 1A-identificatie", set(ext) == {"87", "9B"} and ext["87"]["text"] == "SIMECU-ID-87", str(ext))
    check("opgeslagen foutcode", obd.read_dtcs(kl, 0x03) == ["P0171"])
    check("pending leeg", obd.read_dtcs(kl, 0x07) == [])
    fr = obd.read_freeze(kl)
    check("freeze frame", fr.get("dtc") == "P0171" and "rpm" in fr, str(fr))


def test_byte_trick_and_noecho():
    print("Fast init met byte-truc, en echo-detectie")
    kl = KLine(sim.FakeSerial(sim.SimECU("kwp-fast")))
    check("fast-byte werkt", kl.connect(methods=("fast-byte",)) == "fast-byte")

    class Dead(sim.FakeSerial):                     # kabel waarvan de K-line niet op de FTDI zit
        def write(self, data):
            return len(data)
    dead = KLine(Dead(sim.SimECU()))
    dead.fast_attempts = 1
    try:
        dead.connect()
        check("NoEcho bij dode kabel", False)
    except NoEcho:
        check("NoEcho bij dode kabel", True)

    class Quiet(sim.FakeSerial):                    # kabel die niet echoot, ECU antwoordt wel
        def write(self, data):
            now = time.monotonic()
            if self.baudrate < 1000:
                self.ecu.awake_until = now + 1.0
                self.ecu.session = False
                return len(data)
            for delay, chunk in self.ecu.on_bytes(bytes(data), now):
                for i, b in enumerate(chunk):
                    self._push(now + delay + 0.001 * i, b)
            return len(data)
    kq = KLine(Quiet(sim.SimECU()))
    kq.connect()
    check("kabel zonder echo: terugval werkt", kq.check_echo is False and obd.read_pid(kq, 0x0C)["rpm"] > 0)

    # sessie-timeout: na >5 s stilte vergeet de ECU de sessie en moet je opnieuw aanknopen
    ecu = sim.SimECU()
    kl = KLine(sim.FakeSerial(ecu))
    kl.connect()
    ecu.last_rx -= 6
    try:
        obd.read_pid(kl, 0x0C)
        check("verlopen sessie wordt gemeld", False)
    except Exception as e:
        check("verlopen sessie wordt gemeld", type(e).__name__ == "NoResponse", type(e).__name__)


def test_noise():
    print("Ruis op de lijn: elk 2e antwoord heeft een foute checksum")
    fs = sim.FakeSerial(sim.SimECU())
    fs.corrupt_every = 2
    kl = KLine(fs)
    kl.connect()
    check("aanknopen ondanks aangetast StartCommunication-antwoord", kl.init_method == "fast-break")
    n0 = kl.checksum_errors
    vals = [obd.read_pid(kl, 0x0C)["rpm"] for _ in range(12)]
    check("alle verzoeken slagen via opnieuw aanvragen", all(v > 0 for v in vals), str(vals))
    check("ruis wordt geteld", kl.checksum_errors - n0 >= 10, str(kl.checksum_errors - n0))


def test_plausibility_and_events():
    print("Plausibiliteit en stotter-detectie (nagemaakte rijsituaties)")
    from collections import deque
    tmp = tempfile.mkdtemp(prefix="matiz-selftest-")
    mon = analyzer.Monitor(lambda: None, tmp, log=lambda *a: None)
    now = time.monotonic()
    check("onmogelijk toerental genegeerd", mon._validate(None, 0x0C, {"rpm": 15360}, now) is None)
    check("STFT -60% genegeerd", mon._validate(None, 0x06, {"stft": -60.2}, now) is None)
    check("plausibele waarde blijft", mon._validate(None, 0x0C, {"rpm": 900}, now) == {"rpm": 900})
    mon.latest["rpm"] = (now - 0.3, 900)
    orig = obd.read_pid
    try:
        obd.read_pid = lambda kl, pid: {"rpm": 910}
        check("niet-bevestigde sprong genegeerd", mon._validate(None, 0x0C, {"rpm": 3500}, now) is None)
        obd.read_pid = lambda kl, pid: {"rpm": 3480}
        check("bevestigde sprong geaccepteerd", mon._validate(None, 0x0C, {"rpm": 3500}, now) == {"rpm": 3500})
    finally:
        obd.read_pid = orig

    def scenario(speed, tps_pts, rpm_pts):
        m = analyzer.Monitor(lambda: None, tmp, log=lambda *a: None)
        t = time.monotonic()
        for key, pts in (("tps", tps_pts), ("rpm", rpm_pts), ("speed", [(-1.0, speed)])):
            m.hist[key] = deque((t + dt, v) for dt, v in pts)
            m.latest[key] = (t + pts[-1][0], pts[-1][1])
        m.hist["tps"].appendleft((t - 60, 10.2))          # gasklep dicht eerder in de sessie
        m._detect("rpm", t, rpm_pts[-1][1])
        return [e["kind"] for e in m.events]

    dip = [(-1.4, 3000), (-0.7, 2500), (0.0, 1900)]
    check("uitrollen (gasklep dicht, rijdend) telt niet", scenario(40, [(-2.0, 10.2), (-0.5, 10.2)], dip) == [])
    check("schakelen (gas gaat los) telt niet", scenario(40, [(-2.0, 35.0), (-0.5, 12.0)], dip) == [])
    check("haperen bij open gas wordt gezien", scenario(30, [(-2.0, 35.0), (-0.5, 35.5)], dip) == ["rpm_dip"])
    check("stotteren stationair wordt gezien", scenario(0, [(-2.0, 10.2), (-0.5, 10.4)],
                                                        [(-1.4, 850), (-0.7, 700), (0.0, 520)]) == ["rpm_dip"])
    check("te weinig gaspedaal-metingen: niet beoordelen", scenario(30, [(-0.5, 35.0)], dip) == [])
    # Opnames hebben ~1,2 s per ronde (grof ritme): rijdend is een toerentalsprong dan geen stotter te noemen
    # (rit 23-11: 9 van 9 "pieken" waren gewoon optrekken of schakelen), stilstaand blijft het wel een signaal.
    coarse_idle = [(-6.0, 905), (-4.8, 900), (-3.6, 903), (-2.4, 902), (-1.2, 880), (0.0, 600)]
    coarse_surge = [(-6.0, 2000), (-4.8, 2010), (-3.6, 2005), (-2.4, 2000), (-1.2, 2300), (0.0, 2650)]
    steady = [(-3.0, 35.0), (-1.5, 35.5)]
    check("grof ritme, stilstaand: dip wordt gezien", scenario(0, [(-3.0, 10.2), (-1.5, 10.3)], coarse_idle) == ["rpm_dip"])
    check("grof ritme, rijdend: toerentalpiek bij optrekken telt niet", scenario(40, steady, coarse_surge) == [])
    check("zelfde piek bij snel ritme (0,7 s) telt wel",
          scenario(40, [(-2.0, 35.0), (-0.5, 35.5)], [(-1.4, 2000), (-0.7, 2300), (0.0, 2650)]) == ["rpm_surge"])
    check("net gestarte motor: opstarttoerental telt niet",
          scenario(0, [(-3.0, 10.2), (-1.5, 10.3)], [(-6.0, 0), (-4.8, 0), (-3.6, 0), (-2.4, 0), (-1.2, 1850), (0.0, 1600)]) == [])
    check("bijna afgeslagen blijft altijd een moment",
          scenario(40, steady, [(-6.0, 1500), (-4.8, 1500), (-3.6, 1500), (-2.4, 1500), (-1.2, 1400), (0.0, 380)]) == ["rpm_dip"])
    def map_case(rpm_pts):
        ms = analyzer.Monitor(lambda: None, tmp, log=lambda *a: None)
        t_ = time.monotonic()
        for key, pts in (("rpm", rpm_pts), ("tps", [(-2.0, 10.2), (-0.5, 10.3)]), ("map", [(-1.2, 102.0), (0.0, 69.0)])):
            ms.hist[key] = deque((t_ + dt, v) for dt, v in pts)
            ms.latest[key] = (t_ + pts[-1][0], pts[-1][1])
        ms._detect("map", t_, 69.0)
        return [e["kind"] for e in ms.events]
    check("MAP-val bij stilstaand draaiende motor wordt gezien", map_case([(-6.0, 950), (-3.0, 940), (-1.0, 945)]) == ["map"])
    check("MAP-val bij het starten van de motor telt niet", map_case([(-6.0, 0), (-3.0, 0), (-1.0, 1100)]) == [])
    m2 = analyzer.Monitor(lambda: None, tmp, log=lambda *a: None)
    t2 = time.monotonic()
    for i in range(8):
        m2._record("stft", -9.0 + (i % 3 - 1), t2 - 8 + i)
    m2._record("ltft", -7.5, t2 - 1)
    titles = [a["title"] for a in m2.evaluate(t2)]
    check("totale correctie (-9,25 + -7,5 = -17%) wordt gemeld",
          any("Totale brandstofcorrectie -17%" in x for x in titles), str(titles))
    m = analyzer.Monitor(lambda: None, tmp, log=lambda *a: None)
    m.mark()
    check("handmatig markeren legt een moment vast", [e["kind"] for e in m.events] == ["handmatig"])


def test_analysis(seconds=38):
    print(f"Monitor + analyse ({seconds} s simulatie, stotter op t=12 s en t=30 s)")
    mon = analyzer.Monitor(lambda: (sim.FakeSerial(sim.SimECU("kwp-fast")), "DEMO"),
                           tempfile.mkdtemp(prefix="matiz-selftest-"), demo=True, log=lambda *a: None,
                           profile="matiz08")
    mon.start()
    time.sleep(seconds)
    mon.request_dtc_read()
    time.sleep(2.5)
    snap = mon.snapshot()
    mon.stop()
    json.dumps(snap, allow_nan=False)
    check("status verbonden", snap["status"]["state"] == "verbonden", snap["status"]["text"])
    check("demo-vlag gezet", snap["status"]["demo"] is True)
    kinds = [e["kind"] for e in snap["events"]]
    rpm_events = [k for k in kinds if k.startswith("rpm")]
    check("toerental-dip vastgelegd", "rpm_dip" in kinds, str(kinds))
    check("dip en herstel tellen als één moment", 1 <= len(rpm_events) <= 3, str(kinds))
    ready = [e for e in snap["events"] if e["ready"]]
    series = mon.event_detail(ready[0]["id"])["series"] if ready else {}
    check("event bevat meetreeks rond het moment", all(series.get(k) for k in ("rpm", "map", "stft")), str(list(series)))
    # Let op: kortdurende pieken in STFT/MAP (~1 s) kunnen tussen twee metingen vallen (~1,2 s per PID).
    titles = " | ".join(a["title"] for a in snap["alerts"])
    check("LTFT-afwijking gemeld", "LTFT" in titles, titles)
    check("DTC-melding", "foutcode" in titles, titles)
    check("foutcode gelezen", snap["dtc"] and snap["dtc"]["stored"][0]["code"] == "P0171", str(snap["dtc"]))
    ident = snap["identity"] or {}
    check("identiteit in snapshot", ident.get("vin") == sim.SIM_VIN and ident.get("fuel") == "benzine", str(ident))
    check("profiel", ident.get("profile") == "matiz08", str(ident.get("profile")))
    check("identiteit bewaard naast log", os.path.exists(mon.log_path.replace(".csv", ".identity.json")))
    check("meerdere waarden", len(snap["values"]) >= 12, str(len(snap["values"])))
    rate = snap["values"]["rpm"]["n"] / seconds
    print(f"       toerental {rate:.1f}×/s, totaal {sum(v['n'] for v in snap['values'].values()) / seconds:.1f} verzoeken/s")
    check("toerental ≥ 1,5×/s", rate >= 1.5, f"{rate:.1f}")
    print("       alerts:", titles or "geen")


if __name__ == "__main__":
    test_decode()
    for p in ("kwp-fast", "kwp-slow", "iso9141-slow"):
        test_protocol(p)
    test_byte_trick_and_noecho()
    test_noise()
    test_plausibility_and_events()
    if "--quick" not in sys.argv:
        test_analysis()
    print()
    print("ALLES OK" if not failures else f"{len(failures)} FOUT(EN): {failures}")
    sys.exit(1 if failures else 0)
