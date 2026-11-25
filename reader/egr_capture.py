"""Verbinding met de ECU: leest de datablokken, logt ze en zet de laatste waarden klaar voor de webpagina. Alleen lezen.

Gebruik: egr_capture.py <uitvoer.jsonl> [seconden] [--wacht-op-gas]      (Ctrl-C stopt netjes)

Dit proces is het enige dat de kabel opent. Het
  - verbindt opnieuw als de ECU even wegvalt (starten, contact uit/aan);
  - herhaalt een mislukt verzoek een keer;
  - schrijft elke ronde een regel ruwe data naar <uitvoer.jsonl>;
  - schrijft elke ronde live.json: de laatste waarden omgerekend, plus de kwaliteit van de verbinding;
  - schrijft elke leesfout naar <uitvoer zonder extensie>.fouten.log (JSON-regels).
Elke ronde: de kernblokken plus de korte brandstofcorrectie, en een uit de roulerende lijst (elk ~22 s aan de beurt).
Welke blokken en PID's dat zijn staat in het voertuigbestand (KERN_BLOKKEN, ROULEREND_BLOKKEN, PID_ELKE_RONDE,
PID_ROULEREND in signalen.py); PID's die de ECU niet kent vallen na het aanknopen vanzelf uit het plan.
"""
import json
import os
import sys
import time

from kline import KLine, KLineError, NegativeResponse
from paden import datamap, in_datamap

try:
    import signalen
except Exception as e:  # live.json is bijzaak: de opname mag er nooit op stuklopen
    if os.environ.get("KLINE_VOERTUIG"):  # maar een gevraagd voertuig dat niet laadt is een fout, geen bijzaak
        sys.exit(f"voertuigbestand laden mislukt: {e}")
    signalen = None

if getattr(signalen, "TRANSPORT", "kwp2000") == "kw1281":
    import runpy
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kw1281_opname.py"), run_name="__main__")
    sys.exit(0)

LIVE = os.path.join(datamap(), "live.json")


def plan(naam, standaard):
    """Een onderdeel van het leesplan uit het voertuigbestand (signalen.py); ontbreekt het, dan het plan van de Matiz."""
    waarde = getattr(signalen, naam, None) if signalen else None
    return list(waarde) if waarde is not None else standaard


# Standaardwaarden = het plan van de Matiz 0.8 (zo is elke rit tot nu toe opgenomen):
# 01 = ruwe ADC's (o.a. EGR-klepstand, klopsensor, beide lambdasondes), 0C = toerental/MAP/closed-loop-vlag,
# 0D = toestandsvlaggen, 03 = krukas-onrust per cilinder, 02 = EGR-stuursignaal, rijsnelheid (byte 4), lucht per slag
CORE = plan("KERN_BLOKKEN", [0x01, 0x0C, 0x0D, 0x03, 0x02])
# overige blokken met inhoud; 06/08/09/0A/16/17 zijn altijd 0 en 13 is statisch
ROTATE = plan("ROULEREND_BLOKKEN", [0x04, 0x05, 0x07, 0x0B, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x14, 0x15])
# standaard-PID's die roulerend meelopen: snelheid (controle op 02[4]), closed-loop-status, LTFT, koelvloeistof,
# belasting, voorontsteking
PIDS = plan("PID_ROULEREND", [0x0D, 0x03, 0x07, 0x05, 0x04, 0x0E])
# elke ronde: de korte brandstofcorrectie, om naast de sonde na de katalysator te leggen
VAST = plan("PID_ELKE_RONDE", [0x06])
AANKNOPEN = tuple(plan("AANKNOPEN", ["fast"]))  # init-methoden in volgorde; de Matiz gebruikt alleen fast init

out = in_datamap(sys.argv[1])
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
# --wacht-op-gas: stop vanzelf zodra er GAS_S seconden boven GAS_RPM is gedraaid en het toerental daarna
# NA_S seconden weer stationair is; `seconds` is dan de maximale wachttijd
WAIT_FOR_REV = "--wacht-op-gas" in sys.argv
GAS_RPM, GAS_S, NA_S = 1700, 40.0, 10.0

# kwaliteit van de verbinding; "mislukt" = ook na de herhaling geen bruikbaar antwoord
stats = {"verzoeken": 0, "geen_antwoord": 0, "checksum": 0, "echo": 0, "negatief": 0, "herhaald": 0, "mislukt": 0}
last = {}  # kanaal -> (ruwe bytes, kloktijd van lezen)


def w(hexstr, off):
    b = bytes.fromhex(hexstr)
    return int.from_bytes(b[off:off + 2], "big")


def connect(k):
    k.aanknopen(AANKNOPEN, tries=3)
    if k.mode == "kwp" and getattr(k, "ecu", None) is not None:
        k.use_physical()


