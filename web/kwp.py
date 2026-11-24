"""K-line transport voor OBD2 (ISO 14230-4 KWP2000 en ISO 9141-2) via een FTDI KKL-kabel.

De K-line is één draad: alles wat we versturen komt als echo terug op RX. Die echo gebruiken
we als controle dat de kabel echt op de K-line zit (NoEcho = kabel/voeding/protocol-probleem).
"""
import time

BAUD = 10400
ECU_ADDR = 0x33      # functioneel adres voor OBD2
TESTER_ADDR = 0xF1
P3_MIN = 0.055       # minimale pauze tussen antwoord van de ECU en het volgende verzoek
P4 = 0.005           # pauze tussen tester-bytes (ISO 14230-4 / 9141-2: 5..20 ms)
TICK = 0.02          # vaste leestimeout van de poort; wachttijden houden we zelf bij (zie KLine._read)
ISO9141_KEYBYTES = {(0x08, 0x08), (0x94, 0x94)}
# (laag, hoog) in ms voor de fast-init wekpuls; ISO zegt 25/25. Volgorde: eerst de combinaties die op de
# testkabel (FT232R, macOS) een antwoord van de ECU gaven.
FAST_TIMINGS = [(20, 20), (30, 40), (35, 40), (25, 25), (25, 40), (20, 40), (30, 25), (22, 30)]
_last_good = None        # laatst gelukte combinatie; blijft bewaard over herverbindingen


class KLineError(Exception):
    pass


class NoEcho(KLineError):
    """Verstuurde bytes komen niet terug: de K-line zit niet (werkend) op de FTDI."""


class NoResponse(KLineError):
    """Echo is er, maar de ECU zwijgt (contact uit, ander protocol, sessie verlopen)."""


class BadFrame(KLineError):
    pass


class ChecksumError(BadFrame):
    """Antwoord is binnen maar de checksum klopt niet (ruis op de lijn). Bevat het ruwe frame."""

    def __init__(self, frame, head):
        super().__init__(f"checksum fout: {frame.hex(' ')}")
        self.frame = frame
        self.payload = frame[head:-1]


class Negative(KLineError):
    def __init__(self, service, code):
        super().__init__(f"negatief antwoord op service {service:02X}: code {code:02X}")
        self.service = service
        self.code = code


def open_port(path, dtr=None, rts=None):
    """Open de kabel met DTR en RTS standaard UIT, al vóór het openen.

    Gemeten op de gebruikte kabel (FT232R): met RTS aan (de standaard van pyserial) verdwijnt de K-line-echo
    volledig; met RTS uit echoot elke byte. Overschrijven kan met dtr=1 / rts=1 (--dtr/--rts)."""
    import serial
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.bytesize, ser.parity, ser.stopbits = path, BAUD, 8, "N", 1
    ser.timeout = TICK
    ser.dtr = bool(dtr) if dtr is not None else False
    ser.rts = bool(rts) if rts is not None else False
    ser.open()
    return ser


