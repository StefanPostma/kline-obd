"""K-line (ISO 9141-2 / ISO 14230 KWP2000) over een domme KKL-kabel met FTDI-chip.

De kabel echoot alles wat gezonden wordt (half-duplex) en werkt alleen met RTS laag.
Alle timing staat hier bij elkaar; scripts gebruiken alleen KLine.request().
"""
import glob
import os
import time

import serial


def poorten():
    """Kandidaat-poorten voor de kabel: de omgevingsvariabele KLINE_PORT, anders wat er als USB-serieel aangesloten is."""
    env = os.environ.get("KLINE_PORT")
    if env:
        return [env]
    return sorted(glob.glob("/dev/cu.usbserial-*")) + sorted(glob.glob("/dev/ttyUSB*"))


PORT = (poorten() or ["/dev/cu.usbserial-geen-kabel"])[0]
BAUD = 10400

TESTER = 0xF1


class KLineError(Exception):
    pass


class NegativeResponse(KLineError):
    CODES = {
        0x10: "generalReject",
        0x11: "serviceNotSupported",
        0x12: "subFunctionNotSupported/invalidFormat",
        0x21: "busy-repeatRequest",
        0x22: "conditionsNotCorrect",
        0x23: "routineNotComplete",
        0x31: "requestOutOfRange",
        0x33: "securityAccessDenied",
        0x35: "invalidKey",
        0x78: "responsePending",
        0x80: "serviceNotSupportedInActiveSession",
    }

    def __init__(self, sid, code):
        self.sid = sid
        self.code = code
        super().__init__(f"7F {sid:02X} {code:02X} ({self.CODES.get(code, 'onbekend')})")


def hx(data):
    return data.hex(" ").upper() if data else "-"


def checksum(data):
    return sum(data) & 0xFF


def _sleep_until(t):
    while True:
        dt = t - time.perf_counter()
        if dt <= 0:
            return
        if dt > 0.003:
            time.sleep(dt - 0.002)


def _spin_until(t):
    while time.perf_counter() < t:
        pass