def soort(e):
    """geen_antwoord = ECU zweeg; checksum = antwoord beschadigd; echo = eigen verzending beschadigd op de lijn."""
    if isinstance(e, NegativeResponse):
        return "negatief"
    s = str(e)
    return "checksum" if "checksum" in s else "echo" if "echo" in s else "geen_antwoord"


def ask(k, svc, ident, t_rel, errlog):
    """Een verzoek, bij een leesfout een keer herhaald. Geeft (payload of None, antwoordde de ECU)."""
    for poging in (1, 2):
        stats["verzoeken"] += 1
        try:
            return k.query([svc, ident])[2:], True
        except KLineError as e:
            kind = soort(e)
            stats[kind] += 1
            errlog.write(json.dumps({"t": t_rel, "verzoek": f"{svc:02X} {ident:02X}", "poging": poging, "soort": kind,
                                     "tekst": str(e)[:160]}) + "\n")
            errlog.flush()
            if kind == "negatief" or poging == 2:
                stats["mislukt"] += 1
                return None, kind == "negatief"
            stats["herhaald"] += 1
            time.sleep(0.03)  # lijn even stil voor de herhaling


def publish(t_rel, connected, round_s, rounds, reconnects):
    """Zet de laatste waarden klaar voor de webpagina (atomair vervangen, zodat een lezer nooit een half bestand ziet)."""
    if signalen is None:
        return
    try:
        waarden = signalen.decode({key: val for key, (val, _) in last.items() if len(key) == 2})
        waarden.update(signalen.decode_pids({key: val for key, (val, _) in last.items() if key.startswith("pid")}))
        now = time.time()
        doc = {"tijd": round(now, 1), "t": t_rel, "verbonden": connected, "ronde_s": round(round_s, 2),
               "bron": os.path.basename(out),
               "waarden": {key: (round(v, 3) if isinstance(v, float) else v) for key, v in waarden.items()},
               "vensters": signalen.vensters(waarden),
               "leeftijd_s": {key: round(now - t_abs, 1) for key, (_, t_abs) in last.items()},
               "verbinding": dict(stats, rondes=rounds, herverbonden=reconnects)}
        with open(LIVE + ".tmp", "w") as fh:
            json.dump(doc, fh)
        os.replace(LIVE + ".tmp", LIVE)
    except Exception:
        pass


extra = [("21", b) for b in ROTATE] + [("01", p) for p in PIDS]
pids_gefilterd = False


def filter_pids(k):
    """Eenmalig na het aanknopen: vraag welke standaard-PID's de ECU kent (01 00, 01 20, ...) en haal de rest uit het
    plan. Een ECU zwijgt op een PID die hij niet kent, en vier keer zwijgen telt als een verbroken verbinding.
    Antwoordt de ECU niet op 01 00, dan blijft het plan zoals het is."""
    global PIDS, VAST, extra, pids_gefilterd
    if pids_gefilterd or not (PIDS or VAST):
        return
    kent, basis = set(), 0x00
    try:
        while True:
            antwoord = k.query([0x01, basis])
            if len(antwoord) < 6:  # geen volledige bitkaart: liever niets schrappen
                return
            bits = int.from_bytes(antwoord[2:6], "big")
            kent |= {basis + i + 1 for i in range(32) if bits & (1 << (31 - i))}
            if not bits & 1 or basis >= 0xC0:
                break
            basis += 0x20
    except KLineError:
        if not kent:
            return
    pids_gefilterd = True
    weg = [p for p in PIDS + VAST if p not in kent]
    if weg:
        PIDS = [p for p in PIDS if p in kent]
        VAST = [p for p in VAST if p in kent]
        extra = [("21", b) for b in ROTATE] + [("01", p) for p in PIDS]
        meta["leesplan"].update(elke_ronde_pid=[f"{p:02X}" for p in VAST], roulerend_pid=[f"{p:02X}" for p in PIDS],
                                niet_ondersteund_pid=[f"{p:02X}" for p in weg])
        write_meta()
        print("niet ondersteund door deze ECU, uit het plan gehaald: PID " + " ".join(f"{p:02X}" for p in weg), flush=True)

# zijbestand naast de opname voor de webpagina: identiteit van de ECU en het gebruikte leesplan (zie docs/opnameformaat.md)
META = os.path.splitext(out)[0] + ".meta.json"
meta = {"gestart": round(time.time(), 1), "script": os.path.basename(__file__), "ecu": {},
        "leesplan": {"kern_blokken": [f"{b:02X}" for b in CORE], "elke_ronde_pid": [f"{p:02X}" for p in VAST],
                     "roulerend_blokken": [f"{b:02X}" for b in ROTATE], "roulerend_pid": [f"{p:02X}" for p in PIDS]}}


def write_meta():
    try:
        with open(META + ".tmp", "w") as fh:
            json.dump(meta, fh, indent=1)
        os.replace(META + ".tmp", META)
    except Exception:
        pass


