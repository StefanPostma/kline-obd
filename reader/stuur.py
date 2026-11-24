#!/usr/bin/env python3
"""Een aansturing aan het verbindingsproces vragen, of zien wat ermee gebeurde. Opent zelf nooit de kabel.

    stuur.py lijst                    wat er aangestuurd kan worden en of de bytes al bekend zijn
    stuur.py vraag <sleutel> [waarde] [--duur s]    een opdracht neerleggen in opdrachten/nieuw/
    stuur.py status [id]              de afgehandelde opdrachten (opdrachten/klaar/) tonen
    stuur.py stop                     alles onmiddellijk laten afbreken (zet de stopvlag)
    stuur.py hervat                   de stopvlag weghalen

Het verbindingsproces moet met --klasse C of D draaien om een opdracht uit te voeren; een gewone rit-opname niet,
die stuurt dus nooit iets. Zonder bekende, bevestigde bytes weigert het proces elke opdracht met reden.
"""
import json
import os
import sys

import opdracht
import stuurbaar
from paden import datamap

BASIS = os.path.join(datamap(), "opdrachten")


def lijst():
    print(f"{'sleutel':22} {'klasse':6} {'toestand':12} {'verzendbaar':11} omschrijving")
    for sleutel, a in stuurbaar.ACTIES.items():
        vb = "ja" if stuurbaar.verzendbaar(sleutel) else f"nee ({a['bron']})"
        print(f"{sleutel:22} {a['klasse']:6} {a['toestand']:12} {vb:11} {a['omschrijving']}")
    print("\n'verzendbaar nee' = de bytes voor deze ECU zijn nog niet bekend; het proces weigert de opdracht.")


def vraag(argv):
    if not argv:
        sys.exit("gebruik: stuur.py vraag <sleutel> [waarde] [--duur s]")
    sleutel = argv[0]
    if sleutel not in stuurbaar.ACTIES:
        sys.exit(f"onbekende actie {sleutel!r}; zie 'stuur.py lijst'")
    waarde = None
    duur = None
    rest = argv[1:]
    if "--duur" in rest:
        i = rest.index("--duur")
        duur = float(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]
    if rest:
        waarde = int(rest[0])
    pad = opdracht.leg_neer(BASIS, sleutel, waarde, duur, door="stuur.py")
    print(f"neergelegd: {os.path.basename(pad)}  ({sleutel}" + (f" = {waarde}" if waarde is not None else "") + ")")
    print("het verbindingsproces handelt het af zodra het de volgende ronde doet; zie 'stuur.py status'.")
    if not stuurbaar.verzendbaar(sleutel):
        print("let op: de bytes voor deze actie zijn nog niet bekend, dus dit wordt geweigerd met reden.")


def status(argv):
    klaar = os.path.join(BASIS, "klaar")
    if not os.path.isdir(klaar):
        print("nog niets afgehandeld.")
        return
    namen = sorted(n for n in os.listdir(klaar) if n.endswith(".json"))
    if argv:
        namen = [n for n in namen if argv[0] in n]
    for n in namen[-20:]:
        d = json.load(open(os.path.join(klaar, n)))
        regel = f"{d.get('sleutel'):20} {d.get('uitkomst','?'):10} {d.get('reden','')}"
        print(regel)
        if d.get("verzonden"):
            print(f"    verzonden {d['verzonden']}  antwoord {d.get('antwoord')}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, argv = sys.argv[1], sys.argv[2:]
    if cmd == "lijst":
        lijst()
    elif cmd == "vraag":
        vraag(argv)
    elif cmd == "status":
        status(argv)
    elif cmd == "stop":
        os.makedirs(BASIS, exist_ok=True)
        open(os.path.join(BASIS, "stop"), "w").write("stop\n")
        print("stopvlag gezet: het proces breekt elke lopende actie af en voert niets nieuws uit.")
    elif cmd == "hervat":
        try:
            os.remove(os.path.join(BASIS, "stop"))
            print("stopvlag weg.")
        except OSError:
            print("er stond geen stopvlag.")
    else:
        sys.exit(f"onbekend commando {cmd!r}; zie de hulp zonder argumenten.")


if __name__ == "__main__":
    main()
