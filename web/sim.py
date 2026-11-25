"""Gesimuleerde Matiz-ECU + nepkabel voor demo en tests. Alles wat hieruit komt is NEP-data.

FakeSerial heeft dezelfde API als pyserial (write/read/break_condition/...), zodat kwp.py, obd.py,
analyzer.py en de webinterface onveranderd tegen de simulator draaien.
"""
import bisect
import math
import random
import time

SUPPORTED = {0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x14, 0x1C, 0x1F, 0x33, 0x42}
SIM_VIN = "KL1DEM0009S000001"                 # nep: geen echt voertuig
SIM_INFO = {0x02: b"\0\0\0" + SIM_VIN.encode(), 0x04: b"SIM-CAL-0001".ljust(16, b"\0"),
            0x0A: b"SIMULATED-ECU".ljust(20, b"\0")}
SIM_KWP_IDENT = {0x87: b"SIMECU-ID-87", 0x9B: b"SIM-SW-1.0"}
P2 = 0.02                                   # reactietijd ECU
DLEN_9141 = {0x01: 2, 0x02: 3, 0x03: 1, 0x07: 1, 0x09: 2}


class Engine:
    """Warme-start stationair met een 'stotter' elke 18 s (toerental-dip + trim-piek)."""

    def __init__(self, scenario="stutter", speedup=1.0):
        self.t0 = time.monotonic()
        self.speedup = speedup
        self.rng = random.Random(7)
        self.dtcs = ["P0171"] if scenario == "stutter" else []
        self.ltft = 12.0 if scenario == "stutter" else 1.5
        self.running = True                          # False = contact aan, motor uit
        self.batt = None                             # vaste accuspanning (anders de dynamo-waarde van een draaiende motor)
        self.mon_d = 0x00                            # PID 01 byte D: bit = monitor NIET voltooid

    def clear_dtcs(self):
        """Mode 04: codes, freeze frame en readiness weg; de geleerde waarden (LTFT) blijven staan."""
        self.dtcs = []
        self.mon_d = 0xE5

    def values(self):
        t = (time.monotonic() - self.t0) * self.speedup
        g = self.rng.gauss
        warm = 1 - math.exp(-t / 90)
        ect = 55 + 37 * warm
        phase = (t - 12) % 18 if t >= 12 else 99
        s = math.sin(math.pi * phase / 1.2) if phase < 1.2 else 0.0      # stotterintensiteit 0..1
        closed = ect >= 70
        o2 = 0.45 + 0.42 * math.tanh(3 * math.sin(2 * math.pi * t / 1.3)) if closed else 0.45
        off = not self.running
        return {
            "rpm": 0.0 if off else 820 + 260 * math.exp(-t / 60) + 10 * math.sin(t * 1.3) + g(0, 6) - 380 * s,
            "tps": 11.0 + g(0, 0.3),
            "map": 36 + g(0, 1.2) + 14 * s,
            "stft": 5 * math.sin(2 * math.pi * t / 2.6) + 28 * s + g(0, 0.5),
            "ltft": self.ltft,
            "o2_b1s1": max(0.05, min(0.95, o2 + g(0, 0.02) - 0.35 * s)),
            "ect": ect, "iat": 35 + 8 * warm,
            "batt": self.batt if self.batt is not None else (12.4 if off else 14.1) + g(0, 0.04),
            "load": 22 + g(0, 0.5), "timing": 12 + g(0, 0.3), "speed": 0,
            "fuelsys": 2 if closed else 1, "runtime": int(t) + 30, "baro": 101,
            "dtc_count": len(self.dtcs), "mil": 1 if self.dtcs else 0, "mon_d": self.mon_d,
        }


def _clamp(x, lo=0, hi=255):
    return max(lo, min(hi, int(round(x))))


