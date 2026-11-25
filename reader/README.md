# reader

Command-line tools that talk to the ECU over a plain FTDI KKL cable. They only send read requests. Run them through
`../kline-obd` or directly with `python <script>`. Messages are in Dutch.

Only one program can use the cable at a time.

| Script | `kline-obd` command | What it does |
| --- | --- | --- |
| `kline.py` | | The K-line library: fast init and 5-baud init, half-duplex echo, framing, checksums, timing. |
| `signalen.py` | | Loads the chosen vehicle file from `../vehicles` (`KLINE_VOERTUIG`, default `matiz_m200_08`). |
| `paden.py` | | Where output goes: `KLINE_DATA`, else `../recordings`. |
| `obd_scan.py` | `scan` | Standard OBD2 scan: supported PIDs, monitor status, trouble codes, freeze frame, live values, modes 06 and 09. |
| `ecu_info.py` | `ident` | Modes 06/09, KWP2000 identification (1A), trouble codes (18) and all local data blocks (21). Fast init only. The output can contain the ECU's serial number. |
| `identify.py` | | Tries fast init on 0x33, fast init on 0x11 and 5-baud init, and reports what answers. |
| `probe_adapter.py`, `probe_line.py` | | Is this an ELM327 or a plain KKL cable, and does the line echo? Sends no ECU init. |
| `rit.py` | `rit` | `start <label>`: status snapshot, then a recording in the background. `stop <label>`: stop, snapshot again, compare, report. |
| `egr_capture.py` | `opname` | The recorder. Each round it reads the core blocks and PIDs of the vehicle file plus one rotating item, retries a failed request once, and reconnects when the ECU goes silent. Writes the recording, `<name>.meta.json`, `<name>.fouten.log` and `live.json`. |
| `selftest_snapshot.py`, `selftest_diff.py` | `status`, `vergelijk` | Snapshot of trouble codes, monitor status, mode 06 results and all data blocks, and the difference between two snapshots. The web page writes the same format before and after clearing trouble codes. |
| `rit_report.py` | `rapport` | Report of a recording. Written for the Matiz signals: warm-up, closed loop, EGR valve, roughness per cylinder, both lambda sensors, self-test windows, marked moments. |
| `onrust_snel.py`, `analyse_onrust.py` | `onrust` | Reads only the roughness blocks, about twice as often as the normal recording, and compares two recordings per cylinder channel. Matiz only. |
| `luister.py`, `kwp_ontleed.py` | `luister` | Passive listener: decodes KWP2000 frames on the line, pairs requests with answers and flags actuator, security and write services. Sends nothing. Meant for listening to a scan tool through a passive OBD splitter. |
| `stuurbaar.py`, `opdracht.py`, `stuur.py` | `stuur` | Design-stage command channel for actuator tests: an allow-list, preconditions and guarded execution. No action has known command bytes, so every request is refused. |
| `nep_ecu.py`, `selftest.py` | `test` | A simulated ECU and the self-test that runs the scripts above against it. |

## Timing notes that cost time to find

- The cable only receives with **RTS low**.
- **Do not assign `timeout` on an open pyserial port** at 10400 baud on macOS. pyserial then reconfigures the port
  through 38400 baud and corrupts the bytes in flight. `kline.py` sets it once and keeps its own deadlines. In a
  comparison on the same car this was the difference between 37 and 120 good answers out of 120.
- The fast-init wake-up pulse needs **busy-wait timing**; `time.sleep` overshoots the 1 ms tolerance on macOS.
- One retry per failed request removed almost all losses while driving (183 damaged answers, 3 left).
