# Calibration guide

The analyzer uses a **thru calibration**: a reference sweep through the
measurement fixture (cables and any fixed attenuator) is subtracted from the
DUT measurement. After calibration the reference path reads 0 dB.

For the general measurement workflow see the
[measurement guide](measurement.md).

## What the calibration does

```
S21_dB(f) = dBFS_dut(f) − dBFS_thru(f)
```

`dBFS_thru(f)` is the reference path response measured on the same grid,
interpolated on a log-frequency axis. It removes the frequency response of
the cables, connectors and fixed pads, as well as the Pluto's own gain
ripple.

Calibration does **not** remove noise, phase errors or the board's absolute
power uncertainty. It also cannot create S11 or phase data.

## Prepare the reference fixture

```
Pluto TX1 ── cable ── [fixed pad] ── cable ── Pluto RX1
```

- Remove the DUT. The reference path must contain exactly the cables, pads and
  adapters that will be present during the measurement.
- The default working setup is a 10 dB attenuator with `tx_gain=0` and
  `rx_gain=10`; the same settings must be used later.
- Keep IF, sample rate, tone level and gains identical. The tool warns if the
  calibration metadata and the measurement settings differ.
- Use the same or a denser frequency grid than the measurement. In particular,
  a sparse calibration cannot resolve the gain-table step around 4.04 GHz.

## CLI procedure

```bash
# 1. reference sweep, saved as cal/s21_thru.npz + cal/s21_thru.json
pluto-na-cli cal --points 501 --start 70 --stop 6000 \
    --dwell-ms 100 --out cal/s21_thru

# 2. normal measurement with the DUT in place
pluto-na-cli measure --cal cal/s21_thru --points 501 \
    --start 70 --stop 6000 --out results/run1
```

Notes:

- `cal` performs a full sweep and refuses to save if the reference contains
  CLIP, LOW or UNSTABLE points. Fix the fixture and repeat; use
  `--allow-bad-cal` only for diagnostics.
- The output path is a file stem. The command writes
  `cal/s21_thru.npz` (arrays `freqs_hz`, `thru_db`) and
  `cal/s21_thru.json` (version, UTC date, URI, hardware, settings, summary).
- `measure --cal` accepts the stem or the `.npz`/`.json` path and adds the
  `s21_db` column to `trace.csv`.
- Both commands use the shared-device busy check; exit code 4 means another
  client is reconfiguring the Pluto.

## GUI procedure

1. Connect to the Pluto.
2. Connect the reference fixture and press **Cal Thru**. Choose a file stem
   such as `cal/s21_thru`; the sweep runs with the current fields.
3. Put the DUT in place and run a normal sweep. Calibration is applied
   automatically to the displayed curve and exported data.
4. **Load Cal** selects an existing calibration; **Clear Cal** removes it
   from the session.
5. The status line shows the active calibration state.

The calibration captured in the GUI is applied to points measured after it
was loaded. If you load a calibration after a sweep, repeat the sweep.

## Validate the calibration

Measure the reference path again with the calibration enabled. The result
should be flat around 0 dB; the validation run gave mean −0.02 dB, standard
deviation 0.30 dB over 70–6000 MHz (100 MHz–6 GHz within about ±0.3 dB).

A useful absolute check is to insert a known 10 dB pad into the calibrated
path. The reading should change by about 10 dB. The acceptance run measured
−9.54…−10.37 dB when the RX gain was lowered by 10 dB, confirming the dB
scale.

## Offset

`--offset-db` (CLI) or **Offset (dB)** (GUI, if present) adds a constant to
the calibrated result. Use it when the reference path contains a known pad:

- calibrate with a 10 dB pad in the reference path;
- set `--offset-db -10`;
- then a DUT with 30 dB loss reads about −30 dB.

The offset is only a display correction; it does not change the measured
data or the calibration file.

## Warnings and recalibration

The tool warns when:

- measurement points fall outside the calibrated frequency span;
- RX/TX gain, tone level, IF or sample rate differ from the calibration;
- the reference sweep contained CLIP/LOW/UNSTABLE points;
- the calibration file is older than 30 days.

Recalibrate after changing:

- cables, connectors, adapters or the fixed attenuator;
- the board position or the RF path;
- the firmware or the Pluto device;
- any sweep setting that affects gain or the IF.

Treat an old calibration as indicative only. A clean thru reference is the
foundation of every calibrated measurement.

## Common mistakes

| Mistake | Effect | Fix |
|---|---|---|
| DUT left in the reference path | DUT response is subtracted | remove the DUT and recalibrate |
| Different RX gain or tone level | wrong dB scale / warnings | use identical settings |
| Sparse calibration grid | spikes near 4 GHz | calibrate on the same or denser grid |
| Reference sweep with CLIP/LOW | invalid correction | fix levels and repeat |
| Calibration loaded after the sweep | curve stays uncalibrated | load it first or rerun the sweep |
| Using `.s2p` for phase calculations | phase is a placeholder | use magnitude only |
