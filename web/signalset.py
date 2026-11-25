"""Signaalset: zet een opnameregel om in waarden, voor één voertuig.

Wat voertuigonafhankelijk is staat hier: standaard-PID's (`pidXX`, via obd.PIDS) en het schema van een signalenbestand.
Wat per auto verschilt, staat in dat bestand en in het profiel (`vehicle.PROFILES[..]["feed"]`): welk blok, welke byte,
welke schaal. Een andere auto = een ander signalenbestand + een profielregel, geen wijziging in pagina of server.

Schema van een signalenbestand (Python-module, bv. vehicles/matiz_m200_08.py):

    SIGNALEN = {sleutel: (blok, functie(bytes) -> waarde, eenheid, omschrijving, zekerheid, opmerking)}
    zekerheid:  "pid" (geijkt met een standaard-PID), "handboek" (past bij het werkplaatshandboek),
                "gedrag" (alleen afgeleid uit hoe de waarde beweegt), "firmware" (uit het programma van de ECU
                gelezen), "aanname" (aangenomen sensorkromme of waarde van een andere auto; nog te meten).
                Een label dat de pagina niet kent, toont ze als "onbekend label", nooit als niets.
    TRANSPORT = "kwp2000"               (optioneel, standaard "kwp2000") hoe de uitlezer met de ECU praat. De kabelmodus
                van de pagina spreekt alleen KWP2000/OBD2 en weigert elk ander transport (bv. "kw1281"); meekijken met
                een opname (--follow, --replay) werkt voor elk transport.
    BLOKLENGTE = 32                     (optioneel) kortere blokken worden overgeslagen
    vensters(waarden) -> {naam: bool}   (optioneel) bv. "zit de motor in een zelftest-venster van de ECU"
    VENSTER_LABELS = {naam: tekst}      (optioneel)
    VENSTER_OPMERKINGEN = {naam: tekst} (optioneel) uitleg bij een chip, bv. dat het venster ruimer is dan dat van de ECU
    CANONICAL = {sleutel: "rpm"|"map"|"ect"|"iat"|"speed"|"o2_b1s1"|...}   (optioneel) welke signalen de
                afwijkingsregels mogen gebruiken; alleen wat met een standaard-PID is geijkt
    TPS = {"signal": sleutel, "rest": V, "full": V}   (optioneel) gaskleppercentage schatten uit een spanning
    HIGHLIGHT = [sleutel, ...]          (optioneel) grafieken bij een vastgelegd moment
    KERN_BLOKKEN = [id, ...]            (optioneel) leesplan voor de kabelmodus: blokken (service 21) die elke ronde
    ROULEREND_BLOKKEN = [id, ...]       gelezen worden, blokken waarvan er elke ronde één aan de beurt is,
    PID_ELKE_RONDE = [pid, ...]         standaard-PID's elke ronde en
    PID_ROULEREND = [pid, ...]          standaard-PID's waarvan er elke ronde één aan de beurt is
    ALLE_BLOKKEN = [id, ...]            (optioneel) alle lokale ID's die de ECU kent; voor de momentopname vóór/na foutcodes wissen
                Zonder deze namen leest de kabelmodus alleen de standaard-PID's die de ECU ondersteunt. Alleen blokken
                waar dit bestand een signaal voor heeft worden opgevraagd.

CANONICAL, TPS en HIGHLIGHT mogen ook in het profiel staan; staan ze in het bestand, dan gaat het bestand voor. Een
nieuwe auto is dus één signalenbestand met `server.py --signals <bestand>`; een profielregel is alleen handig voor
de map met opnames en het standaard stationair toerental.

De opnameregel zelf: {"t": s, "e": epoch, "<blok>": "<hex>", "pid<XX>": "<hex zonder 41 XX>"}.
"""
import importlib.util
import os

import obd

CABLE_TRANSPORT = "kwp2000"                         # het enige transport dat de kabelmodus (kwp.py) spreekt
UNIT_TEXT = {"C": "°C", "km/u": "km/h"}


def _decimals(unit):
    return 2 if unit == "V" else 1 if unit in ("kPa", "C", "km") else 0


