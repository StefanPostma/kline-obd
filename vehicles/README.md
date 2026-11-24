# vehicles

One Python file per car. It tells the reader **what to read** and both the reader and the web page **what the bytes
mean**. Nothing else in the project knows about a specific car.

| File | Car | Tested on a car |
| --- | --- | --- |
| `matiz_m200_08.py` | Chevrolet/Daewoo Matiz M200 0.8, Siemens Sirius D32 (`5WY5407A`) | yes |
| `generic_obd2.py` | any car that speaks OBD2 over the K-line; standard PIDs only | no, simulator only |

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
- `"gedrag"` derived only from how the value moves; documented nowhere.

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
