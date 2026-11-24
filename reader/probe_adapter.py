"""Stel vast wat voor adapter er op de seriele poort hangt: ELM327 of domme KKL-kabel.

Alleen lezen/identificeren: stuurt ATZ/ATI en kijkt wat er terugkomt.
"""
import sys
import time

import serial

from kline import PORT as STANDAARD_POORT

PORT = sys.argv[1] if len(sys.argv) > 1 else STANDAARD_POORT
BAUDS = [38400, 9600, 115200, 57600, 19200]


def probe(baud):
    with serial.Serial(PORT, baud, timeout=0.3) as s:
        s.reset_input_buffer()
        # modemlijnen laten zien; een KKL-kabel gebruikt die soms voor de L-lijn
        lines = f"CTS={s.cts} DSR={s.dsr} RI={s.ri} CD={s.cd}"
        s.write(b"\r")
        time.sleep(0.2)
        s.reset_input_buffer()
        s.write(b"ATZ\r")
        deadline = time.time() + 2.5
        buf = b""
        while time.time() < deadline:
            chunk = s.read(64)
            if chunk:
                buf += chunk
                if b">" in buf:
                    break
        return lines, buf


for baud in BAUDS:
    try:
        lines, raw = probe(baud)
    except serial.SerialException as e:
        print(f"{baud:>6}: FOUT {e}")
        break
    text = raw.decode("ascii", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
    print(f"{baud:>6}: {lines}  raw={raw.hex(' ')}  text={text!r}")
    if b"ELM" in raw or b">" in raw:
        print(f"--> ELM327-achtige adapter op {baud} baud")
        break
    if raw.startswith(b"ATZ\r") and len(raw) == 4:
        print("--> alleen echo van eigen bytes: wijst op KKL-kabel (K-lijn half-duplex)")
