#!/usr/bin/env python3
"""Start en stop een gelogde rit. Alleen lezen: er wordt niets aangestuurd of gewist.

  rit.py start <label> [--voertuig <naam>]   contact aan (motor mag nog uit): zelfteststatus vastleggen en de opname starten
  rit.py stop <label>                        opname stoppen, status opnieuw lezen, vergelijken met ervoor en het rapport maken

De opname draait los van de terminal, houdt de computer wakker en verbindt opnieuw na starten of contact uit/aan.
Er kan maar een programma tegelijk op de kabel: zet de webpagina niet tegelijk op "Kabel".
--voertuig kiest het voertuigbestand (zie vehicles/); zonder die optie geldt KLINE_VOERTUIG of de standaard.
"""
import os
import shutil
import subprocess
import sys
import time

import kline
from paden import HERE, datamap

os.chdir(datamap())
PY = sys.executable


def script(naam):
    return os.path.join(HERE, naam)


def capture_running(label=None):
    pattern = f"egr_capture.py rit_{label}.jsonl" if label else "egr_capture.py"
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def start(label):
    if not kline.poorten():
        sys.exit("Kabel niet gevonden. Sluit de OBD-kabel aan en probeer opnieuw (of zet KLINE_PORT op de juiste poort).")
    if capture_running():
        sys.exit("Er loopt al een opname. Stop die eerst met: rit.py stop <label>")
    if os.path.exists(f"rit_{label}.jsonl"):
        sys.exit(f"rit_{label}.jsonl bestaat al; kies een ander label.")
    print("1/2 zelfteststatus vastleggen (ongeveer een halve minuut)...", flush=True)
    if subprocess.run([PY, script("selftest_snapshot.py"), f"{label}_voor"]).returncode != 0:
        sys.exit("Status lezen mislukt. Staat het contact aan? Daarna opnieuw: rit.py start " + label)
    print("\n2/2 opname starten...", flush=True)
    log = open(f"rit_{label}.log", "w")
    wakker = ["caffeinate", "-i"] if shutil.which("caffeinate") else []  # macOS: niet in slaap vallen tijdens de rit
    subprocess.Popen(wakker + [PY, script("egr_capture.py"), f"rit_{label}.jsonl", "7200"],
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        time.sleep(1)
        if os.path.exists(f"rit_{label}.jsonl") and os.path.getsize(f"rit_{label}.jsonl") > 0:
            print(open(f"rit_{label}.log").read().strip().splitlines()[-1])
            print(f"\nDe opname loopt (rit_{label}.jsonl, maximaal 2 uur). Je kunt starten en rijden; klep van de laptop open laten.")
            return
    print(open(f"rit_{label}.log").read().strip())
    print("\nDe opname is gestart maar krijgt nog geen antwoord van de ECU; hij blijft het proberen. Controleer het contact.")


def stop(label):
    if capture_running(label):
        subprocess.run(["pkill", "-INT", "-f", f"egr_capture.py rit_{label}.jsonl"])
        for _ in range(60):
            if not capture_running(label):
                break
            time.sleep(0.25)
        else:
            subprocess.run(["pkill", "-KILL", "-f", f"egr_capture.py rit_{label}.jsonl"])
            time.sleep(0.5)
        print("opname gestopt:", open(f"rit_{label}.log").read().strip().splitlines()[-1], "\n")
    else:
        print("er liep geen opname meer voor dit label\n")
    if kline.poorten():
        if (subprocess.run([PY, script("selftest_snapshot.py"), f"{label}_na"]).returncode == 0
                and os.path.exists(f"zelftest_{label}_voor.json")):
            print()
            subprocess.run([PY, script("selftest_diff.py"), f"{label}_voor", f"{label}_na"])
        else:
            print("Status na de rit niet gelezen (contact aan?). Later alsnog: selftest_snapshot.py " + label + "_na")
    else:
        print("Kabel niet aangesloten: status na de rit overgeslagen.")
    print()
    if os.path.exists(f"rit_{label}.jsonl"):
        subprocess.run([PY, script("rit_report.py"), f"rit_{label}.jsonl"])


args = sys.argv[1:]
if "--voertuig" in args:
    i = args.index("--voertuig")
    if i + 1 >= len(args):
        sys.exit(__doc__)
    os.environ["KLINE_VOERTUIG"] = args[i + 1]  # de gestarte scripts erven dit
    del args[i:i + 2]
if len(args) != 2 or args[0] not in ("start", "stop"):
    sys.exit(__doc__)
{"start": start, "stop": stop}[args[0]](args[1])
