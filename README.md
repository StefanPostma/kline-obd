# kline-obd

<a href="https://buymeacoffee.com/stefanpostma"><img src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=stefanpostma&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" alt="Buy me a coffee" height="36"></a>

Read live engine data from an older car over the K-line (ISO 14230 KWP2000, ISO 9141-2) with a cheap FTDI "KKL" USB
cable. Three parts:

- **`reader/`** command-line tools that connect to the ECU, record a drive and report on it;
- **`web/`** a local web page that shows the values live, follows a recording, and logs the moments you mark;
- **`vehicles/`** one file per car that says which data to read and what the bytes mean.

Everything is **read-only**, with one exception: the web page can clear trouble codes (OBD mode 04) after a status
snapshot and a two-step confirmation. Nothing else is written to the car and no actuator is driven.

## What has been verified, and what has not

| | Status |
| --- | --- |
| Chevrolet/Daewoo Matiz M200 0.8 (2009, Siemens Sirius D32 ECU `5WY5407A`) | **Tested on the car.** Fast init, OBD modes 01/02/03/06/07/09, KWP2000 services 1A, 18 and 21 (manufacturer data blocks). Several recorded drives; on an 18-minute drive 3 of 6097 requests were lost (a failed request is retried once). |
| VW 1600i (Mexican Beetle), Digifant ECU 043 906 022 B, protocol KW1281 | **Never tried on an ECU or car.** Transport, recorder and vehicle file are built from an analysis of the ECU's program (`docs/vw-1600i-digifant.md`) and tested against a simulated ECU only. |
| Any other car | **Not tested on a car.** A generic OBD2 vehicle file exists. 5-baud init (KWP2000 and ISO 9141-2) is tested only against a simulated ECU. |
| The reader's vehicle selection, port detection and data folder | Tested only against a simulated ECU. They were added after the last drive. |
| The web page's cable mode reading manufacturer blocks | Tested only against the simulator. |
| macOS, Python 3.9 and 3.14 | Used for all of the above; the self-tests pass on both Python versions. |
| Linux | Untested. The reader looks for `/dev/ttyUSB*`; the web page needs `--serial /dev/ttyUSB0`. |
| Clearing trouble codes and resetting the engine light | Built in the web page (cable mode). **Tested only against the simulator; never used on a car.** |
| Actuator tests and writing | **Not built.** A guarded command channel exists as a design (`docs/aansturen-ontwerp.md`), and it refuses every action because the command bytes for this ECU are unknown. |

The meaning of the manufacturer data blocks is not documented by the manufacturer. It was derived from how the values
behave and from a workshop manual; every signal carries a certainty label (`pid`, `handboek`, `gedrag`). See
`docs/matiz-m200-08.md`.

## Quick start (no car needed)

Python 3.9 or newer; the only dependency is pyserial.

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

