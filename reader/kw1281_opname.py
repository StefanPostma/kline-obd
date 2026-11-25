"""Opname voor een voertuig dat KW1281 spreekt (bv. de VW 1600i met Digifant). Alleen lezen.

Gebruik: kw1281_opname.py <uitvoer.jsonl> [seconden]        (Ctrl-C stopt netjes)
         kw1281_opname.py --rapport <opname.jsonl>           korte samenvatting van een opname

Je start dit meestal niet zelf: egr_capture.py (en dus `rit.py` en `kline-obd opname`) schakelt hierheen zodra het
voertuigbestand TRANSPORT = "kw1281" heeft. Zelfde opnameformaat en zijbestanden als de gewone opname.

Elke ronde: de lezingen uit LEZING_ELKE_RONDE en één uit LEZING_ROULEREND. De tester mag nooit langer dan een seconde
zwijgen, anders verbreekt de ECU de sessie; valt de sessie weg, dan wordt opnieuw gewekt (na ruim 2 s stilte).
Dit script kan alleen de benoemde lezingen van kw1281.py laten versturen; een ruwe bloktitel opgeven kan niet.

Nooit op een echte ECU geprobeerd: alleen tegen de nagebootste ECU (nep_kw1281.py).
"""
import json
import os
import statistics
import sys
import time

import kw1281
import signalen
from kline import KLineError
from paden import datamap, in_datamap

SOORTEN = {"tien_waarden": 0, "groep": 1, "ram_venster": 1}  # benoemde lezing -> aantal argumenten


def controleer_plan():
    lezingen = getattr(signalen, "LEZINGEN", None)
    if getattr(signalen, "TRANSPORT", None) != "kw1281" or not isinstance(lezingen, dict):
        sys.exit("dit voertuigbestand is niet voor KW1281 (TRANSPORT = \"kw1281\" en LEZINGEN ontbreken)")
    for sleutel, lezing in lezingen.items():
        if (len(sleutel) != 2 or not lezing or lezing[0] not in SOORTEN or len(lezing) - 1 != SOORTEN[lezing[0]]
                or (lezing[0] == "groep" and not 1 <= lezing[1] <= 8)):
            sys.exit(f"voertuigbestand: lezing {sleutel!r} = {lezing!r} is geen toegestane benoemde lezing")
    elke, roulerend = list(getattr(signalen, "LEZING_ELKE_RONDE", [])), list(getattr(signalen, "LEZING_ROULEREND", []))
    onbekend = [s for s in elke + roulerend if s not in lezingen]
    if onbekend or not elke:
        sys.exit(f"voertuigbestand: LEZING_ELKE_RONDE is leeg of noemt onbekende lezingen: {onbekend}")
    return lezingen, elke, roulerend


