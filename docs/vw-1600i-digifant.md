# VW 1600i (Mexican Beetle) Digifant ECU: what kline-obd needs to read it

> **Status in this repository (added by the kline-obd maintainer).** Since this document was written, a KW1281 transport
> (`reader/kw1281.py`), a simulated ECU, a recorder and the vehicle file `vehicles/vw_1600i_digifant.py` have been built
> from it. They are tested against the simulator only; **nothing has been tried on a real ECU or car.** The transport can
> send only the read titles listed below, by name; it has no way to send any other title. Where this document makes
> proposals for the vehicle-file format, `vehicles/README.md` describes what was actually chosen.

For whoever adds the 1600i to kline-obd. It covers the protocol this ECU speaks, which requests are reads and which
must never be sent, what every byte of the answers means, and where the current vehicle-file schema does not fit.

**A vehicle file alone is not enough.** kline-obd speaks ISO 14230 (KWP2000) and ISO 9141-2 at 10400 baud. This ECU
speaks neither. It speaks **KW1281 at 1200 baud on address 0x01**, has no OBD2 modes, no PIDs and no service 21. The
work is therefore: a KW1281 transport first, then the vehicle file.

## Scope and status

| | Status |
| --- | --- |
| ECU | Temic **043 906 022 B**, software "DIGIFANT 1.8" (Mexican Beetle 1600i, engine code ACD, 1993 to about 1998). The earlier **043 906 022** (no suffix) has the same diagnostic code and tables, byte for byte. |
| 043 906 022 D and E ("DIGIFANT 1.82", later cars) | **Not covered.** Different program; nothing below was checked for them. |
| Protocol, requests, answer layouts, timings | Read from the ECU's program (the stock B image that 1600i.de publishes). **Never exercised on the wire by us.** |
| Baud rate and protocol name | Confirmed by 1600i.de ("the ECU works at 1200 baud", VAG-COM shows KW1281). |
| Meaning of the ten values of the main data block | Program and 1600i.de agree on the order. The conversions reproduce a real log of a B ECU (80 samples, engine running): plausible temperatures, voltage and speed, and the injection time agrees with a model of the fuel calculation in 75 of 77 samples. |
| Manifold pressure in kPa | **Assumed** sensor curve, not measured. Raw counts are certain. |
| Everything via RAM reads (titles 01 and 08) | Program only. |
| Tested on a car with kline-obd | **No.** |

Certainty labels used below: **program** = read from the ECU's code (which variable is sent, how the ECU itself scales
it); **site** = documented on 1600i.de (VAG-COM label file, block descriptions) or in the Brunson guide; **log** = seen
in the real log; **assumed**.

## The car side

- Diagnostic connector: a 3-pin plug in the engine bay. Pin 1 = K-line, pin 2 = ground, pin 3 = +12 V. No L-line. A
  plain KKL cable works through an adapter to OBD pin 7 (K), 4/5 (ground) and 16 (+12 V). K-line is ECU pin 20.
- Ignition on is enough; the engine may run.
- VAG-COM needs 10 s and several attempts on this ECU because it tries other baud rates first. The ECU itself answers
  0.4 s after the address byte.

## Protocol as this ECU implements it

### Wake-up

| Step | What | Timing |
| --- | --- | --- |
| 1 | K-line idle (high, no bytes) | at least 2 s before the ECU accepts an address; allow 2.6 s. Opening the serial port can put a pulse on the line, so wait after opening. |
| 2 | Tester sends address **0x01 at 5 baud**: start bit, 8 data bits LSB first, stop bit; 200 ms per bit | The ECU samples the line itself every 205 ms. Parity and stop bit are not checked. Only 0x01 is accepted. |
| 3 | ECU sends `55 01 8A` at **1200 baud, 8N1** | 0x55 about 0.4 s after the last data bit (0.15 s after the end of the stop bit), 0x01 about 40 ms later, 0x8A directly after it |
| 4 | Tester sends `75` (the complement of 0x8A) | must be **completely received within about 80 ms after the end of the 0x8A**. Without it the ECU repeats `55 01 8A` once, 0.2 s after that window closes, waits another 80 ms and then gives up. |
| 5 | ECU starts its first block | about 50 ms after the 0x75 |

Three things about step 1 that the program does and a tester has to allow for:

