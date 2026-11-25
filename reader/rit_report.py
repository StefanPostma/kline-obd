"""Rapport van een rit-opname van egr_capture.py: temperatuurverloop, EGR-werking, cilinderwaarden, stationair.

Gebruik: rit_report.py <opname.jsonl>

Betekenis van de signalen is afgeleid uit gedrag (zie docs/matiz-m200-08.md): blok 01 byte 20-21 = EGR-klepstand,
blok 02 byte 24 = EGR-stuursignaal, 0C[3] = closed-loop-vlag, 0C[14] = koelwater (0,75*N-48), 03[28..30] = per cilinder.
"""
import datetime
import json
import os
import statistics as st
import sys

path = sys.argv[1]
rows = []
for line in open(path):
    try:
        rows.append(json.loads(line))
    except ValueError:
        pass  # laatste regel kan half geschreven zijn
if not rows:
    sys.exit("geen metingen in de opname")
# kloktijd: uit de opname zelf (veld "e"), anders --start UU:MM:SS, anders de aanmaaktijd van het bestand
if "e" in rows[0]:
    t_file = datetime.datetime.fromtimestamp(rows[0]["e"] - rows[0]["t"])
elif "--start" in sys.argv:
    h, m, s = (int(v) for v in sys.argv[sys.argv.index("--start") + 1].split(":"))
    t_file = datetime.datetime.fromtimestamp(os.stat(path).st_mtime).replace(hour=h, minute=m, second=s, microsecond=0)
else:
    _st = os.stat(path)
    t_file = datetime.datetime.fromtimestamp(getattr(_st, "st_birthtime", _st.st_mtime))  # Linux kent geen aanmaaktijd
clock = lambda t: (t_file + datetime.timedelta(seconds=t)).strftime("%H:%M:%S")
channels = lambda r: sum(1 for key in r if key not in ("t", "e"))


def rec(r):
    b1 = bytes.fromhex(r["01"]) if "01" in r else None
    c = bytes.fromhex(r["0C"]) if "0C" in r else None
    d = bytes.fromhex(r["0D"]) if "0D" in r else None
    b3 = bytes.fromhex(r["03"]) if "03" in r else None
    w = lambda o: int.from_bytes(b1[o:o + 2], "big")
    b2 = bytes.fromhex(r["02"]) if "02" in r else None
    # snelheid: standaard-PID als die er is, anders blok 02 byte 4 (gelijk binnen 2 km/u)
    x = {"t": r["t"], "sp": int(r["pid0D"][:2], 16) if "pid0D" in r else b2[4] if b2 else None}
    x["tps"], x["egr"] = (w(4), w(20)) if b1 else (None, None)
    x["o2f"], x["o2r"], x["knock"] = (w(10) * 5 / 1023, w(14) * 5 / 1023, w(6) * 5 / 1023) if b1 else (None, None, None)
    x["iat"] = b2[1] * 0.75 - 48 if b2 else None
    x["e"] = r.get("e")
    x["rpm"] = int.from_bytes(c[5:7], "big") if c else None
    x["map"] = int.from_bytes(c[19:21], "big") / 550.8 if c else None
    x["ect"] = c[14] * 0.75 - 48 if c else None
    x["cl"] = c[3] if c else None
    x["cyl"] = (b3[28], b3[29], b3[30]) if b3 else None
    x["exc"] = (b3[24], b3[25], b3[26]) if b3 else None  # niet 0 zodra de onrust van die cilinder boven de drempel komt
    x["cmd"] = bytes.fromhex(r["02"])[24] if "02" in r else None
    x["stft"] = (int(r["pid06"][:2], 16) - 128) * 100 / 128 if "pid06" in r else None
    x["ltft"] = (int(r["pid07"][:2], 16) - 128) * 100 / 128 if "pid07" in r else None
    return x


R = [rec(r) for r in rows]
med = lambda v: st.median(v) if v else float("nan")
dt = (R[-1]["t"] - R[0]["t"]) / max(1, len(R) - 1)

# ---- 1. overzicht ---------------------------------------------------------------------------------------------
print(f"=== {os.path.basename(path)}: {len(R)} rondes, {R[-1]['t'] / 60:.1f} min, start {clock(0)}, {dt:.2f} s per ronde ===")
moving = [x["t"] for x in R if x["sp"]]
if moving:
    t0, t1 = moving[0], moving[-1]
    drive = [x for x in R if t0 - 5 <= x["t"] <= t1 + 5]
    km = sum((b["t"] - a["t"]) * (b["sp"] or 0) / 3600 for a, b in zip(drive, drive[1:]) if b["t"] - a["t"] < 10)
    sp = [x["sp"] for x in drive if x["sp"] is not None]
    print(f"rit: {clock(t0)} tot {clock(t1)} ({(t1 - t0) / 60:.1f} min), ongeveer {km:.1f} km, topsnelheid {max(sp)} km/u")
