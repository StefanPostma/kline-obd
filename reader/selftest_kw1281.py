#!/usr/bin/env python3
"""Zelftest van het KW1281-transport (kw1281.py) tegen een nagebootste Digifant-ECU (nep_kw1281.py). Geen auto, geen kabel.

    python selftest_kw1281.py

De nep-ECU zit op draadniveau: 5-baud-adres, 55 01 8A, complement per byte, om en om een blok, sessie verbreken na
~1,1 s stilte. Hij faalt luid op elke titel die nooit naar zo'n ECU mag. Niet getest: een echte ECU, een echte kabel.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import kw1281
import nep_kw1281

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
fouten = []


def check(naam, ok, detail=""):
    print(f"  {'ok ' if ok else 'FOUT'}  {naam}" + (f"   <- {str(detail)[:300]}" if not ok and detail != "" else ""))
    if not ok:
        fouten.append(naam)


def mislukt(functie, *args, soort=kw1281.KW1281Fout):
    try:
        functie(*args)
    except soort as e:
        return str(e)
    return None


def lijn(**kw):
    poort = nep_kw1281.NepPoort(rust_s=0.3, **kw)
    return kw1281.KW1281(serieel=poort, rust=0.4), poort


print("Wekken en identiteit")
k, ecu = lijn()
blokken = k.wek()
check("adres 01 op 5 baud aangenomen, sessie geopend", ecu.adressen == [(0x01, True)] and ecu.sessies == 1, ecu.adressen)
check("drie identiteitsblokken ontvangen", [len(b) for b in blokken] == [12, 24, 4] and blokken[0].startswith(b"043906022B"), blokken)

print("Lezen")
check("tien waarden: 10 bytes, gelijk aan die van de ECU", k.tien_waarden() == ecu.waarden)
voor, lijnbytes = ecu.gezien.count(0x29), k.verzonden
g = k.groep(4)
check("groep 4: de ECU stuurt eerst de kop (50 bytes op de lijn), daarna 4 bytes data",
      len(g) == 4 and ecu.gezien.count(0x29) - voor == 2 and k.verzonden - lijnbytes == 2 * 5 + 49 + 7, (g, k.verzonden - lijnbytes))
voor = ecu.gezien.count(0x29)
k.groep(8)
check("volgende groep direct erna: geen kop meer, één verzoek", ecu.gezien.count(0x29) - voor == 1)
check("foutgeheugen leeg", k.fouten() == [])
ecu.fouten = [(522, 0x23), (525, 0x23)]
check("foutgeheugen met twee fouten", k.fouten() == [(522, 0x23), (525, 0x23)])
ecu.fouten = [(518, 0x23), (519, 0x23), (522, 0x23), (523, 0x23), (525, 0x23), (532, 0x23)]
check("zes fouten: twee FC-blokken, elk bevestigd", [c for c, _ in k.fouten()] == [518, 519, 522, 523, 525, 532])
check("één byte RAM", k.ram_byte(0x35) == 210)
check("venster van tien bytes RAM", k.ram_venster(0x95) == bytes(ecu.ram[0x95:0x9F]))

print("Wat nooit verstuurd kan worden")
check("de lijst van toegestane titels is precies 01 06 07 08 09 12 29", sorted(kw1281.TOEGESTAAN) == [0x01, 0x06, 0x07, 0x08, 0x09, 0x12, 0x29])
publiek = sorted(n for n in dir(kw1281.KW1281) if not n.startswith("_"))
check("geen openbare functie die een ruwe titel aanneemt",
      publiek == ["close", "fouten", "groep", "houd_wakker", "lees", "log", "ram_byte", "ram_venster", "stop", "tien_waarden", "wek"], publiek)
k.houd_wakker()
zender, lijnbytes = k.verzonden, ecu.bytes_van_tester
geweigerd = [t for t in [0x11, 0x04, 0x05, 0x10, 0x1A, 0x00, 0x02, 0x03, 0x19, 0x21] + list(range(0x30, 0x100))
             if mislukt(k._stuur_blok, t, b"", soort=kw1281.VerbodenTitel) is not None]
check("basisinstelling, stelgliedtest, wissen, coderen, EEPROM en elke titel vanaf 30: geweigerd", len(geweigerd) == 10 + 0xD0, len(geweigerd))
check("groep 0, 9 en 16 geweigerd (de ECU controleert het nummer niet)",
      all(mislukt(k.groep, n, soort=kw1281.VerbodenTitel) for n in (0, 9, 16)))
check("een onbekende benoemde lezing wordt geweigerd", mislukt(k.lees, "basisinstelling", soort=kw1281.VerbodenTitel) is not None)
check("een toegestane titel met verkeerde data wordt geweigerd", mislukt(k._stuur_blok, 0x12, b"\x01", soort=kw1281.VerbodenTitel) is not None)
check("bij al die weigeringen ging er geen byte de lijn op", k.verzonden == zender and ecu.bytes_van_tester == lijnbytes,
      (k.verzonden - zender, ecu.bytes_van_tester - lijnbytes))
check("de sessie leeft daarna nog", k.tien_waarden() == ecu.waarden)

print("De sessie openhouden")
for _ in range(4):
    time.sleep(0.5)
    k.houd_wakker()
check("met een ACK elke halve seconde blijft de sessie 2 s open", k.tien_waarden() == ecu.waarden and ecu.verbroken == 0, ecu.verbroken)
time.sleep(1.3)
zender = k.verzonden
tekst = mislukt(k.tien_waarden)
check("na 1,3 s zwijgen: 'sessie verlopen', en er wordt niets meer in de oude sessie gestuurd",
      tekst is not None and "verlopen" in tekst and k.verzonden == zender and not k.verbonden, tekst)
ecu.read(0)  # de nep-ECU verwerkt zijn klok pas als iemand naar de poort kijkt
check("de ECU had de sessie zelf ook verbroken", ecu.verbroken == 1, ecu.verbroken)
k.wek()
check("opnieuw wekken lukt", ecu.sessies == 2 and k.tien_waarden() == ecu.waarden, ecu.sessies)
lijnbytes = k.verzonden
k.stop()
check("afsluiten stuurt titel 06 en houdt op na de titelbyte (de ECU bevestigt die niet)",
      ecu.gezien[-1] == 0x06 and k.verzonden - lijnbytes == 3 and k.t_lijn <= time.perf_counter(), (ecu.gezien[-3:], k.verzonden - lijnbytes))
check("de ECU kreeg geen enkele titel die nooit mag", ecu.overtredingen == [], ecu.overtredingen)

print("Storing op de lijn")
k, ecu = lijn(laat_complement_vallen=11)  # het elfde complement van de ECU blijft weg: midden in het eerste verzoek
k.wek()
tekst = mislukt(k.tien_waarden)
check("een ontbrekend complement breekt het verzoek af en de sessie geldt als verloren", tekst is not None and not k.verbonden, tekst)
k.wek()
check("daarna opnieuw wekken en lezen lukt", k.tien_waarden() == ecu.waarden and ecu.sessies == 2, ecu.sessies)

print("De regels van de ECU (echte tijden)")
ecu = nep_kw1281.NepPoort(rust_s=2.0)
k = kw1281.KW1281(serieel=ecu, rust=0.5)
tekst = mislukt(k.wek)
check("adres na te korte stilte: de ECU negeert het", tekst is not None and ecu.adressen == [(0x01, False)] and ecu.sessies == 0, (tekst, ecu.adressen))
k = kw1281.KW1281(serieel=ecu)
t = time.time()
k.wek()
check("met de standaardwachttijd van het transport (2,6 s) lukt het", ecu.sessies == 1 and time.time() - t > 2.5, ecu.adressen)
k.stop()
ecu = nep_kw1281.NepPoort(rust_s=2.0, doof_voor_75=2)
k = kw1281.KW1281(serieel=ecu)
tekst = mislukt(k.wek)
check("de ECU hoort de 75 twee keer niet: wekken mislukt", tekst is not None and ecu.sessies == 0, tekst)
k.wek()
check("de volgende poging wacht lang genoeg en lukt (de ECU geeft pas 0,65 s na het adres op)",
      ecu.sessies == 1 and ecu.adressen == [(0x01, True), (0x01, True)], ecu.adressen)
k.stop()

print("Herhalingen van de ECU")
k, ecu = lijn(doof_voor_75=1)
k.wek()
check("75 te laat: de ECU herhaalt 55 01 8A, het transport bevestigt opnieuw en gaat door", ecu.sessies == 1 and len(k.identiteit_blokken) == 3)
check("daarna gewoon lezen", k.tien_waarden() == ecu.waarden)
k, ecu = lijn(mis_complement=5)
blokken = k.wek()
check("de ECU mist een complement en stuurt het blok opnieuw: het transport begint dat blok opnieuw",
      ecu.herhaalde_blokken == 1 and blokken[0].startswith(b"043906022B") and len(blokken) == 3, (ecu.herhaalde_blokken, blokken))

print("Alleen adres 01")
k, ecu = lijn()
k.adres = 0x33
tekst = mislukt(k.wek, soort=kw1281.VerbodenTitel)
check("een ander wekadres (0x33, zoals OBD2) weigert het transport zonder de lijn aan te raken",
      tekst is not None and ecu.adressen == [] and not ecu._randen and kw1281.TOEGESTANE_ADRESSEN == {0x01}, (tekst, ecu.adressen))

print("Voertuigbestand VW 1600i")
vpad = next((p for p in (os.path.join(HERE, "..", "vehicles", "vw_1600i_digifant.py"),
                         os.path.join(HERE, "repo_sjabloon", "vehicles", "vw_1600i_digifant.py")) if os.path.exists(p)), None)
if vpad:
    spec = importlib.util.spec_from_file_location("v1600", vpad)
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    nep = nep_kw1281.NepPoort()
    w = v.decode({"12": nep.waarden, "A4": bytes([23, 199, 26, 88]), "A8": bytes([23, 199, 26, 1]),
                  "B2": bytes(nep.ram[0x2E:0x38]), "B9": bytes(nep.ram[0x95:0x9F])})
    check("warm stationair: toerentalindex 199 = 929 tpm, 13,65 V, cilinderkop 118 C", 928 < w["toerental"] < 931 and abs(w["accuspanning"] - 13.65) < 0.01
          and w["motortemperatuur"] == 118.0, w)
    check("groep 4: ontstekingsbyte 26 is 4 graden voor BDP (voorbeeld van 1600i.de); 250 loopt rond naar 36",
          w["voorontsteking_groep"] == 4 and v.decode({"A4": bytes([0, 0, 250, 0])})["voorontsteking_groep"] == 36, w["voorontsteking_groep"])
    check("RAM 35: ontstekingstelling 210 is 4,2 graden voor BDP, dezelfde rekensom als de groep", abs(w["voorontsteking"] - 4.17) < 0.01, w["voorontsteking"])
    check("RAM 2E: toestand stationair, niet in de basisinstelling", w["toestand"] == 1 and w["basisinstelling"] == 0
          and v.decode({"B2": bytes([0x41]) + bytes(9)})["toestand"] == 2 and v.decode({"B2": bytes([0x01]) + bytes(9)})["basisinstelling"] == 1)
    check("RAM 95..9E: luchtdruk ~101 kPa, dichtstand gasklep, adaptaties", 100 < w["luchtdruk"] < 103 and w["gasklep_dichtstand"] == 52
          and w["mengseladaptatie"] == 128 and w["stationairadaptatie"] == 20, w)
    check("toerentalindex 240 = staat stil", v.decode({"12": bytes(9) + bytes([240])})["toerental"] == 0)
    check("groep 8: toestand stationair", w["toestand_groep"] == 1)
    ident = v.identiteit([b"043906022B  ", b"DIGIFANT 1.8        1234", b"\x00\x01\x02\x03"])
    check("identiteit: onderdeelnummer en naam, zonder de vier cijfers en zonder de codering",
          ident == {"onderdeelnummer": "043906022B", "software": "DIGIFANT 1.8"}, ident)
    check("een te kort blok geeft geen waarde in plaats van een fout", v.decode({"12": bytes(4)}) == {})
    check("alle lezingen in het bestand zijn benoemde lezingen", all(l[0] in ("tien_waarden", "groep", "ram_venster") for l in v.LEZINGEN.values()))
else:
    print("  --   overgeslagen (voertuigbestand niet gevonden)")

print("Opname, momentopname en blokkades via de scripts")
if os.path.exists(os.path.join(HERE, "..", "vehicles", "vw_1600i_digifant.py")):
    BOOT = ("import sys, runpy, kw1281, nep_kw1281; kw1281.open_poort = lambda port, baud: nep_kw1281.NepPoort(rust_s=0.3); "
            "kw1281.RUST_S = 0.4; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
    tmp = tempfile.mkdtemp(prefix="kw1281_selftest_")

    def run(args, voertuig="vw_1600i_digifant", nep=True):
        env = dict(os.environ, KLINE_DATA=tmp, KLINE_VOERTUIG=voertuig, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(([PY, "-c", BOOT] if nep else [PY]) + args, cwd=HERE, env=env, capture_output=True, text=True, timeout=120)

    try:
        r = run(["egr_capture.py", "rit_kever.jsonl", "14"])
        rijen = [json.loads(l) for l in open(os.path.join(tmp, "rit_kever.jsonl"))] if os.path.exists(os.path.join(tmp, "rit_kever.jsonl")) else []
        check("de gewone opname schakelt door naar KW1281 en loopt", r.returncode == 0 and len(rijen) >= 5, r.stdout[-300:] + r.stderr[-500:])
        check("elke regel heeft de tien waarden (10 bytes)", bool(rijen) and all(len(x.get("12", "")) == 20 for x in rijen))
        gezien = set().union(*(set(x) for x in rijen)) if rijen else set()
        check("de roulerende lezingen komen langs, en geen meetwaardegroepen", {"B2", "B9"} <= gezien and not gezien & {"A2", "A4", "A8"}, sorted(gezien))
        meta = json.load(open(os.path.join(tmp, "rit_kever.meta.json"))) if rijen else {}
        check("zijbestand: onderdeelnummer en naam, zonder de vier cijfers",
              meta.get("ecu") == {"onderdeelnummer": "043906022B", "software": "DIGIFANT 1.8"} and "0000" not in json.dumps(meta), meta)
        live = json.load(open(os.path.join(tmp, "live.json"))) if rijen else {}
        check("live.json: omgerekende waarden", 928 < live.get("waarden", {}).get("toerental", 0) < 931, live.get("waarden"))
        check("geen verloren sessie en geen mislukte lezing", "sessie_verloren 0" in r.stdout and "mislukt 0" in r.stdout, r.stdout[-200:])
        r = run(["kw1281_opname.py", "--rapport", os.path.join(tmp, "rit_kever.jsonl")], nep=False)
        check("samenvatting van de opname", r.returncode == 0 and "toerental" in r.stdout and "firmware" in r.stdout, r.stdout[-200:] + r.stderr[-300:])
        ra, rb = run(["selftest_snapshot.py", "kever_a"]), run(["selftest_snapshot.py", "kever_b"])
        snap = json.load(open(os.path.join(tmp, "zelftest_kever_a.json"))) if os.path.exists(os.path.join(tmp, "zelftest_kever_a.json")) else {}
        check("momentopname: identiteit, leeg foutgeheugen, elke lezing drie keer",
              ra.returncode == 0 and rb.returncode == 0 and snap.get("stored") == [] and sorted(snap.get("blocks", {})) == ["12", "A2", "A4", "A8", "B2", "B9"]
              and all(len(reeks) == 3 for reeks in snap["blocks"].values()), ra.stdout[-200:] + ra.stderr[-400:])
        r = run(["selftest_diff.py", "kever_a", "kever_b"], nep=False)
        check("twee momentopnames vergelijken", r.returncode == 0 and "VOOR:" in r.stdout, r.stdout[-200:] + r.stderr[-300:])
        for script, wat in (("obd_scan.py", "OBD2-scan"), ("ecu_info.py", "blokkenscan"), ("identify.py", "init-methoden proberen"),
                            ("onrust_snel.py", "snelle onrustmeting")):
            r = run([script] + (["x", "1"] if script == "onrust_snel.py" else []), nep=False)
            check(f"{wat} weigert bij dit voertuig, zonder de poort te openen", r.returncode != 0 and "could not open port" not in r.stderr
                  and ("KWP2000" in r.stderr or "SNEL_BLOKKEN" in r.stderr), r.stderr[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
else:
    print("  --   overgeslagen (alleen in de repo-indeling, waar het voertuig met KLINE_VOERTUIG te kiezen is)")

print()
if fouten:
    print(f"{len(fouten)} FOUT: " + "; ".join(fouten))
    sys.exit(1)
print("ALLES OK")
