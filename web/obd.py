"""OBD2 (SAE J1979) services bovenop kwp.KLine: live data (01), freeze frame (02), foutcodes (03/07).
"""
from kwp import BadFrame, KLineError, Negative, NoResponse


def _o2(d, key):
    out = {key: d[0] / 200}
    if d[1] != 0xFF:                       # 0xFF = sensor levert geen trim
        out[key + "_trim"] = d[1] * 100 / 128 - 100
    return out


# pid: (aantal databytes, decoder -> {key: waarde})
PIDS = {
    # byte B bit 3: 0 = vonkontsteking (benzine), 1 = compressieontsteking (diesel)
    0x01: (4, lambda d: {"mil": d[0] >> 7, "dtc_count": d[0] & 0x7F, "diesel": (d[1] >> 3) & 1}),
    0x03: (2, lambda d: {"fuelsys": d[0]}),
    0x04: (1, lambda d: {"load": d[0] * 100 / 255}),
    0x05: (1, lambda d: {"ect": d[0] - 40}),
    0x06: (1, lambda d: {"stft": d[0] * 100 / 128 - 100}),
    0x07: (1, lambda d: {"ltft": d[0] * 100 / 128 - 100}),
    0x0A: (1, lambda d: {"fuelpress": d[0] * 3}),
    0x0B: (1, lambda d: {"map": d[0]}),
    0x0C: (2, lambda d: {"rpm": (d[0] * 256 + d[1]) / 4}),
    0x0D: (1, lambda d: {"speed": d[0]}),
    0x0E: (1, lambda d: {"timing": d[0] / 2 - 64}),
    0x0F: (1, lambda d: {"iat": d[0] - 40}),
    0x10: (2, lambda d: {"maf": (d[0] * 256 + d[1]) / 100}),
    0x11: (1, lambda d: {"tps": d[0] * 100 / 255}),
    0x14: (2, lambda d: _o2(d, "o2_b1s1")),
    0x15: (2, lambda d: _o2(d, "o2_b1s2")),
    0x1C: (1, lambda d: {"obd_std": d[0]}),
    0x1F: (2, lambda d: {"runtime": d[0] * 256 + d[1]}),
    0x33: (1, lambda d: {"baro": d[0]}),
    0x42: (2, lambda d: {"batt": (d[0] * 256 + d[1]) / 1000}),
}

# key: (label, eenheid, decimalen, tonen als kaart)
META = {
    "rpm": ("Toerental", "rpm", 0, True),
    "tps": ("Gaskleppositie", "%", 1, True),
    "map": ("Inlaatdruk (MAP)", "kPa", 0, True),
    "stft": ("Brandstofcorrectie kort (STFT)", "%", 1, True),
    "ltft": ("Brandstofcorrectie lang (LTFT)", "%", 1, True),
    "o2_b1s1": ("Lambdasonde voor kat", "V", 2, True),
    "o2_b1s1_trim": ("Lambda-trim voor kat", "%", 1, False),
    "o2_b1s2": ("Lambdasonde na kat", "V", 2, True),
    "o2_b1s2_trim": ("Lambda-trim na kat", "%", 1, False),
    "ect": ("Koelwatertemperatuur", "°C", 0, True),
    "iat": ("Inlaatluchttemperatuur", "°C", 0, True),
    "batt": ("Accuspanning", "V", 2, True),
    "load": ("Motorbelasting", "%", 0, True),
    "timing": ("Ontstekingstijdstip", "°", 1, True),
    "speed": ("Snelheid", "km/h", 0, True),
    "maf": ("Luchtmassa (MAF)", "g/s", 2, True),
    "fuelpress": ("Brandstofdruk", "kPa", 0, True),
    "fuelsys": ("Brandstofsysteem", "", 0, True),
    "runtime": ("Looptijd motor", "s", 0, True),
    "baro": ("Luchtdruk", "kPa", 0, True),
    "mil": ("Storingslamp (MIL)", "", 0, False),
    "dtc_count": ("Aantal foutcodes", "", 0, False),
    "diesel": ("Dieselmotor", "", 0, False),
    "obd_std": ("OBD-norm", "", 0, False),
}