- The 2 s are counted by the ECU only while it is waiting for an address, and **any byte it receives starts the count
  again**. They are four ticks of a 0.51 s clock, so the real time is 1.5 to 2.0 s.
- After a **failed wake-up** (the ECU took the address but got no `75`) it keeps trying until 0.65 s after the end of the
  address and only then starts counting. A new address is accepted at the latest 2.7 s after the end of the failed
  one: wait 3.3 s, not 2.6.
- After a **lost session** the count starts when the ECU itself gives up, which is up to 1.35 s after its last block
  (see "Blocks"). A new address is accepted at the latest 3.4 s after that block.

**Send no address other than 0x01.** The program does not ignore another value cleanly: it stores the received value
as its link state. Values `09` to `10` hex put it in the middle of the sync or block exchange for a few tenths of a
second without a wake-up. So do not walk through addresses on this ECU either.

### What an OBD2 or KWP2000 tester does to this ECU

Worth knowing, because every generic tester tries these two things. Both come out harmless in the program; neither
was tried on a real ECU.

- **Address 0x33 at 5 baud** (ISO 9141-2 and KWP2000 slow init). The ECU reads the address correctly, finds it is
  not 0x01 and, through the quirk above, restarts the diagnostic part of its program, exactly as at ignition-on: it
  clears its own working memory (RAM `A8`-`BC`), reloads the stored faults from the EEPROM and sets up the serial
  port again. It sends nothing. Fuel and ignition are not involved, nothing is written to the EEPROM, and a fault that
  was halfway through its confirmation time (about 5 s) starts that time again. The ECU listens for an address again
  1.6 to 2.7 s later, if the line stays quiet. Switching the ignition off and on is not needed and does the same thing.
- **A fast-init pulse** (25 ms low, then bytes at 10400 baud). The ECU looks at the line again 98 to 106 ms after it
  first saw it low, to confirm a start bit. By then the pulse and the request behind it are over, so it calls it a
  false start and goes back to waiting. The only effect is that its 2 s of step 1 start again. If it had not finished
  those 2 s yet, the pulse merely restarts them.
- Bytes at 10400 baud are noise to its 1200 baud receiver. While they keep coming the ECU stays in step 1 and never
  listens for an address.

The one pattern to avoid is a line that is low again 98 to 106 ms after an earlier falling edge and stays busy for
the 1.7 s after that: the ECU then samples eight "address bits" from whatever is on the line.

### Blocks

`<length> <counter> <title> <data ...> 03`, where length counts every byte after itself.

- Every byte except the final `03` is answered by the receiver with its complement (`FF - byte`) before the next byte
  is sent. The ECU's program runs the link on a tick of 8.2 ms: it starts a complement 8 to 16 ms after it has the
  byte, sends its own next byte one tick after it has seen the tester's complement, and waits up to 0.2 s for that
  complement. A wrong complement is ignored, not answered.
- **One byte with its complement takes about 30 to 50 ms in either direction**, depending on how fast the cable turns
  the line around. A block of 14 bytes takes about 0.5 s, not the 0.12 s its bytes would take at 1200 baud.
- The two sides alternate, one block each. The ECU answers about 50 ms after the tester's block.
- The ECU uses the tester's counter plus one and does not check the counter it receives. Its first identification
  block carries whatever counter it had left, so take that value as given and do not expect 1.
- **Keep-alive:** after an ECU block the tester must start its own block within about 1.1 s. With nothing to ask, send
  ACK (`03 <ctr> 09 03`); the ECU answers ACK. After 1.13 s without a byte the ECU forgets a half-received block, and
  after another 0.2 s it drops the session and step 1 applies again. Inside a tester block the limit is 0.2 s per
  byte.
- If a complement from the tester is missing, the ECU waits 0.2 s twice and then sends the whole block again from its
  first byte, once. If that fails too, it drops the session.
- The ECU hears its own bytes on the line and expects to: it waits for the echo of each byte it sent before it listens
  for the tester.
- These times come from the code with the engine stopped; none was measured. With the engine running the ECU looks at
  the line only between its engine calculations, so each of them can shift by a few milliseconds either way. The window
  for the `75` is the one where that matters.

### Identification

Right after the wake-up the ECU sends three blocks with title `F6` (ASCII). The tester answers each with ACK.

| Block | Data | Example (stock B) |
| --- | --- | --- |
| 1 | part number, 12 characters | `043906022B  ` |
| 2 | component, 20 characters, plus 4 digits | `DIGIFANT 1.8        1596` |
| 3 | 4 bytes: `00` and three coding bytes from the EEPROM | per car |

