# vehicles

One Python file per car. It tells the reader **what to read** and both the reader and the web page **what the bytes
mean**. Nothing else in the project knows about a specific car.

| File | Car | Tested on a car |
| --- | --- | --- |
| `matiz_m200_08.py` | Chevrolet/Daewoo Matiz M200 0.8, Siemens Sirius D32 (`5WY5407A`) | yes |
| `generic_obd2.py` | any car that speaks OBD2 over the K-line; standard PIDs only | no, simulator only |
| `vw_1600i_digifant.py` | VW 1600i (Mexican Beetle), Digifant 043 906 022 B, protocol KW1281 | no, simulator only; conversions checked against one real log |

Choose one with `kline-obd --voertuig <name> ...` or `KLINE_VOERTUIG=<name>`; a path to a file outside this folder works
too. The web page takes the same file through `--signals <file>` or a profile in `web/vehicle.py`.

## What a vehicle file contains

The names are Dutch because the code is. All of them are optional except `SIGNALEN`.

**Connecting**

| Name | Meaning |
| --- | --- |
| `NAAM` | Name shown in listings. |
| `AANKNOPEN` | Init methods to try, in order: `"fast"` (ISO 14230 fast init on 0x33) and `"slow"` (5-baud init on 0x33). Default `("fast",)`. |
| `IDENTITEIT_VERZOEK`, `identiteit(answer)` | Request that returns the ECU identity and a function that turns the answer into `{"onderdeelnummer", "software", "kalibratie", ...}`. Stored next to each recording. Leave out anything that identifies one individual car. |

**Another transport: KW1281**

An ECU that speaks KW1281 (5-baud wake-up, 1200 baud, blocks acknowledged byte by byte) has no PIDs and no service 21.
Its vehicle file says so and names its reads instead:

| Name | Meaning |
| --- | --- |
| `TRANSPORT` | `"kw1281"`. Without this name the reader uses OBD2/KWP2000. |
| `ADRES`, `BAUD` | 5-baud address and baud rate: `0x01` and `1200`. The transport refuses any other address: on the one ECU it was written for, a wrong address becomes the state of its diagnostic part (harmless for most values, not for 09 to 10 hex). |
| `LEZINGEN` | `{key: named read}`. The key is two hex characters and becomes the key in the recording. A named read is `("tien_waarden",)`, `("groep", n)` with n from 1 to 8, or `("ram_venster", address)`. |
| `LEZING_ELKE_RONDE`, `LEZING_ROULEREND` | keys read every round, and keys of which one is read per round. On the Digifant a measuring group costs 2 to 5 s per round because the ECU first sends a header of 50 to 77 bytes; a RAM window has no header. |
| `FOUTCODES` | `{code: text}` for the fault memory. |

There is deliberately no way to put a raw request number in a vehicle file. On these ECUs an unknown request is not
harmless (see `docs/vw-1600i-digifant.md`), so the list of requests lives in `reader/kw1281.py` and a vehicle file can
only choose among them. `SIGNALEN` works as below; set `BLOKLENGTE` to the shortest block (4 for a measuring group).
The web page can follow or replay a recording of such a vehicle, but its cable mode speaks OBD2/KWP2000 only and
refuses to open the cable for another transport.

**Read plan** (lists of numbers)

| Name | Meaning |
| --- | --- |
| `KERN_BLOKKEN` | Manufacturer data blocks read every round with KWP2000 service 21: request `21 <id>`, answer `61 <id>` plus data. |
| `ROULEREND_BLOKKEN` | Blocks of which one is read per round. |
| `PID_ELKE_RONDE`, `PID_ROULEREND` | Standard OBD2 PIDs (mode 01), every round and rotating. The reader drops PIDs the ECU does not support. |
| `SNEL_BLOKKEN` | Blocks for the fast roughness recording. |
| `ALLE_BLOKKEN` | All block ids, for a complete status snapshot. |

**Meaning**

```python
SIGNALEN = {
    "toerental": ("0C", lambda b: int.from_bytes(b[5:7], "big"), "rpm", "toerental", "pid", ""),
    #  key        block  function(bytes of the block) -> value     unit   description  certainty  remark
}
```

`certainty` is shown under every value in the web page, so that a guess is never presented as a fact:

- `"pid"` compared with the standard OBD2 PID at several operating points;
- `"handboek"` the behaviour matches an input or parameter described in a workshop manual;
- `"gedrag"` derived only from how the value moves; documented nowhere;
- `"firmware"` read from the ECU's own program: which variable is sent and how the ECU scales it;
- `"aanname"` an assumed sensor curve or a value taken from one other car; still to be measured.

The web page shows a text for each of these five, and "unknown certainty label" for anything else.

Also: `decode(blocks)`, `PIDS` and `decode_pids(pids)` for the reader's own reports, `vensters(values)` with
`VENSTER_LABELS` and `VENSTER_OPMERKINGEN` for "the engine is now inside the window in which the ECU runs self-test X",
and `CANONICAL`, `TPS`, `HIGHLIGHT` for the web page. The full schema is in the docstring of `web/signalset.py` and in
`docs/opnameformaat.md`.

## Adding a car

1. Start with `generic_obd2.py`: `kline-obd --voertuig generic_obd2 scan`, then record a drive. If that works you have
   the standard values, and the web page shows them without a vehicle file.
2. For more, copy `generic_obd2.py` and add blocks. Finding out what an undocumented block means is slow work: record
   every block in several engine states (ignition on, idle, a steady 2500 rpm, a drive), look at which bytes move with
   what, and check candidates against a standard PID before you trust them. `docs/matiz-m200-08.md` shows how that went
   for the Matiz, including the guesses that turned out wrong.
3. A vehicle file only describes what to read and what it means; it never sends anything by itself.
4. In the file's docstring, say which car and ECU it is for and whether it was tested on a car. Label each signal
   honestly.
