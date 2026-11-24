"""KWP2000 (ISO 14230) frames uit een bytestroom halen en verzoeken aan antwoorden koppelen.

Gebruikt door luister.py (meeluisteren met een tester via een passieve OBD-splitter) en voor het herlezen van een
opname. Knipt op lengte en controlesom, niet op stiltes: zo blijven een verzoek en zijn snelle antwoord uit elkaar.
Puur ontleden; opent geen kabel en verzendt niets.

Frameopbouw (ISO 14230-2):
    [fmt] [doel] [bron] [len?] [SID + data ...] [controlesom]
  - fmt bit7 = adresbytes aanwezig (doel/bron), bit6 = functioneel (1) of fysiek (0); bits5..0 = lengte
  - lengte 0 in fmt => aparte lengtebyte na de kop
  - controlesom = som van alle voorgaande bytes & 0xFF
"""

TESTER = 0xF1  # bron van een verzoek; doel van een antwoord

SERVICES = {
    0x10: "startDiagnosticSession", 0x11: "ecuReset", 0x14: "clearDiagnosticInformation",
    0x18: "readDTCByStatus", 0x1A: "readEcuIdentification", 0x20: "stopDiagnosticSession",
    0x21: "readDataByLocalId", 0x22: "readDataByCommonId", 0x23: "readMemoryByAddress",
    0x27: "securityAccess", 0x28: "disableNormalMsgTransmission", 0x2C: "dynamicallyDefineLocalId",
    0x2F: "inputOutputControlByLocalId", 0x30: "inputOutputControl (GM?)", 0x31: "startRoutineByLocalId",
    0x32: "stopRoutineByLocalId", 0x33: "requestRoutineResultsByLocalId", 0x34: "requestDownload",
    0x35: "requestUpload", 0x36: "transferData", 0x37: "requestTransferExit", 0x3B: "writeDataByLocalId",
    0x3D: "writeMemoryByAddress", 0x3E: "testerPresent", 0x81: "startCommunication", 0x82: "stopCommunication",
    0x83: "accessTimingParameters", 0x7F: "negatiefAntwoord",
}

# de commando's die we willen opvangen: actuatoren aansturen, routines, beveiliging, wissen en schrijven
ACTUATOR = {0x2F, 0x30, 0x31, 0x32, 0x33}
BEVEILIGING = {0x27}
SCHRIJVEN = {0x14, 0x3B, 0x3D, 0x34, 0x35, 0x36, 0x37}
OPVALLEND = ACTUATOR | BEVEILIGING | SCHRIJVEN | {0x10, 0x11}

NRC = {
    0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported/invalidFormat",
    0x21: "busy-repeatRequest", 0x22: "conditionsNotCorrect", 0x23: "routineNotComplete",
    0x31: "requestOutOfRange", 0x33: "securityAccessDenied", 0x35: "invalidKey", 0x36: "exceedNumberOfAttempts",
    0x78: "responsePending", 0x80: "serviceNotSupportedInActiveSession",
}


def _soort_sid(sid):
    if sid in ACTUATOR:
        return "actuator"
    if sid in BEVEILIGING:
        return "beveiliging"
    if sid in SCHRIJVEN:
        return "schrijven/wissen"
    return ""


