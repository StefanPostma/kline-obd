#!/usr/bin/env python3
"""Legt de fabrikant-specifieke datablokken van de ECU vast (KWP service 21) en vergelijkt toestanden. Alleen lezen.

  python blocks.py capture --label koeo      # motor uit, contact aan
  python blocks.py capture --label stationair
  python blocks.py capture --label 2500rpm
  python blocks.py diff koeo stationair      # welke bytes veranderen tussen twee toestanden?

Een capture leest de blokken enkele keren en tegelijk de standaard OBD-waarden, zodat bytes die meebewegen met
bekende waarden (toerental, MAP, temperatuur...) herkend kunnen worden. Eerst `probe_ecu.py` draaien voor de ID-lijst.
Stop eerst server.py: de kabel kan maar door één programma tegelijk worden gebruikt.
"""
import argparse
import collections
import glob
import json
import os
import statistics as st
import sys
import time
from datetime import datetime

import obd
from kwp import BadFrame, KLine, KLineError, Negative, NoResponse, open_port

PIDS = (0x0C, 0x05, 0x0B, 0x0F, 0x11, 0x06, 0x07, 0x0D, 0x0E, 0x14)


def latest(pattern):
    files = sorted(glob.glob(pattern), key=os.path.getmtime)
    return files[-1] if files else None


def mode(xs):
    return collections.Counter(xs).most_common(1)[0][0]


def capture(args):
    if args.ids:
        ids = [int(x, 16) for x in args.ids.split(",")]
    else:
        probe = latest("logs/probe21-*.json")
        if not probe:
            sys.exit("Eerst probe_ecu.py draaien (die maakt de lijst met ID's), of geef --ids 03,0C,...")
        p = json.load(open(probe))
        ids = sorted(int(k, 16) for k in list(p["positive"]) + p["silent"])
    ser = open_port(args.serial or sorted(glob.glob("/dev/cu.usbserial-*"))[0])
    kl = KLine(ser)
    kl.connect()
    print(f"verbonden ({kl.init_method}); {len(ids)} blokken x {args.cycles} rondes, label '{args.label}'")
    data = {f"{i:02X}": [] for i in ids}
    pids = collections.defaultdict(list)
    rounds = []                                   # per ronde: tijd + alle blokken, om blokken onderling te kunnen koppelen
    for c in range(args.cycles):
        snap = {"t": time.time(), "blocks": {}}
        for i in ids:
            try:
                r = kl.request(bytes([0x21, i]), timeout=0.4)
                if r[:2] == bytes([0x61, i]):
                    data[f"{i:02X}"].append(list(r[2:]))
                    snap["blocks"][f"{i:02X}"] = list(r[2:])
            except (Negative, NoResponse, BadFrame):
                pass
        rounds.append(snap)
        if not args.no_pids:
            for pid in PIDS:
                try:
                    for k, v in obd.read_pid(kl, pid).items():
                        pids[k].append(v)
                except KLineError:
                    pass
        if (c + 1) % 10 == 0 or c + 1 == args.cycles:
            print(f"  ronde {c + 1}/{args.cycles} klaar")
    ser.close()
    out = f"logs/blocks-{args.label}-{datetime.now():%Y%m%d-%H%M%S}.json"
    json.dump({"label": args.label, "blocks": data, "pids": pids, "rounds": rounds}, open(out, "w"))
    print("opgeslagen:", out)
    ctx = ", ".join(f"{k} {st.fmean(v):.0f}" for k, v in pids.items() if k in ("rpm", "map", "ect", "iat", "tps", "stft", "ltft"))
    print("standaardwaarden tijdens capture:", ctx)
    for i, rounds in data.items():
        if len(rounds) >= 2:
            vol = [n for n in range(len(rounds[0])) if len({r[n] for r in rounds if len(r) > n}) > 1]
            if vol:
                print(f"  blok {i}: veranderende bytes binnen deze capture: {vol}")


def load(label):
    f = latest(f"logs/blocks-{label}-*.json")
    if not f:
        sys.exit(f"geen capture met label '{label}' gevonden")
    return json.load(open(f))


def diff(args):
    a, b = load(args.a), load(args.b)
    print(f"Vergelijking '{args.a}' -> '{args.b}'  (alleen bytes die in beide captures stabiel zijn of consistent verschillen)")
    for k in ("rpm", "map", "ect", "iat", "tps", "stft", "ltft"):
        if a["pids"].get(k) and b["pids"].get(k):
            print(f"  {k}: {st.fmean(a['pids'][k]):.1f} -> {st.fmean(b['pids'][k]):.1f}")
    print()
    found = 0
    for i in sorted(a["blocks"]):
        ra, rb = a["blocks"][i], b["blocks"].get(i, [])
        if not ra or not rb:
            continue
        for n in range(min(len(ra[0]), len(rb[0]))):
            va, vb = [r[n] for r in ra if len(r) > n], [r[n] for r in rb if len(r) > n]
            if mode(va) != mode(vb):
                dyn = " (beweegt binnen een capture)" if len(set(va)) > 1 or len(set(vb)) > 1 else ""
                print(f"  blok {i} byte {n:2d}: {mode(va):3d} (0x{mode(va):02X}) -> {mode(vb):3d} (0x{mode(vb):02X}){dyn}")
                found += 1
    print(f"\n{found} byte(s) verschillen")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--label", required=True)
    c.add_argument("--cycles", type=int, default=3)
    c.add_argument("--ids", help="alleen deze blokken, hex gescheiden door komma's (sneller, meer rondes)")
    c.add_argument("--no-pids", action="store_true", help="geen standaard OBD-waarden meelezen")
    c.add_argument("--serial")
    d = sub.add_parser("diff")
    d.add_argument("a")
    d.add_argument("b")
    args = ap.parse_args()
    {"capture": capture, "diff": diff}[args.cmd](args)


if __name__ == "__main__":
    main()