def rapport(pad):
    rijen = []
    for lijn in open(pad):
        try:
            rijen.append(json.loads(lijn))
        except ValueError:
            pass
    if not rijen:
        sys.exit("geen metingen in de opname")
    duur = rijen[-1]["t"] - rijen[0]["t"]
    print(f"=== {os.path.basename(pad)}: {len(rijen)} rondes, {duur / 60:.1f} min"
          + (f", {duur / (len(rijen) - 1):.2f} s per ronde ===" if len(rijen) > 1 else " ==="))
    reeksen = {}
    for rij in rijen:
        blokken = {k: bytes.fromhex(v) for k, v in rij.items() if k not in ("t", "e") and isinstance(v, str)}
        for sleutel, waarde in signalen.decode(blokken).items():
            reeksen.setdefault(sleutel, []).append(waarde)
    print(f"  {'signaal':22s}{'n':>6}{'laagste':>10}{'mediaan':>10}{'hoogste':>10}  eenheid, zekerheid")
    for sleutel, xs in reeksen.items():
        _blok, _fn, eenheid, _tekst, zeker, *_ = signalen.SIGNALEN[sleutel]
        print(f"  {sleutel:22s}{len(xs):6d}{min(xs):10.2f}{statistics.median(xs):10.2f}{max(xs):10.2f}  {eenheid or '-'}, {zeker}")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--rapport":
        return rapport(sys.argv[2])
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    lezingen, elke, roulerend = controleer_plan()
    out = in_datamap(sys.argv[1])
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    basis = os.path.splitext(out)[0]
    live_pad = os.path.join(datamap(), "live.json")
    stats = {"lezingen": 0, "mislukt": 0, "gewekt": 0, "sessie_verloren": 0}
    last = {}
    meta = {"gestart": round(time.time(), 1), "script": os.path.basename(__file__), "ecu": {},
            "leesplan": {"transport": "kw1281", "elke_ronde": elke, "roulerend": roulerend}}

    def schrijf(pad, doc):
        try:
            with open(pad + ".tmp", "w") as fh:
                json.dump(doc, fh, indent=1)
            os.replace(pad + ".tmp", pad)
        except OSError:
            pass

    def publiceer(t_rel, verbonden, rondes):
        try:
            waarden = signalen.decode({key: val for key, (val, _) in last.items()})
            now = time.time()
            schrijf(live_pad, {"tijd": round(now, 1), "t": t_rel, "verbonden": verbonden, "bron": os.path.basename(out),
                               "waarden": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in waarden.items()},
                               "leeftijd_s": {k: round(now - t_abs, 1) for k, (_, t_abs) in last.items()},
                               "verbinding": dict(stats, rondes=rondes)})
        except Exception:
            pass  # live.json is bijzaak: de opname mag er nooit op stuklopen

    schrijf(basis + ".meta.json", meta)
    k = kw1281.KW1281(adres=getattr(signalen, "ADRES", kw1281.ADRES), baud=getattr(signalen, "BAUD", kw1281.BAUD))
    rondes = 0
    start = time.time()
    last_print = -99.0
    try:
        with open(out, "w") as f, open(basis + ".fouten.log", "w") as errlog:
            while time.time() - start < seconds:
                t_rel = round(time.time() - start, 3)
                if not k.verbonden:
                    try:
                        blokken = k.wek()
                        stats["gewekt"] += 1
                        if not meta["ecu"]:
                            meta["ecu"] = signalen.identiteit(blokken)
                            schrijf(basis + ".meta.json", meta)
                    except KLineError as e:
                        print(f"[{t_rel:6.1f}] geen verbinding: {e}; nieuwe poging...", flush=True)
                        publiceer(t_rel, False, rondes)
                        time.sleep(1.0)  # wek() wacht daarna zelf de verplichte stilte af
                        continue
                row = {"t": round(time.time() - start, 3), "e": round(time.time(), 1)}
                for sleutel in elke + ([roulerend[rondes % len(roulerend)]] if roulerend else []):
                    stats["lezingen"] += 1
                    try:
                        data = k.lees(*lezingen[sleutel])
                    except kw1281.VerbodenTitel:
                        raise  # fout in het voertuigbestand of in dit script: nooit doorgaan
                    except KLineError as e:
                        stats["mislukt"] += 1
                        errlog.write(json.dumps({"t": row["t"], "lezing": sleutel, "tekst": str(e)[:160]}) + "\n")
                        errlog.flush()
                        if not k.verbonden:
                            stats["sessie_verloren"] += 1
                            break
                        continue
                    row[sleutel] = data.hex()
                    last[sleutel] = (data, time.time())
                if len(row) > 2:
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                    rondes += 1
                publiceer(row["t"], k.verbonden, rondes)
                if row["t"] - last_print >= 5 and last:
                    last_print = row["t"]
                    w = signalen.decode({key: val for key, (val, _) in last.items()})
                    print(f"[{row['t']:6.1f}] " + "  ".join(f"{naam} {w[naam]:.1f}" for naam in list(w)[:6]), flush=True)
    except KeyboardInterrupt:
        print("gestopt")
    finally:
        try:
            k.stop()
        except Exception:
            pass
        k.close()
        publiceer(round(time.time() - start, 3), False, rondes)
    print(f"{rondes} rondes in {out}")
    print("verbinding: " + ", ".join(f"{key} {val}" for key, val in stats.items()))


if __name__ == "__main__":
    main()
