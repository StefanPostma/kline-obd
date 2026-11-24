"""Voertuigherkenning en profielen: VIN-decodering en per-voertuig instellingen voor de analyse.

Een profiel bevat alleen wat per auto echt verschilt (bv. normaal stationair toerental). De overige
drempels in analyzer.py zijn generiek voor een benzinemotor. Voeg een profiel toe voor een nieuwe auto
door een regel aan PROFILES toe te voegen en `--profile <naam>` mee te geven.
"""
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # de map boven web/

# Eerste 3 tekens van het VIN (WMI) -> fabrikant. Alleen opgenomen wat zeker is; de rest wordt "onbekend".
WMI = {
    "KL1": "GM Korea (Chevrolet/Daewoo)", "KLY": "GM Korea (Daewoo)", "KLA": "GM Korea (Daewoo)",
    "KMH": "Hyundai", "KNA": "Kia", "KND": "Kia",
    "WVW": "Volkswagen", "WAU": "Audi", "WBA": "BMW", "WDB": "Mercedes-Benz", "W0L": "Opel",
    "VF1": "Renault", "VF3": "Peugeot", "VF7": "Citroën", "ZFA": "Fiat", "TMB": "Škoda", "VSS": "SEAT",
}

_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"      # positie 10 van het VIN; cyclus van 30 jaar


def decode_vin(vin):
    """Geeft {wmi, maker, year} of None bij een onbruikbaar VIN. Modeljaar is de kandidaat in 1996..2026."""
    if not vin or len(vin) != 17:
        return None
    wmi = vin[:3].upper()
    out = {"wmi": wmi, "maker": WMI.get(wmi, f"onbekend merk (WMI {wmi})"), "year": None}
    i = _YEAR_CODES.find(vin[9].upper())
    if i >= 0:
        for base in (1980, 2010):
            if 1996 <= base + i <= 2026:
                out["year"] = base + i
    return out


PROFILES = {
    "generic-petrol": {"label": "Generiek (benzine)", "idle_rpm": None},
    "matiz08": {
        "label": "Chevrolet/Daewoo Matiz 0.8 (indicatief)", "idle_rpm": (650, 1000),
        # Meekijken met een opname (feed.py). Alles wat bij deze auto hoort staat hier en in het signalenbestand;
        # de pagina zelf kent geen byte-indelingen. Zie "Een andere auto" in de README.
        "feed": {
            "dir": os.path.join(_REPO, "recordings"), "patterns": ["rit*.jsonl", "egr_*.jsonl"],
            "signals": os.path.join(_REPO, "vehicles", "matiz_m200_08.py"),       # SIGNALEN in blok/byte-vorm, geschreven door de uitlezer
            # signaal in het bestand -> sleutel die de afwijkingsregels begrijpen (alleen wat met een standaard-PID
            # is geijkt; standaard-PID's in de opname gaan altijd voor)
            "canonical": {"toerental": "rpm", "map": "map", "koelwater": "ect", "inlaatlucht": "iat",
                          "snelheid": "speed", "lambda_voor_v": "o2_b1s1", "lambda_na_v": "o2_b1s2"},
            # gaskleppercentage schatten uit de sensorspanning (rust/vol); alleen voor de stotterdetectie
            # full = hoogste gemeten waarde (rit 23-11, ADC 937); volgas is niet gemeten, dus een ondergrens
            "tps": {"signal": "gasklep_v", "rest": 0.513, "full": 4.58},
            # signalen die in de grafieken bij een vastgelegd moment horen
            "highlight": ["onrust_1", "onrust_2", "onrust_3", "boven_drempel_1", "boven_drempel_2", "boven_drempel_3",
                          "o2_b1s2", "egr_stand_v", "egr_stuur"],
        },
    },
}


def pick_profile(requested="auto"):
    """Een profiel wordt nooit uit het merk afgeleid (een GM Korea-VIN kan ook een Spark of Aveo zijn):
    zonder --profile gebruiken we het generieke profiel."""
    if requested in PROFILES:
        return requested
    return "generic-petrol"
