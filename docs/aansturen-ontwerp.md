# Ontwerp: aansturen en schrijven via de K-line

Stand 2026-11-24. Fase 0 is gebouwd en droog getest (witte lijst, opdrachtenkanaal, luisterprogramma); er is nog nooit
iets naar de ECU geschreven of aangestuurd, en geen enkele actie is verzendbaar zolang de bytes onbekend zijn. De regel
blijft: op de auto alleen lezen, tot de eigenaar per stap ja zegt.

## 1. Wat "blokken veranderen" bij deze ECU betekent

De meetblokken (`21 01` t/m `21 17`) zijn een venster op het werkgeheugen van de ECU. Terugschrijven in zo'n blok is
geen bestaande functie. Een waarde in een blok verandert op drie manieren, en dat zijn ook de drie dingen die we
kunnen bouwen:

| Manier | Wat het doet | Voorbeeld | Blijvend |
| --- | --- | --- | --- |
| Tijdelijk overnemen | de ECU gebruikt onze waarde in plaats van zijn eigen | EGR-klep 50 % open, stationairregelaar, relais | nee, stopt bij loslaten of contact uit |
| Geleerde waarden wissen | de ECU begint opnieuw met leren | brandstofcorrecties, gasklep- en stationairregelaar-leerwaarden | ja, maar hij leert ze terug |
| Kalibratie herschrijven | de tabellen waaruit de ECU rekent veranderen | brandstofkaart, verrijking, ontsteking, EGR-kaart | ja |

## 2. Wat vaststaat en wat niet

**Vaststaat**

- Aansturen bestaat voor deze ECU. Twee testers (OpenDiag, Scanmatik) noemen voor "Matiz/Spark, SIRIUS-D32, 0.8L" naast
  uitlezen ook "ИМ" (aansturing van actuatoren), "Сброс ЭБУ" (ECU-reset), "Сброс адаптации" (adaptatie wissen) en
  "Сброс обучения дросселя и РХХ" (leerwaarden gasklep en stationairregelaar wissen).
- Een brandstofinstelling via de diagnose bestaat niet. "Регулировка СО" (CO-afstelling) staat alleen bij de oudere
  FENIX 5MR, niet bij de Sirius-familie.
- De ECU heeft een Infineon C167-processor en 256 of 512 kB flash (29F200/29F400). Via de OBD-stekker lezen en
  schrijven de gangbare laders alleen het kalibratiegebied van 32 kB; in een volledige uitlezing is dat adres
  0x8000 t/m 0xFFFF. Het volledige geheugen kan alleen met de printplaat op tafel (boot-pen via 1–2 kΩ aan massa).
- Het kalibratiegebied heeft een controlesom die de laders narekenen.
- De immobilizer zit op dezelfde K-line. Volgens een laderhandleiding verstoort hij de communicatie bij lezen en
  schrijven; daar trekt men zijn stekker los en zet een doorverbinding. De lijn loopt dus door zijn stekker.
- Onze `kline.py` handelt "antwoord volgt" (NRC 78) al af; de lijn zelf is betrouwbaar bij stilstand (0,1 % fouten).

**Niet bekend**

- Welke service en identifiers de testers gebruiken (waarschijnlijk `30`, `31`, `11`, `14`). Nergens gepubliceerd.
- Hoe een lader toegang krijgt (sessie, `27` met sleutelberekening) en met welke services hij leest en schrijft.
- De tabelindeling van ónze software. Kant-en-klare bestanden bestaan voor Matiz 0.8 met de kenmerken 1AFM, 1BAO, 1BVK,
  1BWV en voor Spark 0.8 met 1BFD, 1BGY, 1BOX, 1BQP, 1BQR, 1BQU, 1BRA, 1BRC. Ons kenmerk is `S0011BOT` (1BOT) en staat op
  geen enkele lijst. Verkopers zeggen dat bestanden tussen kenmerken niet uitwisselbaar zijn.
- Of het herschrijven van 32 kB bij ónze hardware (5WY5407A, Euro 4) hetzelfde werkt als bij de Euro 3-uitvoering.

## 3. Risicoklassen

