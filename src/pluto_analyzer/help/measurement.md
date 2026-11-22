# Measurement guide

This guide covers a normal S21 sweep: preparing the fixture, connecting the
Pluto, choosing sweep settings, reading the result and exporting it. For the
reference measurement see the [calibration guide](calibration.md).

## Before you start

- The analyzer measures **magnitude only**. Phase, S11, S12 and S22 are not
  available on a 1×1 AD9363 board.
- The signal path is `TX1 → DUT → RX1`. Protect the RX input: use a fixed
  attenuator when the DUT can pass a strong signal.
- The Pluto may be shared over the network. Another client or a beacon changes
  LO and gain for everyone. Before a sweep the tool watches the device for
  about one second and refuses to measure if another client reconfigures it
  (exit code 4). Free the device instead of using `--allow-busy` for real
  measurements.
- Calibrate the fixture before the first measurement and repeat the
  calibration after changing cables, the attenuator, the board position or the
  firmware. See the [calibration guide](calibration.md).

## 1. Connect

CLI:

```bash
pluto-na-cli info --uri ip:192.168.2.1
pluto-na-cli info --uri usb:
```

The `info` command prints the model, firmware, LO/gain ranges and validates
the requested span. In the GUI choose the URI from the list or type a custom
one, then press **Connect**. The status line shows the model and firmware.

Use `ip:...` when the board is shared over the network and `usb:` for a local,
exclusive connection. A USB bus/address changes after re-enumeration, so the
generic `usb:` URI is preferred.

## 2. Prepare the fixture

```
Pluto TX1 ── cable ── [pad] ── DUT ── cable ── Pluto RX1
```

- Keep the cables and adapters the same as during calibration.
- The default `rx_gain=10 dB` and `tx_gain=0 dB` match a
  `TX → 10 dB attenuator → RX` validation fixture. For a different fixture
  adjust the RX gain; TX attenuation has effectively one usable step, so keep
  `tx_gain=0`.
- A DUT with gain can overload the ADC: lower `--rx-gain` (or `--tone-db`)
  until the CLIP flag disappears.
- A DUT with high loss can drive the level below the noise floor: raise
  `--rx-gain` step by step (1 dB resolution) and recalibrate at the same gain.

## 3. Choose the sweep settings

| Option | CLI | Default | Notes |
|---|---|---|---|
| Start / stop | `--start`, `--stop` | 70 / 6000 MHz | MHz; single point allowed |
| Points | `--points` | 500 | linear grid |
| IF | `--if-khz` | 500 kHz | keep 500 kHz unless you have a reason |
| Sample rate | `--fs-mhz` | 8 MSPS | 2.083–61.44 MSPS |
| Samples | `--samples` | 262144 | per capture |
| Captures | `--captures` | 0 | 0 = 3 below 1 GHz, 2 above |
| Dwell | `--dwell-ms` | 300 ms | settling time per point |
| TX gain | `--tx-gain` | 0 dB | keep 0 |
| RX gain | `--rx-gain` | 10 dB | must match calibration |
| Tone level | `--tone-db` | −6 dBFS | lower it if the ADC clips |

A 500-point sweep takes several minutes because every point includes dwell,
settling and about 1 MB of samples transferred over the network. Use fewer
points while setting up the fixture.

## 4. Run the measurement

```bash
# one sweep into results/run1
pluto-na-cli measure --start 70 --stop 6000 --points 101 \
    --dwell-ms 100 --out results/run1

# calibrated sweep with report files
pluto-na-cli measure --cal cal/s21_thru --points 501 \
    --start 70 --stop 6000 --out results/run1 \
    --png results/run1/report.png \
    --pdf results/run1/report.pdf \
    --touchstone results/run1/trace.s2p
```

In the GUI set the same fields and press **Run** (or enable **Continuous**).
**Stop** finishes the current point and stops the sweep. Closing the window
stops the sweep and restores the Pluto settings.

## 5. Read the result

`trace.csv` contains one row per point:

- `freq_hz` — requested grid frequency;
- `lo_hz`, `tone_hz` — actual LO and signed detection frequency;
- `amplitude`, `dbfs` — raw lock-in amplitude and level;
- `s21_db` — calibrated value (only with `--cal`);
- `captures`, `spread_db` — number of captures and spread between them;
- `clip`, `low`, `unstable` — quality flags.

Flags:

| Flag | Meaning | Action |
|---|---|---|
| `CLIP` | ADC peak ≥ 0.8 full scale | lower RX gain or tone level |
| `LOW` | level below −90 dBFS | check cables/DUT, raise RX gain |
| `UNSTABLE` | capture spread > 0.5 dB | free the Pluto, increase dwell |

`meta.json` stores the URI, hardware passport, complete sweep configuration,
warnings and summary. `results/*.png/pdf` are static reports; `.s2p` is a
magnitude-only Touchstone file with placeholder phase and other S-parameters.

## 6. Typical problems

| Symptom | Likely cause | What to do |
|---|---|---|
| All points `LOW` | TX/RX path open, wrong URI, occupied device | check the fixture, run `info`, free the Pluto |
| Many `CLIP` points | RX gain or tone level too high | lower `--rx-gain` or `--tone-db` |
| Values jump between sweeps | another client or transmitter | stop the other process, repeat the sweep |
| Exit code 4 | another client reconfigures DDS/LO/rate/gain | free the device; `--allow-busy` only for diagnostics |
| Spikes near 4 GHz | sparse thru calibration at the gain-table step | calibrate on the same or a denser grid |
| Poor accuracy at 70 MHz or above 3.8 GHz | unofficial AD9363 range extension | use the result as indicative, not metrology-grade |

## 7. Accuracy and limits

- A calibrated thru reads 0 dB; 100 MHz–6 GHz is within about ±0.3 dB in the
  21-point validation run. The 70 MHz edge is the worst point.
- Only |S21| is measured. Do not use the Touchstone file for phase-sensitive
  calculations.
- AD9363 is specified for 325 MHz–3.8 GHz. The 70–325 MHz and 3.8–6 GHz ranges
  work only because of the range-extended firmware and have a worse noise
  floor and accuracy.
- The Pluto gain table changes around 4.04 GHz; calibrate with the same or a
  denser grid than the measurement.
