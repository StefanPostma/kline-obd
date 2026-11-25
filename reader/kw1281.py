"""KW1281 (VAG, de voorganger van KWP2000) over een domme KKL-kabel. Alleen lezen.

Voor ECU's die geen OBD2 en geen KWP2000 spreken, zoals de Digifant van de VW 1600i: wekken met een adres op 5 baud,
daarna blokken op 1200 baud waarbij de ontvanger elke byte met het complement bevestigt. Tester en ECU sturen om en om
een blok, en de tester moet binnen ruim een seconde blijven praten, anders verbreekt de ECU de sessie.

Veiligheid. Bij deze ECU's is een verzoek dat de ECU niet kent NIET onschuldig: de Digifant controleert het bereik van
de bloktitel niet, en een onbekende titel kan uitkomen bij de basisinstelling of bij schrijven naar het EEPROM (zie
docs/vw-1600i-digifant.md). Daarom:
  - dit transport kan alleen de titels in TOEGESTAAN versturen; _stuur_blok weigert al het andere vóór de eerste byte;
  - het wekt alleen op de adressen in TOEGESTANE_ADRESSEN: bij de Digifant wordt de adreswaarde door een fout in het
    programma de toestand van zijn diagnosedeel. Voor de meeste waarden (ook 0x33 van OBD2) herstart dat deel alleen,
    maar 09 tot 10 hex zetten hem midden in een blokwisseling;
  - er is geen functie "stuur titel X": alleen de benoemde leesfuncties hieronder;
  - laat nooit een script titels aflopen of proberen op zo'n ECU.

Stand: gebouwd uit de beschrijving van de firmware en getest tegen een nagebootste ECU (nep_kw1281.py). Nooit op een
echte ECU geprobeerd. De tijden en de volgorde zijn door de makers van die beschrijving tegen de firmware gelegd.
Onbewezen op de lijn: het wekken op 5 baud met deze kabel, het venster van ~80 ms voor de 0x75 en het complement per
byte via USB.
"""
import time

import kline

BAUD = 1200
ADRES = 0x01

ACK, NAK, EINDE = 0x09, 0x0A, 0x06
# de enige titels die dit transport kan versturen; alles daarbuiten wordt geweigerd voordat er iets de lijn op gaat
TOEGESTAAN = {
    0x09: "bevestiging (ACK)",
    0x06: "einde sessie",
    0x12: "tien ruwe waarden",
    0x29: "meetwaardegroep 1 t/m 8",
    0x07: "foutgeheugen lezen",
    0x08: "een byte RAM lezen",
    0x01: "tien bytes RAM lezen",
}
# het enige adres waarop dit transport wekt (motor-ECU); zie de kop voor wat een ander adres bij de Digifant doet
TOEGESTANE_ADRESSEN = {0x01}
RUST_S = 2.6        # stilte op de lijn voor het wekken; de ECU telt 1,5 tot 2,0 s
SESSIE_S = 1.0      # de ECU verbreekt na ~1,1 s stilte van de tester; daarboven sturen we niets meer in de oude sessie
BIT5 = 0.2          # 5 baud: 200 ms per bit


class KW1281Fout(kline.KLineError):
    pass


class VerbodenTitel(KW1281Fout):
    """Er is geprobeerd een titel of een wekadres te versturen dat dit transport niet toestaat. Er is niets verzonden."""


def open_poort(port, baud):
    """Seriële poort voor de kabel. De timeout staat vast: toewijzen na het openen herconfigureert de poort (zie kline.py)."""
    import serial
    s = serial.Serial()
    s.port, s.baudrate = port, baud
    s.bytesize, s.parity, s.stopbits = serial.EIGHTBITS, serial.PARITY_NONE, serial.STOPBITS_ONE
    s.timeout = 0.02
    s.dtr = True
    s.rts = False  # RTS hoog = geen ontvangst op deze kabel
    s.open()
    return s