| Klasse | Inhoud | Wanneer toegestaan |
| --- | --- | --- |
| A | lezen (modus 01–09, `1A`, `18`, `21`) | altijd, huidige stand |
| B | verkennen zonder effect: welke services bestaan, `23` geheugen lezen, `30 xx 01` toestand opvragen | eerst op een losse ECU; op de auto alleen wat daar schoon bleek |
| C | tijdelijk aansturen (`30`, `31`) | na jouw ja per sessie, met bewaking uit hoofdstuk 5 |
| D | geleerde waarden wissen, ECU-reset, foutcodes wissen | na jouw ja per actie, na een automatische momentopname vooraf |
| E | kalibratie schrijven | eerst op een losse ECU; op de auto pas met dubbele back-up, lader en herstelpad |
| F | immobilizercodes, EEPROM, volledig geheugen op de auto | niet |

Waarom E zo streng is: de ECU is aan de immobilizer gekoppeld. Een reserve-ECU start de auto niet zonder opnieuw
inleren, dus na een mislukte schrijfactie staat de auto stil tot de eigen ECU op tafel is hersteld.

## 4. De commando's leren

De commando's raden op de auto is de enige route die ik afraad. Twee routes zonder risico voor de auto:

1. **Meeluisteren met een tester die het al kan.** Een tweede KKL-kabel aan een passieve OBD-splitter, alleen ontvangend,
   terwijl een tester (OpenDiag of Scanmatik; Chevrolet Explorer 1.6 werkt volgens het Matiz-forum met deze ECU) de
   EGR-test, de relais en "adaptatie wissen" uitvoert. Levert de echte bytes op, inclusief sessie en volgorde.
   **Gebouwd (2026-11-24):** `luister.py` + `kwp_ontleed.py` zijn een passieve KWP2000-sniffer die op lengte knipt, elk
   verzoek aan zijn antwoord koppelt, de service benoemt en de actuator-/beveiligings-/schrijfcommando's eruit licht; een
   ruwe dump (`luister_<label>.raw`) en een offline-stand (`--ontleed`) laten een eenmalige testersessie later herlezen.
   Elektrisch kan het met een passieve splitter: de splitter zet pin 7 parallel, onze kabel blijft stil.
   Nog nodig: een tester die de commando's daadwerkelijk stuurt.
2. **Een losse ECU op tafel.** Een tweedehands 5WY5407A (GM 96801800) met 12 V en onze kabel. Daarop mag alles: services
   aftasten, `30 xx 01`, `27`, geheugen lezen, schrijven en het herstel oefenen. Aansluitingen volgens de
   laderhandleiding voor D32/D42: 24 en 28 massa, 29 geschakelde 12 V, 30 vaste 12 V, 56 K-line. Die nummers eerst
   narekenen aan het M200-schema voordat er spanning op gaat.

Route 1 is het snelst voor klasse C en D. Route 2 is nodig voor alles met de kalibratie (klasse B en E).

## 5. Architectuur

**Eén eigenaar van de lijn.** Het verbindingsproces blijft het enige dat de kabel opent. Een opdracht wordt door
datzelfde proces tussen twee leesvragen verstuurd. Zo botst er niets op de lijn en staat de actie in dezelfde tijdbasis
als de meting.

```
stuur.py (terminal)  ─┐
weergave (later)     ─┴─> opdrachten/nieuw/*.json ──> verbindingsproces ──> K-line ──> ECU
                                                        │  bewaking + lijst stuurbaar.py
                          opdrachten/klaar/*.json <─────┤
                          rit_<label>.acties.log  <─────┤
                          rit_<label>.jsonl (veld "actie") <──┘
```

**Opdrachtenkanaal.** Een opdracht is een klein JSON-bestand, atomair geschreven:
`{"id", "actie", "waarde", "duur_s", "door"}`. Het proces pakt het binnen één ronde op (hooguit 1,3 s), toetst het en
schrijft het resultaat naar `opdrachten/klaar/<id>.json`: `uitgevoerd`, `geweigerd` of `afgebroken`, met de reden, de
verzonden en ontvangen bytes en de meetwaarden ervoor en erna. `stuur.py stop` breekt alles direct af.

**Lijst van wat mag: `stuurbaar.py`.** Naast `signalen.py`, per voertuig. Per actie: sleutel, omschrijving, klasse, het
verzoek, het toegestane waardenbereik, de voorwaarden (uitgedrukt in signaalsleutels), de maximale duur, het
loslaat-verzoek, de blokken die tijdens de actie snel worden gelezen, en de bron met zekerheid ("afgeluisterd",
"bevestigd op tafel", "vermoed"). Wat niet in de lijst staat, wordt niet verzonden. "Vermoed" mag alleen op tafel.