else:
    t0 = t1 = None
    print("geen beweging in deze opname")
want = 7 * len(rows)
print(f"gemiste verzoeken: {want - sum(channels(r) for r in rows)} van {want}")

# ---- 2. temperatuur en closed loop ----------------------------------------------------------------------------
ect = [(x["t"], x["ect"]) for x in R if x["ect"] is not None]
print("\n--- temperatuur en closed loop ---")
if ect:
    print(f"koelwater: begin {ect[0][1]:.0f} C, einde {ect[-1][1]:.0f} C, hoogste {max(e for _, e in ect):.0f} C")
    if t0 is not None:
        print(f"koelwater bij vertrek: {min(ect, key=lambda p: abs(p[0] - t0))[1]:.0f} C")
    for target in (40, 60, 80):
        hit = next((t for t, e in ect if e >= target), None)
        if hit is not None and ect[0][1] < target:
            print(f"   {target} C bereikt na {hit / 60:.1f} min ({clock(hit)})")
cl = [(x["t"], x["cl"], x["ect"], x["rpm"]) for x in R if x["cl"] is not None]
running = [p for p in cl if p[3] and p[3] > 400]
if running:
    first_cl = next((p for p in running if p[1] == 1), None)
    if first_cl is None:
        print("closed loop: tijdens deze opname nooit actief")
    elif first_cl is running[0]:
        print(f"closed loop: al actief bij de eerste meting met draaiende motor ({first_cl[2]:.0f} C)")
    else:
        print(f"closed loop: actief {first_cl[0] - running[0][0]:.0f} s na de eerste meting met draaiende motor, bij {first_cl[2]:.0f} C ({clock(first_cl[0])})")

# ---- 3. EGR ---------------------------------------------------------------------------------------------------
E = [x for x in R if x["egr"] is not None]
print("\n--- EGR-klep ---")
if E:
    volt = lambda n: n * 5 / 1023
    rest0 = med([x["egr"] for x in E[:40] if x["tps"] <= 110]) if any(x["tps"] <= 110 for x in E[:40]) else med([x["egr"] for x in E[:40]])
    thr = max(150, rest0 + 20)
    print(f"ruststand bij het begin: {rest0:.0f} ({volt(rest0):.2f} V); 'open' geteld vanaf {thr:.0f}")
    first = next((x for x in E if x["egr"] >= thr), None)
    if first is None:
        print("de klep is in deze opname niet opengegaan")
    else:
        since = f", {(first['t'] - t0) / 60:.1f} min na vertrek" if t0 is not None else ""
        ect_txt = f"{first['ect']:.0f} C" if first["ect"] is not None else "?"
        print(f"eerste opening: {clock(first['t'])}{since}, koelwater {ect_txt}, {first['sp']} km/u, {first['rpm']} rpm")
        runs, cur = [], None
        for x in E:
            if x["egr"] >= thr:
                cur = [x["t"], x["t"]] if cur is None else [cur[0], x["t"]]
            elif cur:
                runs.append(cur)
                cur = None
        if cur:
            runs.append(cur)
        ref = [x for x in E if t0 is None or t0 - 5 <= x["t"] <= t1 + 5]
        share = 100 * sum(1 for x in ref if x["egr"] >= thr) / max(1, len(ref))
        top = max(x["egr"] for x in E)
        print(f"{len(runs)} keer open, langste periode {max(b - a for a, b in runs):.0f} s, {share:.0f} % van de rit, hoogste stand {top} ({volt(top):.2f} V)")
        G = [x for x in E if x["rpm"] is not None]
        mb = [(20, 35), (35, 45), (45, 55), (55, 65), (65, 80), (80, 105)]
        print("mediaan klepstand per toerental en inlaatdruk (aantal metingen):")
        print("  rpm        " + "".join(f"{a:>3d}-{b:<3d}kPa  " for a, b in mb))
        for lo, hi in ((700, 1200), (1200, 1800), (1800, 2400), (2400, 3000), (3000, 3600), (3600, 7000)):
            cells = []
            for a, b in mb:
                v = [x["egr"] for x in G if lo <= x["rpm"] < hi and a <= x["map"] < b]
                cells.append(f"{int(med(v)):4d} ({len(v):3d})  " if v else "   -        ")
            print(f"  {lo:4d}-{hi:<4d}  " + "".join(cells))
        C = [i for i, x in enumerate(E) if x["cmd"] is not None]
        on = [i for i in C if E[i]["cmd"] > 0]
        same = sum(1 for i in on if E[i]["egr"] >= thr)
        nxt = sum(1 for i in on if E[i]["egr"] < thr and i + 1 < len(E) and E[i + 1]["egr"] >= thr)
        off_open = sum(1 for i in C if E[i]["cmd"] == 0 and E[i]["egr"] >= thr)
        print(f"stuursignaal > 0 in {len(on)} metingen: klep open in {same}, een ronde later open in {nxt}, niet gevolgd in {len(on) - same - nxt}")
        print(f"stuursignaal 0 in {len(C) - len(on)} metingen: klep toch open in {off_open}")
        lift = [x["egr"] for x in E if x["tps"] <= 110 and x["sp"] and x["sp"] > 20]
        if lift:
            print(f"gas los en rijdend ({len(lift)} metingen): hoogste klepstand {max(lift)}")
    closed_end = [x["egr"] for x in E[-60:] if x["tps"] <= 110]
    if closed_end:
        print(f"ruststand aan het eind: {med(closed_end):.0f} ({volt(med(closed_end)):.2f} V), begin was {rest0:.0f}")