def _load(path, log):
    spec = importlib.util.spec_from_file_location("signalenbestand_" + os.path.basename(path)[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not isinstance(getattr(mod, "SIGNALEN", None), dict):
        raise ValueError("geen SIGNALEN-dict in het bestand")
    return mod


class SignalSet:
    def __init__(self, cfg=None, signals_path=None, log=print):
        cfg = cfg or {}
        self.canonical = dict(cfg.get("canonical", {}))
        self.tps_cfg = cfg.get("tps")
        self.highlight = list(cfg.get("highlight", []))
        self.defs, self.block_len, self._vensters, self.window_labels, self.window_notes = {}, 32, None, {}, {}
        self.plan = None                            # leesplan voor de kabelmodus, zie het schema bovenaan
        self.all_blocks = []                        # ALLE_BLOKKEN: alle lokale ID's voor een volledige momentopname
        self.transport = CABLE_TRANSPORT            # TRANSPORT uit het signalenbestand
        self.error = None
        self.path = os.path.expanduser(signals_path or cfg.get("signals") or "") or None
        if self.path:
            try:
                mod = _load(self.path, log)
                self.defs = dict(mod.SIGNALEN)
                self.block_len = getattr(mod, "BLOKLENGTE", 32)
                self.transport = str(getattr(mod, "TRANSPORT", CABLE_TRANSPORT)).strip().lower() or CABLE_TRANSPORT
                self._vensters = getattr(mod, "vensters", None)
                self.window_labels = getattr(mod, "VENSTER_LABELS", {})
                self.window_notes = getattr(mod, "VENSTER_OPMERKINGEN", {})
                self.canonical.update(getattr(mod, "CANONICAL", {}))
                self.tps_cfg = getattr(mod, "TPS", None) or self.tps_cfg
                self.highlight = list(getattr(mod, "HIGHLIGHT", self.highlight))
                self.plan = self._read_plan(mod)
                self.all_blocks = [i for i in (getattr(mod, "ALLE_BLOKKEN", None) or []) if isinstance(i, int) and 0 <= i <= 255]
            except Exception as e:                  # bestand weg of stuk: de pagina werkt dan alleen met standaard-PID's
                self.error = f"signalenbestand {self.path} niet geladen: {type(e).__name__}: {e}"
                log(self.error)
        self._raw = {}
        # per uitvoersleutel: wat de pagina erbij moet weten (label, eenheid, kaartgroep, zekerheid)
        self.meta = {}
        for key, spec in self.defs.items():
            out_key = self.canonical.get(key, key)
            if out_key in obd.META:
                continue                            # een bekende grootheid: label en eenheid komen uit obd.META
            _blok, _fn, unit, text, certainty, note = (list(spec) + [""] * 6)[:6]
            self.meta[out_key] = {"label": text[:1].upper() + text[1:], "unit": UNIT_TEXT.get(unit, unit),
                                  "dec": _decimals(unit), "card": True, "group": "mfr",
                                  "certainty": certainty, "note": note}
        if self.tps_cfg:
            self.meta["tps"] = {"label": "Gaskleppositie (geschat uit sensorspanning)"}

    def cable_refusal(self):
        """Waarom de kabelmodus dit voertuigbestand niet mag gebruiken, of None als het mag. De kabelmodus (kwp.py) spreekt
        alleen KWP2000/OBD2 op 10400 baud; bij een ander transport zou hij daarop gaan aanknopen en niets terugkrijgen, en de
        verzoeken van de pagina (ook het wissen met modus 04) betekenen op zo'n ECU iets anders."""
        if self.transport == CABLE_TRANSPORT:
            return None
        return (f"Dit voertuigbestand gebruikt transport '{self.transport}'. De kabelmodus van de pagina spreekt alleen "
                f"{CABLE_TRANSPORT.upper()} en stuurt hier niets naar de ECU. Kijk mee met een opname (--follow of --replay) "
                "van het uitleesscript.")

    def _read_plan(self, mod):
        """Leesplan uit het signalenbestand, of None als het bestand er geen heeft. Een blok zonder signaal wordt niet
        opgevraagd; id's die geen getal van 0 t/m 255 zijn, worden genegeerd."""
        def ids(name):
            raw = getattr(mod, name, None)
            return [i for i in (raw if isinstance(raw, (list, tuple)) else []) if isinstance(i, int) and 0 <= i <= 255]
        plan = {"kern": ids("KERN_BLOKKEN"), "rot_blocks": ids("ROULEREND_BLOKKEN"),
                "pids": ids("PID_ELKE_RONDE"), "rot_pids": ids("PID_ROULEREND")}
        if not any(plan.values()):
            return None
        known = {str(spec[0]).upper() for spec in self.defs.values()}
        for k in ("kern", "rot_blocks"):
            plan[k] = [b for b in plan[k] if f"{b:02X}" in known]
        return plan if any(plan.values()) else None

    # ---------- decoderen ----------
    @staticmethod
    def pids(row):
        out = {}
        for key, h in row.items():
            if not key.startswith("pid"):
                continue
            try:
                n, fn = obd.PIDS[int(key[3:], 16)]
                data = bytes.fromhex(h)
            except (KeyError, ValueError, TypeError):
                continue
            if len(data) >= n:
                out.update(fn(data))
        return out

    def _block(self, row, name):
        try:
            b = bytes.fromhex(row.get(name) or "")
        except (ValueError, TypeError):             # beschadigde regel: dit blok overslaan
            return None
        return b if len(b) >= self.block_len else None

    def decode(self, row):
        """Opnameregel -> {sleutel: waarde}. Ontbrekende of kapotte blokken geven gewoon minder sleutels."""
        out = self.pids(row)                        # standaard-PID's gaan voor: die zijn voor elke auto hetzelfde
        raw = {}
        for key, spec in self.defs.items():
            b = self._block(row, spec[0])
            if b is None:
                continue
            try:
                raw[key] = spec[1](b)
            except (IndexError, ValueError, TypeError, ZeroDivisionError):
                continue
        for key, v in raw.items():
            out.setdefault(self.canonical.get(key, key), v)
        if self.tps_cfg and self.tps_cfg["signal"] in raw:
            rest, full = self.tps_cfg["rest"], self.tps_cfg["full"]
            out.setdefault("tps", min(100.0, max(0.0, (raw[self.tps_cfg["signal"]] - rest) * 100 / (full - rest))))
        self._raw.update(raw)
        return out

    def windows(self):
        """Testvensters van de ECU volgens het signalenbestand: [{name, label, active}]."""
        if not self._vensters:
            return []
        try:
            res = self._vensters(dict(self._raw))
        except Exception:
            return []
        return [{"name": n, "label": self.window_labels.get(n, n.replace("_", " ")), "active": bool(on),
                 "note": self.window_notes.get(n, "")} for n, on in res.items()]