OBD_STD = {
    1: "OBD-II (CARB)", 2: "OBD (EPA)", 3: "OBD + OBD-II", 4: "OBD-I", 5: "niet OBD-conform",
    6: "EOBD (Europa)", 7: "EOBD + OBD-II", 8: "EOBD + OBD", 9: "EOBD + OBD + OBD-II",
    10: "JOBD (Japan)", 11: "JOBD + OBD-II", 12: "JOBD + EOBD", 13: "JOBD + EOBD + OBD-II",
}

FUEL_STATUS = {
    1: "Open lus (koude motor)",
    2: "Gesloten lus (lambdaregeling actief)",
    4: "Open lus (belasting/afremmen)",
    8: "Open lus (systeemfout)",
    16: "Gesloten lus (met sensorfout)",
}

# Gewenste verversingstijd (s) per PID; de poller kiest steeds de PID die het meest 'te laat' is.
# Zo krijgt het toerental (belangrijk voor stotteren) de meeste verzoeken.
POLL_PERIOD = {
    0x0C: 0.30, 0x11: 0.60, 0x0B: 0.60, 0x06: 0.80, 0x14: 0.80,
    0x04: 1.5, 0x0D: 1.5, 0x0E: 2.0, 0x10: 2.0,
    0x05: 5.0, 0x07: 5.0, 0x42: 5.0, 0x03: 5.0, 0x01: 5.0,
    0x0F: 10.0, 0x1F: 10.0, 0x15: 5.0, 0x0A: 10.0, 0x33: 30.0,
}

DTC_TEXT = {
    "P0100": "Luchtmassameter (MAF) circuit", "P0101": "MAF bereik/prestatie",
    "P0102": "MAF signaal te laag", "P0103": "MAF signaal te hoog",
    "P0105": "Inlaatdruksensor (MAP) circuit", "P0106": "MAP bereik/prestatie",
    "P0107": "MAP signaal te laag", "P0108": "MAP signaal te hoog",
    "P0110": "Inlaattemperatuursensor circuit", "P0112": "Inlaattemperatuur signaal te laag",
    "P0113": "Inlaattemperatuur signaal te hoog", "P0115": "Koelwatertemperatuursensor circuit",
    "P0116": "Koelwatertemperatuur bereik/prestatie", "P0117": "Koelwatertemperatuur signaal te laag",
    "P0118": "Koelwatertemperatuur signaal te hoog", "P0120": "Gasklepsensor (TPS) circuit",
    "P0121": "TPS bereik/prestatie", "P0122": "TPS signaal te laag", "P0123": "TPS signaal te hoog",
    "P0125": "Koelwater te koud voor regeling in gesloten lus (thermostaat?)",
    "P0130": "Lambdasonde voor kat: circuit", "P0131": "Lambdasonde voor kat: spanning te laag",
    "P0132": "Lambdasonde voor kat: spanning te hoog", "P0133": "Lambdasonde voor kat: trage respons",
    "P0134": "Lambdasonde voor kat: geen activiteit", "P0135": "Lambdasonde voor kat: verwarming",
    "P0141": "Lambdasonde na kat: verwarming", "P0171": "Mengsel te arm",
    "P0172": "Mengsel te rijk", "P0201": "Injector cilinder 1 circuit",
    "P0202": "Injector cilinder 2 circuit", "P0203": "Injector cilinder 3 circuit",
    "P0300": "Willekeurige/meerdere misfires", "P0301": "Misfire cilinder 1",
    "P0302": "Misfire cilinder 2", "P0303": "Misfire cilinder 3", "P0304": "Misfire cilinder 4",
    "P0325": "Klopsensor circuit", "P0335": "Krukaspositiesensor circuit",
    "P0336": "Krukaspositiesensor bereik/prestatie", "P0340": "Nokkenassensor circuit",
    "P0351": "Ontstekingsspoel 1 circuit", "P0352": "Ontstekingsspoel 2 circuit",
    "P0353": "Ontstekingsspoel 3 circuit", "P0420": "Katalysator rendement onder drempel",
    "P0443": "EVAP-purgeventiel circuit", "P0500": "Snelheidssensor",
    "P0505": "Stationair-toerentalregeling", "P0506": "Stationair toerental lager dan verwacht",
    "P0507": "Stationair toerental hoger dan verwacht", "P0560": "Systeemspanning",
    "P0562": "Systeemspanning te laag", "P0563": "Systeemspanning te hoog",
    "P0601": "ECU geheugenfout",
}