class KLine:
    def __init__(self, ser, log=lambda *a: None, debug=False):
        self.ser = ser
        if ser.timeout != TICK:
            ser.timeout = TICK
        self.log = log
        self.debug = debug       # log elke TX/RX-frame als hex
        self.proto = None        # "kwp" of "iso9141"
        self.keybytes = None
        self.init_method = None
        self.check_echo = True   # False voor kabels die niet echoën (gescheiden TX/RX)
        self.timing_used = None  # welke wekpuls de ECU wakker maakte
        self.fast_attempts = 16  # aantal wekpuls-pogingen voordat we opgeven (kans per poging is ~1 op 4)
        self.checksum_errors = 0  # teller voor de linkkwaliteit
        self.p4 = P4
        self._last_rx = 0.0

    # ---------- framing ----------
    @staticmethod
    def _checksum(b):
        return sum(b) & 0xFF

    def _frame(self, data):
        data = bytes(data)
        if self.proto == "iso9141":
            body = bytes([0x68, 0x6A, TESTER_ADDR]) + data
        else:
            body = bytes([0xC0 | len(data), ECU_ADDR, TESTER_ADDR]) + data
        return body + bytes([self._checksum(body)])

    def _read(self, n, timeout):
        """Lees maximaal n bytes binnen `timeout` seconden.

        De timeout van de poort blijft vast (TICK): pyserial herconfigureert de poort bij elke toewijzing aan
        `timeout`, en bij 10400 baud zet dat op macOS de snelheid eventjes op 38400. Bytes die dan binnenkomen
        raken beschadigd (gemeten: 37 van 120 blokverzoeken goed met, 120 van 120 zonder herconfiguratie)."""
        end = time.monotonic() + timeout
        buf = b""
        while len(buf) < n:
            buf += self.ser.read(n - len(buf))
            if time.monotonic() >= end:
                break
        return buf

    def _send(self, frame):
        wait = self._last_rx + P3_MIN - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        s = self.ser
        s.reset_input_buffer()
        for i, b in enumerate(frame):               # byte voor byte met P4-pauze, de echo lezen we pas aan het eind
            s.write(bytes([b]))
            if i < len(frame) - 1 and self.p4:
                time.sleep(self.p4)
        s.flush()
        if not self.check_echo:
            if self.debug:
                self.log(f"TX {frame.hex(' ')} (zonder echo-controle)")
            return
        echo = self._read(len(frame), 0.3)
        if self.debug:
            self.log(f"TX {frame.hex(' ')}  echo {bytes(echo).hex(' ') or '-'}")
        if bytes(echo) != frame:
            # Een echo begint altijd met ons eigen eerste byte. Iets anders (of niets) = de kabel echoot niet.
            if not echo or echo[0] != frame[0]:
                raise NoEcho("geen echo: K-line niet bereikbaar via de kabel, of de kabel echoot niet")
            raise BadFrame(f"echo wijkt af: verstuurd {frame.hex(' ')} terug {bytes(echo).hex(' ')}")

    def _recv(self, timeout, expect=None):
        first = self._read(1, timeout)
        if not first:
            raise NoResponse("geen antwoord van de ECU")
        if self.proto == "iso9141":
            buf = bytearray(first)
            if expect:
                need = 3 + expect + 1
                buf += self._read(need - 1, 0.15)
                if len(buf) != need:
                    raise BadFrame("onvolledig antwoord")
            else:
                while True:
                    chunk = self._read(32, 0.04)
                    if not chunk:
                        break
                    buf += chunk
            frame = bytes(buf)
            if len(frame) < 5 or frame[0] != 0x48 or frame[1] != 0x6B:
                raise BadFrame(f"onverwachte header: {frame.hex(' ')}")
            head = 3
        else:
            fmt = first[0]
            head_len = 3 if fmt & 0xC0 else 1
            head = first + self._read(head_len - 1, 0.15)
            if len(head) != head_len:
                raise BadFrame("onvolledige header")
            n = fmt & 0x3F
            if n == 0:
                lb = self._read(1, 0.15)
                if not lb:
                    raise BadFrame("lengtebyte ontbreekt")
                head += lb
                n = lb[0]
            body = self._read(n + 1, 0.15)
            if len(body) != n + 1:
                raise BadFrame("onvolledig antwoord")
            frame = bytes(head) + body
            head = len(head)
        if self.debug:
            self.log(f"RX {frame.hex(' ')}")
        self._last_rx = time.monotonic()
        if self._checksum(frame[:-1]) != frame[-1]:
            raise ChecksumError(frame, head)
        return frame[head:-1]

    def request(self, data, expect=None, timeout=0.5, retries=2):
        """Stuur data (service + parameters) en geef het payload van het antwoord terug.

        expect = verwachte payloadlengte; alleen nodig voor ISO 9141 (daar staat geen lengte in de header).
        Bij een checksumfout (ruis) wordt het verzoek tot `retries` keer opnieuw verstuurd: de ECU stuurt
        een verloren antwoord niet uit zichzelf nog eens.
        """
        frame = self._frame(data)
        attempt = 0
        self._send(frame)
        while True:
            try:
                payload = self._recv(timeout, expect)
            except ChecksumError:
                self.checksum_errors += 1
                if attempt >= retries:
                    raise
                attempt += 1
                if self.debug:
                    self.log("checksum fout, verzoek opnieuw")
                self._send(frame)
                continue
            if payload[:1] == b"\x7f" and len(payload) >= 3:
                if payload[2] == 0x78:      # response pending: ECU is nog bezig
                    timeout = 5.0
                    continue
                raise Negative(payload[1], payload[2])
            return payload

    def request_all(self, data, expect=None, timeout=0.5, gap=0.25):
        """Als request(), maar verzamelt alle antwoordberichten (bv. VIN = 5 berichten van de ECU)."""
        frames = [self.request(data, expect=expect, timeout=timeout)]
        while True:
            try:
                frames.append(self._recv(gap, expect))
            except (NoResponse, BadFrame):
                return frames

    # ---------- initialisatie ----------
    def start_fast(self, method="break"):
        """ISO 14230-4 fast init: wekpuls (25 ms laag + 25 ms hoog), dan StartCommunication.

        De puls wordt via TIOCSBRK/TIOCCBRK gemaakt en de USB-vertraging van Apple's FTDI-driver (enkele ms,
        wisselend) maakt de breedte onbetrouwbaar; de ECU is daar kieskeurig over. Daarom proberen we een reeks
        (laag, hoog)-tijden en onthouden we wat werkte (gemeten: ~1 op 5 pogingen met 25/25 ms)."""
        global _last_good
        s = self.ser
        s.baudrate = BAUD
        self.proto = "kwp"
        if method == "byte":
            # Alternatief: 0x00 op 300 baud = 9 bits x 3,33 ms = 30 ms laag, hardwarematig getimed.
            # (360 baud wordt door de Apple-driver niet gehaald; 300 is een standaardsnelheid.)
            # Gemeten op de testkabel: ECU reageerde hier niet op; daarom niet in de standaardvolgorde.
            s.reset_input_buffer()
            time.sleep(0.35)
            t0 = time.monotonic()
            s.baudrate = 300
            s.write(b"\x00")
            s.flush()
            time.sleep(max(0.0, 0.05 - (time.monotonic() - t0)))
            s.baudrate = BAUD
            s.reset_input_buffer()
            self._last_rx = 0.0
            payload = self.request(b"\x81", timeout=0.3)
            self.timing_used = "0x00 op 300 baud"
        else:
            timings = list(FAST_TIMINGS)
            if _last_good in timings:
                timings.remove(_last_good)
                timings.insert(0, _last_good)
            timings = (timings * (self.fast_attempts // len(timings) + 1))[:self.fast_attempts]
            last_err = None
            for low, high in timings:
                s.reset_input_buffer()
                time.sleep(0.35)                       # W5: bus minimaal 300 ms rustig
                s.break_condition = True
                time.sleep(low / 1000)
                s.break_condition = False
                time.sleep(high / 1000)
                s.reset_input_buffer()
                self._last_rx = 0.0
                try:
                    payload = self.request(b"\x81", timeout=0.3, retries=0)   # StartCommunication
                except ChecksumError as e:
                    # ECU antwoordde wel; de inhoud is voorspelbaar, dus een aangetast checksum-byte telt als wakker
                    if e.payload[:1] != b"\xC1" or len(e.payload) < 3:
                        last_err = e
                        continue
                    payload = e.payload
                except (NoResponse, BadFrame) as e:
                    last_err = e
                    if self.debug:
                        self.log(f"wekpuls {low}/{high} ms: geen geldig antwoord ({e})")
                    continue
                _last_good = (low, high)
                self.timing_used = f"wekpuls {low}/{high} ms"
                break
            else:
                raise last_err or NoResponse("geen antwoord op StartCommunication")
        if payload[:1] != b"\xC1" or len(payload) < 3:
            raise BadFrame(f"onverwacht antwoord op StartCommunication: {payload.hex(' ')}")
        self.keybytes = (payload[1], payload[2])
        self.init_method = f"fast-{method}"

    def start_slow(self, addr=ECU_ADDR):
        """5-baud init (ISO 9141-2 / ISO 14230-2): adres met 5 bit/s uitsturen via break."""
        s = self.ser
        s.baudrate = BAUD
        s.reset_input_buffer()
        time.sleep(0.35)
        bits = [0] + [(addr >> i) & 1 for i in range(8)] + [1]      # start, LSB eerst, stop
        t = time.monotonic()
        for bit in bits:
            s.break_condition = (bit == 0)
            t += 0.2
            d = t - time.monotonic()
            if d > 0:
                time.sleep(d)
        s.break_condition = False
        s.reset_input_buffer()
        sync = self._read(1, 0.5)
        if not sync:
            raise NoResponse("geen sync-byte na 5-baud init")
        if sync != b"\x55":
            raise BadFrame(f"sync-byte {sync.hex()} in plaats van 55 (andere baudrate?)")
        kb = self._read(2, 0.5)
        if len(kb) < 2:
            raise BadFrame("keybytes onvolledig")
        time.sleep(0.04)
        s.write(bytes([kb[1] ^ 0xFF]))
        s.flush()
        if self.check_echo:
            self._read(1, 0.5)                       # echo van ons eigen byte
        inv = self._read(1, 0.5)
        if inv != bytes([addr ^ 0xFF]):
            raise BadFrame(f"verwacht {addr ^ 0xFF:02X} van ECU, kreeg {inv.hex() or 'niets'}")
        self.keybytes = (kb[0], kb[1])
        self.proto = "iso9141" if self.keybytes in ISO9141_KEYBYTES else "kwp"
        self.init_method = "slow-5baud"
        self._last_rx = time.monotonic()

    def connect(self, methods=("fast-break", "slow")):
        """Probeer de init-methodes na elkaar.

        Komt er geen echo, dan proberen we één keer opnieuw zonder echo-controle: sommige kabels
        (gescheiden TX/RX) echoën niet maar werken wel. Zwijgt de ECU dan ook, dan is het NoEcho.
        """
        self.check_echo = True
        try:
            return self._connect(methods)
        except NoEcho as first:
            self.log("geen echo op de K-line; nog één poging zonder echo-controle")
            self.check_echo = False
            try:
                m = self._connect(methods)
                self.log("LET OP: werkt zonder echo (kabel met gescheiden TX/RX?)")
                return m
            except KLineError:
                self.check_echo = True
                raise first

    def _connect(self, methods):
        errors = []
        for m in methods:
            try:
                if m == "fast-break":
                    self.start_fast("break")
                elif m == "fast-byte":
                    self.start_fast("byte")
                else:
                    self.start_slow()
                self.log(f"init gelukt via {self.init_method}"
                         f"{f' ({self.timing_used})' if self.timing_used else ''}, protocol {self.proto}, "
                         f"keybytes {self.keybytes[0]:02X} {self.keybytes[1]:02X}")
                return self.init_method
            except NoEcho:
                raise
            except KLineError as e:
                errors.append(f"{m}: {e}")
            except (ValueError, OSError) as e:       # bv. baudrate 360 of break niet ondersteund
                errors.append(f"{m}: {type(e).__name__}: {e}")
            self.proto = None
            time.sleep(0.5)
        raise NoResponse("; ".join(errors))
