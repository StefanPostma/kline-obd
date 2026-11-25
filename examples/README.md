# examples

One real drive, recorded with `kline-obd rit start` on a Chevrolet/Daewoo Matiz 0.8 (2009): 18 minutes, 871 rounds,
starting with a cold engine.

| File | What |
| --- | --- |
| `rit_voorbeeld.jsonl` | the recording, unchanged; the format is in `../docs/opnameformaat.md` |
| `rit_voorbeeld.meta.json` | ECU part number, software and calibration id, and the read plan that was used |
| `rit_voorbeeld.markeringen.log` | eight times "nu stotterde hij" ("it stuttered just now"), pressed by the driver in the web page; the button is now called "Annotatie: probleem" and writes that text |

```
../kline-obd web --replay rit_voorbeeld.jsonl --speedup 25 --profile matiz08
../kline-obd rapport rit_voorbeeld.jsonl
```

Left out: the raw ECU identification answer (it holds the ECU's serial number) and the production date. This ECU has
no VIN programmed, so there is none in the data. The recording does contain the odometer reading (block `05`) and the
date and time of the drive.
