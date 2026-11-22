# Design and measurement notes

This document consolidates the engineering decisions, measurement method and
known limitations of Pluto S21 Analyzer. It replaces the original development
plan. Step-by-step instructions are in the
[measurement](../src/pluto_analyzer/help/measurement.md) and
[calibration](../src/pluto_analyzer/help/calibration.md) guides.

## 1. Scope

The tool measures the **magnitude of S21 only** with a single ADALM-PLUTO.
The signal path is `TX1 → DUT → RX1` with an optional fixed attenuator or
amplifier. It is intended for checking filters, cables, attenuators and
antenna paths where insertion loss is the quantity of interest.

Non-goals:

- **phase of S21** — there is no reference channel and the PLL is not
  phase-coherent between points;
- **S11, S12, S22** — the 1×1 board has one receiver and no reflectometer
  bridge;
- metrology-grade accuracy — the AD9363 has a 1 dB gain step and its RF
  response is calibrated only over the measured span.

## 2. Hardware platform

- ADALM-PLUTO Rev.B (Z7010), physically an **AD9363**, firmware v0.38-dirty
  with the unofficial range extension. The device tree reports `AD9364`.
- One RX and one TX channel (I/Q of a single receiver).
- RX/TX LO: 70 MHz–6 GHz after the range patch (TX synthesizer starts at
  46.875 MHz). Stock AD9363 limits are 325 MHz–3.8 GHz.
- Sample rate: 2.083–61.44 MSPS; RX gain: −3…71 dB in 1 dB steps; RF
  bandwidth: 0.2–56 MHz.
- TX attenuation has effectively one usable step: `0` or about `−89.75` dB.
  Intermediate values make the TX path unstable, so `tx_gain=0` is the
  working default and the level is controlled by the RX gain.
- The AD9361 gain table ends at 4 GHz, which produces a reference-level step
  of a few dB around 4.04 GHz. Calibration must use the same or a denser
  frequency grid than the measurement, otherwise spikes appear near 4 GHz.
- On this host the board is **shared** over the network (`ip:192.168.2.1`).
  Any sweep changes LO and gain for every client. The code observes the
  device for ~1 s before a sweep (`PlutoVNA.check_quiet`) and aborts with exit
  code 4 if another client reconfigures DDS/LO/rate/gain. `--allow-busy`
  overrides the check and marks the trace as suspect.

## 3. Architecture

```
src/pluto_analyzer/
  config.py       SweepConfig defaults, frequency grid, LO planning
  dsp.py          CW tone, complex lock-in, dBFS, CLIP, smoothing
  hwcheck.py      reads the real device capabilities, validates the span
  sdr.py          PlutoVNA: single owner of the IIO context, state restore
  sweep.py        SweepEngine + SweepResult (no IIO access)
  calibration.py  thru calibration: build, interpolate, save/load, warnings
  export.py       trace.csv and meta.json
  report.py       matplotlib PNG/PDF and Touchstone .s2p
  cli.py          pluto-na-cli: info / measure / cal
  gui/            PyQt6 + pyqtgraph: worker thread, plot, main window
```

The central rule is that **only `PlutoVNA` talks to IIO**. The GUI owns it in
a worker thread (`gui/worker.py`) and communicates through Qt signals and a
command queue. The sweep engine is independent of the GUI and is unit-tested
with a mock SDR. Device state (sample rate, gains, bandwidths, LOs and the
FIR enable flag) is captured on open and restored on close, so a shared Pluto
is left as it was found.

## 4. Measurement principle

### 4.1 Frequency grid and LO planning

The measurement grid is linear by default (`start..stop`, `points`); a single
point is also allowed. The real probed frequency is always exactly on this
grid:

- normally `LO = f − IF` and the tone is detected at `+IF`;
- near the lower tuning limit (for example 70 MHz with IF = 500 kHz) the LO
  would fall below the synthesizer minimum, so the offset sign is flipped:
  `LO = f + IF`, detection at `−IF`.

