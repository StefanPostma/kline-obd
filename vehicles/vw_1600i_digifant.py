"""Voertuigbestand voor de VW 1600i (Mexicaanse Kever) met Digifant-ECU 043 906 022 B ("DIGIFANT 1.8").

Deze ECU spreekt geen OBD2 en geen KWP2000 maar KW1281 op 1200 baud (reader/kw1281.py). Er zijn geen PID's en geen
service 21; in plaats daarvan benoemde lezingen (LEZINGEN). Alle kennis komt uit docs/vw-1600i-digifant.md: de firmware
van de ECU en 1600i.de.

NIET OP EEN AUTO OF ECU GETEST. Alleen tegen een nagebootste ECU. De omrekeningen zijn nagerekend tegen het ROM-beeld en
tegen één echt meetlog van een B-ECU met draaiende motor (80 metingen), dat niet is gepubliceerd.

Een OBD2-tester of `scan` krijgt van deze ECU geen antwoord. Volgens het programma doet het wekadres 0x33 geen kwaad
(het diagnosedeel herstart zoals bij contact aan); de adreswaarden 09 tot 10 hex wil je niet sturen. Niet op een echte
ECU geprobeerd. Het transport wekt daarom alleen op adres 01.

Geldt alleen voor 043 906 022 en 043 906 022 B. De latere D en E ("DIGIFANT 1.82") hebben een ander programma.

Zekerheid per signaal:
  "firmware"  uit het programma van de ECU gelezen (welke variabele, en hoe de ECU hem zelf schaalt)
  "handboek"  beschreven op 1600i.de (labelbestand, uitleg bij de meetwaardeblokken)
  "aanname"   aangenomen sensorkromme of waarde van één andere auto; nog te meten
"""

NAAM = "VW 1600i (Mexicaanse Kever), Digifant 043 906 022 B"
TRANSPORT = "kw1281"
ADRES = 0x01
BAUD = 1200

# -- Leesplan -----------------------------------------------------------------------------------------------------
# sleutel in de opname (2 hexcijfers) -> benoemde lezing van reader/kw1281.py. Er bestaat geen manier om hier een ruwe
# bloktitel op te geven: op deze ECU is een onbekende titel niet onschuldig.
LEZINGEN = {
    "12": ("tien_waarden",),        # titel 12: de tien ruwe waarden, één antwoord (0,6 tot 0,9 s)
    "B2": ("ram_venster", 0x2E),    # RAM 2E..37: toestand (2E) en ontstekingstelling (35); 0,7 tot 1,0 s
    "B9": ("ram_venster", 0x95),    # RAM 95..9E: luchtdruk bij contact aan, dichtstand gasklep, geleerde waarden
    "A2": ("groep", 2),             # meetwaardegroep 2: gasklephoek, belasting
    "A4": ("groep", 4),             # groep 4: ontsteking, belasting
    "A8": ("groep", 8),             # groep 8: ontsteking, toestand
}
LEZING_ELKE_RONDE = ["12"]
# Eén per ronde. Bewust geen meetwaardegroepen: de ECU stuurt vóór de data eerst een kop van 50 tot 77 bytes, en elke
# andere titel ertussen brengt die kop terug. Groep 4 erbij kost zo 2,2 tot 3,3 s per ronde, groep 8 3,1 tot 4,6 s.
# De RAM-vensters geven dezelfde grootheden zonder kop. De groepen blijven beschikbaar voor de momentopname (`status`).
LEZING_ROULEREND = ["B2", "B2", "B2", "B9"]

# de KWP2000-kant van het schema is hier leeg
AANKNOPEN = ()
KERN_BLOKKEN = []
ROULEREND_BLOKKEN = []
SNEL_BLOKKEN = []
ALLE_BLOKKEN = []
PID_ELKE_RONDE = []
PID_ROULEREND = []
BLOKLENGTE = 4          # kortste blok (een meetwaardegroep); de tien waarden en een RAM-venster zijn 10 bytes
IDENTITEIT_VERZOEK = None


def identiteit(blokken):
    """De F6-blokken die de ECU na het wekken uit zichzelf stuurt -> onderdeelnummer en componentnaam.

    De vier cijfers achter de componentnaam en het derde blok (codering) laten we weg: bij een gechipte ECU staat daar
    het werknummer van de tuner, en de codering is per auto."""
    uit = {}
    if len(blokken) >= 1:
        uit["onderdeelnummer"] = blokken[0].decode("ascii", "replace").strip()
    if len(blokken) >= 2:
        uit["software"] = blokken[1][:20].decode("ascii", "replace").strip()
    return uit


