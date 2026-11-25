# Chevrolet/Daewoo Matiz M200 0.8: wat er gemeten en gevonden is

Werknotities van november 2026, in de volgorde waarin ze zijn ontstaan. Een correctie staat bij de bewering die ze
corrigeert, zodat ook te zien is welke vermoedens niet klopten. Alles is alleen-lezend gemeten. De betekenis van de
fabrikantblokken is afgeleid uit gedrag en uit een werkplaatshandboek, en door de fabrikant niet bevestigd.

Namen van ritopnames, momentopnames en ruwe ECU-antwoorden verwijzen naar metingen die niet in deze repo staan: ze
beschrijven één auto. Eén rit staat als voorbeeld in `examples/`. Paden zijn relatief aan de repo.

## Gemeten op de echte Matiz 0.8 (2026-11-23)

- ECU antwoordt op **KWP2000 fast init**, keybytes `EF 8F`, ECU-adres `0x11`. **Gecorrigeerd:** de ECU antwoordt op het
  functionele adres `0x33` én op het fysieke adres `0x11` (StartCommunication `81 11 F1 81 04`, verzoeken
  `8x 11 F1 ..`; zo zijn service 1A en alle 21-blokken gelezen). De eerdere bewering "fysiek adres blijft stil" klopt
  niet. Ondersteunde PID's: 01 03 04 05 06 07 0B 0C 0D 0E 0F 11 13 14 15 1C 21. Standaard OBD-norm EOBD, benzine.
- **Mode 09 werkt wel** (gecorrigeerd; eerder stond hier "zonder bruikbare data"): `09 00` geeft `49 00 01 FC 00 00 00`.
  De `01` is de berichtenteller, het masker is `FC 00 00 00`, dus infotype 01 t/m 06. `obd.mode09_supported` leest het
  masker één byte te vroeg (`p[2:6]`) en kwam zo op 08 t/m 0E, waardoor VIN en kalibratie-ID nooit zijn opgevraagd
  (hersteld in `obd.py`: bij 7 bytes wordt de teller overgeslagen; alleen tegen de simulator getest). Gelezen: kalibratie-ID (`09 04`) `8P04B231`, CVN (`09 06`) `00 00 DA 9B`; de VIN
  (`09 02`) is niet in de ECU geprogrammeerd (17x `FF`).