./kline-obd test                 # all offline self-tests, about two minutes
./kline-obd web --demo           # simulated ECU, open http://127.0.0.1:8777
./kline-obd web --replay examples/rit_voorbeeld.jsonl --speedup 25 --profile matiz08
./kline-obd rapport examples/rit_voorbeeld.jsonl
```

`examples/` holds one real 18-minute drive with a cold start.

## In the car

You need an FTDI-based KKL cable (the plain kind without an ELM327 chip). Plug it into the OBD port, ignition on.

```
./kline-obd poort                # is the cable there?
./kline-obd scan                 # supported PIDs, trouble codes, monitor status, live values
./kline-obd rit start mylabel    # status snapshot, then recording in the background
#   ... start the engine and drive ...
./kline-obd rit stop mylabel     # stop, read the status again, compare, print the report
```

While a recording runs, watch it live in a second terminal. The web page then follows the file and leaves the cable
alone:

```
./kline-obd web --follow --profile matiz08
```

Or let the web page use the cable itself: `./kline-obd web --profile matiz08`. Only one program can hold the cable at a
time; the page refuses to switch to the cable while a recording has it open.

Recordings go to `recordings/` (ignored by git). Set `KLINE_DATA` to use another folder and `KLINE_PORT` to force a
serial port.

## Another car

```
./kline-obd voertuigen
./kline-obd --voertuig generic_obd2 opname mycar.jsonl 600
./kline-obd web --follow recordings/mycar.jsonl
```

`vw_1600i_digifant` is for an ECU that speaks neither OBD2 nor KWP2000 but the older VW protocol KW1281; `opname`,
`rit` and `status` switch to that transport by themselves, and `scan` and `ident` refuse to run for it.

`generic_obd2` reads standard OBD2 values only. To add what your ECU offers beyond that, write a vehicle file; the
format is in `vehicles/README.md`. Please say in the file how each signal was established, and whether it was tested on
a car.

## Layout

| Path | What |
| --- | --- |
| `kline-obd` | one command for everything below |
| `reader/` | K-line library (`kline.py`), recorder (`egr_capture.py`, `rit.py`), status snapshot, report, passive listener; see `reader/README.md` |
| `web/` | the web page and its own K-line stack and simulator; see `web/README.md` (Dutch) |
| `vehicles/` | vehicle files; see `vehicles/README.md` |
| `examples/` | a sample recording |
| `docs/` | the recording format, what was measured and found on the Matiz, the design notes (Dutch), and the description of the VW 1600i ECU (English) |
| `tests/` | the reader's K-line library against the web page's simulator |

`reader/` and `web/` each have their own K-line implementation. They were written separately and share only the
recording format and the vehicle files (`docs/opnameformaat.md`).

## Language

This README, `reader/README.md` and `vehicles/README.md` are in English. Code comments, the messages the tools print,
identifiers such as `SIGNALEN` and `KERN_BLOKKEN`, and everything in `docs/` and `web/README.md` are in Dutch.

## Safety

- The tools send only read requests: OBD modes 01, 02, 03, 06, 07, 09 and KWP2000 services 1A, 18 and 21, plus
  StartCommunication and StopCommunication.
- The one request that changes something is OBD mode 04, clear trouble codes, in the web page. It is only offered in
  cable mode with the engine off and the car standing still, with a battery voltage above 11.8 V, after a snapshot of
  the codes, monitors and data blocks, and after a two-step confirmation. It is sent once and never repeated. Clearing
  cannot be undone: it also erases the freeze frame, the readiness status and the monitor results, and it does not fix
  the cause. See `web/README.md`.
- On the Matiz the engine ECU is paired with the immobilizer. Do not send it services you do not understand.
- **Never scan or probe an old ECU you do not know.** A request the ECU does not recognise is harmless under KWP2000,
  but not everywhere: the Digifant of the VW 1600i does not check the request number, and an unknown one can end up
  in its basic-setting or EEPROM-write routine. The KW1281 transport here can therefore only send a fixed list of
  read requests, by name, and the scan commands refuse to run for such a vehicle.
- **On the VW 1600i Digifant, use `--voertuig vw_1600i_digifant`.** An OBD2 or KWP2000 tester on that ECU (`scan`,
  the generic OBD2 vehicle file, the web page's cable mode) gets no answer. According to the ECU's program it does no
  harm either: the wake-up address 0x33 only makes the diagnostic part restart itself as at ignition-on, the engine
  control is not involved and nothing is stored. That was read from the program, not tried on a real ECU. The hard
  rule is about requests inside a KW1281 session, and about wake-up addresses 09 to 10 hex, which drop the ECU into
  the middle of a block exchange; the KW1281 transport therefore wakes on address 01 only and refuses any other.
- `ident` prints the raw identification answer of the ECU. It can contain the ECU's serial number and a VIN: check it
  before you share it. Recordings made by this tool do not contain it.
- Do not operate a laptop while driving. Start the recording before you set off.

## Contributing

Contributions are welcome. What helps most:

- **A report from another car.** Run `./kline-obd --voertuig generic_obd2 scan` and say which car and ECU it was, which
  init method worked and what did not. A failure report is as useful as a success.
- **A vehicle file** for your car (`vehicles/README.md`). Label every signal with how you established it.
- **Linux**, other cables, and anything in the status table above that says "not tested".
- Fixes and improvements to the reader, the web page or the docs.

Before you open a pull request, run `./kline-obd test`. Anything that changes something on the car must ask for
confirmation and say what it will do; actuator tests wait until the command channel has verified commands. Do not
include data that identifies one car (VIN, ECU serial number) in files you add.

## Support

If this saves you time, a coffee is always appreciated:
[buymeacoffee.com/stefanpostma](https://buymeacoffee.com/stefanpostma). The project stays free and open source either
way.

## License

MIT, see `LICENSE`. Use the code as you like.
