#!/usr/bin/env python3
"""Zelftest van de uitlezer zonder auto en zonder kabel. Draait de echte scripts tegen een nep-ECU (nep_ecu.py).

    python selftest.py

Getest wordt alles boven de draad: het leesplan uit het voertuigbestand, aanknopen in de juiste volgorde, herhalen na een
beschadigd antwoord, het schrappen van PID's die de ECU niet kent, de opname en zijn zijbestanden, de momentopname, het
rapport en de ontleder van meegeluisterde frames. Niet getest: de timing en de bytes op een echte K-line.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
# een script starten met de nep-ECU op de plek van kline.py
BOOT = ("import sys, runpy, nep_ecu; sys.modules['kline'] = nep_ecu; sys.argv = sys.argv[1:]; "
        "runpy.run_path(sys.argv[0], run_name='__main__')")
VEHICLES = os.path.normpath(os.path.join(HERE, "..", "vehicles"))
VOORBEELD = os.path.normpath(os.path.join(HERE, "..", "examples", "rit_voorbeeld.jsonl"))

fouten = []


def check(naam, ok, detail=""):
    print(f"  {'ok ' if ok else 'FOUT'}  {naam}" + (f"   <- {str(detail)[:300]}" if not ok and detail != "" else ""))
    if not ok:
        fouten.append(naam)


def run(args, tmp, nep=None, voertuig=None, timeout=90):
    env = dict(os.environ, KLINE_DATA=tmp, PYTHONDONTWRITEBYTECODE="1")
    env.pop("KLINE_VOERTUIG", None)
    env.pop("NEP_ECU", None)
    if voertuig:
        env["KLINE_VOERTUIG"] = voertuig
    if nep:
        env["NEP_ECU"] = nep
        args = [PY, "-c", BOOT] + args
    else:
        args = [PY] + args
    return subprocess.run(args, cwd=HERE, env=env, capture_output=True, text=True, timeout=timeout)


def regels(pad):
    uit = []
    if os.path.exists(pad):
        for lijn in open(pad):
            try:
                uit.append(json.loads(lijn))
            except ValueError:
                pass
    return uit


def main():
    tmp = tempfile.mkdtemp(prefix="kline_selftest_")
    try:
        print("Ontleder van meegeluisterde frames, opdrachtenkanaal")
        r = run(["luister.py", "--droog"], tmp)
        check("luister.py --droog: vier verzoek/antwoord-paren herkend", r.returncode == 0 and "alle controles geslaagd" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
        r = run(["opdracht.py", "--droog"], tmp)
        check("opdracht.py --droog: vier weigeringslagen", r.returncode == 0 and "alle controles geslaagd" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
        r = run(["stuur.py", "lijst"], tmp)
        check("stuur.py lijst: geen enkele actie is verzendbaar zolang de bytes onbekend zijn",
              r.returncode == 0 and "nee (vermoed)" in r.stdout and "\n".join(l for l in r.stdout.splitlines() if " ja " in l) == "", r.stdout[-300:] + r.stderr[-300:])

        print("Opname van de Matiz tegen de nep-ECU")
        r = run(["egr_capture.py", "rit_proef.jsonl", "2"], tmp, nep="matiz")
        rows = regels(os.path.join(tmp, "rit_proef.jsonl"))
        check("opname loopt en stopt netjes", r.returncode == 0 and len(rows) >= 20, r.stdout[-300:] + r.stderr[-400:])
        kern = {"t", "e", "01", "0C", "0D", "03", "02", "pid06"}
        check("elke regel heeft de kernblokken en de brandstofcorrectie", bool(rows) and all(kern <= set(x) for x in rows),
              next((sorted(x) for x in rows if not kern <= set(x)), ""))
        gezien = set().union(*(set(x) for x in rows)) if rows else set()
        roulerend = {"04", "05", "07", "0B", "0E", "0F", "10", "11", "12", "14", "15", "pid0D", "pid03", "pid07", "pid05", "pid04", "pid0E"}
        check("alle roulerende blokken en PID's komen langs", roulerend <= gezien, sorted(roulerend - gezien))
        check("blokken zijn 32 bytes", bool(rows) and all(len(x["01"]) == 64 for x in rows))
        meta = json.load(open(os.path.join(tmp, "rit_proef.meta.json"))) if os.path.exists(os.path.join(tmp, "rit_proef.meta.json")) else {}
        check("zijbestand: identiteit uit 1A 80", meta.get("ecu", {}).get("onderdeelnummer") == "5WY5407A"
              and meta["ecu"].get("kalibratie") == "8P04B231" and meta["ecu"].get("software") == "KR7714520100", meta)
        check("zijbestand: geen ruw identiteitsantwoord (serienummer)", "ecu_ruw" not in meta and "00000000000" not in json.dumps(meta), meta)
        check("zijbestand: leesplan", meta.get("leesplan", {}).get("kern_blokken") == ["01", "0C", "0D", "03", "02"]
              and "niet_ondersteund_pid" not in meta.get("leesplan", {}), meta.get("leesplan"))
        flog = regels(os.path.join(tmp, "rit_proef.fouten.log"))
        check("beschadigde antwoorden zijn herhaald en niets ging verloren",
              any(f["soort"] == "checksum" and f["poging"] == 1 for f in flog) and "mislukt 0" in r.stdout, r.stdout[-200:])
        live = json.load(open(os.path.join(tmp, "live.json"))) if os.path.exists(os.path.join(tmp, "live.json")) else {}
        check("live.json: omgerekende waarden", live.get("waarden", {}).get("toerental") == 2614
              and 84 < live["waarden"].get("koelwater", 0) < 86 and 1.29 < live["waarden"].get("egr_stand_v", 0) < 1.32, live.get("waarden"))

        print("Voertuigbestand")
        r = run(["-c", "import signalen, json; print(json.dumps({'naam': signalen.NAAM, 'init': list(signalen.AANKNOPEN), "
                       "'kern': signalen.KERN_BLOKKEN, 'n': len(signalen.SIGNALEN), "
                       "'v': signalen.vensters({'toerental': 2700, 'map': 80, 'koelwater': 85})}))"], tmp)
        info = json.loads(r.stdout) if r.returncode == 0 else {}
        check("Matiz: naam, init-methode, leesplan en signalen", info.get("init") == ["fast"] and info.get("kern") == [1, 12, 13, 3, 2]
              and info.get("n", 0) >= 27 and info.get("v", {}).get("egr_test") is True, r.stdout + r.stderr[-300:])
        r = run(["egr_capture.py", "x.jsonl", "1"], tmp, nep="matiz", voertuig="bestaat_niet")
        check("een onbekend voertuig is een fout, geen stille terugval", r.returncode != 0 and "voertuigbestand" in (r.stderr + r.stdout)
              or not os.path.isdir(VEHICLES), r.stdout[-200:] + r.stderr[-200:])

        if os.path.exists(os.path.join(VEHICLES, "generic_obd2.py")):
            print("Een andere auto: alleen standaard OBD2 (voertuigbestand generic_obd2)")
            r = run(["egr_capture.py", "rit_obd2.jsonl", "2"], tmp, nep="obd2", voertuig="generic_obd2")
            rows = regels(os.path.join(tmp, "rit_obd2.jsonl"))
            check("opname loopt", r.returncode == 0 and len(rows) >= 20, r.stdout[-300:] + r.stderr[-400:])
            check("geen fabrikantblokken gevraagd, wel toerental en snelheid in elke regel",
                  bool(rows) and all({"pid0C", "pid0D"} <= set(x) and not any(len(k) == 2 for k in x if k not in ("t", "e")) for x in rows),
                  sorted(rows[0]) if rows else "")
            meta = json.load(open(os.path.join(tmp, "rit_obd2.meta.json")))
            weg = meta.get("leesplan", {}).get("niet_ondersteund_pid", [])
            check("PID's die deze ECU niet kent zijn uit het plan gehaald", {"0B", "07", "0E", "15"} <= set(weg)
                  and "0C" not in weg and "niet ondersteund" in r.stdout, (weg, r.stdout[-200:]))
            check("daarna geen verloren verzoeken en geen herverbinding", "mislukt 0" in r.stdout and "0 keer opnieuw verbonden" in r.stdout, r.stdout[-200:])
            r = run(["egr_capture.py", "rit_9141.jsonl", "6"], tmp, nep="obd2_9141", voertuig="generic_obd2")
            rows = regels(os.path.join(tmp, "rit_9141.jsonl"))
            check("auto met alleen 5-baud init: fast init mislukt, slow init lukt, opname loopt",
                  r.returncode == 0 and len(rows) >= 20 and all("pid0C" in x for x in rows), r.stdout[-300:] + r.stderr[-400:])
            r = run(["selftest_snapshot.py", "obd2"], tmp, nep="obd2", voertuig="generic_obd2")
            snap = json.load(open(os.path.join(tmp, "zelftest_obd2.json"))) if os.path.exists(os.path.join(tmp, "zelftest_obd2.json")) else {}
            check("momentopname zonder fabrikantblokken", r.returncode == 0 and snap.get("blocks") == {} and "monitors" in snap, r.stdout[-200:] + r.stderr[-300:])
            r = run(["onrust_snel.py", "x", "1"], tmp, nep="obd2", voertuig="generic_obd2")
            check("snelle onrustmeting weigert bij een auto zonder zulke blokken", r.returncode != 0 and "SNEL_BLOKKEN" in r.stderr, r.stderr[-200:])
        else:
            print("Een andere auto: overgeslagen (geen ../vehicles/generic_obd2.py; die staat alleen in de repo-indeling)")

        print("Momentopname en vergelijking (Matiz)")
        ra = run(["selftest_snapshot.py", "a"], tmp, nep="matiz")
        rb = run(["selftest_snapshot.py", "b"], tmp, nep="matiz")
        snap = json.load(open(os.path.join(tmp, "zelftest_a.json"))) if os.path.exists(os.path.join(tmp, "zelftest_a.json")) else {}
        check("momentopname: monitors, en elk blok 2 of 3 keer gelezen (een beschadigd antwoord kost een lezing)",
              ra.returncode == 0 and rb.returncode == 0 and len(snap.get("blocks", {})) == 23
              and all(2 <= len(v) <= 3 for v in snap["blocks"].values()) and snap.get("monitors", {}).get("EGR") == "NIET voltooid",
              ra.stdout[-200:] + ra.stderr[-300:])
        r = run(["selftest_diff.py", "a", "b"], tmp)
        check("vergelijking van twee momentopnames", r.returncode == 0 and "VOOR:" in r.stdout, r.stdout[-200:] + r.stderr[-300:])

        print("Snelle onrustmeting en analyse (Matiz)")
        r = run(["onrust_snel.py", "proef", "2"], tmp, nep="matiz")
        rows = regels(os.path.join(tmp, "onrust_proef.jsonl"))
        check("alleen blok 03, 0C en 02, vaker dan de gewone opname", r.returncode == 0 and len(rows) >= 40
              and all(set(x) <= {"t", "e", "03", "0C", "02"} for x in rows), r.stdout[-200:] + r.stderr[-300:])
        r = run(["analyse_onrust.py", os.path.join(tmp, "onrust_proef.jsonl")], tmp)
        check("analyse per cilinderkanaal", r.returncode == 0 and "cil-waarde 2" in r.stdout, r.stdout[-200:] + r.stderr[-300:])

        if os.path.exists(VOORBEELD):
            print("Rapport over de meegeleverde voorbeeldrit")
            r = run(["rit_report.py", VOORBEELD], tmp)
            check("rit_report.py leest de voorbeeldrit", r.returncode == 0 and "EGR-klep" in r.stdout and "krukas-onrust" in r.stdout
                  and "markeringen uit de webpagina" in r.stdout, r.stdout[-300:] + r.stderr[-400:])
        else:
            print("Rapport over de voorbeeldrit: overgeslagen (geen ../examples/rit_voorbeeld.jsonl)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fouten:
        print(f"{len(fouten)} FOUT: " + "; ".join(fouten))
        sys.exit(1)
    print("ALLES OK")


if __name__ == "__main__":
    main()
