"""Snelle uitlezing van de krukas-onrust per cilinder: alleen blok 03 (+ 0C en 02), zo vaak mogelijk.

Voor de bougiekabel-wisseltest: de gewone opname leest blok 03 maar eens per ~1,2 s, te traag voor een losse misser.
Dit leest alleen 03 (onrust per cilinder), 0C (toerental/MAP) en 02 (snelheid/EGR-stuur) in een strakke lus, zo'n 4-6×
per seconde, zodat één zwakke cilinder duidelijk uitkomt en we de onrust vóór/na de wissel kunnen vergelijken.

Alleen lezen; stuurt niets aan. Er kan maar één programma tegelijk op de kabel (niet samen met rit.py of server.py).

    onrust_snel.py <label> [seconden]      ->  onrust_<label>.jsonl   (standaard 900 s; Ctrl-C stopt netjes)

Analyse achteraf: `python analyse_onrust.py onrust_<label>.jsonl` of met rit_report.py (die leest hetzelfde formaat).
"""
import json
import os
import sys
import time

from kline import KLine, KLineError, NegativeResponse
from paden import in_datamap

try:  # alleen voor het zijbestand met ECU-identiteit; de opname mag er nooit op stuklopen
    import signalen
except Exception as e:
    if os.environ.get("KLINE_VOERTUIG"):
        sys.exit(f"voertuigbestand laden mislukt: {e}")
    signalen = None

# onrust per cilinder, toerental/MAP, snelheid/EGR-stuur (uit het voertuigbestand; anders die van de Matiz)
BLOKKEN = list(getattr(signalen, "SNEL_BLOKKEN", [0x03, 0x0C, 0x02]) if signalen else [0x03, 0x0C, 0x02])
if not BLOKKEN or getattr(signalen, "TRANSPORT", "kwp2000") != "kwp2000":
    sys.exit("dit voertuigbestand heeft geen SNEL_BLOKKEN: de snelle onrustmeting bestaat alleen voor auto's met zulke blokken")

label = sys.argv[1] if len(sys.argv) > 1 else "snel"
seconden = float(sys.argv[2]) if len(sys.argv) > 2 else 900.0
out = in_datamap(f"onrust_{label}.jsonl")
META = in_datamap(f"onrust_{label}.meta.json")
_meta_done = False


def write_meta(k):
    """Eenmalig na het aanknopen: ECU-identiteit + leesplan in het zijbestand (zoals de gewone opname). Optioneel."""
    global _meta_done
    if _meta_done:
        return
    doc = {"gestart": round(time.time(), 2), "script": "onrust_snel.py", "ecu": {},
           "leesplan": {"elke_ronde_blokken": [f"{b:02X}" for b in BLOKKEN]}}
    try:
        verzoek = getattr(signalen, "IDENTITEIT_VERZOEK", None) if signalen else None
        if verzoek:
            doc["ecu"] = signalen.identiteit(k.query(list(verzoek))[2:])
    except Exception:
        pass
    try:
        with open(META + ".tmp", "w") as fh:
            json.dump(doc, fh, indent=1)
        os.replace(META + ".tmp", META)
        _meta_done = True
    except Exception:
        pass


def w(hexstr, off):
    b = bytes.fromhex(hexstr)
    return int.from_bytes(b[off:off + 2], "big")


def ask(k, ident):
    """Eén blok lezen, bij een leesfout één keer herhaald. Geeft de hex-payload of None."""
    for poging in (1, 2):
        try:
            return k.query([0x21, ident])[2:].hex()
        except NegativeResponse:
            return None
        except KLineError:
            if poging == 2:
                return None
            time.sleep(0.02)


def connect(k):
    k.fast_init(0x33, True, tries=3)
    k.use_physical()


def main():
    k = KLine(verbose=False)
    start = time.time()
    rounds = fouten = reconnects = 0
    last_print = -9.0
    connected = False
    misses = 0
    try:
        with open(out, "w") as f:
            while time.time() - start < seconden:
                if not connected:
                    try:
                        connect(k)
                        connected, misses = True, 0
                        write_meta(k)
                    except KLineError as e:
                        print(f"[{time.time()-start:6.1f}] geen verbinding: {e}; opnieuw...", flush=True)
                        time.sleep(6.0)
                        continue
                row = {"t": round(time.time() - start, 3), "e": round(time.time(), 2)}
                for ident in BLOKKEN:
                    key = f"{ident:02X}"
                    payload = ask(k, ident)
                    if payload is not None:
                        row[key] = payload
                        misses = 0
                    else:
                        fouten += 1
                        misses += 1
                if misses >= 6:
                    connected = False
                    reconnects += 1
                    time.sleep(1.0)
                    continue
                f.write(json.dumps(row) + "\n")
                f.flush()
                rounds += 1
                if "03" in row and "0C" in row and row["t"] - last_print >= 2.0:
                    last_print = row["t"]
                    b03 = bytes.fromhex(row["03"])
                    spd = bytes.fromhex(row["02"])[4] if "02" in row else -1
                    hz = rounds / (time.time() - start) if time.time() > start else 0
                    print(f"[{row['t']:6.1f}] {spd:3d} km/u  rpm {w(row['0C'],5):4d}  "
                          f"onrust {b03[28]:3d} {b03[29]:3d} {b03[30]:3d}  ({hz:.1f}/s)", flush=True)
    except KeyboardInterrupt:
        print("gestopt")
    finally:
        try:
            k.stop()
        except Exception:
            pass
        k.close()
    dur = time.time() - start
    print(f"{rounds} rondes in {out} ({dur:.0f} s, {rounds/dur:.1f}/s); {fouten} leesfouten, {reconnects}x herverbonden")


if __name__ == "__main__":
    main()
