"""Nagebootste Digifant-ECU (VW 1600i) op draadniveau, voor tests van kw1281.py zonder auto en zonder kabel.

NepPoort heeft de vorm van een pyserial-poort (write/read/break_condition/...). Erachter zit een ECU die doet wat de
beschrijving van de firmware zegt (docs/vw-1600i-digifant.md); de tijden zijn door de makers van die beschrijving tegen
het programma gelegd:
  - een adres pas aannemen na 2 s stilte, alleen adres 0x01 op 5 baud;
  - 55 01 8A, compleet 2,16 s na de startflank; de 75 moet uiterlijk 80 ms na de 8A binnen zijn, anders één herhaling,
    en 0,65 s na het eind van het adres geeft de ECU op;
  - drie identiteitsblokken, complement per byte (8 tot 16 ms na de byte), om en om een blok;
  - de kop van een meetwaardegroep is 50 bytes op de lijn (groep 3: 67, groep 8: 77);
  - mist de ECU een complement, dan stuurt hij het blok nog één keer, 0,41 s na de byte;
  - de tester moet binnen 1,13 s aan zijn blok beginnen, anders is de sessie weg;
  - titel 06 beëindigt de sessie op de titelbyte: geen complement, en de 03 wordt niet afgewacht.
De kabel echoot alles wat de tester zendt.

De ECU hier FAALT LUID (VerbodenVerzoek) op elke titel die nooit verstuurd mag worden (11, 04, 05, 10, 1A en alles
vanaf 30) en op elk ander wekadres dan 01. Een tester die titels of adressen afloopt, komt dus niet door de tests.

Identiteit, codering en meetwaarden zijn verzonnen of algemeen (een standaard-B-ECU); er staat geen echte auto in.
"""
import time

NOOIT = {0x11: "basisinstelling", 0x04: "stelgliedtest", 0x05: "foutgeheugen wissen", 0x10: "hercoderen",
         0x1A: "EEPROM schrijven"}
BYTE_S = 10 / 1200          # één byte op 1200 baud
T_COMPLEMENT = 0.012        # de ECU bevestigt een byte 8 tot 16 ms na de byte
T_VOLGENDE = 0.012          # en stuurt zijn volgende byte kort na het complement van de tester
T_ANTWOORD = 0.049          # de ECU begint zijn blok 49 ms na het blok van de tester
T_WACHT = 0.205             # zo lang wacht de ECU op een complement of op de volgende byte van een blok
T_HERHAAL = 0.41            # een blok waarvan een complement uitbleef, komt 0,41 s na die byte opnieuw
T_SESSIE = 1.13             # binnen deze tijd na een ECU-blok moet de tester aan zijn blok beginnen
T_75 = 0.08                 # de 75 moet uiterlijk 80 ms na het eind van de 8A compleet binnen zijn
KOPLENGTE = {3: 63, 8: 73}  # databytes in de kop van een groep; de andere groepen 46 (op de lijn 4 bytes meer)


class VerbodenVerzoek(AssertionError):
    """De tester stuurde een titel of een wekadres dat op deze ECU nooit verstuurd mag worden."""