`plan_lo()` implements this and raises if the LO cannot be placed. The LO
step is 1 Hz, so the requested RF frequency is not moved or rounded.

### 4.2 TX tone and capture

- Complex CW tone at the IF, written to the cyclic TX buffer:
  `A·exp(j2π·f_IF·n/fs)`.
- Defaults: `fs = 8 MSPS`, `N = 2^18` complex samples (~32.8 ms, ~1 MB per
  capture), `A = 2^14` counts (about −6 dBFS of the 16-bit DAC).
- `IF = 500 kHz` by default. `fs/IF = 16`, which is used by the detector.
- Before each point: set LO, wait `dwell_s = 300 ms`, discard two captures,
  then re-apply manual RX/TX gain (the gain table can change at band
  boundaries), wait 50 ms and take the measurement captures.

### 4.3 Lock-in detector

For every capture (`dsp.lockin_amplitude`):

1. mix the complex signal to zero: `y[n] = x[n]·exp(−j2π·f_detect·n/fs)`;
2. if `fs/|f_detect|` is an integer, average blocks of that size; this
   decimates by 16 and places sinc nulls at multiples of the IF;
3. apply a Hann window and coherently average:
   `A = |Σ y·w| / Σ w`.

Coherent averaging is used instead of averaging magnitudes, which gives
better noise rejection. A Kaiser FIR with a 500 Hz passband was rejected: at
8 MSPS it would need hundreds of thousands of taps per point.

### 4.4 Per-point quality

Each point stores the measured amplitude, dBFS, ADC peak, capture count and
spread:

- **CLIP** — ADC peak ≥ 0.8 of full scale (2048 counts). It reflects the raw
  ADC peak, so a transient or another user's transmission can trigger it even
  when the lock-in tone is weak.
- **LOW** — `|S21| < −90 dBFS`.
- **UNSTABLE** — spread between captures > 0.5 dB.

Below 1 GHz three captures are taken, above 1 GHz two; the reported amplitude
is their median. `spread_db` is written to the CSV for diagnostics.

## 5. Calibration

The thru calibration removes the frequency response of the fixture (cables
and any fixed attenuator):

```
S21_dB(f) = dBFS_dut(f) − dBFS_thru(f)
```

- The reference is a normal sweep with the fixture connected `TX → RX` and
  the same IF, sample rate, tone level and RX/TX gains as the measurement.
- `dBFS_thru` is interpolated on a **log-frequency** axis onto the measurement
  grid.
- Calibration is stored as `cal/s21_thru.npz` (`freqs_hz`, `thru_db`) plus
  `cal/s21_thru.json` with app version, UTC date, URI, hardware passport,
  sweep settings and summary.
- Warnings are issued when points fall outside the calibrated span, when
  RX/TX gain, tone level, IF or sample rate differ from the calibration, when
  the reference sweep had CLIP/LOW/UNSTABLE points, or when the file is older
  than 30 days. `--allow-bad-cal` overrides the bad-reference refusal.
- `--offset-db` adds a constant to the calibrated result. It is used when the
  reference path contains a known pad: calibrate with a 10 dB pad and set
  `--offset-db -10` to display the DUT correctly.

Recalibrate after changing cables, the attenuator, the board position or the
firmware. The thru reference is only valid for the exact fixture it was
measured with.

## 6. Output formats

- `trace.csv` — `freq_hz, lo_hz, tone_hz, captures, amplitude, dbfs, s21_db,
  peak, spread_db, clip, low, unstable`. `dbfs` is always the raw level;
  `s21_db` is written only when calibration is applied.
- `meta.json` — app version, UTC timestamp, URI, full `SweepConfig`, hardware
  passport, warnings and summary (min/max/mean, flag counts, duration).
- report PNG/PDF — matplotlib (Agg): curve, raw points, min/max annotations,
  points count, calibration flag, URI and UTC date.
- Touchstone `.s2p` — measured |S21| in `[DB]`. Phase is written as 0° and
  S11/S12/S22 as −100 dB placeholders; the header states that the file is
  magnitude-only. It is suitable for importing an amplitude response, **not**
  for phase-sensitive analysis.

