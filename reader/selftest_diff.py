"""Vergelijk twee momentopnames van selftest_snapshot.py.

Gebruik: selftest_diff.py <label_voor> <label_na>
"""
import json
import sys

from paden import in_datamap

a = json.load(open(in_datamap(f"zelftest_{sys.argv[1]}.json")))
b = json.load(open(in_datamap(f"zelftest_{sys.argv[2]}.json")))
print(f"VOOR: {a['time']}   NA: {b['time']}\n")

changed = False


def line(name, va, vb):
    global changed
    mark = "  " if va == vb else "->"
    if va != vb:
        changed = True
    print(f"{mark} {name:28s} {va!s:28s} {'' if va == vb else vb}")


line("MIL", "aan" if a.get("mil") else "uit", "aan" if b.get("mil") else "uit")
line("aantal codes", a.get("dtc_count"), b.get("dtc_count"))
line("opgeslagen codes", ", ".join(a["stored"]) or "geen", ", ".join(b["stored"]) or "geen")
line("pending codes", ", ".join(a["pending"]) or "geen", ", ".join(b["pending"]) or "geen")
line("freeze frame", a["freeze_dtc"] or "geen", b["freeze_dtc"] or "geen")
line("service 18", a["dtc18"], b["dtc18"])
for name in a.get("monitors", {}):
    if a["monitors"][name] != "niet aanwezig":
        line(f"monitor {name}", a["monitors"][name], b.get("monitors", {}).get(name))

print("\nModus 06 (test-ID / onderdeel: waarde en limiet):")
for tid in sorted(set(a["mode06"]) | set(b["mode06"])):
    ea = {e[:2]: e for e in a["mode06"].get(tid, [])}
    eb = {e[:2]: e for e in b["mode06"].get(tid, [])}
    for cid in sorted(set(ea) | set(eb)):
        def fmt(e):
            if not e:
                return "-"
            x = bytes.fromhex(e)
            return f"waarde {int.from_bytes(x[1:3], 'big'):5d} / {'min' if x[0] & 0x80 else 'max'} {int.from_bytes(x[3:5], 'big'):5d}"
        line(f"TID {tid} onderdeel {cid}", fmt(ea.get(cid)), fmt(eb.get(cid)))

# blokken: alleen bytes die binnen beide momentopnames stilstaan en toch verschillen
print("\nFabrikantblokken: bytes die binnen elke momentopname stabiel zijn maar tussen voor en na verschillen")
print("(blokken 01/02/03/04/0C/0D zijn live meetwaarden en worden overgeslagen)")
LIVE = {"01", "02", "03", "04", "0C", "0D"}
found = 0
for blk in sorted(set(a["blocks"]) & set(b["blocks"])):
    if blk in LIVE:
        continue
    ra = [bytes.fromhex(x) for x in a["blocks"][blk]]
    rb = [bytes.fromhex(x) for x in b["blocks"][blk]]
    if not ra or not rb:
        continue
    for n in range(min(len(ra[0]), len(rb[0]))):
        sa, sb = {r[n] for r in ra}, {r[n] for r in rb}
        if len(sa) == 1 and len(sb) == 1 and sa != sb:
            found += 1
            print(f"-> blok {blk} byte {n:2d}: {next(iter(sa)):3d} (0x{next(iter(sa)):02X})  ->  {next(iter(sb)):3d} (0x{next(iter(sb)):02X})")
if not found:
    print("   geen")
print("\nEr is iets veranderd." if changed or found else "\nGeen verschillen.")
