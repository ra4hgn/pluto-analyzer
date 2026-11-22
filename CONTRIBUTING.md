# Contributing

Issues and pull requests are welcome.

## Development setup

```bash
git clone https://github.com/ra4hgn/pluto-analyzer.git
cd pluto-analyzer
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

The unit tests are hardware-independent; a PlutoSDR is only needed for manual
checks. `scripts/check_env.py` and `pluto-na-cli info` verify a connected
device.

## Guidelines

- Keep hardware access behind `pluto_analyzer.sdr.PlutoVNA`; the GUI must not
  talk to IIO directly.
- A PlutoSDR may be shared with other users: always restore the device state on
  close and keep the busy check intact.
- Only scalar |S21| is measured. Do not add code that implies phase, S11 or
  S12/S22 data exists.
- Add or update a unit test for behaviour changes and run the full test suite
  before opening a pull request.
- User guides live in `src/pluto_analyzer/help/` and are packaged with the
  application; keep them in sync with workflow changes and run
  `tests/test_help.py`.

By contributing you agree that your changes are released under the MIT
License.