def dtc_text(code):
    if code in DTC_TEXT:
        return DTC_TEXT[code]
    if code[1] in "1357" or code[0] in "BCU":
        return "Fabrikantspecifieke code (zoek op Daewoo/Chevrolet)"
    return "Onbekende code"


def read_pid(kl, pid):
    nbytes, decode = PIDS[pid]
    p = kl.request(bytes([0x01, pid]), expect=2 + nbytes)
    if len(p) < 2 + nbytes or p[0] != 0x41 or p[1] != pid:
        raise BadFrame(f"onverwacht antwoord op PID {pid:02X}: {p.hex(' ')}")
    return decode(p[2:2 + nbytes])


def supported_pids(kl):
    """Vraag de ondersteunde-PID-bitmaskers op (00, 20, 40, ...)."""
    found = set()
    base = 0x00
    while base <= 0xC0:
        try:
            p = kl.request(bytes([0x01, base]), expect=6)
        except NoResponse:
            if base == 0:
                raise
            break
        if len(p) < 6 or p[0] != 0x41 or p[1] != base:
            raise BadFrame(f"onverwacht antwoord op PID {base:02X}: {p.hex(' ')}")
        mask = int.from_bytes(p[2:6], "big")
        for i in range(32):
            if mask & (1 << (31 - i)):
                found.add(base + i + 1)
        if not mask & 1:
            break
        base += 0x20
    return sorted(x for x in found if x % 0x20)


def _printable(data):
    return bytes(x for x in data if 0x20 <= x < 0x7F).decode("ascii").strip()


def read_infotype(kl, it):
    """Mode 09 (voertuiginfo): VIN (02), kalibratie-ID (04), ECU-naam (0A). Antwoord = meerdere berichten
    van elk 4 databytes met een volgnummer; die en de nul-opvulling vallen weg bij het filteren."""
    frames = kl.request_all(bytes([0x09, it]), expect=7)
    data = bytearray()
    for p in frames:
        if len(p) >= 3 and p[0] == 0x49 and p[1] == it:
            data += p[2:]
    return _printable(data) or None


def mode09_supported(kl):
    try:
        p = kl.request(bytes([0x09, 0x00]), expect=6)
    except KLineError:
        return []
    if len(p) < 6 or p[0] != 0x49 or p[1] != 0x00:
        return []
    # De echte Matiz geeft `49 00 01 FC 00 00 00`: eerst een berichtenteller, dan het masker (infotype 01..06).
    mask = int.from_bytes(p[3:7] if len(p) >= 7 else p[2:6], "big")
    return [i + 1 for i in range(32) if mask & (1 << (31 - i))]


def identify(kl, supported):
    """Welke auto/ECU zit er aan de andere kant? Alleen standaard, lezende OBD2-vragen."""
    info = {"vin": None, "cal_id": None, "ecu_name": None, "mode09": [], "obd_std": None,
            "diesel": None, "notes": []}
    if 0x01 in supported:
        try:
            info["diesel"] = bool(read_pid(kl, 0x01).get("diesel"))
        except KLineError:
            pass
    if 0x1C in supported:
        try:
            n = read_pid(kl, 0x1C)["obd_std"]
            info["obd_std"] = OBD_STD.get(n, f"onbekend ({n})")
        except KLineError:
            pass
    info["mode09"] = mode09_supported(kl)
    for it, key in ((0x02, "vin"), (0x04, "cal_id"), (0x0A, "ecu_name")):
        if it in info["mode09"]:
            try:
                info[key] = read_infotype(kl, it)
            except KLineError as e:
                info["notes"].append(f"Mode 09 {it:02X} lezen mislukt: {e}")
    if info["vin"] and len(info["vin"]) != 17:         # onvolledig (verloren bericht door ruis): niet tonen
        info["notes"].append(f"VIN onvolledig ontvangen ('{info['vin']}'), genegeerd; start opnieuw om het nog eens te proberen.")
        info["vin"] = None
    if not info["mode09"]:
        info["notes"].append("ECU ondersteunt geen voertuiginfo (mode 09): geen VIN of ECU-naam beschikbaar. "
                             "Kies zelf een profiel met --profile.")
    return info