The four digits are a software number on a stock ECU (1596 on B, 1365 on the plain 022). **A chip tuner can put his own
job number there**, which then identifies one ECU: treat the digits and block 3 like a serial number in anything
published.

### Requests

| Title | What it does | Answer | Use |
| --- | --- | --- | --- |
| `12` | **the ten raw values** (see below) | `F4` + 10 bytes | read, the main one |
| `29 <n>` | measuring group n = 1..8 | first time `02` (a header of 50 to 77 bytes that describes the four values), after that `F4` + 4 bytes | read, but slow: see "Title 29" |
| `02` | the header of the group selected last | `02` | read |
| `07` | fault memory | `FC` + 3 bytes per fault | read |
| `08 <a>` | one byte of RAM page zero at address a | `FB 00 <value>` | read |
| `01 <n> <hi> <lo>` | ten bytes of RAM page zero from address lo (n and hi are ignored) | `FE` + 10 bytes | read |
| `19 <n> <hi> <lo>` | twelve EEPROM cells from index lo | `EF` + 12 bytes | read; not needed, and the cells are per car |
| `00` | sends the three identification blocks again | `F6` | read |
| `09` | ACK | `09` | keep-alive |
| `06` | end of session | none. The ECU leaves the session the moment it has the title byte: **it returns no complement for the `06`** and does not wait for the closing `03`. Then step 1 applies. | to close |
| `03` | answered as a RAM read, not a ROM read | `FE` | no use |
| `21`, `22`, `28`, `2A`, `2B` and the other titles below `30` | not implemented | `0A` (NAK) + counter | |
| `30` and above | **not checked by the ECU**, see below | unpredictable | never |

**Never send these.** They are not reads.

| Title | Why not |
| --- | --- |
| `11` basic setting | Puts the engine control in a special mode: fixed ignition timing, fixed idle target, and the ECU learns and **stores** throttle and idle values in its EEPROM. A wrong basic setting is a known cause of a badly idling car. |
| `04` actuator test | Switches the fuel pump relay, the idle valve and the charcoal canister valve (engine stopped). |
| `05` clear fault memory | Erases EEPROM cells. If it is ever built, give it the same guard as the mode 04 clear on other cars. |
| `10` recoding, `1A` write EEPROM | Write EEPROM cells. A wrong value can change how the engine runs. |
| **any title `30` or higher** | The ECU looks the title up in a 48-entry table without checking the range, so a higher title picks up a stray byte as its handler. Worked out from the program: `37`, `5F`, `87`, `8F`, `97`, `9F`, `B7`, `C7`, `CF`, `DF`, `E7`, `EF`, `F7` and `FF` run the basic-setting handler, `3B`, `53` and `7E` the EEPROM write, `7F` and `BF` the fault-memory clear, `AE` the recoding, and `41` and `46` make the ECU jump into data. |

So: **send only the titles in the read list, and never scan or probe titles on this ECU.** A probing script that walks
through service numbers, as is harmless on a KWP2000 ECU, can change stored values here.

Reading has no side effects in the code: the handlers for `12`, `29`, `08`, `01` and `07` only copy values out.

## The data

### Title 12: ten raw values

One request, one answer, the same ten numbers VAG-COM shows as "group 000". A cycle is 4 bytes out and 14 back and
takes 0.6 to 0.9 s by the program's own timing, so at best one to one and a half samples per second. The only
measured figure is slower: the real log named under "Sources" has 1.27 s between samples, taken with VAG-COM. Treat
every time in this document as a lower limit.