# ---- 4. waarden per cilinder ----------------------------------------------------------------------------------
Cy = [x for x in R if x["cyl"] and x["tps"] is not None and x["rpm"] is not None]
print("\n--- krukas-onrust per cilinder (blok 03 byte 28-30) en aandeel boven de drempel (byte 24-26 niet 0) ---")


def cyl_line(name, sel):
    if len(sel) < 3:
        return
    cols = list(zip(*[x["cyl"] for x in sel]))
    hi = [max(range(3), key=lambda i: x["cyl"][i]) for x in sel]
    over = [100 * sum(1 for x in sel if x["exc"][i]) / len(sel) for i in range(3)]
    print(f"  {name:38s} n={len(sel):3d}  gemiddeld {st.fmean(cols[0]):5.1f} {st.fmean(cols[1]):5.1f} {st.fmean(cols[2]):5.1f}"
          f"   hoogste {max(cols[0]):3d} {max(cols[1]):3d} {max(cols[2]):3d}   boven drempel {over[0]:3.0f} {over[1]:3.0f} {over[2]:3.0f} %")


cyl_line("stilstaand stationair", [x for x in Cy if x["sp"] == 0 and x["tps"] <= 110 and x["rpm"] > 400])
cyl_line("gas los, rijdend", [x for x in Cy if x["tps"] <= 110 and x["sp"] and x["sp"] > 20])
cyl_line("optrekken onderin (1000-2200 rpm)", [x for x in Cy if 1000 <= x["rpm"] < 2200 and x["tps"] > 150 and x["sp"]])
cyl_line("optrekken midden (2200-3200 rpm)", [x for x in Cy if 2200 <= x["rpm"] < 3200 and x["tps"] > 150])
for lo, hi in ((-50, 50), (50, 75), (75, 200)):
    cyl_line(f"optrekken onderin, koelwater {max(lo, 0)}-{hi} C",
             [x for x in Cy if 1000 <= x["rpm"] < 2200 and x["tps"] > 150 and x["sp"] and x["ect"] is not None and lo <= x["ect"] < hi])
print("  hoogste uitschieters:")
for x in sorted(Cy, key=lambda x: -max(x["cyl"]))[:10]:
    ect_txt = f"{x['ect']:.0f}" if x["ect"] is not None else "?"
    print(f"    {clock(x['t'])}  {x['sp'] if x['sp'] is not None else '?':>3} km/u  {x['rpm']:5d} rpm  gasklep {x['tps']:4d}  {ect_txt:>3} C   {x['cyl'][0]:4d} {x['cyl'][1]:4d} {x['cyl'][2]:4d}")

# ---- 5. stationair --------------------------------------------------------------------------------------------
print("\n--- stilstaand stationair (gas dicht, motor draait) ---")
I = [x for x in R if x["sp"] == 0 and x["tps"] is not None and x["tps"] <= 110 and x["rpm"] and 600 < x["rpm"] < 1400]
for lo, hi in ((-50, 50), (50, 75), (75, 200)):
    sel = [x for x in I if x["ect"] is not None and lo <= x["ect"] < hi]
    if len(sel) >= 5:
        s, l = [x["stft"] for x in sel if x["stft"] is not None], [x["ltft"] for x in sel if x["ltft"] is not None]
        print(f"  koelwater {max(lo, 0):3d}-{hi:<3d} C  n={len(sel):3d}  toerental {med([x['rpm'] for x in sel]):.0f}  MAP {med([x['map'] for x in sel]):.1f} kPa"
              f"  STFT {med(s):+.1f} %  LTFT {med(l):+.1f} %")