class KLine:
    # fast-init wekpuls: laag-tijd en moment van StartCommunication t.o.v. de neergaande flank
    tinil = 0.025
    twup = 0.050

    def __init__(self, port=PORT, baud=BAUD, verbose=True, dtr=True):
        self.verbose = verbose
        self.t0 = time.perf_counter()
        s = serial.Serial()
        s.port = port
        s.baudrate = baud
        s.bytesize = serial.EIGHTBITS
        s.parity = serial.PARITY_NONE
        s.stopbits = serial.STOPBITS_ONE
        s.timeout = 0.02
        s.dtr = dtr
        s.rts = False  # RTS hoog = geen ontvangst op deze kabel
        s.open()
        self.s = s
        self.mode = None  # "kwp" of "iso9141"
        self.target = None
        self.addr_mode = None  # 0x80 fysiek, 0xC0 functioneel
        self.last_rx = time.perf_counter()
        # het openen van de poort geeft een glitch op de lijn; ECU eerst laten uitrazen
        time.sleep(2.6)
        self.s.reset_input_buffer()

    # -- hulpfuncties -------------------------------------------------------
    def log(self, msg):
        if self.verbose:
            print(f"[{time.perf_counter() - self.t0:8.3f}] {msg}")

    def close(self):
        self.s.close()

    def _read(self, n, timeout):
        """Lees maximaal n bytes binnen timeout."""
        end = time.perf_counter() + timeout
        buf = b""
        while len(buf) < n and time.perf_counter() < end:
            buf += self.s.read(n - len(buf))
        return buf

    def _read_gap(self, first_timeout, gap=0.08, total=2.0):
        """Lees tot de lijn `gap` seconden stil is."""
        start = time.perf_counter()
        buf = b""
        last = start
        while True:
            chunk = self.s.read(256)
            now = time.perf_counter()
            if chunk:
                buf += chunk
                last = now
            elif buf and now - last > gap:
                break
            elif not buf and now - start > first_timeout:
                break
            if now - start > total:
                break
        if buf:
            self.last_rx = time.perf_counter()
        return buf

    def _tx(self, data, gap=0.005):
        """Zend met P4-tussenruimte en haal de eigen echo weer van de lijn."""
        for b in data:
            self.s.write(bytes([b]))
            self.s.flush()
            time.sleep(gap)
        echo = self._read(len(data), 0.5)
        if echo != data:
            raise KLineError(f"echo wijkt af: gezonden {hx(data)}, terug {hx(echo)}")

    # -- initialisatie ------------------------------------------------------
    def fast_init(self, target=0x33, functional=True, tries=5):
        """Fast init met herhaling: de wekpuls gaat via USB en heeft ~1 ms jitter."""
        for n in range(1, tries + 1):
            try:
                return self._fast_init_once(target, functional)
            except KLineError as e:
                self.log(f"fast init poging {n}/{tries} mislukt: {e}")
                if n == tries:
                    raise
                time.sleep(2.6)  # ECU terug naar rust (W5)

    def _fast_init_once(self, target, functional):
        """ISO 14230 fast init: 25 ms laag, 25 ms hoog, dan StartCommunication."""
        fmt = (0xC0 if functional else 0x80) | 1
        req = bytes([fmt, target, TESTER, 0x81])
        req += bytes([checksum(req)])
        self.s.reset_input_buffer()
        # busy-wait: time.sleep schiet op macOS milliseconden over de ±1 ms tolerantie heen
        t = time.perf_counter()
        self.s.break_condition = True
        t_low = time.perf_counter()
        _spin_until(t_low + self.tinil)
        self.s.break_condition = False
        t_high = time.perf_counter()
        _spin_until(t_low + self.twup)
        self.s.write(req)
        t_tx = time.perf_counter()
        self.s.flush()
        raw = self._read_gap(first_timeout=0.6, gap=0.12, total=1.5)
        self.init_timing = ((t_low - t) * 1e3, (t_high - t_low) * 1e3, (t_tx - t_low) * 1e3)
        self.log(f"fast init -> {hx(req)} | ruw terug: {hx(raw)} | ioctl {self.init_timing[0]:.2f} ms,"
                 f" laag {self.init_timing[1]:.2f} ms, start tx {self.init_timing[2]:.2f} ms")
        idx = raw.find(req)
        if idx < 0:
            raise KLineError("eigen StartCommunication niet teruggezien (geen echo)")
        resp = raw[idx + len(req):]
        if not resp:
            raise KLineError("geen antwoord op StartCommunication")
        frames = self._split_kwp(resp)
        for f in frames:
            if f["data"][:1] == b"\xC1":
                self.mode = "kwp"
                self.target = target
                self.addr_mode = 0xC0 if functional else 0x80
                self.ecu = f["src"]
                self.keybytes = f["data"][1:3]
                self.log(f"KWP2000 gestart, ECU-adres {self.ecu:02X}, keybytes {hx(self.keybytes)}")
                return f
        raise KLineError(f"onverwacht antwoord op StartCommunication: {hx(resp)}")

    def aanknopen(self, methoden=("fast",), adres=0x33, tries=3):
        """Probeer de init-methoden in de opgegeven volgorde ("fast" = ISO 14230 fast init, "slow" = 5-baud init).

        Welke methoden een auto nodig heeft staat in zijn voertuigbestand (AANKNOPEN). Geeft de gelukte methode terug;
        mislukt alles, dan de fout van de laatste poging. De adressering blijft functioneel; overschakelen op het
        fysieke adres doet de aanroeper (use_physical) als de ECU zijn adres heeft laten zien.
        """
        fout = KLineError("geen init-methode opgegeven")
        for i, methode in enumerate(methoden):
            if i:
                time.sleep(2.6)  # ECU terug naar rust voor een andere methode
            try:
                if methode == "fast":
                    self.fast_init(adres, True, tries=tries)
                elif methode == "slow":
                    self.slow_init(adres)
                else:
                    raise KLineError(f"onbekende init-methode: {methode!r}")
                return methode
            except KLineError as e:
                fout = e
        raise fout

    def slow_init(self, address=0x33):
        """5-baud init (ISO 9141-2 / KWP2000)."""
        self.s.reset_input_buffer()
        bits = [0] + [(address >> i) & 1 for i in range(8)] + [1]
        t = time.perf_counter()
        for i, bit in enumerate(bits):
            self.s.break_condition = not bit
            if i == len(bits) - 1:
                # tijdens de stopbit de break-rommel weggooien
                _sleep_until(t + i * 0.2 + 0.15)
                self.s.reset_input_buffer()
            _sleep_until(t + (i + 1) * 0.2)
        sync = self._read(3, 1.0)
        got = time.perf_counter()
        self.log(f"5-baud init adres {address:02X} | sync+keybytes: {hx(sync)}")
        if len(sync) < 3 or sync[0] != 0x55:
            extra = self._read_gap(0.2)
            raise KLineError(f"geen geldige sync (55 KB1 KB2): {hx(sync + extra)}")
        kb1, kb2 = sync[1], sync[2]
        _sleep_until(got + 0.025)
        inv = bytes([kb2 ^ 0xFF])
        self.s.write(inv)
        self.s.flush()
        tail = self._read(2, 0.3)
        self.log(f"~KB2 {hx(inv)} gezonden | terug: {hx(tail)}")
        if len(tail) < 2 or tail[1] != (address ^ 0xFF):
            raise KLineError(f"geen geinverteerd adres terug: {hx(tail)}")
        self.keybytes = bytes([kb1, kb2])
        self.last_rx = time.perf_counter()
        if kb2 == 0x8F:  # ISO 14230: keywords 2025..2031 hebben allemaal KB2 = 8F (ISO 9141-2 geeft 08 08 of 94 94)
            self.mode = "kwp"
            self.addr_mode = 0xC0
            self.target = address
        else:
            self.mode = "iso9141"
        self.ecu = None
        self.log(f"5-baud init gelukt, keybytes {hx(self.keybytes)} -> {self.mode}")
        return self.keybytes

    # -- frames -------------------------------------------------------------
    def _build(self, data):
        if self.mode == "iso9141":
            frame = bytes([0x68, 0x6A, TESTER]) + data
        elif len(data) < 64:
            frame = bytes([self.addr_mode | len(data), self.target, TESTER]) + data
        else:
            frame = bytes([self.addr_mode, self.target, TESTER, len(data)]) + data
        return frame + bytes([checksum(frame)])

    @staticmethod
    def _split_kwp(raw):
        frames = []
        i = 0
        while i < len(raw):
            fmt = raw[i]
            n = fmt & 0x3F
            j = i + 1
            tgt = src = None
            if fmt & 0xC0:
                if j + 2 > len(raw):
                    break
                tgt, src = raw[j], raw[j + 1]
                j += 2
            if n == 0:
                if j >= len(raw):
                    break
                n = raw[j]
                j += 1
            if j + n + 1 > len(raw):
                frames.append({"raw": raw[i:], "data": raw[j:], "src": src, "tgt": tgt, "ok": False})
                break
            data = raw[j:j + n]
            cs = raw[j + n]
            frames.append({
                "raw": raw[i:j + n + 1], "data": data, "src": src, "tgt": tgt,
                "ok": cs == checksum(raw[i:j + n]),
            })
            i = j + n + 1
        return frames

    @staticmethod
    def _split_9141(raw):
        # geen lengteveld: een frame per stilte, kop 48 6B <ecu>
        if len(raw) < 5:
            return [{"raw": raw, "data": raw[3:-1], "src": None, "tgt": None, "ok": False}]
        return [{
            "raw": raw, "data": raw[3:-1], "src": raw[2], "tgt": None,
            "ok": raw[-1] == checksum(raw[:-1]),
        }]

    def use_physical(self, target=None):
        """Schakel binnen de lopende sessie over op fysieke adressering van de ECU."""
        self.target = target if target is not None else self.ecu
        self.addr_mode = 0x80

    def _read_kwp_frame(self, timeout):
        """Lees precies een KWP-frame op lengte; geeft ruwe bytes of b'' bij stilte."""
        buf = self._read(1, timeout)
        if not buf:
            return b""
        fmt = buf[0]
        if fmt & 0xC0:
            buf += self._read(2, 0.1)
        n = fmt & 0x3F
        if n == 0:
            lb = self._read(1, 0.1)
            buf += lb
            n = lb[0] if lb else 0
        buf += self._read(n + 1, 0.05 + n * 0.025)
        self.last_rx = time.perf_counter()
        return buf

    def query(self, data, timeout=0.5):
        """Snelle variant voor fysieke adressering: een verzoek, een antwoord-payload."""
        data = bytes(data)
        if self.mode == "iso9141":  # geen lengte in de kop: lezen tot de lijn stil is
            return self.request(data, timeout=timeout, quiet=True)[0]
        frame = self._build(data)
        _sleep_until(self.last_rx + 0.056)  # P3min
        self.s.reset_input_buffer()
        self._tx(frame)
        deadline = time.perf_counter() + 6.0
        while True:
            raw = self._read_kwp_frame(timeout)
            if not raw:
                raise KLineError(f"geen antwoord op {hx(data)}")
            frames = self._split_kwp(raw)
            if len(frames) != 1 or not frames[0]["ok"]:
                raise KLineError(f"checksum/lengte fout in {hx(raw)} (verzoek {hx(data)})")
            d = frames[0]["data"]
            if d[:1] == b"\x7F" and len(d) >= 3:
                if d[2] == 0x78 and time.perf_counter() < deadline:
                    timeout = 5.0
                    continue
                raise NegativeResponse(d[1], d[2])
            return d

    def request(self, data, timeout=1.0, quiet=False):
        """Stuur een service-verzoek, geef lijst met antwoord-payloads terug."""
        data = bytes(data)
        frame = self._build(data)
        _sleep_until(self.last_rx + 0.06)  # P3min
        self.s.reset_input_buffer()
        self._tx(frame)
        out = []
        pending_until = time.perf_counter() + 6.0
        first = True
        while True:
            raw = self._read_gap(first_timeout=timeout if first else 0.15, gap=0.08)
            first = False
            if not raw:
                break
            frames = self._split_kwp(raw) if self.mode == "kwp" else self._split_9141(raw)
            if not quiet:
                self.log(f"-> {hx(data):<20} <- {' | '.join(hx(f['raw']) for f in frames)}")
            again = False
            for f in frames:
                if not f["ok"]:
                    raise KLineError(f"checksum/lengte fout in {hx(f['raw'])}")
                d = f["data"]
                if d[:1] == b"\x7F" and len(d) >= 3:
                    if d[2] == 0x78 and time.perf_counter() < pending_until:
                        again = True
                        continue
                    raise NegativeResponse(d[1], d[2])
                out.append(d)
            if not again and self.mode == "kwp":
                # meerdere ECU's/frames kunnen nog volgen bij functioneel adres
                more = self._read_gap(first_timeout=0.12, gap=0.08)
                if more:
                    for f in self._split_kwp(more):
                        if not quiet:
                            self.log(f"   (vervolg)            <- {hx(f['raw'])}")
                        if f["ok"] and f["data"][:1] != b"\x7F":
                            out.append(f["data"])
                break
            if not again:
                break
        if not out:
            if not quiet:
                self.log(f"-> {hx(data):<20} <- (geen antwoord)")
            raise KLineError(f"geen antwoord op {hx(data)}")
        return out

    def stop(self):
        """StopCommunication (alleen KWP2000); ISO 9141 loopt vanzelf af na 5 s."""
        if self.mode == "kwp":
            try:
                self.request([0x82], timeout=0.5)
            except KLineError as e:
                self.log(f"StopCommunication: {e}")
