"""Lijndiagnose: wat doet de RX-lijn bij luisteren, bij DTR/RTS-combinaties en bij zenden?

Doel: onderscheid maken tussen KKL-kabel zonder 12V, KKL-kabel met 12V (echo),
en een ELM327 op een afwijkende baudrate. Er wordt geen ECU-init gestuurd.
"""
import sys
import time

import serial

from kline import PORT as STANDAARD_POORT

PORT = sys.argv[1] if len(sys.argv) > 1 else STANDAARD_POORT


def drain(s, seconds):
    end = time.time() + seconds
    buf = b""
    while time.time() < end:
        buf += s.read(256)
    return buf


print("== 1. passief luisteren op 10400 baud (2 s), niets zenden ==")
with serial.Serial(PORT, 10400, timeout=0.05) as s:
    time.sleep(0.3)
    s.reset_input_buffer()
    got = drain(s, 2.0)
    print(f"   ontvangen: {len(got)} bytes  {got[:32].hex(' ')}")

print("== 2. echo-test per DTR/RTS-combinatie op 10400 baud ==")
for dtr in (True, False):
    for rts in (True, False):
        s = serial.Serial()
        s.port = PORT
        s.baudrate = 10400
        s.timeout = 0.05
        s.dtr = dtr
        s.rts = rts
        s.open()
        time.sleep(0.3)
        pre = drain(s, 0.3)
        s.write(b"\x55\xaa\x0f")
        s.flush()
        echo = drain(s, 0.5)
        print(
            f"   DTR={dtr!s:5} RTS={rts!s:5}  voor zenden: {pre.hex(' ') or '-':8}"
            f"  na zenden 55 aa 0f: {echo.hex(' ') or '-'}"
            f"  (CTS={s.cts} DSR={s.dsr} RI={s.ri} CD={s.cd})"
        )
        s.close()

print("== 3. break-test: staat RX vast laag? ==")
with serial.Serial(PORT, 10400, timeout=0.05) as s:
    time.sleep(0.3)
    s.reset_input_buffer()
    s.break_condition = True
    time.sleep(0.2)
    s.break_condition = False
    got = drain(s, 0.5)
    print(f"   na 200 ms break: {got.hex(' ') or '-'}")

print("== 4. ELM327 op afwijkende baudrates ==")
for baud in (230400, 500000, 1000000, 2000000, 4800, 2400, 1200, 10400, 14400, 28800):
    try:
        with serial.Serial(PORT, baud, timeout=0.2) as s:
            time.sleep(0.2)
            s.reset_input_buffer()
            s.write(b"ATI\r")
            got = drain(s, 1.2)
    except (serial.SerialException, ValueError, OSError) as e:
        print(f"   {baud:>7}: niet ondersteund ({e})")
        continue
    print(f"   {baud:>7}: {got.hex(' ') or '-'}  {got!r}")
