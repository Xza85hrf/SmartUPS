# Changelog

All notable changes to SmartUPS are documented in this file.

## [1.2.0] — 2026-04-25

### Fixed
- **INA219 calibration register was 1000x too low** — `Current_LSB` was in milliamps but the calibration formula treated it as amps, producing `Cal = 4` instead of `4096`. Current and power readings were effectively zero.
- **INA219 config register used 9-bit ADC resolution** — changed from `0x3807` to `0x399F` for 12-bit resolution on both bus and shunt ADC.
- **Calibration register written after config** — ensures calibration takes effect on the correct ADC settings.
- **Remaining time ignored battery percentage** — previously assumed 100% capacity regardless of actual charge level.
- **Battery capacity default was 100 Wh** — corrected to 30 Wh for the Waveshare UPS 3S (3× 18650).
- **CPU temp crash when sensor unavailable** — now displays "N/A" instead of raising an exception.

### Added
- **Rolling average smoothing** for display values (`--smoothing`, default 5). Raw sensor values are always written to CSV for accurate analysis.
- **Daily CSV rotation** — CSV files are automatically date-stamped (e.g. `ina219_data_log_2026-04-25.csv`).
- **CSV auto-pruning** — `--csv-keep-days` (default 30) deletes old CSV files on startup.
- **`--i2c-bus`** and **`--i2c-address`** CLI flags — no more editing source code to use a different INA219 board.
- **`--battery-capacity`** CLI flag — configure battery Wh from the command line.
- **`--version`** flag.
- **`setup.sh` installer script** — one-command setup: creates venv, installs deps, checks I2C, optionally installs systemd service with correct user/paths. Supports `--uninstall`.
- **Charging status in display** — shows "Charging" / "On Battery" with color coding.
- **Current direction indicator** — displays absolute current with `(in)` / `(out)`.
- **Battery percentage color** — green (>50%), yellow (>20%), red (≤20%).
- **Alert logging** — warnings when voltage, current, or power exceed safe thresholds.
- **INA219 overflow detection** — logs a warning when the bus voltage register OVF bit is set.
- **Calibration re-write before each read** — guards against I2C bus glitches silently clearing the calibration register.
- **Rotating text log** — `RotatingFileHandler` at 5 MB with 3 backups (was unbounded `FileHandler`).
- **Grouped `--help` output** — CLI options organized into hardware, monitoring, shutdown guard, and logging sections with examples.
- **Troubleshooting section in README** — common issues and fixes.

### Changed
- Display layout redesigned — separator bars, compact labels, cleaner formatting.
- Remaining time shows "AC Power" when charging instead of a meaningless estimate.
- systemd service `User=` updated; `setup.sh` auto-generates the unit with the correct user and paths.
- README fully rewritten with step-by-step venv setup for novice users, all CLI options in tables, and a quick-start section.
- Version bumped to 1.2.0.

## [1.1.0] — 2026-04-25

### Added
- Graceful shutdown guard with configurable threshold and consecutive-reading requirement.
- System-tray icon (pystray + Pillow) reflecting battery state.
- Daemon mode (`--daemon`) for headless operation.
- systemd service units for auto-start on boot.
- Charging detection based on current direction and bus voltage.
- Cross-platform test support for CPU temperature sensor patching.

## [1.0.0] — 2024-10-30

Initial release.

- INA219-based voltage, current, and power monitoring.
- Battery percentage estimation from bus voltage.
- CSV data logging.
- Optional real-time matplotlib plotting.
- Colored terminal output.