| # | Variable | Conversion | Normal, warm idle (site) | Certainty |
| --- | --- | --- | --- | --- |
| 1 | intake air temperature, ADC | table A below; higher = colder | | program |
| 2 | battery voltage, ADC | V = raw x 24 / 255, about 0.094 V per count | 130-150 | program (the ECU's own header gives 24 V full scale); the range is from the site |
| 3 | engine temperature (cylinder head sender), ADC, unfiltered | table E below; higher = colder | below 44 (= above 87 C) | program, site |
| 4 | load: the manifold pressure ADC after a filter. With a failed pressure signal it is a substitute from throttle and speed. | kPa = 0.369 x raw + 14.3 (**assumed** curve); the ECU itself shows raw x 100 / 255 % | 50-120 | program; kPa assumed |
| 5 | lambda sensor, ADC, **inverted**: high = lean, low = rich | V = max(205 - raw x 0.89, 0) x 2 / 255. About 0.84 V at 110, 0 V from 230 | swings 100-250; a steady 250 = no control | program, site |
| 6 | lambda control dead-time counter | raw | 0 | site (name), program (which variable) |
| 7 | lambda control counter | raw; 127 until closed loop has started | mostly 0 | site, program |
| 8 | throttle potentiometer, ADC | raw. Closed 40-60 per the site; 64 closed and 234 wide open in the log | 40-60 | program, site, log |
| 9 | injector pulse, high byte | ms = raw x 0.512, including about 0.5 ms opening time. A tester shows raw x 0.5, going by the ECU's header. All four injectors fire together once per crank revolution. | 6-8 | program, log |
| 10 | engine speed index: 16 per map column, a **higher number is a lower speed** | see "Speed" below; 240 = not turning or below 480 rpm | about 200 | program, site |

Table E, engine temperature in C at raw 0, 16, 32, ... 256 (interpolate linearly):

`150, 132, 100, 83, 70, 61, 52, 45, 38, 30, 24, 17, 10, 1, -9, -24, -40`

Table A, intake air temperature in C at raw 0, 16, 32, ... 256:

`120, 60, 40, 28, 18, 10, 4, -2, -7, -13, -18, -24, -29, -36, -40, -40, -40`

The ECU sends both tables itself in the group header (title `02`). Table E follows a standard VW sender (2.5 kohm at
20 C) to within 1 C with one fitted pull-up value.

**Speed.** The index runs linearly in the *period* between sixteen breakpoints. Period in units of 2 us per half crank
revolution at index 0, 16, ... 240:

`3072, 3568, 3952, 4400, 4736, 5152, 5664, 6256, 6976, 8336, 9088, 11120, 14288, 18528, 22736, 31232`

rpm = 15,000,000 / period, which gives 4883, 4204, 3796, 3409, 3167, 2911, 2648, 2398, 2150, 1799, 1651, 1349, 1050,
810, 660 and 480 rpm at the breakpoints. Resolution is coarse: at idle one count is about 15 rpm, near 4000 rpm about
25. The ECU's own display table interpolates in rpm instead and reads up to 2 % higher between breakpoints; 1600i.de's
"168 = 1508 rpm" comes from that table (by period it is 1485).

**Manifold pressure.** The sensor is on the ECU board (Motorola, marked 105 kPa). The kPa formula assumes the
Motorola MPX4100A curve. It fits the ECU's own fuel calibration, and on overrun it gives 22-25 kPa, which matches a
vacuum gauge reading from a forum user. One measurement settles it: the raw value with ignition on and engine off,
together with the barometric pressure of that day (expected about 236 at 101 kPa).

### Title 29: measuring groups 1 to 8

`04 <ctr> 29 <n> 03`. Use 1 to 8 only: the number is not checked, 0 and 9 to 16 return group 1 and higher numbers
wrap around.

- The first `29` after any other title is answered with the header (title `02`), not with data. Ask again for data.
  The header is not sent again when only the group number changes. Any other title in between, an ACK included, brings
  the header back.
- **The header is long:** 50 bytes on the line for groups 1, 2, 4, 5, 6 and 7, 67 for group 3 and 77 for group 8, each
  byte with its complement. That is 1.6 to 2.4 s for the short one and 2.5 to 3.7 s for group 8. A group read that
  follows another title therefore costs 2.2 to 3.3 s in all (group 8: 3.1 to 4.6 s), against 0.4 to 0.6 s for a group
  read that needs no header.
- The header holds four descriptors: `<type> <a> <k>` followed by k table bytes. The type codes are not the formula
  numbers of later VAG ECUs. What is known: `8C 28 11` + 17 bytes = temperature table (value minus 40); `8B 1A 11` +
  17 bytes = speed table (value x 26 rpm); `85 <a>` = volts, full scale a at 255; `81 64` = percent; `89 32` = ms,
  raw x 0.5; `8D 01 1B` + text = one of three 9-byte texts. Decode with your own formulas and keep the header only as
  a cross-check.
- Value 1 is always the engine temperature (raw, table E) and value 2 the speed index (`FF` when the engine is not
  turning).

| Group | Value 3 | Value 4 |
| --- | --- | --- |
| 1 | lambda voltage byte L = max(205 - raw x 0.89, 0); V = L x 2 / 255 | injector pulse byte, as value 9 above |
| 2 | throttle angle in degrees from the learned closed position, about 83-85 wide open | load, as value 4 above (0 when not turning) |
| 3 | intake air temperature, raw | battery, raw |
| 4 | ignition byte X: **timing = 30 - X degrees BTDC** | load |
| 5 | throttle angle | battery |
| 6 | lambda control counter | lambda dead-time counter |
| 7 | ignition byte X | the same X as a plain number |
| 8 | ignition byte X | state: 0 part load (`TEILLAST`), 1 idle (`LEERLAUF`), 2 full load (`VOLLAST`) |

Ignition: X = (count x 90 / 256) - 48, where count is the ECU's spark delay after the distributor's reference edge.
"30 - X" is confirmed by a 1600i.de example (group 7 showing 4.0 degrees BTDC next to the number 26). It is the true
angle only if the distributor is set to the 6 degree specification. Above 30 degrees BTDC the byte wraps (timing =
286 - byte), the first header byte changes from `83` to `04`, and the ECU sends the header again before each data
block.

For logging, title `12` is the better choice: everything in one answer and no header logic. The groups add the
ignition angle, the throttle angle and the idle / part load / full load state, but at the price of the header: above
30 degrees BTDC, which is most of normal driving, every read of group 4, 7 or 8 pays for it. **A RAM window gives the
same two values without a header:** `01 0A 00 2E` returns addresses `2E` to `37`, with the state flags in the first
byte and the spark count (`35`) in the eighth, in 0.7 to 1.0 s. The group's state number is 1 when bit 7 of `2E` is
set, else 2 when bit 6 is set, else 0; the group's ignition byte is (count x 90 / 256) - 48, whole numbers.

### Titles 01 and 08: RAM

Useful addresses in page zero. All from the program; none was ever read over the wire.

| Address | Content |
| --- | --- |
| `00`-`03`, `05`, `06` | the ADC values of the ten-value block (battery, load, lambda, throttle, engine temperature, air temperature) |
| `07` | engine temperature as the maps use it (substitute value when the sender has failed) |
| `0D` | speed index, averaged, as the maps use it (`0C` is the unaveraged one in the ten-value block) |
| `10`-`12` | time between distributor pulses, 24 bit, 2 us units: rpm = 15,000,000 / value. The exact speed. |
| `2E` | state flags: bit 7 idle, bit 6 full load, bits 1-0 = 01 while in basic-setting mode |
| `35` | spark delay count: timing = 78.1 - 0.3516 x count degrees BTDC |
| `3C`-`3D` | injector pulse, 16 bit, 2 us units, including opening time |
| `53`-`54` | lambda correction, signed, logarithmic: 512 = factor 2, 0 = none |
| `66` | idle valve demand, 0-255 |
| `95` | barometric pressure: the manifold pressure ADC taken at key-on with the engine stopped. Slowly updated at full throttle at low engine speed. If the program starts while the engine is already turning faster than about 300 rpm, it uses a fixed 240 instead. |
| `96` | learned closed position of the throttle potentiometer (ADC); the throttle angle of the groups counts from it |
| `9B` | learned mixture adaptation, 128 = neutral, 1 to 254. Loaded from the EEPROM at key-on. |
| `9D` | learned idle valve offset, 0 to 54. Loaded from the EEPROM at key-on (12 if the stored value cannot be read). |

One `01` request returns ten consecutive bytes, so `01 0A 00 35` gives `35`-`3E` in one answer and `01 0A 00 95` gives
`95`-`9E`: barometric value in the first byte, closed throttle in the second, mixture adaptation in the seventh and
idle offset in the ninth. The answer takes the address from the last of the three request bytes; the count and the
high byte are ignored.

### Title 07: fault memory

Three bytes per fault: code high, code low, status. `FF FF 88` means no fault stored. The memory holds seven faults
and a block at most four: with more, the ECU continues in a second `FC` block after an ACK. The ACK after the last
`FC` block is answered with ACK. If the tester asks something else before the list is complete, the rest comes out
as the answer to its next ACK. The B program knows these codes:

| Code | Meaning (site) |
| --- | --- |
| 00518 | throttle potentiometer |
| 00519 | manifold pressure sender (hose, or the sensor on the ECU board) |
| 00522 | engine temperature sender |
| 00523 | intake air temperature sender |
| 00525 | lambda sensor |
| 00532 | supply voltage |
| 65535 | ECU defective |

A stored fault puts this ECU in a fallback mode until it is erased, and a failing Hall sender is not stored at all
(site). There are no readiness monitors, no freeze frame and nothing like mode 06.

## Where the vehicle-file schema does not fit

Proposals. Each of them touches the shared format, so they are for the people who own it to decide.

| Schema today | This ECU | Proposal |
| --- | --- | --- |
| `AANKNOPEN`: `"fast"`, `"slow"` on 0x33, 10400 baud fixed | 5-baud on 0x01, 1200 baud, then KW1281 blocks | a third method, e.g. `"kw1281"`, with address and baud rate in the vehicle file |
| Blocks are KWP2000 service 21 ids, 32 bytes each (`BLOKLENGTE` default 32) | title `12` gives 10 bytes, a group gives 4, a RAM read 10 | let a block id name a KW1281 read, e.g. `00` = title 12, `01`-`08` = groups, and a separate form for a RAM window; `BLOKLENGTE` 4 |
| `PID_ELKE_RONDE`, `PID_ROULEREND`, the "which PIDs do you support" query | no PIDs | empty lists; skip the query for this method |
| Certainty `"pid"` = compared with a standard PID | impossible here | a new label for "read from the ECU's program", next to `"handboek"` (1600i.de) and `"gedrag"` |
| `CANONICAL` only for signals calibrated with a PID | none can be | decide whether program-derived speed, temperatures and lambda may feed the deviation rules; manifold pressure only after the one measurement above |
| `IDENTITEIT_VERZOEK` + `identiteit()` on a KWP2000 answer | identity arrives unasked as three `F6` blocks | part number from block 1, component text and the four digits from block 2; keep the digits and block 3 out of recordings |
| Status snapshot: modes 03, 07, 06, readiness | only the fault memory (title 07) | snapshot = fault list plus the ten values |
| Clear trouble codes = mode 04 | title 05 | not without its own decision and guard |
| Report scripts (`rapport`, `onrust`) | written for the Matiz signals | no equivalent signals here |

A round with only title `12` takes 0.6 to 0.9 s. A RAM window adds 0.7 to 1.0 s. A group adds 2.2 to 4.6 s when it
follows another title, because of its header. The tester must never be silent for more than a second. (An earlier
version of this document said 0.5 s per round and 0.4-0.5 s per extra read; that left out the complement handshake
and the header.)

## How to test it

1. **Simulator first.** A fake ECU needs: the 2 s idle rule (counted from the moment the ECU gives up, restarted by
   any byte), the address at 5 baud, `55 01 8A`, an 80 ms window for the `75`, the complement handshake, three
   identification blocks, ACK for ACK, title `12`, title `29` with the header-first rule and headers of the real
   length (50 to 77 bytes), title `07` answering `FF FF 88`, title `06` without a complement for the title byte, NAK
   for the other titles below `30`, and dropping the session after 1.1 s of silence. Let it fail the test loudly on
   any never-send title, including every title from `30` up, so that a tester that probes cannot pass.
2. **First contact with a real ECU**, ignition on, engine off, reads only: identification, then title `12` a few
   times. Expect battery about 125-135, speed index 240, throttle at its closed value, and the load value at the
   barometric reading. Write that load value down with the air pressure of the day.
3. **Warm idle:** compare with the "normal" column above. Then titles `29` and `07`.
4. A bench ECU works too (supply, ground, K-line to the cable). One caution: if at power-on the engine temperature
   input reads open while the throttle and the air temperature inputs both read near 0 V, the program enters a factory
   test routine instead of normal operation and KW1281 will not answer.

## Sources

- The ECU program: the stock B image on 1600i.de (`1600i-diagnose/steuergeraet/steuergeraet.html`), disassembled as
  68HC11 code. Everything labelled "program".
- 1600i.de: the VAG-COM label file for 043 906 022 B, the pages on reading faults and measuring blocks, the list of
  fault codes, "Start von VAG-COM beschleunigen" (1200 baud), and the forum archive.
- Tom Brunson's guide on 1600i.de (connector pinout, KW1281). Its block data is for the E ECU.
- One real ten-value log of a B ECU with the engine running. Not published.
