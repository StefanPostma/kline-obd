"""Nep-ECU voor tests zonder auto en zonder kabel: dezelfde klasse KLine als kline.py, maar de antwoorden komen hieruit.

Gebruikt door selftest.py, dat `kline` door deze module vervangt voordat een script start. Alles boven de draad is echt
(aanknopen-volgorde, leesplan, herhalen, filteren van PID's, zijbestanden); alleen de bytes van en naar de auto niet.

Scenario via de omgevingsvariabele NEP_ECU:
  matiz       (standaard) fast init, antwoordt vanaf 0x11, fabrikantblokken 21 xx en identiteit 1A 80
  obd2        fast init, alleen standaard-PID's; fabrikantservices worden geweigerd
  obd2_9141   alleen 5-baud init (ISO 9141-2), alleen standaard-PID's

De blokken zijn echte antwoorden van een Matiz 0.8 bij deellast; de kilometerstand en de identiteit zijn verzonnen.
Elke dertiende vraag geeft een beschadigd antwoord, zoals dat onderweg op de lijn voorkomt.
"""
import os
import time

import kline
from kline import BAUD, PORT, KLineError, NegativeResponse, checksum, hx, poorten  # noqa: F401  (zelfde namen als kline)

SCENARIO = os.environ.get("NEP_ECU", "matiz")

BLOKKEN = {
    0x01: "00a502b800b60095000000ab0000001700000000010b01890000017903ff0000",
    0x02: "b15d1e7b2f0bd0dbaa94351ddb063d07300000000003006553b7000000000000",
    0x03: "5d5d5d000000a2000000000000000000080806001b1b1b000000000028292400",
    0x04: "001c0000001f00004ea20000424c00002c4f0000244a00000000122800000000",
    0x05: "f5fd00000000000000000000000000000000ffb50000000000000000000186a0",
    0x07: "00000000000000000100190001421f0000000000000000000000000000000000",
    0x0B: "0100000001000000000100000000000100000000000000c8735f310049001600",
    0x0C: "00000001000a3629002300001400b15eb9007c7cfe0000000016550000000003",
    0x0D: "00030100000000000000390303b6f5fdffb500ad267d2118616161009e00009f",
    0x0E: "ff00000001000000000100000000000100000000000000000000000000000000",
    0x0F: "ff01000100000000000100000000000100000000000000000000000000000000",
    0x10: "ff01000100000000000100000000000100000000000000000000000000000000",
    0x11: "ff01000100000000000100000000000100000000000000000000000000000000",
    0x12: "ff01000100000000000100000000000100000000000000000000000000000000",
    0x14: "0101010000000100010000f3fb00f412f412f4120000728d000000000000728d",
    0x15: "3393000028eb9a00000000000003b60300361c1c339333931f47000000000000",
}
BLOKKEN.update({i: "00" * 32 for i in (0x06, 0x08, 0x09, 0x0A, 0x16, 0x17)})
BLOKKEN[0x13] = "0c" + "00" * 31
# antwoord op 1A 80 in de vorm van de echte ECU, verzonnen: het serienummer is nullen, het stempel is alleen een datum
# (29-07-2008) met nullen als tijd
IDENTITEIT = (b"\xff" * 4 + b"SAT001SATFR\x00\x00S0011BOT" + b"\xff" * 7 + b"KR7714520100KR77145113008P04B2310000"
              + b"\xff" * 40 + b"SASCO   5WY5407A--00000000000   08072900000000" + b"\xff" * 17)

PID_DATA = {0x01: "0007a181", 0x03: "0200", 0x04: "3a", 0x05: "7d", 0x06: "74", 0x07: "77", 0x0B: "2d", 0x0C: "0e9c",
            0x0D: "00", 0x0E: "90", 0x0F: "41", 0x10: "012c", 0x11: "1a", 0x13: "03", 0x14: "5aff", 0x15: "18ff",
            0x1C: "06", 0x21: "0000"}
ONDERSTEUND = {
    "matiz": {0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x13, 0x14, 0x15, 0x1C, 0x20, 0x21},
    "obd2": {0x01, 0x03, 0x04, 0x05, 0x06, 0x0C, 0x0D, 0x0F, 0x10, 0x11, 0x13, 0x14, 0x1C},
}
ONDERSTEUND["obd2_9141"] = ONDERSTEUND["obd2"]


class KLine(kline.KLine):
    """De echte klasse zonder seriële poort: aanknopen() en use_physical() zijn die van kline.py."""

    def __init__(self, port=None, baud=None, verbose=True, dtr=True):
        self.verbose = verbose
        self.t0 = time.perf_counter()
        self.mode = self.target = self.addr_mode = self.ecu = None
        self.last_rx = time.perf_counter()
        self.vragen = 0

    def close(self):
        pass

    def fast_init(self, target=0x33, functional=True, tries=5):
        if SCENARIO == "obd2_9141":
            raise KLineError("geen antwoord op StartCommunication")
        self.mode, self.target, self.addr_mode = "kwp", target, 0xC0 if functional else 0x80
        self.ecu = 0x11 if SCENARIO == "matiz" else 0x10
        self.keybytes = bytes([0xEF, 0x8F])

    def slow_init(self, address=0x33):
        if SCENARIO != "obd2_9141":
            raise KLineError("geen geldige sync (55 KB1 KB2): -")
        self.mode, self.ecu, self.keybytes = "iso9141", None, bytes([0x08, 0x08])
        return self.keybytes

    def _antwoord(self, data):
        svc, ident = data[0], data[1] if len(data) > 1 else None
        kent = ONDERSTEUND[SCENARIO]
        if svc == 0x01:
            if ident % 0x20 == 0:  # welke PID's in het volgende blok van 32
                if ident and ident not in kent:
                    raise KLineError(f"geen antwoord op {hx(data)}")
                bits = sum(1 << (31 - i) for i in range(32) if ident + i + 1 in kent)
                return bytes([0x41, ident]) + bits.to_bytes(4, "big")
            if ident not in kent:
                raise KLineError(f"geen antwoord op {hx(data)}")  # een ECU zwijgt op een PID die hij niet kent
            return bytes([0x41, ident]) + bytes.fromhex(PID_DATA[ident])
        if svc in (0x03, 0x07):
            return bytes([svc + 0x40, 0, 0, 0, 0, 0, 0])
        if svc == 0x02:
            return bytes([0x42, 0x02, 0x00, 0x00, 0x00])
        if svc == 0x82:
            return bytes([0xC2])
        if SCENARIO == "matiz":
            if svc == 0x21:
                if ident not in BLOKKEN:
                    raise NegativeResponse(svc, 0x12)
                return bytes([0x61, ident]) + bytes.fromhex(BLOKKEN[ident])
            if svc == 0x1A and ident == 0x80:
                return bytes([0x5A, 0x80]) + IDENTITEIT
            if svc == 0x18:
                return bytes([0x58, 0x00])
        if svc in (0x21, 0x1A, 0x18):
            raise NegativeResponse(svc, 0x11)  # dienst niet ondersteund
        raise KLineError(f"geen antwoord op {hx(data)}")

    def query(self, data, timeout=0.5):
        if self.mode is None:
            raise KLineError(f"geen antwoord op {hx(bytes(data))} (niet aangeknoopt)")
        time.sleep(0.002)
        self.vragen += 1
        if self.vragen % 13 == 0:
            raise KLineError(f"checksum/lengte fout in A2 F1 11 (verzoek {hx(bytes(data))})")
        return self._antwoord(bytes(data))

    def request(self, data, timeout=1.0, quiet=False):
        return [self._antwoord(bytes(data))]

    def stop(self):
        self.mode = None
