"""Beoordeel de krukas-onrust per cilinder uit een (snelle) opname; vergelijk desnoods vóór en ná een kabelwissel.

    analyse_onrust.py <opname.jsonl> [tweede_opname.jsonl]

Per cilinderwaarde (de drie kanalen van blok 03 byte 28-30): gemiddelde, mediaan, 95-percentiel, hoogste en het
aandeel boven 100. Plus de tien grootste gebeurtenissen. Twee opnames naast elkaar laat zien of het onrustspoor na het
omwisselen van bougiekabel/bougie van kanaal is gewisseld (dan zit het in dat onderdeel) of op dezelfde cilinder bleef.
Leest zowel onrust_*.jsonl als rit_*.jsonl (zelfde formaat).
"""
import json
import sys
import statistics as st


def laad(pad):
    rijen = []
    for lijn in open(pad):
        try:
            d = json.loads(lijn)
        except ValueError:
            continue
        if "03" not in d or "0C" not in d:
            continue
        b03 = bytes.fromhex(d["03"])
        b0c = bytes.fromhex(d["0C"])
        b02 = bytes.fromhex(d["02"]) if "02" in d else None
        rijen.append({"t": d.get("t", 0.0), "ruw": (b03[28], b03[29], b03[30]),
                      "rpm": int.from_bytes(b0c[5:7], "big"),
                      "spd": b02[4] if b02 else -1})
    return rijen


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.95 * len(xs)))] if xs else 0


def toon(pad, rijen):
    print(f"\n=== {pad}: {len(rijen)} metingen ===")
    if not rijen:
        print("  geen bruikbare rijen (blok 03/0C ontbreekt)")
        return
    dur = rijen[-1]["t"] - rijen[0]["t"]
    print(f"  duur {dur:.0f} s, {len(rijen)/dur:.1f} metingen/s" if dur > 0 else "")
    print(f"  {'kanaal':8}{'gem':>6}{'med':>6}{'p95':>6}{'hoogste':>9}{'>=100':>8}")
    for i in range(3):
        xs = [r["ruw"][i] for r in rijen]
        boven = 100 * sum(1 for x in xs if x >= 100) / len(xs)
        merk = "  <= onrustigst" if i == max(range(3), key=lambda j: st.mean([r["ruw"][j] for r in rijen])) else ""
        print(f"  cil-waarde {i+1}{st.mean(xs):6.0f}{st.median(xs):6.0f}{p95(xs):6.0f}{max(xs):9d}{boven:7.0f}%{merk}")
    print("  tien grootste gebeurtenissen:")
    for r in sorted(rijen, key=lambda r: -max(r["ruw"]))[:10]:
        print(f"    t={r['t']:7.1f}  rpm{r['rpm']:5d} {r['spd']:3d}km/u   onrust {r['ruw'][0]:3d} {r['ruw'][1]:3d} {r['ruw'][2]:3d}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for pad in sys.argv[1:3]:
        toon(pad, laad(pad))
    if len(sys.argv) >= 3:
        print("\nVergelijk de 'onrustigst'-markering tussen beide opnames: verschoof die naar een ander kanaal na het")
        print("omwisselen van kabel/bougie, dan zit de oorzaak in dat verplaatste onderdeel; bleef hij op hetzelfde")
        print("kanaal, dan is het de uitgang van het bobineblok of de compressie/injector van die cilinder.")


if __name__ == "__main__":
    main()