def encode(pid, v):
    if pid == 0x01:
        return bytes([(0x80 if v["mil"] else 0) | v["dtc_count"], 0x07, 0xE5, v.get("mon_d", 0)])
    if pid == 0x03:
        return bytes([v["fuelsys"], 0])
    if pid == 0x04:
        return bytes([_clamp(v["load"] * 255 / 100)])
    if pid == 0x05:
        return bytes([_clamp(v["ect"] + 40)])
    if pid in (0x06, 0x07):
        return bytes([_clamp((v["stft" if pid == 0x06 else "ltft"] + 100) * 128 / 100)])
    if pid == 0x0B:
        return bytes([_clamp(v["map"])])
    if pid == 0x0C:
        return _clamp(v["rpm"] * 4, 0, 65535).to_bytes(2, "big")
    if pid == 0x0D:
        return bytes([_clamp(v["speed"])])
    if pid == 0x0E:
        return bytes([_clamp((v["timing"] + 64) * 2)])
    if pid == 0x0F:
        return bytes([_clamp(v["iat"] + 40)])
    if pid == 0x11:
        return bytes([_clamp(v["tps"] * 255 / 100)])
    if pid == 0x14:
        return bytes([_clamp(v["o2_b1s1"] * 200), 0xFF])
    if pid == 0x1C:
        return bytes([6])                                    # EOBD
    if pid == 0x1F:
        return _clamp(v["runtime"], 0, 65535).to_bytes(2, "big")
    if pid == 0x33:
        return bytes([v["baro"]])
    if pid == 0x42:
        return _clamp(v["batt"] * 1000, 0, 65535).to_bytes(2, "big")
    raise KeyError(pid)


def support_mask(base):
    mask = 0
    for p in SUPPORTED:
        if base < p <= base + 32:
            mask |= 1 << (31 - (p - base - 1))
    if any(p > base + 32 for p in SUPPORTED):
        mask |= 1
    return mask.to_bytes(4, "big")


def dtc_bytes(code):
    a = ("PCBU".index(code[0]) << 6) | (int(code[1]) << 4) | int(code[2], 16)
    return bytes([a, int(code[3:], 16)])


