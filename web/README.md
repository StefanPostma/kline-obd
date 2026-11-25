# Matiz OBD live

Live waarden uitlezen van een auto (gebouwd voor een Chevrolet/Daewoo Matiz 0.8, 2009) via een FTDI
K-line (KKL) kabel, met een webinterface die het voertuig/de ECU herkent en afwijkingen en
"stotter-momenten" vastlegt. Alleen lezen, met één uitzondering: foutcodes wissen (mode 04), alleen in kabelmodus,
na een momentopname en na jouw bevestiging in de pagina (zie "Foutcodes wissen").

De meetresultaten en het onderzoek aan de Matiz staan in `../docs/matiz-m200-08.md`; het opnameformaat in `../docs/opnameformaat.md`.

## Starten

```
cd web

# 1. Zonder auto: gesimuleerde ECU (NEP-data, rode banner in de pagina)
python server.py --demo --profile matiz08

# 2. In de auto: eerst de go/no-go test, dan de interface
python kline_test.py --init
python server.py --profile matiz08

# 3. Meekijken zonder de kabel te openen (het uitleesscript in ../reader houdt de kabel vast)
python server.py --follow --profile matiz08          # nieuwste rit*.jsonl, live
python server.py --replay ../examples/rit_voorbeeld.jsonl --speedup 25 --profile matiz08
```