def read_identity(k):
    """Eenmalig na het aanknopen: identiteit van de ECU lezen en in het zijbestand zetten. Mislukken is geen probleem."""
    verzoek = getattr(signalen, "IDENTITEIT_VERZOEK", None) if signalen else None
    if meta["ecu"] or not verzoek or k.mode != "kwp":
        return
    try:
        antwoord = k.query(list(verzoek))[2:]
        meta["ecu"] = signalen.identiteit(antwoord)  # bewust niet het ruwe antwoord: daar staat het serienummer in
        write_meta()
    except Exception:
        pass


write_meta()
k = KLine(verbose=False)
rounds = errors = reconnects = 0
start = time.time()
last_print = -99.0
last_round = start
gas_time, prev_t, idle_since = 0.0, 0.0, None
try:
    # .log en niet .jsonl: de webpagina volgt de nieuwste rit*.jsonl en mag het foutenlog niet voor een opname aanzien
    with open(out, "w") as f, open(os.path.splitext(out)[0] + ".fouten.log", "w") as errlog:
        connected = False
        misses = 0
        while time.time() - start < seconds:
            if not connected:
                try:
                    connect(k)
                    connected, misses = True, 0
                    read_identity(k)
                    filter_pids(k)
                except KLineError as e:
                    # "geen echo" = kabel los of geen 12 V; "geen antwoord" = kabel goed, ECU zwijgt (contact uit)
                    print(f"[{time.time() - start:6.1f}] geen verbinding: {e}; nieuwe poging...", flush=True)
                    publish(round(time.time() - start, 3), False, 0.0, rounds, reconnects)
                    time.sleep(6.0)  # lijn echt stil laten voor de volgende reeks wekpulsen
                    continue
            row = {"t": round(time.time() - start, 3), "e": round(time.time(), 1)}  # e = kloktijd (epoch)
            todo = [("21", b) for b in CORE] + [("01", p) for p in VAST] + ([extra[rounds % len(extra)]] if extra else [])
            for svc, ident in todo:
                key = f"{ident:02X}" if svc == "21" else f"pid{ident:02X}"
                payload, answered = ask(k, int(svc, 16), ident, row["t"], errlog)
                if payload is not None:
                    row[key] = payload.hex()
                    last[key] = (payload, time.time())
                    misses = 0
                else:
                    errors += 1
                    misses = 0 if answered else misses + 1
            if misses >= 4:
                connected = False
                reconnects += 1
                time.sleep(1.0)
                continue
            f.write(json.dumps(row) + "\n")
            f.flush()
            rounds += 1
            now = time.time()
            publish(row["t"], True, now - last_round, rounds, reconnects)
            last_round = now
            if WAIT_FOR_REV and "0C" in row:
                dt = row["t"] - prev_t
                prev_t = row["t"]
                if w(row["0C"], 5) > GAS_RPM:
                    gas_time += dt
                    idle_since = None
                elif idle_since is None:
                    idle_since = row["t"]
                if gas_time >= GAS_S and idle_since is not None and row["t"] - idle_since >= NA_S:
                    print(f"[{row['t']:6.1f}] {gas_time:.0f} s boven {GAS_RPM} rpm gezien en weer stationair: klaar", flush=True)
                    break
            if all(key in row for key in ("01", "0C", "0D")) and row["t"] - last_print >= 5:
                last_print = row["t"]
                speed = bytes.fromhex(row["02"])[4] if "02" in row else -1
                print(f"[{row['t']:6.1f}] {speed:3d} km/u  rpm {w(row['0C'], 5):4d}  MAP {w(row['0C'], 19) / 550.8:5.1f} kPa"
                      f"  koelwater {bytes.fromhex(row['0C'])[14] * 0.75 - 48:3.0f} C  EGR-stand {w(row['01'], 20) * 5 / 1023:.2f} V"
                      f"  lambda voor/na {w(row['01'], 10) * 5 / 1023:.2f}/{w(row['01'], 14) * 5 / 1023:.2f} V", flush=True)
            elif not CORE and row["t"] - last_print >= 5:  # voertuig zonder fabrikantblokken: alleen standaard-PID's
                last_print = row["t"]
                rpm = f"{int(row['pid0C'], 16) / 4:.0f}" if "pid0C" in row else "?"
                speed = f"{int(row['pid0D'], 16)}" if "pid0D" in row else "?"
                print(f"[{row['t']:6.1f}] {speed} km/u  rpm {rpm}  ({len(row) - 2} waarden deze ronde)", flush=True)
except KeyboardInterrupt:
    print("gestopt")
finally:
    try:
        k.stop()
    except Exception:
        pass
    k.close()
    publish(round(time.time() - start, 3), False, 0.0, rounds, reconnects)
print(f"{rounds} rondes in {out}; {errors} leesfouten, {reconnects} keer opnieuw verbonden")
print("verbinding: " + ", ".join(f"{key} {val}" for key, val in stats.items()))