class SimECU:
    """proto: kwp-fast | kwp-slow | iso9141-slow"""

    def __init__(self, proto="kwp-fast", engine=None, blocks=None):
        """blocks: {id: bytes of functie(values) -> bytes} voor KWP service 21 (fabrikantblokken); zonder: 7F 21 11."""
        self.blocks = blocks
        self.nrc04 = None                            # test: antwoord op mode 04 met 7F 04 <nrc> in plaats van wissen
        self.drop04 = False                          # test: mode 04 krijgt geen antwoord (en wordt toch uitgevoerd)
        self.lose04 = False                          # test: mode 04 komt nooit aan (geen antwoord, niets uitgevoerd)
        self.fail = {}                               # test: {service: nrc} laat een service altijd met 7F weigeren
        self.seen = {}                               # service -> aantal verzoeken (voor tests)
        self.seen_blocks = {}                        # blok-id -> aantal service 21-verzoeken (voor tests)
        self.proto = proto
        self.engine = engine or Engine()
        self.iso = proto.startswith("iso9141")
        self.kb = (0x08, 0x08) if self.iso else (0xEF, 0x8F)
        self.session = False
        self.last_rx = 0.0
        self.awake_until = 0.0
        self.state = "idle"
        self.txbuf = bytearray()
        self._last_byte = 0.0

    def _resp(self, payload):
        head = bytes([0x48, 0x6B, 0x11]) if self.iso else bytes([0x80 | len(payload), 0xF1, 0x11])
        b = head + payload
        return b + bytes([sum(b) & 0xFF])

    def _take_frame(self):
        b = self.txbuf
        if not b:
            return None
        if self.iso:
            if b[0] != 0x68:
                b.clear()
                return None
            if len(b) < 4:
                return None
            total = 3 + DLEN_9141.get(b[3], 1) + 1
            hdr = 3
        else:
            hdr = 3 if b[0] & 0xC0 else 1
            total = hdr + (b[0] & 0x3F) + 1
        if len(b) < total:
            return None
        frame = bytes(b[:total])
        del b[:total]
        return frame, hdr

    def on_bytes(self, data, now):
        """Verwerk bytes van de tester; geef lijst (vertraging, bytes) terug."""
        if now - self._last_byte > 0.1:              # stilte = nieuw bericht; restjes van eerder weggooien
            self.txbuf.clear()
        self._last_byte = now
        self.txbuf += data
        out = []
        while True:
            got = self._take_frame()
            if got is None:
                return out
            frame, hdr = got
            if sum(frame[:-1]) & 0xFF != frame[-1]:
                continue
            reply = self._handle(frame[hdr:-1], now)
            if reply is None:
                continue
            for i, r in enumerate(reply if isinstance(reply, list) else [reply]):
                out.append((P2 + 0.015 * i, self._resp(r)))

    def _neg(self, svc, code):
        return None if self.iso else bytes([0x7F, svc, code])

    def _handle(self, p, now):
        if not self.session:
            if p == b"\x81" and self.proto == "kwp-fast" and now < self.awake_until:
                self.session, self.last_rx = True, now
                return bytes([0xC1, *self.kb])
            return None
        if now - self.last_rx > 5.0:                 # P3max verlopen: sessie is weg
            self.session = False
            return None
        self.last_rx = now
        svc = p[0]
        self.seen[svc] = self.seen.get(svc, 0) + 1
        v = self.engine.values()
        if svc in self.fail:
            return self._neg(svc, self.fail[svc])
        if svc == 0x01 and len(p) >= 2:
            pid = p[1]
            if pid % 0x20 == 0 and pid <= 0x40:
                return bytes([0x41, pid]) + support_mask(pid)
            if pid in SUPPORTED:
                return bytes([0x41, pid]) + encode(pid, v)
            return self._neg(0x01, 0x12)
        if svc == 0x02 and len(p) >= 3:
            pid = p[1]
            if pid == 0x02:
                return bytes([0x42, 0x02, 0x00]) + (dtc_bytes(self.engine.dtcs[0]) if self.engine.dtcs else b"\0\0")
            if pid in SUPPORTED and pid not in (0x01, 0x03):
                return bytes([0x42, pid, 0x00]) + encode(pid, v)
            return self._neg(0x02, 0x12)
        if svc == 0x18 and len(p) >= 4 and not self.iso:  # KWP readDTCByStatus: 58 + aantal
            return bytes([0x58, len(self.engine.dtcs)])
        if svc == 0x03:
            pairs = b"".join(dtc_bytes(c) for c in self.engine.dtcs[:3]).ljust(6, b"\0")
            return bytes([0x43]) + pairs
        if svc == 0x04:                                  # mode 04: foutcodes wissen
            if self.nrc04 is not None:
                return self._neg(0x04, self.nrc04)
            if self.engine.running:                      # veel ECU's: alleen met contact aan en motor uit
                return self._neg(0x04, 0x22)
            if self.lose04:
                return None
            self.engine.clear_dtcs()
            return None if self.drop04 else bytes([0x44])
        if svc == 0x06 and len(p) >= 2 and not self.iso:  # mode 06: testresultaten, één TID met twee onderdelen
            if p[1] == 0x00:
                return bytes([0x46, 0x00, 0x00]) + (1 << 31).to_bytes(4, "big")   # alleen TID 01
            if p[1] == 0x01:
                return [bytes([0x46, 0x01]) + bytes([0x01, 0x00, 0x10, 0x00, 0x40]),
                        bytes([0x46, 0x01]) + bytes([0x02, 0x00, 0x20, 0x00, 0x40])]
            return self._neg(0x06, 0x12)
        if svc == 0x07:
            return bytes([0x47]) + bytes(6)
        if svc == 0x09 and len(p) >= 2:                 # voertuiginfo: elk antwoord = berichten van 4 databytes
            it = p[1]
            if it == 0x00:
                mask = sum(1 << (31 - (i - 1)) for i in SIM_INFO)
                return bytes([0x49, 0x00]) + mask.to_bytes(4, "big")
            if it in SIM_INFO:
                d = SIM_INFO[it]
                return [bytes([0x49, it, n + 1]) + d[4 * n:4 * n + 4] for n in range(len(d) // 4)]
            return self._neg(0x09, 0x12)
        if svc == 0x1A and len(p) >= 2 and not self.iso:  # KWP ReadECUIdentification
            if p[1] in SIM_KWP_IDENT:
                return bytes([0x5A, p[1]]) + SIM_KWP_IDENT[p[1]]
            return self._neg(0x1A, 0x12)
        if svc == 0x21 and len(p) >= 2 and not self.iso and self.blocks is not None:   # KWP readDataByLocalIdentifier
            self.seen_blocks[p[1]] = self.seen_blocks.get(p[1], 0) + 1
            blk = self.blocks.get(p[1])
            if blk is None:
                return self._neg(0x21, 0x31)
            return bytes([0x61, p[1]]) + (blk(v) if callable(blk) else blk)
        return self._neg(svc, 0x11)


class FakeSerial:
    def __init__(self, ecu):
        self.ecu = ecu
        self.baudrate = 10400
        self.timeout = 0.3
        self.dtr = self.rts = None
        self.is_open = True
        self._rx = []                         # (beschikbaar_vanaf, volgnr, byte), gesorteerd
        self._seq = 0
        self._brk = False
        self._brk_events = []                 # [(tijd, toestand)] voor 5-baud decodering
        self._brk_on = None
        self._slow_done = False
        self.corrupt_every = 0                # >0: tast elk n-de antwoord aan (nabootsing van ruis op de lijn)
        self._responses = 0

    # ----- break-lijn -----
    @property
    def break_condition(self):
        return self._brk

    @break_condition.setter
    def break_condition(self, state):
        state = bool(state)
        if state == self._brk:
            return
        now = time.monotonic()
        if state and (not self._brk_events or now - self._brk_events[-1][0] > 0.5):
            self._brk_events, self._slow_done = [], False      # nieuwe reeks (bv. nieuwe 5-baud init)
        self._brk_events.append((now, state))
        if state:
            self._brk_on = now
        elif self._brk_on is not None and 0.015 <= now - self._brk_on <= 0.04:
            self.ecu.awake_until = now + 1.0          # geldige fast-init puls (25 ms ± marge)
            self.ecu.session = False                  # een wekpuls zet de communicatie van de ECU terug
        self._brk = state

    def _poll_slow(self, now):
        ev = self._brk_events
        if self._slow_done or not ev or not ev[0][1] or now < ev[0][0] + 1.95:
            return
        if not self.ecu.proto.endswith("slow"):
            self._slow_done = True
            return
        t0 = ev[0][0]
        bits = []
        for i in range(10):
            ts, level = t0 + 0.1 + 0.2 * i, False
            for t, st in ev:
                if t <= ts:
                    level = st
            bits.append(0 if level else 1)
        addr = sum(b << i for i, b in enumerate(bits[1:9]))
        self._slow_done = True
        if bits[0] == 0 and addr == 0x33:
            for i, b in enumerate((0x55, *self.ecu.kb)):
                self._push(now + 0.06 + 0.003 * i, b)
            self.ecu.state = "await_kb2"

    # ----- seriële API -----
    def _push(self, avail, byte):
        self._seq += 1
        bisect.insort(self._rx, (avail, self._seq, byte))

    def write(self, data):
        now = time.monotonic()
        data = bytes(data)
        if self.baudrate < 1000:                       # byte-truc voor fast init
            self.ecu.awake_until = now + 1.0
            self.ecu.session = False
            self._push(now + 0.03, 0x00)
            return len(data)
        for i, b in enumerate(data):                   # K-line: eigen bytes komen terug
            self._push(now + 0.001 * (i + 1), b)
        if self.ecu.state == "await_kb2":
            if data == bytes([self.ecu.kb[1] ^ 0xFF]):
                self._push(now + 0.03, 0x33 ^ 0xFF)
                self.ecu.session, self.ecu.last_rx, self.ecu.state = True, now, "idle"
            return len(data)
        for delay, chunk in self.ecu.on_bytes(data, now):
            self._responses += 1
            if self.corrupt_every and self._responses % self.corrupt_every == 1:
                chunk = chunk[:-1] + bytes([chunk[-1] ^ 0x10])      # checksum-byte aantasten
            for i, b in enumerate(chunk):
                self._push(now + delay + 0.001 * i, b)
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        now = time.monotonic()
        self._poll_slow(now)
        self._rx = [x for x in self._rx if x[0] > now]

    def read(self, n=1):
        deadline = time.monotonic() + (self.timeout if self.timeout is not None else 1e9)
        out = bytearray()
        while True:
            now = time.monotonic()
            self._poll_slow(now)
            while self._rx and len(out) < n and self._rx[0][0] <= now:
                out.append(self._rx.pop(0)[2])
            if len(out) >= n or now >= deadline:
                return bytes(out)
            time.sleep(0.001)

    def close(self):
        self.is_open = False