**Vergunning per run.** Het proces start standaard in klasse A. Alleen met `--klasse C` (of D) neemt het opdrachten
aan, en `rit.py start` geeft die vlag nooit door. Een gewone ritopname kan dus niets aansturen.

**Bewaking.** Vóór, tijdens en na elke actie:

| Moment | Regel |
| --- | --- |
| Vooraf | meetwaarden jonger dan 3 s; voorwaarden van de actie kloppen; lijnfouten in de laatste minuut onder 2 %; geen herverbinding gaande |
| Tijdens | elke ronde opnieuw toetsen; afbreken bij schending, bij twee mislukte verzoeken achter elkaar, bij een afwijkende echo, bij het einde van de duur of bij `stop` |
| Achteraf | loslaat-verzoek verzonden en bevestigd; teruglezen dat de waarde terug is (EGR-stand in rust, stationairregelaar); alles in het actielog |
| Vangnet | valt ons proces weg, dan moet de ECU zelf loslaten als de tester zwijgt. Dat wordt per actuator eerst op tafel of met de onschuldigste actie getest. Contact uit beëindigt altijd alles |

Standaardvoorwaarden per toestand:

- **Contact aan, motor uit:** toerental 0, accuspanning boven 11,8 V.
- **Stationair:** snelheid 0, gas dicht, 700–1200 toeren, koelwater boven 70 °C, accu boven 13 V, hooguit 10 s per
  actie, afbreken onder 500 toeren.
- **Rijdend:** niet. Tijdens rijden gaat nu 4,6 % van de leesvragen verloren en de oorzaak is onbekend.

**Wat de weergave ervan merkt.** Regels in de opname krijgen tijdens een actie een veld `actie`; het detail staat in
`rit_<label>.acties.log` (de naam past bewust niet op `rit*.jsonl`). De weergave heeft op 2026-11-24 bevestigd (getest)
dat hun lezer een `actie`-veld met een object, lijst of null **negeert**, dus dit mag inline in de opname zonder dat hun
pagina erop stuklloopt; geen contractwijziging aan hun kant. Knoppen op de pagina kunnen later via hetzelfde
opdrachtenkanaal; de pagina opent zelf nooit de kabel.

## 6. Eerste aansturing op de auto: de volgorde

Zodra de commando's bekend zijn, van onschuldig naar nuttig:

1. Storingslamp aan en uit (motor uit). Test het hele pad en het vangnet.
2. Koelventilatorrelais (motor uit).
3. EGR-klep in stappen open en dicht (motor uit), stand teruglezen uit blok 01. Beantwoordt meteen de open vraag of de
   klep goed sluit (rust 0,62 V of 0,65 V).
4. EGR-klep open bij stationair. De motor moet gaan hikken; dat bewijst dat er uitlaatgas door de kanalen stroomt.
5. Stationairregelaar in stappen.
6. Injector per cilinder uitschakelen, als de ECU dat kent. Laat zien welke cilinder het minst bijdraagt en is daarmee
   de directe test voor het stotteren.
7. Adaptatie wissen (klasse D), pas na een reparatie, om te zien wat de ECU dan opnieuw leert.

## 7. Brandstofkaart en verrijking

**Via de diagnose: nee.** Er is geen instelling voor. De brandstofcorrecties (STFT, LTFT) zijn een uitkomst; wissen kan
(klasse D), maar de ECU leert dezelfde waarde terug zolang de oorzaak er is.

**Via de kalibratie: in principe ja.** De kaarten staan in het gebied van 32 kB dat via de OBD-stekker te lezen en te
schrijven is. Daarvoor is nodig:

1. **Uitlezen** met een bestaande lader (Combiloader, ChipLoader, Galletto 1260) of met eigen code zodra we het protocol
   kennen. Twee keer lezen en vergelijken. Dit is tegelijk de back-up.
2. **De tabellen vinden.** Voor 1BOT is geen beschrijving gevonden. Twee wegen: de betaalde ChipTuningPRO-module voor
   Sirius D32/D42 proberen (ondersteuning van 1BOT is niet bevestigd), of de tabellen zelf herkennen. Dat laatste is
   haalbaar: 32 kB is klein en we hebben meetdata om tegen te leggen (inspuittijd, voorontsteking en EGR-stuurwaarde
   tegen toerental en inlaatdruk uit de ritten).