class Ontleder:
    """Voed bytes met een tijdstempel; haal complete frames op. Herstelt de synchronisatie bij ruis of een mis-knip."""

    def __init__(self):
        self.buf = bytearray()
        self.t = []  # tijdstempel per byte

    def voed(self, data, tijd):
        self.buf += data
        self.t += [tijd] * len(data)

    def _een(self, flush):
        """Eén frame vooraan ontleden, of None als er nog te weinig bytes zijn.

        flush=False (live, lopende stroom): wacht op meer bytes als een frame nog niet compleet is.
        flush=True (einde of begrensd blok): er komt niets meer, dus schuif onontcijferbare bytes weg (hersynchroniseer).
        """
        while self.buf:
            b = self.buf
            fmt = b[0]
            met_adres = bool(fmt & 0x80)
            kop = 1 + (2 if met_adres else 0)
            len_in_fmt = fmt & 0x3F
            if len_in_fmt == 0:
                if len(b) < kop + 1:
                    if flush:
                        self._schuif()
                        continue
                    return None
                lengte = b[kop]
                kop_tot = kop + 1
            else:
                lengte, kop_tot = len_in_fmt, kop
            totaal = kop_tot + lengte + 1
            if lengte == 0 or totaal > 300:  # onzinnige lengte => uit de maat; schuif een byte op
                self._schuif()
                continue
            if len(b) < totaal:
                if flush:
                    self._schuif()  # claimt meer bytes dan er zijn en er komt niets meer: ruis
                    continue
                return None
            frame = bytes(b[:totaal])
            if (sum(frame[:-1]) & 0xFF) != frame[-1]:
                self._schuif()  # controlesom klopt niet: waarschijnlijk uit de maat
                continue
            t0 = self.t[0]
            del self.buf[:totaal]
            del self.t[:totaal]
            return _duid(frame, met_adres, kop_tot, lengte, t0)
        return None

    def _schuif(self):
        del self.buf[0]
        del self.t[0]

    def frames(self, flush=False):
        while True:
            f = self._een(flush)
            if f is None:
                return
            yield f


def _duid(frame, met_adres, kop_tot, lengte, t0):
    doel = frame[1] if met_adres else None
    bron = frame[2] if met_adres else None
    payload = frame[kop_tot:kop_tot + lengte]
    sid = payload[0] if payload else None
    verzoek = bron == TESTER if met_adres else (sid is not None and sid < 0x40)
    antwoord = doel == TESTER if met_adres else (sid is not None and sid >= 0x40)
    d = {"t": t0, "hex": frame.hex(), "fmt": frame[0], "doel": doel, "bron": bron,
         "functioneel": bool(frame[0] & 0x40), "sid": sid, "data": payload[1:].hex(),
         "soort": _soort_sid(sid if sid and sid < 0x40 else (payload[1] if sid == 0x7F and len(payload) > 1 else sid)),
         "richting": "verzoek" if verzoek and not antwoord else "antwoord" if antwoord else "?"}
    if sid == 0x7F and len(payload) >= 3:
        d["naam"] = f"negatief op {SERVICES.get(payload[1], hex(payload[1]))}"
        d["nrc"] = NRC.get(payload[2], f"0x{payload[2]:02X}")
    elif sid is not None:
        basis = sid - 0x40 if antwoord and sid not in SERVICES else sid
        d["naam"] = SERVICES.get(basis, SERVICES.get(sid, f"0x{sid:02X}"))
    return d


class Koppelaar:
    """Houdt het laatste onbeantwoorde verzoek vast en koppelt er het antwoord aan (over leesbeurten heen)."""

    def __init__(self):
        self.open_verzoek = None

    def voeg(self, frame):
        """Geef (gekoppeld_paar of None, los_frame of None). Een paar is {'verzoek':.., 'antwoord':.., 'dt_ms':..}."""
        if frame["richting"] == "verzoek":
            vorig = self.open_verzoek
            self.open_verzoek = frame
            return (None, vorig)  # een eventueel onbeantwoord vorig verzoek komt los naar buiten
        if frame["richting"] == "antwoord" and self.open_verzoek is not None:
            paar = {"verzoek": self.open_verzoek, "antwoord": frame,
                    "dt_ms": round((frame["t"] - self.open_verzoek["t"]) * 1000, 1)}
            self.open_verzoek = None
            return (paar, None)
        return (None, frame)


def regel(frame):
    """Korte tekstregel voor een frame."""
    adr = f"{frame['doel']:02X}<-{frame['bron']:02X}" if frame["doel"] is not None else "--"
    merk = f" [{frame['soort'].upper()}]" if frame["soort"] else ""
    extra = f" nrc={frame['nrc']}" if "nrc" in frame else ""
    return f"{frame['richting']:8} {adr} {frame.get('naam','?')}{merk} data={frame['data'] or '-'}{extra}"
