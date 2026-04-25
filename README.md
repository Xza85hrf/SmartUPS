# SmartUPS - UPS Monitoring for Raspberry Pi

**SmartUPS** is a real-time monitoring solution for the [Waveshare UPS Module 3S](https://www.waveshare.com/wiki/UPS_Module_3S) on a Raspberry Pi running Linux. It provides detailed insights into your UPS status, including battery voltage, power consumption, CPU metrics, and more.

## Features

- **Real-Time UPS Monitoring** — voltage, current, power, and battery percentage with smoothed display values
- **Charging Detection** — automatically detects AC power vs battery with color-coded status
- **Estimated Remaining Time** — dynamically calculated from actual power draw and battery level
- **Graceful Shutdown** — safely powers down the Pi when battery gets critically low
- **CSV Data Logging** — daily auto-rotated CSV files with configurable retention
- **System Tray Icon** — optional battery indicator for desktop environments
- **Optional Live Plot** — real-time graphs of voltage, current, and power
- **Auto-Start on Boot** — systemd service with auto-restart on failure

## Hardware Requirements

- **Raspberry Pi** (tested on Raspberry Pi 4 and 5)
- **Waveshare UPS Module 3S** (other INA219-based UPS modules work with `--i2c-address`)

---

## Quick Start (Automated)

The easiest way to get running — one command handles everything:

```bash
git clone https://github.com/Xza85hrf/SmartUPS.git
cd SmartUPS
chmod +x setup.sh
./setup.sh
```

The setup script will:
1. Check that Python 3 and I2C are available
2. Create a virtual environment and install dependencies
3. Optionally install a systemd service for auto-start on boot

That's it! Skip to [Usage](#usage) below.

---

## Manual Installation (Step by Step)

If you prefer to set things up yourself, follow these steps.

### Step 1: Enable I2C on your Raspberry Pi

```bash
sudo raspi-config
```

Navigate to **Interface Options** → **I2C** → **Enable**, then reboot:

```bash
sudo reboot
```

After reboot, verify I2C is working:

```bash
ls /dev/i2c-*          # should show /dev/i2c-1
sudo i2cdetect -y 1    # should show your UPS at address 0x41
```

### Step 2: Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git
```

### Step 3: Clone the repository

```bash
git clone https://github.com/Xza85hrf/SmartUPS.git
cd SmartUPS
```

### Step 4: Create a virtual environment

A virtual environment keeps SmartUPS dependencies isolated from your system Python:

```bash
python3 -m venv venv
```

### Step 5: Activate the virtual environment

```bash
source venv/bin/activate
```

> **Note:** You need to activate the venv every time you open a new terminal.
> Your prompt will show `(venv)` when it's active.

### Step 6: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 7: Run SmartUPS

```bash
python SmartUPS.py
```

To deactivate the virtual environment when you're done:

```bash
deactivate
```

---

## Usage

### Running SmartUPS

If you used `setup.sh`, the venv python is at `./venv/bin/python`:

```bash
# Basic monitoring
./venv/bin/python SmartUPS.py

# With live graphs (needs a display)
./venv/bin/python SmartUPS.py --show-plot

# Headless background mode
./venv/bin/python SmartUPS.py --daemon

# Different UPS board at address 0x40
./venv/bin/python SmartUPS.py --i2c-address 0x40

# Custom battery capacity (e.g. larger 18650 cells)
./venv/bin/python SmartUPS.py --battery-capacity 35
```

### All Command-Line Options

#### Hardware

| Flag | Default | Description |
|------|---------|-------------|
| `--i2c-bus` | `1` | I2C bus number |
| `--i2c-address` | `0x41` | INA219 I2C address (hex). Use `sudo i2cdetect -y 1` to find yours |
| `--battery-capacity` | `30` | Battery capacity in Wh. Default fits 3× 18650 cells |

#### Monitoring

| Flag | Default | Description |
|------|---------|-------------|
| `--log-interval` | `2` | Sampling interval in seconds |
| `--smoothing` | `5` | Rolling-average window for display. Raw values always go to CSV |
| `--show-plot` | off | Show real-time voltage/current/power graphs |
| `--daemon` | off | Background mode: no terminal output, log to file only |
| `--tray` | off | Show system-tray battery icon (needs `pystray` + `Pillow`) |

#### Shutdown Guard

| Flag | Default | Description |
|------|---------|-------------|
| `--shutdown-threshold` | `20` | Battery % at which shutdown arms |
| `--shutdown-consecutive` | `3` | Consecutive critical readings before shutdown fires |
| `--no-shutdown` | off | Disable automatic shutdown (monitoring only) |

#### Logging

| Flag | Default | Description |
|------|---------|-------------|
| `--log-file` | `~/.local/share/smartups/smartups.log` | Text log path. Rotates at 5 MB, keeps 3 backups |
| `--csv-file` | `./ina219_data_log.csv` | CSV base path. Date stamp is appended automatically |
| `--csv-keep-days` | `30` | Auto-delete CSV files older than N days (0 = keep all) |
| `--version` | — | Print version and exit |

---

## Example Output

```
=============================================
  [2026-04-25 01:59:14]
=============================================
  Voltage:        12.368 V
  Current:        0.0277 A (out)
  Power:          0.344 W  [Low]
  Battery:        93.6%  On Battery
  CPU Temp:       64.8°C
  CPU Usage:      51.4%
  Memory:         15.2%
  Remaining:      More than 24 hrs
```

The display shows **smoothed** (rolling average) values for stable readings. Raw sensor values are always written to the CSV for accurate analysis.

---

## CSV Data Logging

CSV files are automatically date-stamped and rotated daily:

```
ina219_data_log_2026-04-25.csv
ina219_data_log_2026-04-26.csv
...
```

Files older than `--csv-keep-days` (default 30) are automatically pruned on startup. Set `--csv-keep-days 0` to keep everything.

---

## Graceful Shutdown

When running on battery and the charge drops to the configured threshold for the configured number of consecutive samples, SmartUPS invokes `sudo shutdown -h now`. The consecutive-reading requirement means a single transient dip will never trigger a shutdown — the condition has to persist across `--shutdown-consecutive` samples (default 3, so 6 seconds at the default 2s interval).

For the shutdown command to succeed non-interactively:

- **Option A (recommended):** Use the systemd service — it has `CAP_SYS_BOOT` built in.
- **Option B:** Add a passwordless sudoers entry:
  ```bash
  echo "$(whoami) ALL=(ALL) NOPASSWD: /sbin/shutdown" | sudo tee /etc/sudoers.d/smartups
  ```

Use `--no-shutdown` for monitoring only (no automated shutdown).

---

## System Tray Icon

With `--tray`, SmartUPS shows a battery icon in your system tray:

| Color | Meaning |
|-------|---------|
| Blue | Charging / plugged in |
| Green | On battery, healthy (>50%) |
| Amber | On battery, low (21–50%) |
| Red | On battery, critical (≤20%) |

Install the optional dependencies:
```bash
./venv/bin/pip install pystray pillow
```

---

## Auto-Start on Boot (systemd)

### Option A: Use the setup script (recommended)

```bash
./setup.sh
```

This automatically generates a service file with your username and paths.

### Option B: Manual installation

```bash
# 1. Create log directory
sudo mkdir -p /var/log/smartups
sudo chown "$(whoami):$(whoami)" /var/log/smartups

# 2. Copy and edit the service file
sudo cp systemd/smartups.service /etc/systemd/system/
# Edit User= and paths to match your setup:
sudo nano /etc/systemd/system/smartups.service

# 3. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now smartups.service

# 4. Check status
systemctl status smartups.service
journalctl -u smartups.service -f
```

### Managing the service

```bash
sudo systemctl status smartups.service     # check status
sudo systemctl restart smartups.service    # restart after config change
sudo systemctl stop smartups.service       # stop temporarily
journalctl -u smartups.service -f          # watch live logs
```

### Desktop tray icon (KDE/GNOME)

For the tray icon in a desktop session, install the user service:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/smartups-tray.service ~/.config/systemd/user/
# Edit WorkingDirectory if your clone isn't at ~/SmartUPS
systemctl --user daemon-reload
systemctl --user enable --now smartups-tray.service
```

---

## Troubleshooting

### "No such file or directory: /dev/i2c-1"
I2C is not enabled. Run `sudo raspi-config` → Interface Options → I2C → Enable, then reboot.

### "Permission denied" on I2C
Add your user to the `i2c` group:
```bash
sudo usermod -aG i2c $(whoami)
```
Then log out and back in.

### Current reads as 0.0 A
Make sure you're using the correct I2C address. Check with:
```bash
sudo i2cdetect -y 1
```
Then pass the address: `python SmartUPS.py --i2c-address 0x41`

### "ModuleNotFoundError: No module named 'smbus2'"
You need to activate the virtual environment first:
```bash
source venv/bin/activate
python SmartUPS.py
```
Or run directly with the venv python:
```bash
./venv/bin/python SmartUPS.py
```

### Service fails to start
Check the logs:
```bash
journalctl -u smartups.service --no-pager -n 50
```

---

## Extending SmartUPS

SmartUPS works with any INA219-based UPS module. If you're using different hardware:

- **I2C Address** — use `--i2c-address` (e.g. `--i2c-address 0x40`)
- **I2C Bus** — use `--i2c-bus` (e.g. `--i2c-bus 0`)
- **Battery Capacity** — use `--battery-capacity` (in watt-hours)

## License

SmartUPS is licensed under the MIT License. See `LICENSE` for details.

## Contributions

Feel free to open issues or submit pull requests!
