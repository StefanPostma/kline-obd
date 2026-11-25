# Het opnameformaat en de afspraken tussen uitlezer en webpagina

De uitlezer (`reader/`) en de webpagina (`web/`) zijn los van elkaar geschreven. Ze delen alleen wat hier staat: het
formaat van een opname, de bestandsnamen en het voertuigbestand. Elk programma dat dit formaat schrijft, werkt met de
webpagina, voor elke auto.

## 1. De kabel

Er kan maar één programma tegelijk op de K-line. Tijdens een rit heeft de uitlezer de kabel (`rit.py start <label>`); de
webpagina kijkt dan mee met `server.py --follow` en opent de poort niet. De pagina weigert naar "Kabel" te wisselen
zolang een opname de poort open heeft.

Beide delen sturen alleen leesverzoeken, met één uitzondering: de webpagina kan in kabelmodus foutcodes wissen (OBD
modus 04), na een momentopname en een bevestiging in twee stappen. Dat verzoek gaat één keer de lijn op en wordt nooit
herhaald; een terugval op KWP-service 14 is er bewust niet. Alles wat verder gaat dan lezen (aansturen, leerwaarden
wissen) hoort in het opdrachtenkanaal van de uitlezer en is niet gebouwd.

## 2. Een opname

Eén JSON-regel per ronde:

```json
{"t": 12.345, "e": 1795522718.9, "01": "<hex, 32 bytes>", "0C": "<hex>", "pid06": "<hex zonder 41 06>"}
```

| Sleutel | Betekenis |
| --- | --- |
| `t` | seconden sinds het begin van de opname |
| `e` | kloktijd als epoch (mag ontbreken in oude opnames) |
| `<blok>` | twee hexcijfers: het antwoord op KWP2000 `21 <blok>` zonder de kop `61 <blok>` |
| `pid<XX>` | het antwoord op OBD2 `01 <XX>` zonder de kop `41 <XX>` |

Een ontbrekende sleutel betekent dat dat verzoek in die ronde is mislukt, ook na één herhaling. Regels zonder `t` slaat
een lezer over; de laatste regel kan half geschreven zijn. Onbekende sleutels worden genegeerd, ook als de waarde een
object is (gereserveerd: `actie`, voor een latere aansturing).

Bij de Matiz duurt een ronde ongeveer 1,3 s: vijf kernblokken en één PID elke ronde, en één roulerend blok of PID, dat
daardoor ongeveer eens per 22 s aan de beurt is. Welke dat zijn staat in het voertuigbestand.

## 3. Bestandsnamen

Alles staat in de datamap (`recordings/`, of `KLINE_DATA`).

| Bestand | Inhoud |
| --- | --- |
| `rit_<label>.jsonl` | opname van een rit |
| `egr_*.jsonl` | opname van een losse test |
| `onrust_<label>.jsonl` | snelle opname van alleen de onrustblokken |
| `<opname>.meta.json` | zijbestand, zie 4 |
| `<opname>.fouten.log` | elke leesfout: tijd, verzoek, poging, soort |
| `zelftest_<label>.json` | momentopname van foutcodes, monitors en blokken |
| `web/logs/zelftest_wissen_<tijd>_voor.json`, `..._na.json` | dezelfde momentopname, door de webpagina gemaakt voor en na het wissen; `selftest_diff.py` leest ze |
| `live.json` | laatste waarden van de lopende opname, elke ronde atomair vervangen; hulpmiddel, geen contract |

Alleen opnames mogen op `rit*.jsonl` of `egr_*.jsonl` passen: de webpagina volgt het nieuwste bestand met zo'n naam.
Daarom heet het foutenlog `.fouten.log` en niet `.jsonl`.

## 4. Het zijbestand `<opname>.meta.json`

```json
{"gestart": 1795522706.6, "script": "egr_capture.py",
 "ecu": {"onderdeelnummer": "5WY5407A", "software": "KR7714520100", "kalibratie": "8P04B231"},
 "leesplan": {"kern_blokken": ["01", "0C"], "elke_ronde_pid": ["06"], "roulerend_blokken": [], "roulerend_pid": []}}
```

Alles is optioneel. `ecu` is leeg tot de ECU heeft geantwoord; de webpagina leest het bestand opnieuw als het verandert.
Het ruwe identiteitsantwoord staat er bewust niet in, want daarin staat het serienummer van de ECU.

## 5. Markeringen

De knop "Annotatie: probleem" in de webpagina schrijft één JSON-regel per druk in `web/logs/<opname zonder
extensie>.markeringen.log`:

```json
{"e": 1795523120.8, "t": 409.3, "opname": "rit_koud.jsonl", "tekst": "annotatie: probleem"}
```

Oudere logs, zoals de voorbeeldrit, hebben als tekst `nu stotterde hij`: zo heette de knop eerst. De opname zelf heeft
één schrijver en wordt nooit aangepast. `rit_report.py` zoekt de markeringen naast de opname en in
`web/logs/`, en legt elke markering naast de meetdata van de 8 s ervoor en 3 s erna.

## 6. Het voertuigbestand

Eén Python-bestand per auto in `vehicles/`. De webpagina leest eruit: `SIGNALEN`, en optioneel `vensters()`,
`VENSTER_LABELS`, `VENSTER_OPMERKINGEN`, `CANONICAL`, `TPS`, `HIGHLIGHT` en het leesplan (`KERN_BLOKKEN`,
`ROULEREND_BLOKKEN`, `PID_ELKE_RONDE`, `PID_ROULEREND`). Andere namen in het bestand zijn van de uitlezer; de pagina
negeert ze. Een sleutel in `SIGNALEN` hernoemen of verwijderen raakt opgeslagen analyses en grafieken; een signaal
toevoegen niet. Het schema staat in `vehicles/README.md` en in de docstring van `web/signalset.py`.
