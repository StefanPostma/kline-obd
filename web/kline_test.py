#!/usr/bin/env python3
"""Go/no-go test voor een FTDI K-line (KKL) kabel op een auto met OBD2 K-line (pin 7).

Gebruikt dezelfde code als de webinterface (kwp.py / obd.py), dus wat hier werkt werkt daar ook.
Plak de volledige uitvoer terug als er iets niet werkt: alle TX/RX-bytes staan erin.

  python kline_test.py            # alleen echo-test (kabel)
  python kline_test.py --init     # + aanknopen, voertuig/ECU herkennen, paar live waarden

Kabel in de OBD-poort (12 V op pin 16 voedt de kabel), contact aan.
Stop eerst server.py: de kabel kan maar door één programma tegelijk worden gebruikt.
"""
import argparse
import glob
import sys
import time

import obd
import vehicle
from kwp import KLine, KLineError, NoEcho, open_port

BAUD = 10400


def hexs(b):
    return " ".join(f"{x:02X}" for x in b) or "(niets)"


def echo_test(ser):
    ser.reset_input_buffer()
    probe = bytes([0x55, 0xAA, 0x33])
    ser.write(probe)
    ser.flush()
    time.sleep(0.2)
    got = ser.read(64)
    ok = got.startswith(probe)
    print(f"echo-test: verstuurd {hexs(probe)}  ontvangen {hexs(got)}  ->",
          "ECHO OK" if ok else "GEEN ECHO (kabel zonder echo, niet gevoed, of K-line niet op de FTDI)")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true", help="ook aanknopen met de ECU en voertuig/ECU uitlezen")
    ap.add_argument("--serial", help="seriële poort, standaard automatisch /dev/cu.usbserial-*")
    ap.add_argument("--dtr", type=int, choices=[0, 1])
    ap.add_argument("--rts", type=int, choices=[0, 1])
    ap.add_argument("--p4-ms", type=float, default=5.0, help="pauze tussen verzoekbytes (standaard 5 ms)")
    args = ap.parse_args()

    ports = sorted(glob.glob("/dev/cu.usbserial-*"))
    port = args.serial or (ports[0] if ports else None)
    if not port:
        sys.exit("Geen /dev/cu.usbserial-* gevonden: kabel niet herkend. Zie README (Problemen).")
    print("poort:", port)
    ser = open_port(port, args.dtr, args.rts)
    echo_test(ser)
    if not args.init:
        return 0

    kl = KLine(ser, log=print, debug=True)
    kl.p4 = args.p4_ms / 1000
    try:
        kl.connect()
    except NoEcho as e:
        print(f"\nRESULTAAT: GEEN ECHO EN GEEN ANTWOORD ({e})")
        print("-> K-line zit niet op de FTDI of de kabel krijgt geen 12 V. Zie README.")
        return 1
    except KLineError as e:
        print(f"\nRESULTAAT: echo OK, maar aanknopen mislukt:\n  {e}")
        print("-> contact aan? Anders: plak deze uitvoer terug, dan pas ik de init aan.")
        return 1

    print(f"\nAANGEKNOPT: {kl.init_method}, protocol {'KWP2000' if kl.proto == 'kwp' else 'ISO 9141-2'}, "
          f"keybytes {hexs(kl.keybytes)}" + ("" if kl.check_echo else ", kabel zonder echo"))
    try:
        sup = obd.supported_pids(kl)
    except KLineError as e:
        print(f"Eerste OBD-verzoek (01 00) mislukt: {e}")
        print("-> init lukte wel, dus dit is een framing-/protocolprobleem. Plak deze uitvoer terug.")
        return 1
    print("ondersteunde PID's:", " ".join(f"{p:02X}" for p in sup))

    info = obd.identify(kl, sup)
    v = vehicle.decode_vin(info["vin"]) if info["vin"] else None
    print("\nVOERTUIG / ECU")
    print("  VIN:        ", info["vin"] or "niet beschikbaar",
          f"({v['maker']}, modeljaar {v['year']})" if v else "")
    print("  ECU-naam:   ", info["ecu_name"] or "-")
    print("  kalibratie: ", info["cal_id"] or "-")
    print("  OBD-norm:   ", info["obd_std"] or "onbekend")
    print("  brandstof:  ", {None: "onbekend", True: "diesel", False: "benzine"}[info["diesel"]])
    print("  mode 09:    ", " ".join(f"{i:02X}" for i in info["mode09"] if i % 0x20) or "niet ondersteund")
    for n in info["notes"]:
        print("  let op:     ", n)

    print("\nLIVE WAARDEN (eenmalig)")
    for pid in (0x0C, 0x05, 0x0B, 0x11, 0x06, 0x07, 0x14, 0x42):
        if pid in sup:
            try:
                for key, val in obd.read_pid(kl, pid).items():
                    label, unit, dec, _ = obd.META[key]
                    print(f"  {label:34s} {val:.{dec}f} {unit}")
            except KLineError as e:
                print(f"  PID {pid:02X}: {e}")
    print("\nRESULTAAT: kabel en ECU werken. Start nu server.py (met --profile matiz08 voor de Matiz).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