- **De wekpuls is grillig**: via `TIOCSBRK` maakt Apple's FTDI-driver de pulsbreedte onvoorspelbaar (USB-vertraging van
  enkele ms); ongeveer 1 op 4 pogingen wordt door de ECU herkend. `kwp.start_fast` probeert daarom tot 16
  (laag/hoog)-combinaties en onthoudt de laatste die werkte. Een hardware-puls via 0x00 op 300 baud werkte niet.
  **Gecorrigeerd:** de driver is niet de beperkende factor. In de lezer in `reader/kline.py` lukte de init
  met `time.sleep` 4 van de 17 keer; met een busy-wait op `time.perf_counter` (25 ms laag, StartCommunication op 50 ms na
  de flank) 7 van de 8 keer en in de reeksen daarna steeds bij de eerste poging. Aan de hostkant gemeten (tijd tussen
  de ioctl's): 25,3 tot 25,5 ms laag, start van het verzoek op 50,0 tot 50,1 ms. `time.sleep` slaapt op macOS
  milliseconden te lang; dat valt buiten de tolerantie van +/- 1 ms. Daarnaast leest `start_fast` het antwoord met
  dezelfde `_recv` als hieronder, dus een deel van de "mislukte wekpulsen" kan een beschadigd antwoord zijn geweest
  (niet apart nagemeten).
- **De lijn is niet ruisgevoelig; het was een fout in onze lezer** (gecorrigeerd, oorzaak staat aan het eind van dit
  punt). Oorspronkelijke waarneming: ~40% van de ECU-antwoorden komt met een foute byte/checksum binnen. Dit hangt niet af
  van de baudrate (plateau 10.000–10.800 baud) of de P4-pauze. Een verloren antwoord wordt opnieuw aangevraagd
  (tot 2×), zodat >90% van de verzoeken slaagt; de pagina toont de foutstatistiek ("Veel mislukte verzoeken").
  **Oorzaak gevonden (2026-11-23):** `kwp._recv` wijst `s.timeout` toe terwijl het antwoord binnenkomt. pyserial roept
  bij elke toewijzing aan `timeout` `_reconfigure_port()` aan, en bij een niet-standaard snelheid (10400) zet die de
  poort op macOS eerst op 38400 baud en daarna met `IOSSIOSPEED` terug op 10400. Bytes die op dat moment binnenkomen
  raken beschadigd; lange antwoorden (de 21-blokken) het vaakst. A/B-meting met motor uit en contact aan, 2x60
  blokverzoeken (`21 01` / `21 0C`) zonder herhaling: `kwp.py` ongewijzigd 37 van 120 goed, dezelfde code met een
  `Serial` die bij een nieuwe timeout de poort niet herconfigureert 120 van 120 goed
  (een A/B-test met dezelfde kabel). DTR aan of uit maakt geen verschil (120 van 120 in beide standen,
  `dtr_test.py`). Oplossing: na het openen `s.timeout` niet meer toewijzen (vaste korte timeout en zelf een deadline
  bijhouden, zoals in `reader/kline.py`). **Doorgevoerd in `kwp.py` (2026-11-23):** de poort krijgt eenmalig
  een vaste timeout (`TICK`, 20 ms) en `KLine._read` houdt de wachttijd zelf bij; nergens in de ontvangstweg wordt
  `s.timeout` nog toegewezen. Alleen getest tegen de simulator (`selftest.py` groen); **nog niet nagemeten op de auto**,
  ook niet met draaiende motor. Ter vergelijking, de lezer in `reader/kline.py` (zelfde aanpak) met
  draaiende motor: stationair 3 gemist van 4417 verzoeken (0,1 %), tijdens een rit 272 van 5943 (4,6 %). Rijdend is er
  dus wel echte storing, maar een orde kleiner dan de eerdere 40 %. De herhaalpogingen blijven staan als vangnet. De wekpuls gebruikt nog `time.sleep`
  (busy-wait op `perf_counter` zoals in `kline.py` is nog niet overgenomen).
- **Ruis glipt soms door de checksum** (1 byte): gezien 15360 rpm en STFT −60%. `analyzer.LIMITS` gooit onmogelijke
  waarden weg en `JUMP` laat een grote sprong meteen opnieuw meten; alleen een bevestigde sprong telt.
  Vermoedelijk dezelfde oorzaak als hierboven (bytes beschadigd door de herconfiguratie); niet apart nagemeten.
- **Eerste rit (9 min, stad en randweg):** 29 automatisch vastgelegde "momenten" bleken bijna allemaal gewoon rijden
  (schakelen, gas loslaten/uitrollen, optrekken). Stotteren is automatisch moeilijk te onderscheiden van rijden;
  daarom de knop/spatiebalk **"Annotatie: probleem"** (toen nog "Nu stotterde hij"; bewaart 8 s ervoor en 3 s erna), en de strengere regels in
  `_detect`. Stationair was stabiel (σ 45 rpm). Zie ook `logs/*.csv` voor de ruwe metingen.
- Aangetaste keybytes bij het aanknopen (bv. `7F FC` i.p.v. `EF 8F`) zijn ruis, geen andere ECU.

## ECU-datablokken (KWP service 21) — wat we weten

`probe_ecu.py` (alleen lezen) vond 18 blokken van 32 bytes die de ECU vrijgeeft (ID 02, 03, 04, 06, 08–0D, 0F, 11–17;
ID F0 antwoordt "voorwaarden niet juist"). `blocks.py capture/diff` legt ze vast per toestand. **Betekenis is onbekend**;
herkend (analyse van 2026-11-23, op 1–3 rondes per toestand, dus voorzichtig): blok 0C byte 5–6 = toerental
(16-bit, 1 rpm, hoge zekerheid); lambda voor kat = byte/200 V op 0C[9] = 01[11] = 04[1] = 0D[19] (hoog); MAP als
16-bit op 0C[19:20] = 02[9:10] (hoog, schaal uit 2 ankerpunten); luchtdruk 02[7:8]; koelwater-ADC 16-bit 0C[14:15] =
02[0:1]; waarschijnlijk accuspanning 0C[18] = 02[3] (schaal onbekend); blok 14 byte 14–19 lijkt een geleerde
brandstofcorrectiefactor (0,89–0,96, laag-middel). **Gecorrigeerd:** blok 04 byte 5 is waarschijnlijk NIET
"inlaattemperatuur + 40" (eerdere bewering, op twee punten gebaseerd), maar de lambdasonde ná de kat. Een
EGR-positie was in die opnames niet te herkennen (alle lage belasting, EEGR dicht). **Inmiddels gevonden**, zie
"EGR-test en rit met blokopname" hieronder.
- **Blok 01 = ruwe ADC-waarden** (16-bit woorden, 10-bit op 5 V; herkend aan gedrag en aan de standaard-PID's): byte 0–1
  koelwater, 2–3 inlaatlucht, 4–5 gasklep (rust 105–106), 6–7 klopsensor, ruiskanaal (zie "Wat de documentatie zegt"), 10–11 lambda voor
  kat, 14–15 lambda na kat, **20–21 EGR-klepstand**, 22–23 MAP, 26–27 vermoedelijk tankvlotter (verspringt bij instappen
  en tijdens rijden), 28–29 open ingang (altijd 1023).
- Verder herkend op 2026-11-23, steeds aan gedrag: 0C[3] closed-loop-vlag (gelijk aan PID 03 in beide geteste toestanden),
  0D[4] vlag "gas dicht/stationair", 0D[28] vermoedelijk stationairregelaar (rond 80 bij warm stationair, 172 met gas open,
  128 met motor uit), 0C[12] = 02[2] loopt op met de gasklepopening, **02[24] EGR-stuursignaal**, 05[29:31] vermoedelijk
  kilometerstand in 0,1 km (liep 115 op tijdens een rit van 11,4 km). **Gecorrigeerd:** 02[0:1] = 0C[14:15] is geen
  16-bit koelwater-ADC maar twee losse bytes, koelwater en inlaatlucht, elk 0,75·N − 48 °C (klopt met PID 05 en 0F op
  meerdere punten, o.a. B8 = 90 °C en 8F = 59 °C).
- Een eerste vermoeden dat blok 03 byte 28–30 "per cilinder" is (derde waarde 25–30% lager bij 2500 rpm) werd met 40
  rondes **niet bevestigd**: stationair is juist de derde hoger, de waarden zijn onrustig. Niet verder op bouwen.
  **Aanvulling (rit met 686 metingen, 2026-11-23):** het patroon hangt van de rijtoestand af en is bij gas los rustig;
  zie "Stotteren" in het volgende hoofdstuk. Het blijft een aanname dat dit een maat per cilinder is.
- **Gecorrigeerd:** EGR-positie en -stuursignaal zijn gevonden (blok 01 byte 20–21 en blok 02 byte 24). Aansturen (service 30) is nooit geprobeerd: zonder
  fabrikantdocumentatie zijn de commando's onbekend. Het Matiz-werkplaatshandboek beschrijft een EEGR-test met een
  scantool; dat is de gedocumenteerde route.

## EGR-test en rit met blokopname (2026-11-23)

**Uitslag: de elektronische EGR-klep werkt.** Alles is alleen-lezend gemeten; er is niets aangestuurd of gewist.
Positiesensor en stuursignaal zijn herkend aan hun gedrag, niet uit documentatie.

- **Signalen:** blok 01 byte 20–21 is de klepstand: 125–127 = dicht (0,61 V), tijdens de rit tot 765 (3,74 V) en alleen
  naar boven. Blok 02 byte 24 is het stuursignaal: 0 = dicht, tijdens de rit 70 tot 138.
- **Stilstaand stuurt de ECU de EGR niet aan.** Vrij gas op 2700–2900 rpm, zowel koud (62–78 °C) als warm (87–93 °C), in
  closed loop: klepstand bleef 126/127 en het stuursignaal 0. Een EGR-test bij stilstand zegt dus niets.
- **Rit 17:56–18:13** (17 min, 11,4 km, tot 77 km/u, `rit2.jsonl`, 1,2 s per ronde): 22 keer open,
  langste periode 40 s, 19 % van de metingen. Open bij ongeveer 2400–3000 rpm en MAP vanaf 55 kPa (mediaan klepstand 215
  bij 55–65 kPa, 325 bij 65–80, 491 bij 80–105). Onder 2400 rpm, bij stationair en bij gas los altijd dicht (159 metingen
  met gas los: hoogste 139).
- **De stand volgt de aansturing.** Bij stuursignaal > 0 stond de klep in 17 van de 21 metingen open en in de andere 4 één
  ronde (ongeveer 1 s) later; blok 02 wordt ongeveer 1 s na blok 01 gelezen. Bij stuursignaal 0 dicht in 138 van 139.
- **Open punt, ruststand:** voor de rit 125–126, na de eerste opening 131–139, stilstaand na de rit 133 (0,65 V). Dat is
  warmte of een klep die niet helemaal op zijn zitting terugkomt; niet uitgezocht (de kabel was toen los). Na de middagrit
  stond hij binnen een kwartier weer op 126.
- **Zelfteststatus voor (17:24) en na (18:13) gelijk:** MIL uit, geen opgeslagen of pending codes, geen freeze frame.
  Monitors katalysator en EGR "niet voltooid", lambdasonde "voltooid" (PID 01 = `00 07 A1 81`). Modus 06: TID 81 = 0 bij
  limiet 0; TID 82 = 35 en 24 bij maximum 64; TID 91 = 16384 (minimum 4882, maximum 34439) en 32767 (minimum 1727). De
  waarden van TID 81 en 91 ogen als "nog niet gemeten": de ECU heeft zijn eigen EGR-test in geen van de ritten afgerond.
  Momentopnames: `zelftest_voor_rit.json`, `zelftest_na_rit.json`.

**Stotteren (klacht: onderin bij optrekken), wat de metingen zeggen**

- De EGR is in dat gebied dicht; de normale werking van de klep verklaart de klacht niet.
- **Blok 03 byte 28–30** gedraagt zich als een onrustmaat per cilinder (aanname). Bij gas los, dus zonder verbranding,
  zijn alle drie rustig (hoogste 57, 59 en 54). Bij optrekken onderin (1000–2200 rpm, gasklep-ADC boven 150, 119
  metingen) piekt de tweede tot 249 en de derde tot 178, de eerste niet (hoogste 59); de tweede is daar in 69 metingen de
  hoogste. In het middengebied (2200–3200 rpm) is de tweede in 150 van 262 metingen de hoogste. Stationair is de derde het
  hoogst (36 van 44). Dat wijst op ongelijke verbranding per cilinder: eerst de ontsteking nakijken (bougies, kabels,
  bobine). Welke waarde bij welke cilinder hoort is onbekend.
- **Stationair te rijk:** warm stationair MAP 46–48 kPa, STFT rond −9 %, LTFT −7 %, samen rond −16 %; bij deellast rond
  −7 % (uit de ritlogs). Een vuile gasklep verklaart dat niet, want het is een MAP-systeem. Kandidaten: EGR die niet
  afsluit, tankontluchting, injector of brandstofdruk.
- **Harde overgang net boven stationair** (onbelast gemeten): zolang de gasklep-ADC onder ongeveer 140 blijft (rust
  105–106) staat 0D[4] op 1, met ontsteking 0–14°, regelaar rond 60 en bij hoog toerental brandstofafsnijding; het
  toerental blijft 1000–1300. Daarboven 0D[4] = 0, ontsteking 40°, regelaar 172 en schiet het toerental naar 2500–2900.
  Onbelast is er tussen ongeveer 1300 en 2500 rpm geen stabiel punt. 0C[12] begint pas te tellen bij gasklep-ADC rond
  123–127. Of die dode zone normaal is voor deze ECU is onbekend; er is geen referentie van een gezonde Matiz.

**Koude rit 2026-11-24 (13:18–13:36, 18 min, 11,5 km, tot 85 km/u, `rit_koud.jsonl`)**

- **Lijn onderweg opgelost:** 183 checksumfouten, maar de herhaling-één-keer ving er 182 op → 3 van 6097 verzoeken
  verloren (0,05 %; 6284 pogingen). De "4,6 % leesfouten tijdens rijden" van 23-11 waren dus checksumruis die met één herhaling verdwijnt, geen
  uitblijvende antwoorden. Pretest (contact aan, motor uit): 238 verzoeken, 0 fouten — de nieuwe opname is bewezen.
- **EGR-ruststand:** koud 128 (0,63 V), warm aan het eind 126 (0,62 V). De 0,65 V van 23-11 was tijdelijk; de klep zet
  goed af. De EGR ging deze rit nauwelijks open (het venster 2500–2900 rpm + MAP > 75 werd niet geraakt; werking al bewezen
  op 23-11).
- **Closed loop al bij 21 °C**, ongeveer 2 minuten na de koude start (koelwater bij vertrek 14 °C).
- **Stotteren: niet de EGR en geen scheef mengsel; één cilinderkanaal springt eruit.** Op de gemarkeerde momenten
  ("nu stotterde hij": 8 drukken, 6 momenten) is de EGR dicht (0,62–0,63 V), schakelt de voorste sonde normaal en is
  closed loop actief. **Gecorrigeerd:** de eerdere zin dat op alle markeringen blok 03 tot 195–253 piekt, klopte niet. De
  markeringen zijn alleen de duidelijkste stotters (de bestuurder kan niet alles aantikken) en een losse onrustpiek is
  geen sluitend stottermerk: 42 metingen met een piek ≥ 100, waarvan 5 vlak vóór een markering; de grootste (253 en 195)
  zonder markering; bij 3 van de 6 momenten alleen een matige verhoging (60–75). Bij 1,2 s per ronde valt één misser ook
  makkelijk tussen twee metingen. Wat wel staat: de gemiddelden van de drie kanalen liggen dicht bij elkaar (38/34/33),
  maar kanaal 2 levert reproduceerbaar de meeste en hoogste uitschieters (7 van de 10 grootste, ook op 23-11).
- **Kabelwissel (24-11, `onrust_wissel.jsonl` en `onrust_origineel.jsonl`, alleen blok 03/0C/02, 1,8 ronde/s).** De
  bougiekabels van cilinder 1 en 2 zijn onderling verwisseld, met elke bobine-uitgang op zijn eigen cilinder. Tijdens de
  wissel: kanaal 1 én 2 slecht (kanaal 2: 12 % van de metingen ≥ 100, tot 253), kanaal 3 rustig. Na terugzetten: kanaal 1
  terug naar 4 %, kanaal 2 blijft op 14 %. Een kabelwissel verandert niets aan injector of compressie en dit veranderde
  wel, dus de fout zit aan de ontstekingskant; kanaal 2 hoort bij cilinder 2 (de middelste) en die blijft zwak met beide
  kabels. Over: bobine-uitgang 2 of de compressie van die cilinder; de verplaatste kabel is daarnaast zelf verdacht. Niet
  vergelijken met de 3 % van de koude rit: die mat half zo vaak.
- **Valse lucht nu onwaarschijnlijk:** de sonde na de kat wordt wél rijk bij koud/rijk optrekken (0,60 V, 94 % rijk), dus
  sonde en uitlaat zijn intact. Warm in closed loop weer arm (0,09–0,19 V). Past bij een licht te rijk aangevende voorste
  sonde (ECU haalt −7 tot −9 % weg) of echte magere regeling, niet bij een gescheurd spruitstuk.
- **Monitors nog niet voltooid:** EGR-venster 0 s geraakt, kat-venster 159 s maar de voorwaarden niet compleet. Afronden
  vraagt warm 1 min gelijkmatig 64–80 km/u én een 2500–2900 rpm hoge-belasting-stuk.
- **Vervolg:** compressietest op cilinder 2; is die goed, dan een nieuwe kabelset en zo nodig de bobinecassette (één
  blok met drie uitgangen). Een injector per cilinder uitschakelen via de ECU kan niet zolang de commando-bytes onbekend
  zijn (meeluisteren met een tester via een passieve splitter). De immobilizer-handshake staat niet op de diagnose-K-line (luister.py ving 0 bytes bij contact aan).

**Wat de documentatie zegt (opgezocht 2026-11-24)**

Een beschrijving van de 21-blokken zelf is niet gevonden. Wel twee bronnen over wat deze ECU-familie bijhoudt: het M-150
werkplaatshandboek (2002, sectie 1F; de PDF van 38 MB is met `curl` op te halen en met `pdftotext` leesbaar, PDF-pagina =
98 + het 1F-paginanummer) en een ScanDoc-parameterlijst van een Matiz 0.8 met Sirius D32
([forum auto-bk.ru](http://www.auto-bk.ru/forum/topic/79382-%D0%BF%D0%BE-%D1%83%D0%BC%D0%BE%D0%BB%D1%87%D0%B0%D0%BD%D0%B8%D1%8E-daewoo-matiz-08l-sirius-d32-%D0%B7%D0%B0%D0%B2%D1%8B%D1%88%D0%B5%D0%BD%D0%BD%D1%8B%D0%B5-%D0%BF%D0%BE%D0%BA%D0%B0%D0%B7%D0%B0%D0%BD%D0%B8%D1%8F-%D1%85%D0%BE%D0%BB%D0%BE%D1%81%D1%82%D0%BE%D0%B3%D0%BE-%D1%85%D0%BE%D0%B4%D0%B0/)).

- **Overslag-detectie** (handboek 1F-16 en 1F-135): op basis van variaties in de draaisnelheid van de krukas; de ECM "looks
  for a quick drop in crankshaft speed", in blokken van 100 omwentelingen. Per cilinder bestaan een "Misfire Current"- en
  een "Misfire History"-teller (overslagen in de laatste 200 ontstekingen), zichtbaar op de scantool. De detectie staat
  uit bij brandstofafsnijding, bij snelle gasklepbeweging en tijdens de EGR-stromingstest.
- **Blok 03 byte 28–30 is daarmee vermoedelijk de krukas-onrust per cilinder** (de meetwaarde achter die detectie), niet
  de tellers zelf: de waarden springen van meting tot meting, zijn nooit 0 bij draaiende motor en komen boven 200.
  **Blok 03 byte 24–26** hoort erbij: meestal 0, en niet 0 zodra de onrust van dezelfde cilinder boven ongeveer 52 komt
  (bij 2200–3200 rpm op 2 metingen per cilinder na altijd). Tijdens de rit van 23-11 was dat bij optrekken onderin het
  geval in 2 %, 43 % en 24 % van de metingen (cilinderwaarde 1, 2, 3), in het middengebied in 17 %, 49 % en 15 %,
  stationair in 0, 0 en 4 %. Er is geen foutcode gezet. Een klopniveau per cilinder is niet uitgesloten maar past
  slechter: waarde 1 reageert niet op belasting, één cilinder is stationair verhoogd en de samenhang met het
  klopsensorkanaal is zwak (0,2 tot 0,4).
- **Klopsensor** (handboek 1F-146; ScanDoc toont "Датчик детонации 0,6 В"): de ECM vergelijkt het signaal met een
  ruiskanaal en zet P0327 onder 0,2 V. Blok 01 byte 6–7 gedraagt zich zo: 0,19 V met motor uit, 0,45–0,65 V stationair en
  bij licht gas, hoger bij belasting.
- **EGR** (handboek 1F-182): de scantool toont "Desired EEGR position" en de werkelijke positie; die "should always be
  near" elkaar. De ECM stuurt de klep op basis van gasklep en MAP. De EGR-diagnose P0400 loopt alleen bij koelwater boven
  80 °C, inlaatlucht boven 15 °C, MAP boven 75 kPa, EEGR boven 3 %, luchtmassa 92–157 mg per slag en 2500–2900 rpm. Dat
  past bij de meting (open rond 2400–3000 rpm, verder open bij hogere MAP) en verklaart waarom de EGR-monitor niet snel
  "voltooid" wordt: het venster is smal. P0405 wordt gezet onder 0,01 V klepstandspanning.
- **Controle op de EGR-uitleg:** de rustwaarde van de klepstand (0,6 V) is toevallig gelijk aan de klopsensorwaarde uit
  de ScanDoc-lijst. De klepstand is echter rustig bij constante rijtoestand (mediaan sprong 7 tegen 34 voor het
  klopkanaal) en sluit bij veel gas en onder 2400 rpm; een geluidssignaal doet dat niet.
- **Symptoomlijst "Hesitation, Sag, Stumble"** (handboek 1F-292, "most severe when first trying to make the vehicle
  move"): brandstofdruk (380 kPa), gasklepsensor (spanning moet gelijkmatig oplopen), MAP-sensor, tankontluchting,
  injectoren (11,6–12,4 Ω, lekkend of verstopt), bougiekabels, bougies en vonk per cilinder, dynamospanning, werking van
  de EEGR-klep. "Cuts Out, Misses" (1F-294): bougiekabels onder 30 kΩ, kabels met waternevel op overslag controleren,
  compressie.
- Het handboek is van de eerste generatie Matiz (Sirius D3). Of de parameternamen en drempels één op één voor de D32 uit
  2008 gelden is niet bevestigd. De ScanDoc-lijst bevat geen waarde per cilinder voor onrust of overslag en geen EGR-waarde.

**Meer uit het handboek, en signalen die erbij kunnen (2026-11-24)**

- **Aansluitschema van de ECU** (handboek 1F-320 t/m 1F-324, tekeningen): analoge ingangen zijn gasklep, inlaatlucht, MAP,
  koelwater, verdampertemperatuur (airco), klopsensor, twee lambdasondes, EEGR-potmeter, tankvlotter en de
  wielsnelheidssensor voor "rough road". Dat dekt alle tien kanalen van blok 01; byte 28–29 (altijd 1023) is de
  verdampertemperatuursensor, die zonder airco open staat. Verder: drie bobines (één per cilinder), drie injectoren,
  tankontluchtingsklep, octaanstekker, snelheidssensor.
- **Signaaldefinities in één bestand:** `vehicles/matiz_m200_08.py` (27 signalen met blok, byte, schaal, eenheid en
  zekerheid; `decode()` zet gelezen blokken om, `vensters()` geeft de zelftest-vensters). Bruikbaar voor de webpagina.
  Nieuw daarin: rijsnelheid = blok 02 byte 4 (binnen 2 km/u gelijk aan PID 0D), boordspanning ≈ 0C[18] × 0,1123 V
  (schaal uit één referentiepunt), kilometerstand = 05[29:31] / 10.
- **Zelftest-vensters** (voorwaarden uit het handboek; de luchtmassa-eis ontbreekt omdat die schaal onbekend is). In de
  rit van 23-11 zat de motor 69 s in het EGR-venster (langste stuk 17 s) en 72 s in het katalysatorvenster (langste stuk
  43 s); beide monitors bleven "niet voltooid". Een indicator "nu in het testvenster" op de webpagina helpt de
  bestuurder de toestand vast te houden. EGR: 2500–2900 rpm, MAP > 75 kPa, koelwater > 80 °C, inlaatlucht > 15 °C.
  Katalysator: 64–80 km/u, 2400–3000 rpm, closed loop, koelwater > 70 °C, toerental binnen 80 rpm stabiel.
- **De mengseldiagnose van de ECU kijkt alleen bij MAP > 70 kPa** (P0172, te rijk; P0171 bij MAP > 90 kPa), koelwater
  80–115 °C en closed loop. De correctie van rond −16 % bij stationair (MAP 47 kPa) valt daarbuiten en geeft dus nooit
  een code.
- **Lambdasonde na de katalysator staat vrijwel altijd arm.** Handboek: ongeveer 0,1 V = arm, 1 V = rijk, normaal
  0,1–0,9 V in closed loop. Gemeten: warm stationair (13 minuten, closed loop) mediaan 0,12 V en nooit boven 0,45 V,
  terwijl de voorste sonde normaal schakelt (51 % rijk) en de ECU brandstof weghaalt; rijdend bij MAP 40–70 kPa 0,18 V;
  alleen bij MAP ≥ 70 kPa soms rijk (tot 0,8 V, dus de sonde werkt). Dezelfde waarde staat in de ritlogs van `server.py`
  (PID 15, mediaan 0,11–0,12 V). Het mengsel is na de katalysator dus niet rijk, ondanks de negatieve correctie.
  Kandidaten, deels uit de diagnosehulp van het handboek bij deze sonde: ongelijke cilinders (verstopte of lekkende
  injector; handboek noemt de injector balance test), valse lucht in de uitlaat (pakking of gescheurd spruitstuk), of een
  voorste sonde die te rijk aangeeft. Niet uitgezocht. Te toetsen bij een koude start: in open loop is het mengsel bewust
  rijk; blijft de sonde na de kat dan toch arm, dan wijst dat op valse lucht.
- **Grenzen uit het handboek, bruikbaar als regels voor de webpagina:** klopsensor onder 0,2 V (warm, > 2500 rpm) is een fout;
  EEGR-potmeter in rust tussen 0,4 en 1,75 V, onder 0,01 V of boven 4,99 V is een circuitfout; lambdasonde onder 0,07 V
  gedurende 40 s of 10 s (voor) / 60 s (na) vast tussen 0,352 en 0,499 V is een fout; boordspanning onder 11,5 V of boven
  16 V; tankvlotter onder 0,05 V of boven 4,9 V; stationair boven 950 rpm wijst volgens het handboek op valse lucht.
  Gemeten op 23-11: klopsensor minimaal 0,38 V, EEGR-rust 0,61 V, alles binnen de grenzen.
- **Opname aangepast:** `egr_capture.py` leest nu elke ronde ook blok 02 (EGR-stuursignaal, snelheid) en de korte
  brandstofcorrectie; `rit_report.py` toont de lambdavergelijking, de tijd in de testvensters en de sensorgrenzen.

**Taakverdeling en koppelvlak (2026-11-24).** De uitlezer (`reader/`) verzorgt de
verbinding met de ECU, de webpagina (`web/`) de weergave. Omdat maar één programma de kabel kan openen, is het
verbindingsproces (`reader/rit.py start <label>`, dat `egr_capture.py` start) het enige dat de poort gebruikt.
De weergave leest wat dat proces klaarzet en opent de poort zelf niet. **Wat beide delen staat in
`docs/opnameformaat.md`.**

- `recordings/live.json` (hulpmiddel van de verbinding, geen onderdeel van het contract), elke ronde (ongeveer
  1,3 s) atomair vervangen. Velden: `tijd` (epoch), `verbonden`
  (false bij uitval of na stoppen), `ronde_s`, `bron` (naam van de lopende opname), `waarden` (omgerekende signalen;
  sleutels en eenheden als in `signalen.py`, plus `stft`, `ltft`, `brandstofsysteem`, `belasting` en `voorontsteking` uit
  de standaard-PID's), `vensters` (zelftest-vensters, true/false), `leeftijd_s` (per blok of PID hoe oud de waarde is;
  roulerende kanalen zijn tot ongeveer 22 s oud) en `verbinding` (tellers: verzoeken, geen_antwoord, checksum, echo,
  negatief, herhaald, mislukt, rondes, herverbonden).
- `rit_<label>.jsonl`: per ronde één regel ruwe data (`t`, `e` = epoch, per blok de 32 bytes als hex, PID's als `pidXX`),
  direct weggeschreven; geschikt om mee te lezen voor grafieken.
- `rit_<label>.fouten.log`: elke leesfout met soort en poging (JSON-regels; bewust geen `.jsonl`, anders past het op het
  volgpatroon `rit*.jsonl` van de pagina).

Een mislukt verzoek wordt één keer herhaald; na vier verzoeken zonder antwoord knoopt het proces opnieuw aan. Droog
getest tegen een nagebootste ECU met leesfouten en een uitval van 6 s (logt weer 7,7 s na het begin van de uitval); nog
niet tegen de auto. In kabelmodus kunnen `server.py` en het verbindingsproces niet tegelijk draaien; met `--follow` wel.

**Onverklaard:** om 17:36, na het stoppen en opnieuw starten van de opname, antwoordde de ECU ongeveer 3 minuten niet op
StartCommunication (ongeveer 30 pogingen), terwijl de motor liep en de kabel echo gaf. Daarna weer normaal. Er was geen
ander programma op de poort en de ECU heeft niets opgeslagen. "Echo wel, ECU zwijgt" betekent dus niet altijd contact uit.

**Volgende meting: rit met koude motor.** `reader/rit.py start koud` met contact aan en de motor nog uit: legt
de zelfteststatus en alle blokken vast en start de opname. Daarna starten en rijden. `rit.py stop koud` na afloop: stopt
de opname, leest de status opnieuw, vergelijkt en maakt het rapport (`rit_report.py`). Er kan maar één programma tegelijk
op de kabel: `server.py` niet tegelijk starten. Waar het om gaat: de ruststand van de klep koud, vanaf welke temperatuur
de ECU de EGR gebruikt, de cilinderwaarden bij koud optrekken, en het moment van closed loop.

## Onderzoek: protocol, aansturen en correcties (2026-11-23)

Onderzoek in openbare bronnen en in de eigen meetdata, zonder verbinding met de auto. Er is **geen publieke documentatie**
gevonden waarmee deze ECU veilig is aan te sturen of te beschrijven; dit is wat wel vaststaat.

**Protocol**
- Transport is ISO 14230-4 (EOBD-profiel): functioneel doeladres 0x33, antwoorden komen van het fysieke adres 0x11
  (**gecorrigeerd:** een verzoek aan 0x11 blijft niet stil; de ECU antwoordt ook op fysieke adressering). Keybytes `EF 8F` = keyword 2031, een toegestaan keyword.
- De inhoud van de service-0x21-blokken is "application specific" en nergens publiek beschreven (geen forum,
  open-source project of handboek). freediag ondersteunt geen Daewoo.
- Het Matiz M-150 handboek (2002, eerste generatie) noemt "Keyword 2000", scantool-parameters (Desired/Actual EEGR
  position, IAC counts) en scantool-uitgangstests (EEGR op 25/50/75/100%, IAC, MIL, koelventilatorrelais met contact
  aan en motor uit). Geen service-ID's of byte-indeling. Of dit voor de M200/M250 uit 2009 geldt, is niet bevestigd.
- Een derdepartij-functielijst voor Daewoo-testers noemt voor de M200 0.8 ECM: ECU-info, foutcodes lezen en wissen,
  live data (geen actuatortest).
- ECU-familie: het handboek noemt Siemens FENIX 5MR en SIRIUS D3 (immobilizer via DLC-pin 7). "KEMSCO D3 = SIRIUS D3" is
  niet bevestigd; onderdeelnummers 5WY5407A / 96801800 komen uit verkoopadvertenties.
  **Gecorrigeerd:** service 1A werkt (fysiek adres 0x11). `1A 80` geeft uit de ECU zelf: `5WY5407A`, software
  `KR7714520100` (ook via `1A 8A`), `KR7714511300`, kalibratie `8P04B231`, `S0011BOT`, `SASCO` en een productiestempel
  (29-07-2008). Ook `1A 81`, `87`, `88` en `90` antwoorden (`90` = VIN, alleen `FF`). 5WY5407A is dus
  uit de ECU bevestigd; 96801800 en de naam "Sirius D32" komen nog steeds alleen uit een advertentie (die noemt bij
  5WY5407A ook software "1BOT", wat past bij `S0011BOT`). De ruwe antwoorden zijn niet gepubliceerd: het serienummer van de ECU staat erin.

**Aansturen**
- Scantools kunnen EEGR/IAC/relais aansturen; hoe dat op de bus loopt (waarschijnlijk service 30/31) is niet
  gedocumenteerd. De ECU is gekoppeld aan een immobilizer: foute vrijgaveberichten (>5×) laten de ECM het
  injectorcircuit uitschakelen (P1628/P1629).
- Een deel van de handboekgegevens kwam alleen uit zoekfragmenten (manualslib is niet via fetch leesbaar): onzeker.
- **Aangevuld 2026-11-24 (bronnen gelezen, niets op de auto geprobeerd).** OpenDiag en Scanmatik noemen voor
  "Matiz/Spark, SIRIUS-D32, 0.8L" naast uitlezen ook aansturing van actuatoren, ECU-reset, adaptatie wissen en het
  wissen van de leerwaarden van gasklep en stationairregelaar. Een CO- of mengselafstelling bestaat alleen bij de
  oudere FENIX 5MR. De ECU heeft een Infineon C167 en 256 of 512 kB flash; via de OBD-stekker lezen en schrijven laders
  (Combiloader, ChipLoader, Galletto 1260) alleen het kalibratiegebied van 32 kB (0x8000–0xFFFF in een volledige
  uitlezing), met controlesom. Voor ons softwarekenmerk (`S0011BOT`, 1BOT) is geen bestand of tabelbeschrijving gevonden.
  De immobilizer zit op dezelfde K-line en kan de communicatie verstoren. Services en identifiers blijven
  ongepubliceerd. Ontwerp, risicoklassen en bronnen: `docs/aansturen-ontwerp.md`.

**Correcties ("schrijven")**
- STFT/LTFT zijn een **gevolg**, geen instelling: overschrijven of wissen verbergt de oorzaak en de ECU leert dezelfde
  waarde terug. Waar de ECU LTFT bewaart en of mode 04, service 14 of de Hella-procedure hem wist: niet gedocumenteerd.
- Mode 04/service 14 wist opgeslagen en pending codes, freeze frame, readiness en monitorresultaten. Met MIL uit levert
  dat niets op en kost het bewijs.
- Hella Flits-info (26 maart 2004, Matiz 0,8 l 38 kW, onregelmatig stationair): na reinigen van stationair-regelaar en
  bypass de ECU opnieuw inleren: contact uit, zekeringen Ef4 (10 A) en Ef19 (15 A) eruit, contact 10 s aan, uit,
  zekeringen terug, proefrit met wisselende toerentallen en >10 km/u, stationair controleren. Terugdraaibaar; het
  bulletin zegt niets over de LTFT.

**Veiligheidsoordeel per actie**

| Actie | Oordeel |
| --- | --- |
| Alleen lezen: mode 01/03/07, service 21 lezen, 1A, meeluisteren | veilig |
| Hella-zekeringprocedure, accu loskoppelen (contact uit), mode 04 | terugdraaibaar (mode 04: freeze frame en readiness weg) |
| Service 30/2F/31 met onbekende identifiers, ECU-reset (11) | riskant |
| Service 3B/3D/34/36/37, elke 27-poging, immobilizer-ID's wijzigen | niet doen |

Redenen: immobilizer-koppeling, een checksum van 1 byte bij 36–42% lijnruis (ruis kwam er al doorheen: 15360 rpm),
geen bekend herstelpad na een foute schrijfactie. Er is nooit iets naar de ECU geschreven of aangestuurd.
**Gecorrigeerd:** de "36–42% lijnruis" was een leesfout in onze eigen software en geen ruis op de lijn (zie "Gemeten op de
echte Matiz"). De andere twee redenen en het oordeel in de tabel blijven staan.

**Wat herkend is in de blokken** staat bij "ECU-datablokken" hierboven. Blokken 01, 05, 07, 0E en 10 bestaan wel
(in `probe_ecu.py` stonden ze door ruis als "stil"): 23 ID's geven data. Statisch: 05, 07, 13; altijd 0: 06, 08, 09, 0A, 16, 17.

**Vervolgstappen (nog niet gedaan)**
1. **Passief meeluisteren** naar een echte scantool-sessie (EEGR-test in de garage) met onze kabel alleen ontvangend:
   levert de echte service en identifiers. Hiervoor moet nog een luisterprogramma komen.
   (Voor het beoordelen van de EGR niet meer nodig: positie en stuursignaal zijn uit de rit herkend.)
2. **Betere opnames**: elke ronde dezelfde standaardwaarden meelezen (rpm, map, ect, iat, tps, stft, ltft, timing,
   load, fuelsys, o2 voor en na kat), blokken beperken tot 02, 03, 04, 0B, 0C, 0D, 14, 15, 8–10 rondes per toestand
   met de mediaan van 3 tegen ruisframes. Toestanden: warme deellast tijdens een rit (bijrijder bedient de laptop),
   koude start, vaste toerentallen (1500/2000/2500/3000), elektrische belasting, uitrollen. Zonder warme deellast is
   een EGR-positie niet te zien, want de EEGR is bij stationair en vrij gas dicht.
   **Gedaan op 2026-11-23 voor warme deellast** (zie "EGR-test en rit met blokopname"); de koude start staat gepland.
3. **ECU-label lezen** (5WY54… of 9680…): **gedaan via `1A 80`**, het is 5WY5407A. Volgens een advertentie
   is dat een Sirius D32, dus een latere generatie dan de SIRIUS D3 uit het M-150 handboek (niet officieel bevestigd).
4. Het **M200/M250-werkplaatshandboek** (Spark 2005–2009, sectie 1F) zoeken en de P0400-, P0505-, P0172-pagina's
   zelf openen.

Bronnen: [ISO 14230-4](https://law.resource.org/pub/us/cfr/ibr/inc/iso/iso.14230-4.2000.pdf) ·
[Matiz M-150 handboek](https://s3cf792cad773e861.jimcontent.com/download/version/1508395484/module/12666607922/name/Daewoo%20Matiz%20Service%20Manual.pdf) ·
[Daewoo functielijst](https://cdn.shopify.com/s/files/1/0085/7699/1298/files/DAEWOO_V11.10_Diagnostic_Function_List_FULL.pdf) ·
[Hella Flits-info stationair](https://hella.com/tibi/bi/nl/electronics/bi_nl_electronics_daewoo_matiz_stationair.pdf) ·
[KWP2000-diensten (IME-Actia)](https://www.ime-actia.de/en/?p=5089) ·
[Wat mode 04 wist](https://x-engineer.org/?p=5201).