# -- Omrekeningen -------------------------------------------------------------------------------------------------
# temperatuur in C bij ruw 0, 16, 32, ... 256 (lineair ertussen); de ECU stuurt deze tabellen zelf in de groepskop
_TEMP_MOTOR = [150, 132, 100, 83, 70, 61, 52, 45, 38, 30, 24, 17, 10, 1, -9, -24, -40]
_TEMP_LUCHT = [120, 60, 40, 28, 18, 10, 4, -2, -7, -13, -18, -24, -29, -36, -40, -40, -40]
# periode per halve krukasomwenteling (eenheden van 2 us) bij toerentalindex 0, 16, ... 240
_PERIODE = [3072, 3568, 3952, 4400, 4736, 5152, 5664, 6256, 6976, 8336, 9088, 11120, 14288, 18528, 22736, 31232]


def _tabel(tabel, ruw):
    i = min(ruw // 16, 15)
    return tabel[i] + (tabel[i + 1] - tabel[i]) * (ruw - 16 * i) / 16.0


def _toerental(index):
    """Toerentalindex (16 per kaartkolom, hoger = lager toerental) -> tpm. Lineair in de periode tussen de steunpunten.
    Vanaf 240 staat de motor stil of draait hij onder 480 tpm: dan 0."""
    if index >= 240:
        return 0.0
    i = min(index // 16, 14)
    periode = _PERIODE[i] + (_PERIODE[i + 1] - _PERIODE[i]) * (index - 16 * i) / 16.0
    return 15_000_000 / periode


def _druk(ruw):
    return 0.369 * ruw + 14.3  # aangenomen: Motorola MPX4100A; de ruwe waarde is zeker


def _lambda(ruw):
    return max(205 - ruw * 0.89, 0) * 2 / 255  # ruw is omgekeerd: hoog = arm


def _ontsteking(byte):
    x = byte - 256 if byte >= 128 else byte  # boven 30 graden voor BDP loopt de byte rond
    return 30 - x


# sleutel: (blok, functie(bytes) -> waarde, eenheid, omschrijving, zekerheid, opmerking)
SIGNALEN = {
    "toerental":         ("12", lambda b: _toerental(b[9]), "rpm", "toerental (uit de toerentalindex)", "firmware",
                          "grof: ~15 tpm per stap stationair, ~25 bij 4000; 0 = staat stil of onder 480 tpm"),
    "toerentalindex":    ("12", lambda b: b[9], "ruw", "toerentalindex (hoger = lager toerental)", "firmware", "240 = staat stil"),
    "inlaatlucht":       ("12", lambda b: _tabel(_TEMP_LUCHT, b[0]), "C", "inlaatluchttemperatuur", "firmware", ""),
    "motortemperatuur":  ("12", lambda b: _tabel(_TEMP_MOTOR, b[2]), "C", "motortemperatuur (cilinderkopsensor, luchtgekoeld)", "firmware",
                          "warm stationair boven 87 C; geen koelwater"),
    "accuspanning":      ("12", lambda b: b[1] * 24 / 255, "V", "boordspanning", "firmware", "ruw 130-150 is normaal"),
    "belasting_ruw":     ("12", lambda b: b[3], "ruw", "belasting: inlaatdruk-ADC na filter", "firmware",
                          "bij een defect druksignaal een vervangwaarde uit gasklep en toerental"),
    "inlaatdruk":        ("12", lambda b: _druk(b[3]), "kPa", "inlaatdruk (aangenomen sensorkromme)", "aanname",
                          "te ijken met één meting: ruwe waarde bij contact aan tegen de luchtdruk van die dag"),
    "lambda_v":          ("12", lambda b: _lambda(b[4]), "V", "lambdasonde", "firmware", "ruwe waarde is omgekeerd: hoog = arm"),
    "lambda_ruw":        ("12", lambda b: b[4], "ruw", "lambdasonde, ruw (hoog = arm)", "handboek",
                          "pendelt 100-250; blijft hij op 250 dan regelt de ECU niet"),
    "lambda_dode_tijd":  ("12", lambda b: b[5], "ruw", "teller dode tijd lambdaregeling", "handboek", "normaal 0"),
    "lambda_teller":     ("12", lambda b: b[6], "ruw", "teller lambdaregeling", "handboek", "127 tot de regeling begonnen is"),
    "gasklep_ruw":       ("12", lambda b: b[7], "ruw", "gasklep-potmeter", "firmware", "dicht 40-60 volgens 1600i.de"),
    "inspuittijd":       ("12", lambda b: b[8] * 0.512, "ms", "inspuittijd, inclusief ~0,5 ms openingstijd", "firmware",
                          "alle vier de injectoren spuiten samen, één keer per krukasomwenteling"),
    "voorontsteking":    ("B2", lambda b: 30 - (b[7] * 90 / 256 - 48), "gr", "voorontsteking voor BDP", "firmware",
                          "RAM 35 (ontstekingstelling); klopt alleen als de verdeler op 6 graden is afgesteld"),
    "toestand":          ("B2", lambda b: 1 if b[0] & 0x80 else 2 if b[0] & 0x40 else 0, "",
                          "toestand: 0 deellast, 1 stationair, 2 vollast", "firmware", "RAM 2E"),
    "basisinstelling":   ("B2", lambda b: 1 if b[0] & 0x03 == 0x01 else 0, "0/1", "ECU staat in de basisinstelling", "firmware",
                          "hoort 0 te zijn; deze tool kan de basisinstelling niet starten"),
    "luchtdruk":         ("B9", lambda b: _druk(b[0]), "kPa", "luchtdruk bij contact aan (aangenomen sensorkromme)", "aanname",
                          "RAM 95; bij vollast en laag toerental langzaam bijgewerkt"),
    "gasklep_dichtstand": ("B9", lambda b: b[1], "ruw", "geleerde dichtstand van de gasklep-potmeter", "firmware", "RAM 96"),
    "mengseladaptatie":  ("B9", lambda b: b[6], "ruw", "geleerde mengseladaptatie", "firmware", "RAM 9B; 128 = neutraal, 1 tot 254"),
    "stationairadaptatie": ("B9", lambda b: b[8], "ruw", "geleerde stationairwaarde", "firmware", "RAM 9D; 0 tot 54"),
    # dezelfde grootheden via de meetwaardegroepen (op 1600i.de beschreven); alleen in de momentopname gelezen
    "voorontsteking_groep": ("A4", lambda b: _ontsteking(b[2]), "gr", "voorontsteking voor BDP (groep 4)", "handboek", ""),
    "toestand_groep":    ("A8", lambda b: b[3], "", "toestand (groep 8): 0 deellast, 1 stationair, 2 vollast", "handboek", ""),
    "gasklephoek":       ("A2", lambda b: b[2], "gr", "gasklephoek vanaf de geleerde dichtstand (groep 2)", "handboek", "volgas ~83-85"),
}
_LENGTE = {"12": 10, "B2": 10, "B9": 10, "A2": 4, "A4": 4, "A8": 4}

# alleen wat de afwijkingsregels van de webpagina veilig kunnen gebruiken. De motortemperatuur is een cilinderkop en
# geen koelwater, en de inlaatdruk is een aanname: die blijven hun eigen naam houden.
CANONICAL = {"toerental": "rpm", "inlaatlucht": "iat"}
HIGHLIGHT = ["lambda_v", "inspuittijd", "inlaatdruk", "voorontsteking", "toestand"]

PIDS = {}


def decode_pids(pids):
    return {}


def decode(blokken):
    """blokken: {"12": bytes, "A4": bytes, ...} -> {sleutel: waarde} voor elk signaal waarvan het blok compleet is."""
    uit = {}
    for sleutel, (blok, fn, *_rest) in SIGNALEN.items():
        if blok in blokken and len(blokken[blok]) >= _LENGTE.get(blok, BLOKLENGTE):
            uit[sleutel] = fn(blokken[blok])
    return uit


def vensters(w):
    return {}


VENSTER_LABELS = {}
VENSTER_OPMERKINGEN = {}

# foutcodes die het B-programma kent (titel 07)
FOUTCODES = {
    518: "gasklep-potmeter", 519: "inlaatdruksensor (slang, of de sensor op de ECU-print)", 522: "motortemperatuursensor",
    523: "inlaatluchttemperatuursensor", 525: "lambdasonde", 532: "voedingsspanning", 65535: "ECU defect",
}