# KWP2000 ReadECUIdentification (service 1A): uitgebreide, fabrikant-afhankelijke identificatie.
KWP_IDENT_OPTIONS = (0x80, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x90, 0x91, 0x92, 0x94, 0x9B)


def probe_kwp_ident(kl):
    """Probeer de gebruikelijke 1A-opties; geef terug wat de ECU antwoordt (ruw als hex + leesbare tekst)."""
    out = {}
    if kl.proto != "kwp":
        return out
    for opt in KWP_IDENT_OPTIONS:
        try:
            p = kl.request(bytes([0x1A, opt]), timeout=0.4)
        except (Negative, NoResponse, BadFrame):
            continue
        if len(p) >= 2 and p[0] == 0x5A and p[1] == opt:
            out[f"{opt:02X}"] = {"hex": p[2:].hex(" "), "text": _printable(p[2:]) or None}
    return out


def decode_dtcs(data):
    """Bytes na de service-id -> lijst codes. Sommige ECU's zetten er een aantalbyte voor."""
    data = bytes(data)
    if len(data) % 2 == 1:
        data = data[1:]
    codes = []
    for i in range(0, len(data) - 1, 2):
        a, b = data[i], data[i + 1]
        if a == 0 and b == 0:
            continue
        codes.append("PCBU"[a >> 6] + f"{(a >> 4) & 3}{a & 0xF:X}{b:02X}")
    return codes


def read_dtcs(kl, service=0x03):
    """service 0x03 = opgeslagen, 0x07 = pending."""
    p = kl.request(bytes([service]))
    if p[0] != service + 0x40:
        raise BadFrame(f"onverwacht antwoord op service {service:02X}: {p.hex(' ')}")
    return decode_dtcs(p[1:])


def clear_dtcs(kl):
    """Mode 04: wist opgeslagen en pending codes, freeze frame, MIL en readiness. Het ENIGE verzoek in dit project dat de ECU
    verandert (zie README.md, "Foutcodes wissen"); alleen aanroepen via analyzer.Monitor, na de controles en de momentopname daar.

    Bewust zonder herhaling (retries=0): na een verloren antwoord weten we niet of de ECU gewist heeft, dus we sturen niet
    blind opnieuw maar lezen terug. `7F 04 78` (antwoord volgt) wacht kwp.request al af. Geen terugval naar service 14."""
    p = kl.request(bytes([0x04]), expect=1, timeout=2.0, retries=0)
    if p[:1] != b"\x44":
        raise BadFrame(f"onverwacht antwoord op mode 04: {p.hex(' ')}")


def read_freeze(kl):
    """Freeze frame (frame 0): de code die hem veroorzaakte plus de belangrijkste waarden."""
    out = {}
    try:
        p = kl.request(bytes([0x02, 0x02, 0x00]))
        if len(p) >= 5 and p[0] == 0x42:
            codes = decode_dtcs(p[3:5])
            out["dtc"] = codes[0] if codes else None
    except (BadFrame, NoResponse) as e:
        out["error"] = str(e)
        return out
    except Exception:
        return out
    for pid in (0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0F, 0x11):
        nbytes, decode = PIDS[pid]
        try:
            p = kl.request(bytes([0x02, pid, 0x00]), expect=3 + nbytes)
        except Exception:
            continue
        if len(p) >= 3 + nbytes and p[0] == 0x42 and p[1] == pid:
            out.update(decode(p[3:3 + nbytes]))
    return out