class NepPoort:
    def __init__(self, rust_s=2.0, fouten=(), waarden=None, laat_complement_vallen=0, mis_complement=0, doof_voor_75=0):
        self.baudrate, self.timeout, self.dtr, self.rts, self.is_open = 1200, 0.02, None, None, True
        self.rust_s = rust_s
        self.fouten = list(fouten)                      # [(code, status)]
        # tien ruwe waarden bij warm stationair, binnen het "normaal"-bereik uit de beschrijving
        self.waarden = bytes(waarden or [43, 145, 23, 88, 133, 0, 0, 64, 14, 199])
        self.ram = bytearray(range(256))                # RAM-pagina nul: herkenbare inhoud, met een paar echte plekken
        self.ram[0x2E], self.ram[0x35] = 0x80, 210      # toestand stationair; ontstekingstelling (~4 graden voor BDP)
        self.ram[0x95], self.ram[0x96], self.ram[0x9B], self.ram[0x9D] = 236, 52, 128, 20
        self.laat_complement_vallen = laat_complement_vallen   # >0: zoveelste complement van de ECU blijft weg
        self.mis_complement = mis_complement            # >0: de ECU mist het zoveelste complement van de tester
        self.doof_voor_75 = doof_voor_75                # zoveel keer de 75 niet horen (alsof hij te laat kwam)
        self.overtredingen = []                         # titels en adressen die nooit mogen en toch kwamen
        self.gezien = []                                # alle titels die de tester stuurde, in volgorde
        self.adressen = []                              # ontvangen 5-baud-adressen (ook genegeerde)
        self.sessies = self.verbroken = self.bytes_van_tester = self.herhaalde_blokken = 0
        self._rx, self._seq = [], 0                     # (beschikbaar_vanaf, volgnr, byte)
        self._brk, self._randen, self._start, self._t_adres = False, [], None, 0.0
        self._activiteit = time.monotonic()             # vanaf wanneer de ECU zijn rust telt
        self._staat = "rust"                            # rust | wacht_75 | ecu_zendt | wacht_blok | ontvangt
        self._deadline = None
        self._herhaald = False
        self._wachtrij = []                             # nog te sturen identiteits- of foutblokken
        self._uit, self._uit_i, self._uit_opnieuw, self._t_byte = b"", 0, False, 0.0
        self._in = bytearray()
        self._teller = 0x3D                             # "whatever counter it had left"
        self._vorige_titel = None
        self._complementen = self._van_tester_complementen = 0

    # ----- de lijn -----
    @property
    def break_condition(self):
        return self._brk

    @break_condition.setter
    def break_condition(self, laag):
        laag = bool(laag)
        now = time.monotonic()
        self._poll(now)
        if laag == self._brk:
            return
        if laag and self._start is None and self._staat == "rust":
            # een neergaande flank is het begin van een adres, maar alleen na genoeg stilte
            self._start = now if now - self._activiteit >= self.rust_s else -1.0
            self._randen = []
            self._t_adres = now
        self._randen.append((now, laag))
        self._brk = laag

    def _niveau_laag(self, t):
        laag = False
        for tijd, staat in self._randen:
            if tijd <= t:
                laag = staat
        return laag

    def _push(self, t, byte):
        self._seq += 1
        self._rx.append((t, self._seq, byte))
        self._rx.sort()

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        now = time.monotonic()
        self._poll(now)
        self._rx = [x for x in self._rx if x[0] > now]

    def read(self, n=1):
        deadline = time.monotonic() + (self.timeout or 0)
        out = bytearray()
        while True:
            now = time.monotonic()
            self._poll(now)
            while self._rx and len(out) < n and self._rx[0][0] <= now:
                out.append(self._rx.pop(0)[2])
            if len(out) >= n or now >= deadline:
                return bytes(out)
            time.sleep(0.0005)

    def write(self, data):
        for byte in bytes(data):
            now = time.monotonic()
            self._poll(now)
            self.bytes_van_tester += 1
            self._push(now + BYTE_S, byte)              # de kabel echoot wat de tester zendt
            if self._staat == "rust":
                self._activiteit = now + BYTE_S         # elke ontvangen byte begint de rusttelling opnieuw
            self._van_tester(byte, now + BYTE_S)
        return len(data)

    # ----- de ECU -----
    def _poll(self, now):
        if self._start is not None and now >= self._t_adres + 2.0:      # adres compleet: start, 8 bits, stop
            geldig, self._start = self._start >= 0, None
            adres = sum((0 if self._niveau_laag(self._t_adres + 0.1025 + 0.205 * (i + 1)) else 1) << i for i in range(8))
            self.adressen.append((adres, geldig))
            if not geldig:
                self._activiteit = self._t_adres + 2.0  # te vroeg: de flanken tellen als activiteit, de rust begint opnieuw
            elif adres == 0x01:
                self._herhaald = False
                self._sync(self._t_adres + 2.15)
            else:
                self.overtredingen.append(("adres", adres))
                raise VerbodenVerzoek(f"wekadres {adres:02X}: het KW1281-transport hoort alleen op adres 01 te wekken (bij de "
                                      "echte ECU wordt een ander adres de toestand van zijn diagnosedeel)")
        if self._staat == "wacht_75" and now > self._deadline:
            if not self._herhaald:                      # geen 75 op tijd: 55 01 8A nog één keer
                self._herhaald = True
                self._sync(self._deadline + 0.1)
            else:                                       # opgeven: 0,65 s na het eind van het adres begint de rust
                self._staat, self._deadline = "rust", None
                self._activiteit = self._t_adres + 2.0 + 0.65
        if self._staat == "ecu_zendt" and self._deadline is not None and now > self._deadline:
            if not self._uit_opnieuw:                   # complement bleef uit: het hele blok nog één keer
                self._uit_opnieuw, self._uit_i = True, 0
                self.herhaalde_blokken += 1
                self._stuur_byte(self._t_byte + T_HERHAAL)
            else:
                self._verbreek()
        if self._staat in ("wacht_blok", "ontvangt") and now > self._deadline:
            self._verbreek()

    def _sync(self, t):
        self._push(t, 0x55)
        self._push(t + 0.041, 0x01)
        self._push(t + 0.041 + BYTE_S, 0x8A)
        self._staat, self._deadline = "wacht_75", t + 0.041 + BYTE_S + T_75

    def _verbreek(self):
        if self._staat != "rust":
            self.verbroken += 1
        # de sessie viel weg op het moment van de deadline (plus 0,2 s), niet pas nu iemand weer naar de poort kijkt
        self._activiteit = (self._deadline + 0.2) if self._deadline is not None else time.monotonic()
        self._staat, self._deadline, self._in = "rust", None, bytearray()

    def _begin_blok(self, titel, data, t):
        self._teller = (self._teller + 1) & 0xFF
        self._uit = bytes([len(data) + 3, self._teller, titel]) + bytes(data) + b"\x03"
        self._uit_i, self._uit_opnieuw, self._staat = 0, False, "ecu_zendt"
        self._stuur_byte(t)

    def _stuur_byte(self, t):
        self._push(t, self._uit[self._uit_i])
        self._t_byte = t
        if self._uit_i == len(self._uit) - 1:           # de 03: geen complement, nu is de tester aan de beurt
            self._staat, self._deadline = "wacht_blok", t + T_SESSIE
        else:
            self._deadline = t + T_WACHT

    def _van_tester(self, byte, t):
        if self._staat == "wacht_75":
            if byte != 0x75 or t > self._deadline:
                return
            if self.doof_voor_75 > 0:
                self.doof_voor_75 -= 1
                return
            self.sessies += 1
            self._wachtrij = [bytes([0xF6]) + b"043906022B  ",
                              bytes([0xF6]) + b"DIGIFANT 1.8        0000",
                              bytes([0xF6]) + bytes([0x00, 0x00, 0x00, 0x00])]
            blok = self._wachtrij.pop(0)
            return self._begin_blok(blok[0], blok[1:], t + T_ANTWOORD)
        if self._staat == "ecu_zendt":
            if byte == self._uit[self._uit_i] ^ 0xFF:
                self._van_tester_complementen += 1
                if self._van_tester_complementen == self.mis_complement:
                    return                              # alsof het complement verloren ging: de deadline loopt door
                self._uit_i += 1
                self._stuur_byte(t + T_VOLGENDE)
            return
        if self._staat == "wacht_blok":
            self._staat, self._in = "ontvangt", bytearray()
        if self._staat != "ontvangt":
            return                                      # geen sessie: de ECU luistert niet naar 1200 baud
        self._in.append(byte)
        self._deadline = t + T_WACHT
        if len(self._in) == 3 and byte == 0x06:         # einde sessie: op de titelbyte, zonder complement, zonder 03
            self.gezien.append(0x06)
            self._staat, self._deadline, self._in, self._activiteit = "rust", None, bytearray(), t
            return
        compleet = len(self._in) >= 2 and len(self._in) == self._in[0] + 1
        if not compleet:
            self._complementen += 1
            if self._complementen != self.laat_complement_vallen:
                self._push(t + T_COMPLEMENT, byte ^ 0xFF)
            return
        self._teller, titel, data = self._in[1], self._in[2], bytes(self._in[3:-1])
        self._in = bytearray()
        self._behandel(titel, data, t + T_ANTWOORD)

    def _kop(self, n):
        return bytes((7 * i + n) & 0xFF for i in range(KOPLENGTE.get(n, 46)))

    def _behandel(self, titel, data, t):
        self.gezien.append(titel)
        if titel in NOOIT or titel >= 0x30:
            self.overtredingen.append(titel)
            raise VerbodenVerzoek(f"titel {titel:02X} ({NOOIT.get(titel, 'niet gecontroleerd door de ECU')}) "
                                  "mag nooit naar deze ECU")
        vorige, self._vorige_titel = self._vorige_titel, titel
        if titel == 0x09:                               # ACK: volgende identiteits- of foutblok, anders ACK terug
            if self._wachtrij:
                blok = self._wachtrij.pop(0)
                return self._begin_blok(blok[0], blok[1:], t)
            return self._begin_blok(0x09, b"", t)
        if titel == 0x12:
            return self._begin_blok(0xF4, self.waarden, t)
        if titel == 0x29:
            n = data[0] if data else 1
            if vorige != 0x29:                          # eerste 29 na een andere titel: de kop, geen data
                return self._begin_blok(0x02, self._kop(n), t)
            return self._begin_blok(0xF4, bytes([self.waarden[2], self.waarden[9], 0x10 + n, 0x20 + n]), t)
        if titel == 0x02:
            return self._begin_blok(0x02, self._kop(1), t)
        if titel == 0x07:                               # zeven plekken, vier per blok: hooguit twee FC-blokken
            def pak(lijst):
                return b"".join(code.to_bytes(2, "big") + bytes([status]) for code, status in lijst)
            self._wachtrij = [bytes([0xFC]) + pak(self.fouten[4:7])] if len(self.fouten) > 4 else []
            return self._begin_blok(0xFC, pak(self.fouten[:4]) or b"\xFF\xFF\x88", t)
        if titel == 0x08:
            return self._begin_blok(0xFB, bytes([0x00, self.ram[data[0]]]), t)
        if titel in (0x01, 0x03):
            lo = data[2] if len(data) >= 3 else 0
            return self._begin_blok(0xFE, bytes(self.ram[(lo + i) & 0xFF] for i in range(10)), t)
        if titel == 0x19:
            return self._begin_blok(0xEF, bytes(12), t)
        if titel == 0x00:
            self._wachtrij = [bytes([0xF6]) + b"DIGIFANT 1.8        0000", bytes([0xF6]) + bytes(4)]
            return self._begin_blok(0xF6, b"043906022B  ", t)
        return self._begin_blok(0x0A, bytes([self._teller]), t)      # niet geïmplementeerd: NAK


def open_poort(port=None, baud=1200):
    """Vervanger van kw1281.open_poort in tests."""
    return NepPoort()