class KW1281:
    def __init__(self, port=None, baud=BAUD, adres=ADRES, rust=None, verbose=False, serieel=None):
        self.verbose, self.adres, self.rust = verbose, adres, RUST_S if rust is None else rust
        self.t0 = time.perf_counter()
        self.s = serieel if serieel is not None else open_poort(port or kline.PORT, baud)
        self.verbonden = False
        self.teller = 0                    # blokteller van het laatste ECU-blok
        self.t_ecu_blok = 0.0              # wanneer het laatste ECU-blok compleet was
        self.t_lijn = time.perf_counter()  # laatste activiteit op de lijn (het openen van de poort geeft een puls)
        self.identiteit_blokken = []       # data van de F6-blokken die de ECU na het wekken uit zichzelf stuurt
        self.verzonden = 0                 # aantal bytes dat dit transport op de lijn zette (voor tests)

    # -- hulpfuncties -------------------------------------------------------
    def log(self, msg):
        if self.verbose:
            print(f"[{time.perf_counter() - self.t0:8.3f}] {msg}", flush=True)

    def _lees(self, n, timeout):
        end = time.perf_counter() + timeout
        buf = b""
        while len(buf) < n and time.perf_counter() < end:
            buf += self.s.read(n - len(buf))
        if buf:
            self.t_lijn = time.perf_counter()
        return buf

    def _zend(self, byte):
        """Eén byte de lijn op en de eigen echo weer van de lijn halen."""
        self.s.write(bytes([byte]))
        self.s.flush()
        self.verzonden += 1
        self.t_lijn = time.perf_counter()
        echo = self._lees(1, 0.15)
        if echo != bytes([byte]):
            raise KW1281Fout(f"echo wijkt af: gezonden {byte:02X}, terug {echo.hex(' ').upper() or '-'}")

    def _verloren(self, tekst):
        """De sessie is weg of onbruikbaar. De ECU zelf geeft hem pas op na ~1,1 s stilte, en telt de verplichte rust
        voor een nieuw adres vanaf dát moment: daarom geldt de lijn hier nog ruim een seconde als bezet."""
        self.verbonden = False
        self.t_lijn = time.perf_counter() + 1.2
        return KW1281Fout(tekst)

    # -- wekken -------------------------------------------------------------
    def wek(self):
        """Adres op 5 baud, synchronisatie 55 01 8A, bevestigen met 75, en de identiteitsblokken ontvangen."""
        if self.adres not in TOEGESTANE_ADRESSEN:
            raise VerbodenTitel(f"wekadres {self.adres:02X} is niet toegestaan (alleen "
                                f"{', '.join(f'{a:02X}' for a in sorted(TOEGESTANE_ADRESSEN))}); niets verzonden")
        self.verbonden = False
        self.identiteit_blokken = []
        wacht = self.t_lijn + self.rust - time.perf_counter()
        if wacht > 0:
            time.sleep(wacht)  # de ECU neemt pas een adres aan na ruim 2 s stilte
        bits = [0] + [(self.adres >> i) & 1 for i in range(8)] + [1]
        t = time.perf_counter()
        for i, bit in enumerate(bits):
            self.s.break_condition = not bit
            if i == len(bits) - 1:
                kline._sleep_until(t + i * BIT5 + 0.15)
                self.s.reset_input_buffer()  # de break-rommel van het adres weggooien, nog binnen de stopbit
            kline._sleep_until(t + (i + 1) * BIT5)
        t_adres = self.t_lijn = time.perf_counter()
        sync = self._lees(3, 1.5)
        self.log(f"adres {self.adres:02X} op 5 baud | terug: {sync.hex(' ').upper() or '-'}")
        if sync != b"\x55\x01\x8A":
            # heeft de ECU het adres toch aangenomen, dan geeft hij pas 0,65 s na het eind ervan op en begint dan pas
            # zijn rust te tellen: de lijn geldt daarom 0,7 s langer als bezet
            self.t_lijn = max(self.t_lijn, t_adres + 0.7)
            if not sync:
                raise KW1281Fout("geen antwoord op het adres (contact aan?)")
            raise KW1281Fout(f"onverwachte synchronisatie: {sync.hex(' ').upper()} (verwacht 55 01 8A)")
        self._zend(0x75)  # complement van 8A; moet uiterlijk ~80 ms na het eind van de 8A binnen zijn
        self.verbonden = True
        b = self._lees(1, 1.0)
        if b == b"\x55":  # de 75 kwam te laat: de ECU herhaalt 55 01 8A nog één keer
            if self._lees(2, 0.3) != b"\x01\x8A":
                raise self._verloren("onverwachte herhaling van de synchronisatie")
            self._zend(0x75)
            b = self._lees(1, 1.0)
        titel, data = self._ontvang_blok(lengtebyte=b)
        for _ in range(8):  # de ECU stuurt drie F6-blokken; elk bevestigen, daarna antwoordt hij met ACK
            if titel != 0xF6:
                break
            self.identiteit_blokken.append(data)
            titel, data = self._wissel(ACK)
        if titel != ACK:
            raise self._verloren(f"onverwacht blok na de identiteit: titel {titel:02X}")
        return self.identiteit_blokken

    # -- blokken ------------------------------------------------------------
    def _ontvang_blok(self, eerste=0.6, lengtebyte=None):
        """Eén blok van de ECU: <lengte> <teller> <titel> <data...> 03. Elke byte behalve de laatste bevestigen.

        Mist de ECU een complement van ons, dan stuurt hij het hele blok nog één keer, 0,41 s na de byte: dat herkennen
        we aan de lange stilte gevolgd door dezelfde lengtebyte, en dan beginnen we opnieuw."""
        b = self._lees(1, eerste) if lengtebyte is None else lengtebyte
        if not b:
            raise self._verloren("geen blok van de ECU")
        lengte = b[0]
        if lengte < 3:
            raise self._verloren(f"onmogelijke bloklengte {lengte:02X}")
        self._zend(lengte ^ 0xFF)
        rest, i, herhaald = bytearray(), 0, False
        while i < lengte:
            t_wacht = time.perf_counter()
            b = self._lees(1, 0.6)
            if not b:
                raise self._verloren(f"blok afgebroken na {len(rest) + 1} van {lengte + 1} bytes")
            if not herhaald and time.perf_counter() - t_wacht > 0.3 and b[0] == lengte:
                herhaald, rest, i = True, bytearray(), 0
                self.log("de ECU stuurt het blok opnieuw")
                self._zend(lengte ^ 0xFF)
                continue
            rest += b
            if i < lengte - 1:
                self._zend(b[0] ^ 0xFF)
            i += 1
        if rest[-1] != 0x03:
            raise self._verloren(f"blok eindigt niet op 03: {rest.hex(' ').upper()}")
        self.teller, titel, data = rest[0], rest[1], bytes(rest[2:-1])
        self.t_ecu_blok = time.perf_counter()
        self.log(f"<- titel {titel:02X} data {data.hex(' ').upper() or '-'}")
        return titel, data

    def _stuur_blok(self, titel, data=b""):
        """Eén blok naar de ECU. Weigert elke titel buiten TOEGESTAAN voordat er een byte verzonden is."""
        data = bytes(data)
        if titel not in TOEGESTAAN:
            raise VerbodenTitel(f"titel {titel:02X} staat niet in de lijst van toegestane leesverzoeken; niets verzonden")
        vorm = {0x29: 1, 0x08: 1, 0x01: 3}.get(titel, 0)
        if len(data) != vorm or (titel == 0x29 and not 1 <= data[0] <= 8):
            raise VerbodenTitel(f"titel {titel:02X} met data {data.hex(' ').upper() or '-'} is geen toegestane vorm; niets verzonden")
        if not self.verbonden:
            raise KW1281Fout("niet verbonden: eerst wekken")
        if time.perf_counter() - self.t_ecu_blok > SESSIE_S:
            raise self._verloren("sessie verlopen: de tester zweeg langer dan een seconde; opnieuw wekken")
        time.sleep(0.02)  # de ECU heeft net zijn laatste byte gestuurd; even de lijn laten keren
        blok = bytes([len(data) + 3, (self.teller + 1) & 0xFF, titel]) + data + b"\x03"
        self.log(f"-> titel {titel:02X} data {data.hex(' ').upper() or '-'}")
        for i, byte in enumerate(blok[:-1]):
            self._zend(byte)
            if titel == EINDE and i == 2:
                return  # de ECU verlaat de sessie zodra hij de 06 heeft: geen complement, en de 03 hoeft niet meer
            c = self._lees(1, 0.3)
            if c != bytes([byte ^ 0xFF]):
                raise self._verloren(f"geen of fout complement van de ECU op {byte:02X}: {c.hex().upper() or '-'}")
        self._zend(0x03)

    def _wissel(self, titel, data=b""):
        """Eén blok sturen en het antwoordblok ontvangen."""
        self._stuur_blok(titel, data)
        antwoord, d = self._ontvang_blok()
        if antwoord == NAK:
            raise KW1281Fout(f"de ECU weigert titel {titel:02X} (NAK)")
        return antwoord, d

    def _verwacht(self, titel, data, antwoordtitel, lengte):
        antwoord, d = self._wissel(titel, data)
        if antwoord != antwoordtitel or len(d) != lengte:
            raise KW1281Fout(f"onverwacht antwoord op titel {titel:02X}: titel {antwoord:02X}, {len(d)} bytes")
        return d

    # -- de benoemde leesfuncties (het enige wat een aanroeper kan vragen) ----
    def houd_wakker(self):
        """Niets te vragen: een ACK houdt de sessie open."""
        antwoord, _ = self._wissel(ACK)
        if antwoord != ACK:
            raise KW1281Fout(f"onverwacht antwoord op ACK: titel {antwoord:02X}")

    def tien_waarden(self):
        """Titel 12: de tien ruwe waarden (bij VAG-COM 'groep 000'). Geeft 10 bytes."""
        return self._verwacht(0x12, b"", 0xF4, 10)

    def groep(self, n):
        """Titel 29: meetwaardegroep n (1 t/m 8). Geeft 4 bytes.

        De eerste 29 na een andere titel beantwoordt de ECU met de kop (titel 02) in plaats van met data: dan nog
        een keer vragen. De kop zelf gebruiken we niet."""
        for _ in range(2):
            antwoord, d = self._wissel(0x29, bytes([n]))
            if antwoord == 0xF4 and len(d) == 4:
                return d
            if antwoord != 0x02:
                raise KW1281Fout(f"onverwacht antwoord op groep {n}: titel {antwoord:02X}, {len(d)} bytes")
        raise KW1281Fout(f"groep {n}: de ECU blijft de kop sturen in plaats van data")

    def fouten(self):
        """Titel 07: het foutgeheugen. Geeft [(code, status), ...]; een lege lijst betekent geen fout opgeslagen.

        Het geheugen heeft zeven plekken, vier per blok: hooguit twee FC-blokken, elk te bevestigen met een ACK."""
        uit = []
        antwoord, d = self._wissel(0x07)
        for _ in range(8):  # meer fouten dan in een blok passen: de ECU gaat verder na een ACK
            if antwoord != 0xFC:
                break
            uit += [(int.from_bytes(d[i:i + 2], "big"), d[i + 2]) for i in range(0, len(d) - 2, 3)]
            antwoord, d = self._wissel(ACK)
        if antwoord != ACK:
            raise KW1281Fout(f"onverwacht antwoord bij het foutgeheugen: titel {antwoord:02X}")
        return [(code, status) for code, status in uit if (code, status) != (0xFFFF, 0x88)]

    def ram_byte(self, adres):
        """Titel 08: één byte uit RAM-pagina nul."""
        return self._verwacht(0x08, bytes([adres & 0xFF]), 0xFB, 2)[1]

    def ram_venster(self, adres):
        """Titel 01: tien opeenvolgende bytes uit RAM-pagina nul vanaf `adres`."""
        return self._verwacht(0x01, bytes([0x0A, 0x00, adres & 0xFF]), 0xFE, 10)

    def lees(self, soort, *args):
        """Een benoemde lezing uit het voertuigbestand uitvoeren: ("tien_waarden",), ("groep", n), ("ram_venster", adres)."""
        functie = {"tien_waarden": self.tien_waarden, "groep": self.groep, "ram_venster": self.ram_venster}.get(soort)
        if functie is None:
            raise VerbodenTitel(f"onbekende lezing {soort!r}; niets verzonden")
        return functie(*args)

    # -- afsluiten ----------------------------------------------------------
    def stop(self):
        """Sessie netjes beëindigen (titel 06); de ECU antwoordt daar niet op."""
        if self.verbonden and time.perf_counter() - self.t_ecu_blok <= SESSIE_S:
            try:
                self._stuur_blok(EINDE)
            except KW1281Fout:
                pass
        self.verbonden = False

    def close(self):
        self.s.close()