# ---- 6. lambdasonde voor en na de katalysator -----------------------------------------------------------------
# Handboek: ~0,1 V = arm, ~0,9 V = rijk. Haalt de ECU brandstof weg (STFT/LTFT negatief) terwijl de sonde na de kat arm
# blijft, dan is het mengsel niet echt te rijk: ongelijke cilinders, afwijkende voorste sonde of valse lucht in de uitlaat.
print("\n--- lambdasonde voor en na de katalysator (0,1 V = arm, 0,9 V = rijk) ---")
last_iat, since = None, 0
for x in R:
    last_iat = x["iat"] if x["iat"] is not None else last_iat
    x["iat"] = last_iat
    if x["cl"] is not None:
        since = since + 1 if x["cl"] == 1 else 0
    x["since"] = since
L = [x for x in R if x["o2f"] is not None and x["rpm"] and x["rpm"] > 400 and x["ect"] is not None and x["cl"] is not None]


def o2_line(name, sel):
    if len(sel) < 8:
        return
    f, r = [x["o2f"] for x in sel], [x["o2r"] for x in sel]
    s = [x["stft"] for x in sel if x["stft"] is not None]
    rich = lambda v: 100 * sum(1 for a in v if a > 0.45) / len(v)
    print(f"  {name:44s} n={len(sel):4d}  voor kat {st.fmean(f):.2f} V, {rich(f):3.0f} % rijk   na kat {med(r):.2f} V, {rich(r):3.0f} % rijk"
          + (f"   STFT {med(s):+.0f} %" if s else ""))


o2_line("open loop koud (< 60 C), gas dicht", [x for x in L if x["cl"] == 0 and x["ect"] < 60 and x["tps"] <= 110])
o2_line("open loop koud (< 60 C), met gas", [x for x in L if x["cl"] == 0 and x["ect"] < 60 and x["tps"] > 110])
o2_line("closed loop stationair, < 75 C", [x for x in L if x["since"] >= 20 and x["ect"] < 75 and x["sp"] == 0 and x["tps"] <= 110])
o2_line("closed loop stationair, warm", [x for x in L if x["since"] >= 20 and x["ect"] >= 75 and x["sp"] == 0 and x["tps"] <= 110])
o2_line("closed loop rijden, MAP 40-70 kPa", [x for x in L if x["since"] >= 20 and x["sp"] and x["sp"] > 20 and x["tps"] > 120 and 40 <= x["map"] < 70])
o2_line("closed loop rijden, MAP >= 70 kPa", [x for x in L if x["since"] >= 20 and x["sp"] and x["sp"] > 20 and x["map"] >= 70])

# ---- 7. testvensters van de ECU volgens het werkplaatshandboek (M-150, sectie 1F) ------------------------------
print("\n--- zelftest-vensters volgens het handboek: hoe lang zat de rit erin ---")
V = [x for x in R if x["rpm"] is not None and x["ect"] is not None]


def window(name, f):
    hits = [bool(f(x)) for x in V]
    longest = cur = 0
    for h in hits:
        cur = cur + 1 if h else 0
        longest = max(longest, cur)
    print(f"  {name:66s} {sum(hits) * dt:5.0f} s totaal, langste stuk {longest * dt:4.0f} s")


window("EGR-test: 2500-2900 rpm, MAP > 75 kPa, koelwater > 80, inlaatlucht > 15", lambda x: 2500 <= x["rpm"] <= 2900 and x["map"] > 75 and x["ect"] > 80 and (x["iat"] is None or x["iat"] > 15))
window("katalysatortest: 64-80 km/u, 2400-3000 rpm, closed loop, koelwater > 70", lambda x: x["sp"] is not None and 64 <= x["sp"] <= 80 and 2400 <= x["rpm"] <= 3000 and x["cl"] == 1 and x["ect"] > 70)
window("mengseldiagnose 'te rijk': closed loop, koelwater 80-115, MAP > 70 kPa", lambda x: x["cl"] == 1 and 80 <= x["ect"] <= 115 and x["map"] > 70)