3. **Schrijven**, met een kloppende controlesom, eerst op de losse ECU.

**Wat het oplevert.** In closed loop regelt de ECU elk mengsel terug naar lambda 1; een andere basiskaart wordt dus
grotendeels weggecorrigeerd. Effect heeft het waar de ECU zonder sonde stuurt: koudstart- en opwarmverrijking,
acceleratieverrijking, vollastverrijking, ontsteking, stationair toerental en de EGR-kaart.

**Voor het stotteren.** Verkopers van aangepaste Matiz-bestanden beloven dat "het gat bij optrekken" verdwijnt; dat
zijn claims zonder meting, en ze horen bij ombouw naar Euro 2 (EGR en tweede sonde uit), wat op de openbare weg niet is
toegestaan. Onze metingen wijzen op de motor zelf: de ECU haalt stationair al 16 % brandstof weg, de achterste sonde
meet arm en de cilinders lopen ongelijk. Een kaart aanpassen zou dat maskeren. Volgorde dus: eerst de koude rit en de
controle van ontsteking en injectoren, daarna pas kalibratie.

## 8. Fasering

| Fase | Inhoud | Auto nodig | Besluit van jou |
| --- | --- | --- | --- |
| 0 | opdrachtenkanaal, bewaking, `stuurbaar.py`, `stuur.py`, `luister.py`; droog getest tegen de nep-ECU | nee | **gedaan** |
| 1 | commando's leren: meeluisteren en/of losse ECU | alleen bij meeluisteren | aanschaf: tweede KKL-kabel, OBD-splitter, tester; en/of losse ECU |
| 2 | eerste aansturing volgens hoofdstuk 6 | ja | ja per sessie (klasse C) |
| 3 | adaptatie wissen na reparatie | ja | ja per actie (klasse D) |
| 4 | kalibratie uitlezen en tabellen zoeken | losse ECU, later de auto | ja voor het lezen op de auto |
| 5 | kalibratie schrijven | eerst losse ECU | apart besluit, na fase 4 |

Fase 0 staat los van de opname: `egr_capture.py` neemt geen opdrachten aan en stuurt alleen leesvragen.

## 9. Bijvangst voor de verbinding

De immobilizer deelt de K-line en verstoort volgens de laderhandleiding soms de communicatie. Dat is een kandidaat
voor de onverklaarde stilte van drie minuten op 23-11 en voor een deel van de leesfouten tijdens rijden. Te
onderzoeken met `luister.py`: staan er frames op de lijn die niet van ons en niet van de motor-ECU zijn?

## Bronnen

- OpenDiag, module Daewoo/Chevrolet: <https://opendiag.pro/sites/default/files/download/Modul%20Daewoo.pdf>
- Scanmatik, module Daewoo/Chevrolet en toepassingstabel: <https://scanmatik.ru/downloads/mod_gm.pdf>,
  <https://scanmatik.ru/applicability/scanmatik2/Chevrolet_2.pdf>
- Laderhandleiding Sirius D32/D42 (processor, geheugen, 32 kB via OBD, boot-pen, immobilizer, pennen):
  <https://reflashecu.com/en/docs/sirius-d32-d42/>
- Bestandenlijsten en kenmerken: <https://chiptuns.com/insertions/kommercheskie-inomarki/koreycy/250-adakt-chevrolet-daewoo.html>,
  <https://vd11.ru/Catalog/Chevrolet/Spark/Sirius_D32>
- ChipTuningPRO (module Sirius D32/D42): <https://chiptuner.ru/content/ctpro/>
- Chipsoft-lader (controlesommen): <https://www.chipsoft.com.ua/forums/viewtopic.php?t=1205>
- Matiz 0.8 via OBD herschrijven, ervaringsverslag: <https://izhtrading.ru/proshivka-matiza-svoimi-rukami/>
- Matiz-forum, Chevrolet Explorer 1.6 met Sirius D32: <https://www.matiz-club.ru/forum/topic/15660/>
- Immobilizer en ECM-leertoestanden: werkplaatshandboek Matiz M-150, sectie 9T.

Let op bij de bronnen: het zijn verkopers- en forumteksten over de Euro 2- en Euro 3-uitvoering. Niets ervan is op onze
ECU bevestigd. De Chipsoft-draad zegt zelfs dat Sirius alleen in de 1,0 en 1,2 zit, wat onze eigen ECU tegenspreekt.