**Meekijken (`--follow` / `--replay`, `feed.py`).** De pagina leest dan de JSONL-opname van het uitleesscript (bij de Matiz
`egr_capture.py`, gestart via `rit.py`) in plaats van de kabel te gebruiken, dus `server.py` en de opname kunnen tegelijk
draaien. Er gaat niets naar de auto. Een opname die langer dan 20 s niet groeit geldt niet als lopende rit (status "Wacht op
opname"); gebruik daarvoor `--replay`. Foutcodes lezen en ECU-identificatie werken in deze modus niet. Gebruik `--profile
matiz08` (een profiel wordt nooit uit de auto afgeleid), anders kent de pagina alleen de standaard-PID's in de opname.
Tests: `selftest_feed.py` (met een verzonnen auto) en `selftest_hub.py`.

**De pagina kent geen enkele auto (`signalset.py`).** Alles wat per voertuig verschilt zit in twee dingen: een
*signalenbestand* en een regel in `vehicle.PROFILES`. De pagina en de analyse kennen alleen standaardsleutels
(`rpm`, `map`, `ect`, `iat`, `speed`, `o2_b1s1`, `tps`, `stft`, `ltft`, ...) en tonen verder wat het bestand aanlevert.
Voor de Matiz is dat `../vehicles/matiz_m200_08.py`, geschreven door de uitlezer; de pagina laadt het bij elke start
van de bron opnieuw, dus een aanpassing daar is na wisselen van bron zichtbaar.

- **Standaard-PID's** (`"pid0D": "<hex>"` in de opname) worden voor elke auto op dezelfde manier gelezen en gaan altijd voor.
- **Fabrikantsignalen** (`"01": "<hex van blok 01>"`) komen uit `SIGNALEN = {sleutel: (blok, functie(bytes), eenheid,
  omschrijving, zekerheid, opmerking)}`. Ze verschijnen als kaarten onder "Fabrikantsignalen", met onder elke waarde hoe
  zeker de betekenis is: ✓ `pid` (geijkt met een standaard-PID), ≈ `handboek`, ? `gedrag` (alleen uit het gedrag afgeleid),
  ◇ `firmware` (uit het programma van de ECU gelezen) en ⚠ `aanname` (aangenomen sensorkromme, nog te meten). Een label
  dat de pagina niet kent, verschijnt als "onbekend zekerheidslabel", nooit als niets. Zo wordt een vermoeden nooit als
  feit getoond.
- **Transport:** `TRANSPORT = "kwp2000"` (standaard) in het signalenbestand zegt hoe de uitlezer met de ECU praat. Meekijken
  met een opname werkt voor elk transport, bv. `"kw1281"` voor de Digifant-ECU van een VW 1600i. De **kabelmodus** van de
  pagina spreekt alleen KWP2000/OBD2 en weigert elk ander transport: `server.py` stopt dan met een melding, en in de pagina
  geeft "Kabel" een melding terwijl het meekijken blijft draaien. Er gaat dan niets naar de ECU en ook het wissen (modus 04)
  is niet bereikbaar. Test: `selftest_transport.py`.
- **Wat de regels mogen gebruiken:** `CANONICAL = {sleutel: "rpm"}` koppelt een signaal aan een standaardsleutel (alleen
  wat geijkt is, anders gaan de afwijkingsregels op een gok draaien). `TPS = {"signal", "rest", "full"}` schat een
  gaskleppercentage uit een spanning (nodig voor de stotterdetectie). `HIGHLIGHT = [...]` kiest de grafieken bij een
  vastgelegd moment. Dit mag in het bestand of in het profiel staan; het bestand gaat voor.
- **Leesplan voor de kabelmodus:** `KERN_BLOKKEN` (elke ronde), `ROULEREND_BLOKKEN` (één per ronde aan de beurt),
  `PID_ELKE_RONDE` en `PID_ROULEREND`, allemaal lijsten met getallen (`[0x01, 0x0C]`). Zonder deze namen leest de
  kabelmodus alleen de standaard-PID's die de ECU ondersteunt. Een blok waar het bestand geen signaal voor heeft, wordt
  niet opgevraagd; een blok of PID die de ECU 3× weigert, wordt losgelaten.
- **Testvensters:** een optionele `vensters(waarden) -> {naam: bool}` (met `VENSTER_LABELS`) toont chips als "EGR-test ●":
  de motor zit nu in het venster waarin de ECU die zelftest uitvoert.
- **Opnameformaat** (het contract met het uitleesscript): één JSON-regel per ronde, `{"t": seconden, "e": epoch,
  "<blok>": "<hex>", "pid<XX>": "<hex zonder 41 XX>"}`. Elk script dat dat schrijft werkt, voor elke auto. Regels zonder
  `t` worden overgeslagen. Optioneel zijbestand `<opnamenaam>.meta.json` met `{"ecu": {"onderdeelnummer", "kalibratie",
  "software"}}` voor het paneel "Voertuig / ECU". Kanalen die roulerend worden gelezen (bv. eens per 22 s) gelden pas als
  verouderd na 2,5× hun gemeten interval (minimaal 10 s). `VENSTER_OPMERKINGEN = {naam: tekst}` in het signalenbestand
  wordt de tooltip bij een testvenster-chip. De knop "Annotatie: probleem" (voorheen "Nu stotterde hij"; het probleem kan van elke aard zijn) bewaart bij `--follow` ook een regel in
  `logs/<opnamenaam>.markeringen.log` (kloktijd `e` en `t` van de laatste opnameregel); de opname zelf wordt nooit aangepast.
  Het volledige formaat staat in `../docs/opnameformaat.md`.

**Een andere auto aansluiten:** (1) laat het uitleesscript voor die auto het opnameformaat schrijven; (2) schrijf een
signalenbestand in bovenstaand schema, of laat het weg om alleen de standaard-PID's te tonen; (3) start
`server.py --follow <map of bestand> --signals <bestand> --profile <naam>`. Een profielregel in `vehicle.py` (met `"feed"`:
map, patronen en het pad naar het signalenbestand) is alleen nodig voor een vast stationair bereik en om het pad niet
steeds mee te geven. De directe kabelmodus werkt zonder leesplan met elke OBD2-auto op de standaard-PID's; staat er in het
signalenbestand een leesplan, dan leest hij ook de fabrikantblokken (zie "Schakelen" hieronder).

**Schakelen tussen opname en kabel (knoppen "Opname" / "Kabel" bovenin de pagina, `hub.py`).** Je hoeft de server niet te
herstarten: de pagina wisselt de bron zelf (`POST /api/source`). Zonder `--follow`/`--replay` begint hij met de kabel, mét
begint hij met de opname. Wisselen naar **Kabel** wordt geweigerd als een ander programma de kabel open heeft (bv. de opname
van het uitleesscript; gecontroleerd met `lsof`, de melding noemt het programma): twee programma's op één K-line storen
elkaar. Wisselen naar **Opname** sluit de kabel weer, zodat het uitleesscript hem kan gebruiken. Verzoeken van andere
websites (vreemde `Origin`, geen JSON) worden geweigerd. Test: `selftest_hub.py` (alleen simulator).

**De kabelmodus is voor auto's met OBD2/KWP2000 (10400 baud).** Na een mislukte fast init probeert `kwp.py` (`connect()`: eerst
`fast-break`, dan `slow`) een 5-baud-init op het functionele adres 0x33. Op een ECU die een ander protocol spreekt, zoals de Digifant
van een VW 1600i (KW1281, 1200 baud, adres 0x01), krijgt dat geen antwoord. Volgens de firmware-analyse van die ECU richt het ook geen schade aan: het diagnosedeel herstart zichzelf zoals bij contact aan, de motorregeling merkt er niets van
en er wordt niets opgeslagen. **Dat is uit het programma van de ECU gelezen en niet op een echte ECU geprobeerd.** De weigering op
`TRANSPORT` beschermt alleen als je het VW-voertuigbestand kiest; de pagina weet niet wat er aan de kabel hangt. Gebruik de
kabelmodus daarom alleen op een auto die OBD2 spreekt, en kijk bij een andere auto mee met een opname (`--follow`, `--replay`).

**Kabelmodus met leesplan (`analyzer.py`, `signalset.py`).** Met `--profile matiz08` (of `--signals <bestand>`) laadt de
kabelmodus hetzelfde signalenbestand als het meekijken en leest hij per ronde de kernblokken via KWP service 21 (verzoek
`21 <id>`, antwoord `61 <id>` + databytes), de vaste PID's en één roulerend blok en één roulerende PID. De waarden worden
net als bij een opname gedecodeerd (kaarten, zekerheidslabels, testvensters, gaskleppercentage), dus beide bronnen tonen
dezelfde grootheden. Alleen leesverzoeken. Service 21 antwoordt op het functionele adres 0x33, dus `kwp.py` verandert niet
(bevestigd door de uitleessessie met de opnames van 23-11). Per ronde zijn dat ~8 verzoeken, op de echte lijn ~0,8
ronde/s; bij zo'n grof ritme beoordeelt de stotterdetectie toerentalsprongen alleen stilstaand, net als bij het
meekijken. Test: `selftest_cable.py` (alleen simulator met verzonnen blokken); **nog niet op de auto geprobeerd**.

**Foutcodes wissen (`analyzer.py`, `snapshot.py`, `obd.clear_dtcs`).** Het enige verzoek van de
pagina dat de ECU verandert: SAE J1979 mode 04 (`04` → `44`). **Dit is niet terug te draaien:** opgeslagen en pending codes, het
freeze frame (vaak het enige bewijs) en de storingslamp gaan weg en alle readiness-monitoren staan daarna op "niet gereed"; of de
geleerde waarden (LTFT, stationair) meegaan is voor deze ECU onbekend, de pagina laat het verschil zien. Zonder codes en met de lamp uit
levert wissen niets op. Werkwijze (knop "Foutcodes wissen…" onder "Foutcodes"):
1. **Voorwaarden** (anders weigert de pagina en noemt de reden): kabelmodus en verbonden; motor uit (toerental ≥ 3 s op nul); auto
   staat stil; accuspanning > 11,8 V (sleutel `batt` of `accuspanning`; de Matiz geeft die via blok 0C, dus start met `--profile matiz08`).
2. **Momentopname vooraf** in het formaat van `zelftest_<label>.json` van de uitleessessie: MIL/readiness (PID 01), codes (03/07/18),
   freeze frame, modus 06, LTFT en alle blokken uit `ALLE_BLOKKEN` van het signalenbestand, elk 3×. Mislukt het essentiële deel (PID 01
   01 of mode 03), dan is er geen momentopname en geen wissen. Bestand: `logs/zelftest_wissen_<tijd>_voor.json`.
3. **Bevestiging** in de pagina: wat er nu op de ECU staat, wat wissen doet, een vinkje en "Wis nu". De momentopname is 5 minuten
   geldig, het id is eenmalig; de voorwaarden worden bij het verzenden nog eens gecontroleerd.
4. **Eén verzoek, geen herhaling en geen terugval naar service 14.** `7F 04 78` (antwoord volgt) wordt afgewacht; een weigering
   (`7F 04 22` motor aan, `11`/`12` niet ondersteund) wordt uitgelegd; bij een zoek antwoord wordt teruggelezen of het gelukt is.
5. **Controle achteraf** in dezelfde toestand: tweede momentopname (`..._na.json`), voor/na in de pagina, een regel in `logs/acties.log`.
   Het volledige verschil: `selftest_diff.py` van de uitleessessie, met `KLINE_DATA=<map met de bestanden>`.
Het eindpunt is `POST /api/dtc/prepare|clear|cancel`: Origin verplicht (alleen de eigen pagina), JSON verplicht, voor wissen het
id van een momentopname en `"confirm": true`. Schakelen naar een andere bron tijdens wissen wordt geweigerd. Test:
`selftest_clear.py` (alleen simulator, inclusief een controle dat mode 04 nergens anders verstuurd kan worden); **nog nooit op de auto
geprobeerd.** Het eerste echte gebruik gebeurt alleen met de eigenaar erbij, contact aan, motor uit.

Open daarna <http://127.0.0.1:8777>. Er kan maar één programma tegelijk
de kabel gebruiken: stop `kline_test.py` voordat je `server.py` start.

## In de auto

1. Kabel in de OBD-poort (pin 16 heeft altijd 12 V, daar wordt de kabel mee gevoed), contact aan.
2. `kline_test.py --init` toont alle TX/RX-bytes en daarna:
   - **AANGEKNOPT + voertuig/ECU + live waarden** → kabel en ECU werken, start `server.py`.
   - **echo, maar aanknopen mislukt** → contact aan? Plak de uitvoer terug, dan pas ik de init aan.
   - **"init lukte, maar eerste OBD-verzoek mislukte"** → framing-/protocolprobleem, niet de kabel.
   - **geen echo en geen antwoord** → K-line zit niet op de FTDI (kabel ongeschikt of niet gevoed).
     Alternatief: een losse ELM327 (Vgate/OBDLink) met `python-obd`.
   - Kabels die niet echoën maar wel werken worden vanzelf herkend (tweede poging zonder echo-controle).
3. In de pagina: bovenaan staat de verbindingsstatus; "Foutcodes lezen" geeft opgeslagen/pending
   codes en de freeze frame. Lees die **eerst**, vóór je gaat rijden.

## Voertuig/ECU-herkenning (voor hergebruik bij andere auto's)

Bij elke verbinding leest de uitlezer, alleen met standaard lezende OBD2-vragen:

- **VIN, kalibratie-ID en ECU-naam** (mode 09; veel oudere ECU's hebben dit niet, dat wordt dan gemeld),
- **OBD-norm** (bv. EOBD) en **benzine/diesel**, protocol, init-methode en keybytes,
- VIN → merk (alleen zekere WMI-codes; anders "onbekend merk") en modeljaar.

Het resultaat staat in het paneel "Voertuig / ECU" en wordt naast elk log opgeslagen
(`logs/<tijd>.identity.json`), zodat meetreeksen aan een auto te koppelen zijn. De knop
"Uitgebreide ECU-identificatie (KWP 1A)" (of `--probe-ident`) probeert daarnaast de gebruikelijke
KWP2000-identificatieopties en toont wat de ECU antwoordt, ruw en als tekst.

**Profielen** (`vehicle.py`): de drempels in `analyzer.py` zijn generiek voor een benzinemotor; wat per auto
verschilt (nu: normaal stationair toerental) staat in een profiel. Een profiel wordt nooit uit het merk
afgeleid (een GM Korea-VIN kan ook een andere auto zijn); kies het met `--profile`. Nieuwe auto: voeg een regel
aan `PROFILES` toe. Bij **diesel** staan de benzine-regels (lambda, brandstofcorrectie, inlaatdruk) automatisch uit.

## Wat de pagina doet

- **Live waarden** met min/gem/max en sparkline; klik een kaart voor een grafiek van 2 minuten.
- **Afwijkingen**: regels met indicatieve drempels (bovenaan `analyzer.py`), bv. onrustig stationair,
  LTFT te hoog, lambdasonde wisselt niet, hoge MAP op stationair, accuspanning, thermostaat.
  **De stationair-regels** (onrustig toerental, toerental buiten het bereik van het profiel, hoge inlaatdruk, springende gasklep) kijken
  alleen naar een aaneengesloten stilstand: pas 3 s na de laatste beweging (snelheid > 0), zonder de laatste 2,4 s (gas geven en
  wegrijden beginnen vóór de snelheid > 0 meldt), met minstens 8 metingen, niet in de 10 s na het starten van de motor, en niet als
  het toerental in dat venster boven 1500 kwam (gas geven of de snelle stationair van een koude start). Een toerental dat rustig
  daalt (koude motor) telt niet als onrust: de spreiding wordt gemeten rond het verloop. Reden (24-11): een venster van vaste lengte
  telde rijden vlak vóór het stoppen of na het wegrijden mee; 18 van de 20 rode "Onrustig stationair"-periodes van één rit waren dat, en
  de toerentalkaart werd daardoor rood. Na de aanpassing geven de vijf opgenomen ritten (rit1, rit2, koud, wissel, origineel) geen valse
  stationair-meldingen meer; een echt schommelend stationair (700 ↔ 1120 rpm, stilstaand) wordt nog steeds gemeld (`selftest.py`).
  Stilstaand gas geven (toerental boven 1500) is ook geen stotter-moment meer; de gasklep wordt maar eens per seconde gemeten en mist
  zo'n stoot.
  Het zijn aanwijzingen, geen diagnose.
- **Vastgelegde momenten**: jouw annotaties (knop "Annotatie: probleem") zijn leidend. Automatisch worden de 8 s ervoor en 3 s
  erna van alle waarden bewaard bij een toerental-dip/-piek zonder gasbeweging, een STFT-uitschieter (>25%), een
  MAP-sprong op stilstand of verbindingsverlies. Die meldingen staan in de lijst als "automatisch · indicatief".
  Bij een grof ritme (opname: ≥ 0,8 s tussen toerentalmetingen) beoordeelt `_detect` geen toerentalsprong zolang de auto
  rijdt (> 1 km/u) en niet in de eerste 10 s na het starten van de motor (ook geen MAP-val): optrekken en schakelen
  geven dan net zulke sprongen. Rit 23-11 (koud): 13 van 13 automatische meldingen waren dat (7 optrekken, 4 wegrijden/
  schakelen, 2 opstarten; getoetst aan toerental, snelheid en gas); na de aanpassing blijft er 0 over. "Bijna afgeslagen" blijft altijd een moment. Wat de
  pagina wel kan zien zijn de onrustkanalen per cilinder (zie het verslag van de uitlezer); een detector daarop is nog
  niet gebouwd, want 6 gemarkeerde momenten uit één rit zijn te weinig om drempels op af te stellen.
- Alle metingen worden ook als CSV weggeschreven in `logs/` (`t_epoch,key,value`).

## Beperkingen (eerlijk)

- **Snelheid:** in de simulator haal ik ~9 verzoeken/s (toerental ~2×/s, de rest 0,3–1×/s). Op een echte kabel
  verwacht ik minder (USB-latency ~16 ms per ronde); dit zijn schattingen. Een piek van ~1 s in bv. STFT of MAP kan
  tussen twee metingen vallen; een toerental-dip wordt wel goed gezien. Er zit 5 ms pauze tussen de bytes van elk
  verzoek (ISO-voorschrift, voorkomt "ECU zwijgt"); `--p4-ms 0` is sneller maar niet elke ECU verdraagt dat.
- Welke waarden de ECU levert (PID's) weet pas de auto zelf; de pagina toont wat de ECU opgeeft.
- De init-methoden (`fast-break`, `fast-byte`, `slow-5baud`), de framing en de identificatie zijn getest tegen de
  simulator (`selftest.py`). Op de echte Matiz is `fast-break` gemeten (zie boven); de timeout-fix in `kwp.py` en de
  09-00-fix in `obd.py` zijn bevestigd in een kabelsessie van 24-11 (66 s, 100 van 100 verzoeken goed, kalibratie-ID
  via mode 09 gelezen; de ECU-naam kwam toen niet binnen).

## Bestanden

| Bestand | Doel |
| --- | --- |
| `kwp.py` | K-line transport: init (fast/slow), framing, echo-controle, P4-pauzes |
| `obd.py` | OBD2-services: PID's, foutcodes, freeze frame, voertuig-identificatie |
| `vehicle.py` | VIN-decodering en voertuigprofielen |
| `analyzer.py` | poller, statistiek, afwijkingen, events, CSV-log |
| `server.py`, `static/index.html` | webinterface (alleen op 127.0.0.1) |
| `feed.py`, `signalset.py`, `selftest_feed.py` | meekijken met een opname van het uitleesscript (`--follow`, `--replay`); `signalset.py` leest het signalenbestand van een auto |
| `hub.py`, `selftest_hub.py` | schakelen tussen opname en kabel vanuit de pagina |
| `selftest_transport.py` | `TRANSPORT` in het signalenbestand: de kabelmodus weigert elk transport behalve KWP2000; zekerheidslabels van de pagina |
| `snapshot.py`, `selftest_clear.py` | foutcodes wissen: momentopname vooraf/achteraf en de bewaakte wisstap |
| `selftest_cable.py` | kabelmodus met leesplan (service 21) tegen de simulator |
| `sim.py` | gesimuleerde ECU + nepkabel voor demo en tests |
| `selftest.py` | test van alles tegen de simulator (`--quick` = alleen protocol) |
| `kline_test.py` | go/no-go test van kabel + ECU met ruwe bytes |

## Problemen

- **Geen `/dev/cu.usbserial-*`**: kabel niet herkend. `ls /dev/cu.*` en probeer een andere USB-poort. Staat er niets,
  controleer dan of de FTDI überhaupt zichtbaar is: `system_profiler SPUSBDataType | grep -i -A8 ftdi`. Is hij
  zichtbaar maar komt er geen `/dev`-entry, dan bindt Apple's driver niet aan deze kabel (eigen VID/PID);
  omweg is pyftdi + libusb (nog niet gebouwd).
- **Geen echo, terwijl de kabel wel herkend wordt**: bij de gebruikte kabel (FT232R) schakelt **RTS aan** de K-line
  uit (gemeten: echo bij RTS=0, geen echo bij RTS=1). `open_port` zet DTR/RTS daarom standaard uit;
  overschrijven kan met `--dtr 1` / `--rts 1`.
- **Echo wel, maar de ECU zwijgt**: staat het contact aan (sleutel in stand 2, dashboardlampjes aan)? Zo niet, dan is
  de ECU niet gevoed. Verder eventueel `--p4-ms 10`.
- **C-compiler faalt op deze Mac** (`tapi error: malformed file`): standaard-SDK 27.0 is te nieuw voor de
  linker. Gebruik `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk` per commando.