## 7. Interfaces

### CLI

```
pluto-na-cli info    [--uri URI] [--json]
pluto-na-cli measure [sweep options] [--out DIR] [--cal PATH]
                     [--png PATH] [--pdf PATH] [--touchstone PATH]
                     [--allow-busy] [--allow-bad-cal]
pluto-na-cli cal     [sweep options] --out cal/s21_thru
```

Exit code 4 means that the Pluto is being reconfigured by another client;
the CLI refuses to measure unless `--allow-busy` is given.

### GUI

`pluto-na` provides a URI selector, sweep controls, Run/Stop/Continuous,
raw/smoothed/calibrated display, markers, cursor readout and CSV/PNG/report/
Touchstone export. Calibration can be taken, loaded and cleared without
leaving the window. The SDR lives in a worker thread; closing the window stops
the sweep and restores the device.

### SDR ownership and restore

`PlutoVNA` stores the original device state on open and restores it in
`close()`: sample rate, RF bandwidths, gain control mode, RX/TX gains, LOs and
the `voltage_filter_fir_en` flag (pyadi enables the FIR when the sample rate
is written). Reconfiguring the shared device for other users is therefore
limited to the duration of a sweep.

## 8. Defaults and performance

| Parameter | Default |
|---|---|
| Frequency range | 70–6000 MHz |
| Points | 500 |
| IF | 500 kHz |
| Sample rate | 8 MSPS |
| Samples per capture | 2^18 |
| TX tone level | 2^14 counts (~−6 dBFS) |
| TX / RX gain | 0 dB / 10 dB |
| RF bandwidth | 4 MHz |
| Dwell | 300 ms |
| Tone settle | 300 ms |
| Captures | 3 below 1 GHz, 2 above |
| UNSTABLE threshold | 0.5 dB |

`tx_gain = 0` is required because intermediate TX attenuator values are
unstable. `rx_gain = 10` matches the `TX → 10 dB attenuator → RX` validation
fixture. One point takes roughly 0.2–0.5 s including dwell, capture and about
1 MB of network transfer per capture; a 500-point sweep takes several
minutes.

## 9. Validation results

A calibrated thru over 70–6000 MHz measured:

- mean −0.02 dB, standard deviation 0.30 dB; all points within ±0.65 dB;
- 70 MHz is the worst point (edge of the range extension), up to ~1.3 dB in
  the 9-point acceptance run;
- 100 MHz–6 GHz within ±0.3 dB in the 21-point run;
- a 10 dB gain-step check (same calibration, RX gain lowered by 10 dB) gave
  −9.54…−10.37 dB, confirming the dB scale.

Two independent uncalibrated sweeps repeated within 0.2 dB from 100 MHz to
6 GHz and within 0.9 dB at 70 MHz; the spread inside a point was
0.00–0.02 dB with no CLIP/LOW/UNSTABLE flags.

## 10. Limitations and risks

1. Only |S21| is available; phase and S11/S22 require a second receiver or an
   external bridge.
2. The 70–325 MHz and 3.8–6 GHz ranges are outside the AD9363 specification;
   accuracy and noise floor degrade there, and 6 GHz is the synthesizer limit.
3. The 4.04 GHz gain-table step makes sparse calibration produce spikes near
   4 GHz. Use the same or a denser grid for calibration and measurement.
4. A fixed RX gain does not cover a low-loss path at 6 GHz and a +30 dB
   amplifier at the same time; external attenuators/amplifiers are required.
5. The Pluto may be shared. Another client or a beacon invalidates the
   measurement; the busy check warns about it but cannot prevent changes
   between points.
6. The Touchstone file is magnitude-only and carries placeholder phase and
   other S-parameters.
7. The network link transfers about 1 MB per capture; slow links increase the
   sweep time.

## 11. Possible future work

- logarithmic frequency grid and sweep presets;
- noise-floor trace and display;
- automatic RX gain selection;
- packaging for PyPI and a Russian UI localization.