# ---- 8. sensorspanningen tegen de grenzen uit het handboek -----------------------------------------------------
print("\n--- sensorspanningen tegen de grenzen uit het handboek ---")
K = [x["knock"] for x in V if x["knock"] is not None and x["ect"] > 80 and x["rpm"] > 2500]
if K:
    print(f"  klopsensor bij warm en > 2500 rpm (fout onder 0,20 V): laagste {min(K):.2f} V, mediaan {med(K):.2f} V")
if E:
    print(f"  EGR-potmeter laagste waarde (grenzen 0,40 en 1,75 V in rust; kortsluiting onder 0,01 V): {min(x['egr'] for x in E) * 5 / 1023:.2f} V")
C = [x for x in L if x["cl"] == 1]
if C:
    print(f"  lambda voor kat in closed loop: {100 * sum(1 for x in C if x['o2f'] < 0.07) / len(C):.0f} % onder 0,07 V, "
          f"{100 * sum(1 for x in C if 0.352 <= x['o2f'] <= 0.499) / len(C):.0f} % in de dode band 0,352-0,499 V")
    print(f"  lambda na kat in closed loop: {100 * sum(1 for x in C if x['o2r'] < 0.07) / len(C):.0f} % onder 0,07 V (fout bij 40 s aaneen), "
          f"{100 * sum(1 for x in C if 0.352 <= x['o2r'] <= 0.499) / len(C):.0f} % in de dode band")

# ---- 9. markeringen uit de webpagina (knop "Annotatie: probleem") ------------------------------------------------------
# De webpagina schrijft elke druk op de knop als JSON-regel {"e", "t", "opname", "tekst"} in zijn eigen logs-map
# (zie docs/opnameformaat.md). Hier wordt elke markering naast de meetdata van de 8 s ervoor en 3 s erna gelegd.
stem = os.path.splitext(os.path.basename(path))[0]
# zonder --markeringen: naast de opname, of in de logs-map van de webpagina
_hier = os.path.dirname(os.path.abspath(__file__))
_kandidaten = [os.path.join(os.path.dirname(os.path.abspath(path)), f"{stem}.markeringen.log")] + [
    os.path.join(_hier, "..", map_, "logs", f"{stem}.markeringen.log") for map_ in ("web",)]
marks_path = (sys.argv[sys.argv.index("--markeringen") + 1] if "--markeringen" in sys.argv
              else next((p for p in _kandidaten if os.path.exists(p)), _kandidaten[0]))
if os.path.exists(marks_path):
    marks = []
    for line in open(marks_path):
        try:
            marks.append(json.loads(line))
        except ValueError:
            pass
    print(f"\n--- markeringen uit de webpagina: {len(marks)} ---")
    for m in marks:
        tm = m.get("t")
        if tm is None and m.get("e") is not None and "e" in rows[0]:
            tm = m["e"] - (rows[0]["e"] - rows[0]["t"])
        if tm is None:
            continue
        win = [x for x in R if tm - 8 <= x["t"] <= tm + 3 and x["rpm"] is not None and x["tps"] is not None]
        if not win:
            print(f"  {clock(tm)}  {m.get('tekst', '')}: geen metingen rond dit moment")
            continue
        rng = lambda key, fmt="{:.0f}": "-".join(dict.fromkeys(fmt.format(v) for v in (min(x[key] for x in win), max(x[key] for x in win))))
        sp = [x["sp"] for x in win if x["sp"] is not None]
        print(f"  {clock(tm)}  \"{m.get('tekst', '')}\": {min(sp) if sp else '?'}-{max(sp) if sp else '?'} km/u, {rng('rpm')} rpm, "
              f"gasklep-ADC {rng('tps')}, MAP {rng('map')} kPa, koelwater {rng('ect')} C")
        c = [x for x in win if x["cyl"]]
        if c:
            print(f"      onrust hoogste {max(x['cyl'][0] for x in c)} {max(x['cyl'][1] for x in c)} {max(x['cyl'][2] for x in c)}; boven de drempel in "
                  f"{sum(1 for x in c if x['exc'][0])}/{sum(1 for x in c if x['exc'][1])}/{sum(1 for x in c if x['exc'][2])} van {len(c)} metingen")
        o = [x for x in win if x["o2f"] is not None]
        if o:
            print(f"      lambda voor kat {min(x['o2f'] for x in o):.2f}-{max(x['o2f'] for x in o):.2f} V, na kat {min(x['o2r'] for x in o):.2f}-{max(x['o2r'] for x in o):.2f} V; "
                  f"EGR-stand hoogste {max(x['egr'] for x in o) * 5 / 1023:.2f} V; closed loop in {sum(1 for x in win if x['cl'] == 1)} van {len(win)}")
