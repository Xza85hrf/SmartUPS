import smbus2 as smbus
import time
import csv
import glob as glob_mod
import logging
import logging.handlers
import signal
import sys
import psutil
from collections import deque
from datetime import datetime, date
import os
import argparse
from colorama import Fore, Style, init

from smartups import __version__
from smartups.shutdown_guard import ShutdownGuard
from smartups.tray import TrayIcon

# matplotlib is only imported when --show-plot is requested so headless /
# daemon installs don't pay its import cost or need a display.
plt = None  # set lazily by _init_plot()

# Initialize colorama for colored terminal output
init()

log = logging.getLogger("smartups")

# INA219 Register Addresses
_REG_CONFIG = 0x00
_REG_SHUNTVOLTAGE = 0x01
_REG_BUSVOLTAGE = 0x02
_REG_POWER = 0x03
_REG_CURRENT = 0x04
_REG_CALIBRATION = 0x05

# Default Constants
DEFAULT_I2C_BUS = 1
DEFAULT_I2C_ADDRESS = 0x41
SAMPLE_INTERVAL = 2  # Data sampling interval in seconds
BATTERY_CAPACITY_WH = 30  # Waveshare UPS 3S: 3× 18650 ≈ 30 Wh
SMOOTHING_WINDOW = 5  # Number of samples for rolling average display

# Thresholds for Alerts
MAX_VOLTAGE = 15.0
MAX_CURRENT = 2.0
MAX_POWER = 10.0

# Data buffers for optional plotting (populated only when --show-plot is set).
time_window = deque(maxlen=50)
voltage_data = deque(maxlen=50)
current_data = deque(maxlen=50)
power_data = deque(maxlen=50)

# Plot figure/axes — created lazily by _init_plot() when --show-plot is set.
fig = None
ax1 = ax2 = ax3 = None


class SmoothedReadings:
    """Rolling average filter for display values. Raw values are always logged to CSV."""

    def __init__(self, window: int = SMOOTHING_WINDOW):
        self._window = max(1, window)
        self._voltage = deque(maxlen=self._window)
        self._current = deque(maxlen=self._window)
        self._power = deque(maxlen=self._window)

    def update(self, voltage: float, current: float, power: float):
        self._voltage.append(voltage)
        self._current.append(current)
        self._power.append(power)

    @property
    def voltage(self) -> float:
        return sum(self._voltage) / len(self._voltage) if self._voltage else 0.0

    @property
    def current(self) -> float:
        return sum(self._current) / len(self._current) if self._current else 0.0

    @property
    def power(self) -> float:
        return sum(self._power) / len(self._power) if self._power else 0.0


def _init_plot():
    """Import matplotlib and create plot axes. Idempotent."""
    global plt, fig, ax1, ax2, ax3
    if fig is not None:
        return
    import matplotlib.pyplot as _plt  # noqa: PLC0415

    plt = _plt
    plt.ion()
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1)


def detect_charging(current_a: float, bus_voltage: float) -> bool:
    """Return True if the UPS is charging (mains connected).

    The INA219 reports signed current: negative values mean current is flowing
    INTO the battery (charging), positive values mean OUT (discharging). A
    small band around zero is treated as idle-but-plugged-in when the bus
    voltage is high enough to indicate mains power.
    """
    if current_a < -0.005:
        return True  # clearly charging
    if current_a > 0.005:
        return False  # clearly discharging
    # near-zero current: use bus voltage as a proxy — full pack on charger
    # typically sits at ~12.5V+, unplugged packs sag under load
    return bus_voltage >= 12.4

class INA219:
    """Class to interface with the INA219 sensor for voltage, current, and power readings."""

    def __init__(self, i2c_bus=DEFAULT_I2C_BUS, addr=DEFAULT_I2C_ADDRESS, shunt_resistance=0.1):
        """
        Initializes the INA219 with default calibration for 32V and 2A range.

        Parameters:
        i2c_bus (int): The I2C bus number.
        addr (int): The I2C address of the INA219.
        shunt_resistance (float): Shunt resistor value in ohms.
        """
        self.bus = smbus.SMBus(i2c_bus)
        self.addr = addr
        self.shunt_resistance = shunt_resistance
        self._current_lsb = 0.1  # Current LSB = 100uA per bit
        self._power_lsb = 0.002  # Power LSB = 2mW per bit
        self.set_calibration_32V_2A()

    def write(self, address, data):
        """Writes a 16-bit value to a register on the INA219 sensor."""
        temp = [data >> 8, data & 0xFF]
        self.bus.write_i2c_block_data(self.addr, address, temp)

    def read(self, address):
        """Reads a 16-bit value from a register on the INA219 sensor."""
        data = self.bus.read_i2c_block_data(self.addr, address, 2)
        return (data[0] << 8) | data[1]

    def set_calibration_32V_2A(self):
        """Sets the INA219 to measure up to 32V and 2A."""
        # _current_lsb is in mA; the datasheet formula needs Amps, so divide by 1000
        current_lsb_a = self._current_lsb / 1000
        self._cal_value = int(0.04096 / (current_lsb_a * self.shunt_resistance))
        # 32V range, /8 gain (320mV), 12-bit bus ADC, 12-bit shunt ADC, continuous
        self.config = 0x399F
        self.write(_REG_CONFIG, self.config)
        self.write(_REG_CALIBRATION, self._cal_value)

    def getShuntVoltage_mV(self):
        """Returns the shunt voltage in mV."""
        value = self.read(_REG_SHUNTVOLTAGE)
        return ((value - 65536) if value > 32767 else value) * 0.01

    def getBusVoltage_V(self):
        """Returns the bus voltage in V."""
        value = self.read(_REG_BUSVOLTAGE)
        if value & 0x01:  # OVF — math overflow, reading unreliable
            log.warning("INA219 math overflow detected — voltage/power may be inaccurate")
        return (value >> 3) * 0.004

    def getCurrent_mA(self):
        """Returns the current in mA."""
        # Re-write calibration to guard against I2C glitches clearing the register
        self.write(_REG_CALIBRATION, self._cal_value)
        value = self.read(_REG_CURRENT)
        return ((value - 65536) if value > 32767 else value) * self._current_lsb

    def getPower_W(self):
        """Returns the power in W."""
        self.write(_REG_CALIBRATION, self._cal_value)
        value = self.read(_REG_POWER)
        return ((value - 65536) if value > 32767 else value) * self._power_lsb

    def getPercent(self, bus_voltage):
        """Calculates battery percentage based on bus voltage."""
        percent = ((bus_voltage - 9) / 3.6) * 100
        return min(max(percent, 0), 100)

    def estimate_remaining_time(self, current_power_draw, battery_percent=100.0):
        """
        Estimates the remaining time based on current power draw and battery level.

        Parameters:
        current_power_draw (float): Current power draw in W.
        battery_percent (float): Current battery charge percentage (0-100).

        Returns:
        float: Estimated remaining time in minutes.
        """
        if current_power_draw > 0:
            usable_wh = BATTERY_CAPACITY_WH * (battery_percent / 100.0)
            remaining_time_hours = usable_wh / current_power_draw
            return min(10000, remaining_time_hours * 60)
        return None

def display_reading(timestamp, bus_voltage, current, power, percent, cpu_temp,
                    cpu_usage, memory_usage, remaining_time, is_charging):
    """Displays a formatted summary of key metrics with color highlights."""
    # Determine power consumption stage based on power level
    abs_power = abs(power)
    if abs_power < 0.005:
        power_stage = "Idle"
    elif abs_power < 0.5:
        power_stage = "Low"
    elif abs_power < 2.0:
        power_stage = "Moderate"
    else:
        power_stage = "High"

    if is_charging:
        charge_label = f"{Fore.BLUE}Charging{Style.RESET_ALL}"
        remaining_time_display = "AC Power"
    elif remaining_time and remaining_time > 1440:
        charge_label = f"{Fore.YELLOW}On Battery{Style.RESET_ALL}"
        remaining_time_display = "More than 24 hrs"
    elif remaining_time and remaining_time > 60:
        charge_label = f"{Fore.YELLOW}On Battery{Style.RESET_ALL}"
        hours = int(remaining_time // 60)
        minutes = int(remaining_time % 60)
        remaining_time_display = f"{hours} hrs {minutes} min"
    else:
        charge_label = f"{Fore.RED}On Battery{Style.RESET_ALL}"
        remaining_time_display = f"{remaining_time:.1f} min" if remaining_time else "Calculating..."

    # Battery percent color
    if percent > 50:
        pct_color = Fore.GREEN
    elif percent > 20:
        pct_color = Fore.YELLOW
    else:
        pct_color = Fore.RED

    cpu_temp_str = f"{cpu_temp:.1f}°C" if cpu_temp is not None else "N/A"

    print(f"\n{Fore.CYAN}{'=' * 45}")
    print(f"  [{timestamp}]")
    print(f"{'=' * 45}{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}Voltage:{Style.RESET_ALL}        {bus_voltage:.3f} V")
    print(f"  {Fore.YELLOW}Current:{Style.RESET_ALL}        {abs(current):.4f} A {'(in)' if current < 0 else '(out)'}")
    print(f"  {Fore.MAGENTA}Power:{Style.RESET_ALL}          {abs_power:.3f} W  [{power_stage}]")
    print(f"  {pct_color}Battery:{Style.RESET_ALL}        {percent:.1f}%  {charge_label}")
    print(f"  {Fore.RED}CPU Temp:{Style.RESET_ALL}       {cpu_temp_str}")
    print(f"  {Fore.CYAN}CPU Usage:{Style.RESET_ALL}      {cpu_usage:.1f}%")
    print(f"  {Fore.LIGHTYELLOW_EX}Memory:{Style.RESET_ALL}         {memory_usage:.1f}%")
    print(f"  {Fore.LIGHTGREEN_EX}Remaining:{Style.RESET_ALL}      {remaining_time_display}")




def _check_alerts(bus_voltage, current, power):
    """Log warnings when readings exceed safe thresholds."""
    if bus_voltage > MAX_VOLTAGE:
        log.warning("Voltage %.2f V exceeds max threshold %.1f V", bus_voltage, MAX_VOLTAGE)
    if abs(current) > MAX_CURRENT:
        log.warning("Current %.3f A exceeds max threshold %.1f A", abs(current), MAX_CURRENT)
    if abs(power) > MAX_POWER:
        log.warning("Power %.2f W exceeds max threshold %.1f W", abs(power), MAX_POWER)


def _csv_path_for_today(base_path: str) -> str:
    """Return a date-stamped CSV path, e.g. data_2026-04-25.csv."""
    stem, ext = os.path.splitext(base_path)
    return f"{stem}_{date.today().isoformat()}{ext}"


def _prune_old_csvs(base_path: str, keep_days: int) -> None:
    """Delete date-stamped CSVs older than *keep_days*."""
    stem, ext = os.path.splitext(base_path)
    pattern = f"{stem}_*{ext}"
    today = date.today()
    for path in glob_mod.glob(pattern):
        fname = os.path.basename(path)
        # extract the date portion between last '_' and ext
        try:
            date_str = fname.rsplit("_", 1)[1].replace(ext, "")
            file_date = date.fromisoformat(date_str)
        except (IndexError, ValueError):
            continue
        if (today - file_date).days > keep_days:
            try:
                os.remove(path)
                log.info("Pruned old CSV: %s", path)
            except OSError as e:
                log.warning("Could not prune %s: %s", path, e)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SmartUPS — Waveshare UPS 3S monitor for Raspberry Pi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python3 SmartUPS.py                        # basic monitoring\n"
               "  python3 SmartUPS.py --show-plot             # with live graphs\n"
               "  python3 SmartUPS.py --daemon                # headless background mode\n"
               "  python3 SmartUPS.py --i2c-address 0x40      # different INA219 address\n",
    )
    parser.add_argument("--version", action="version", version=f"SmartUPS {__version__}")

    hw = parser.add_argument_group("hardware")
    hw.add_argument("--i2c-bus", type=int, default=DEFAULT_I2C_BUS,
                    help=f"I2C bus number (default: {DEFAULT_I2C_BUS})")
    hw.add_argument("--i2c-address", type=lambda x: int(x, 0), default=DEFAULT_I2C_ADDRESS,
                    help=f"INA219 I2C address in hex (default: {DEFAULT_I2C_ADDRESS:#04x})")
    hw.add_argument("--battery-capacity", type=float, default=BATTERY_CAPACITY_WH,
                    help="Battery capacity in watt-hours (default: 30 for 3×18650)")

    mon = parser.add_argument_group("monitoring")
    mon.add_argument("--log-interval", type=int, default=SAMPLE_INTERVAL,
                     help="Sampling interval in seconds (default: 2)")
    mon.add_argument("--smoothing", type=int, default=SMOOTHING_WINDOW,
                     help="Rolling-average window size for display (default: 5). Raw values always go to CSV.")
    mon.add_argument("--show-plot", action="store_true",
                     help="Display real-time plot of metrics")
    mon.add_argument("--daemon", action="store_true",
                     help="Run in background: suppress terminal output, log to file only")
    mon.add_argument("--tray", action="store_true",
                     help="Show a system-tray battery icon (needs pystray + Pillow)")

    sd = parser.add_argument_group("shutdown guard")
    sd.add_argument("--shutdown-threshold", type=float, default=20.0,
                    help="Battery %% at or below which shutdown arms (default: 20)")
    sd.add_argument("--shutdown-consecutive", type=int, default=3,
                    help="Consecutive critical readings before shutdown (default: 3)")
    sd.add_argument("--no-shutdown", action="store_true",
                    help="Disable automatic shutdown (monitoring only)")

    logs = parser.add_argument_group("logging")
    logs.add_argument("--log-file", default=None,
                      help="Text log path (default: ~/.local/share/smartups/smartups.log)")
    logs.add_argument("--csv-file", default="ina219_data_log.csv",
                      help="CSV base path (default: ./ina219_data_log.csv). "
                           "A date stamp is appended automatically, e.g. ina219_data_log_2026-04-25.csv")
    logs.add_argument("--csv-keep-days", type=int, default=30,
                      help="Delete CSV files older than N days (default: 30, 0=keep all)")
    return parser


def _configure_logging(daemon: bool, log_file: str | None) -> None:
    """Configure root logging. In daemon mode we only write to file, otherwise
    stream to stderr as well."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if log_file is None:
        log_file = os.path.expanduser("~/.local/share/smartups/smartups.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if not daemon:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def _get_cpu_temp() -> float | None:
    temps = psutil.sensors_temperatures() or {}
    entry = temps.get("cpu_thermal")
    if entry:
        return entry[0].current
    return None


if __name__ == '__main__':
    args = _build_arg_parser().parse_args()
    BATTERY_CAPACITY_WH = args.battery_capacity
    _configure_logging(daemon=args.daemon, log_file=args.log_file)
    log.info("SmartUPS %s starting (daemon=%s, tray=%s, shutdown=%s, "
             "i2c=%d:0x%02x, capacity=%.0fWh)",
             __version__, args.daemon, args.tray, not args.no_shutdown,
             args.i2c_bus, args.i2c_address, args.battery_capacity)

    stop_requested = False

    def _on_signal(*_a):
        global stop_requested
        stop_requested = True
        log.info("Stop requested — finishing current sample then exiting.")

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    ina219 = INA219(i2c_bus=args.i2c_bus, addr=args.i2c_address)

    guard = ShutdownGuard(
        threshold_pct=args.shutdown_threshold,
        consecutive_required=args.shutdown_consecutive,
        enabled=not args.no_shutdown,
    )
    log.info("ShutdownGuard: threshold=%.1f%% consecutive=%d enabled=%s",
             guard.threshold_pct, guard.consecutive_required, guard.enabled)

    smoother = SmoothedReadings(window=args.smoothing)

    tray: TrayIcon | None = None
    if args.tray:
        if TrayIcon.available():
            tray = TrayIcon(on_quit=_on_signal)
            tray.start(initial_percent=100.0, initial_charging=True)
            log.info("Tray icon started.")
        else:
            log.warning("--tray requested but pystray/Pillow not installed. "
                        "Install: pip install pystray pillow")

    if args.show_plot:
        _init_plot()

    # Prune old CSV files on startup
    if args.csv_keep_days > 0:
        _prune_old_csvs(args.csv_file, args.csv_keep_days)

    csv_headers = ["Timestamp", "Load Voltage (V)", "Current (A)", "Power (W)",
                   "Percent (%)", "Charging", "CPU Temp (°C)", "CPU Usage (%)",
                   "Memory Usage (%)", "Remaining Time (min)"]

    # Mutable state for daily CSV rotation
    csv_state = {"date": None, "file": None, "writer": None}

    try:
        while not stop_requested:
            # Rotate CSV at midnight
            today = date.today()
            if csv_state["date"] != today:
                if csv_state["file"] is not None:
                    csv_state["file"].close()
                csv_path = _csv_path_for_today(args.csv_file)
                file_is_new = not os.path.isfile(csv_path)
                csv_state["file"] = open(csv_path, mode="a", newline="")
                csv_state["writer"] = csv.writer(csv_state["file"])
                if file_is_new:
                    csv_state["writer"].writerow(csv_headers)
                csv_state["date"] = today
                log.info("CSV logging to: %s", csv_path)

            bus_voltage = ina219.getBusVoltage_V()
            current = ina219.getCurrent_mA() / 1000
            power = ina219.getPower_W()
            percent = ina219.getPercent(bus_voltage)
            is_charging = detect_charging(current, bus_voltage)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cpu_temp = _get_cpu_temp()
            cpu_usage = psutil.cpu_percent()
            memory_usage = psutil.virtual_memory().percent
            remaining_time = ina219.estimate_remaining_time(power, percent)

            # Update smoothing filter
            smoother.update(bus_voltage, current, power)

            _check_alerts(bus_voltage, current, power)

            if not args.daemon:
                # Display uses smoothed values for stability
                s_percent = ina219.getPercent(smoother.voltage)
                s_remaining = ina219.estimate_remaining_time(
                    abs(smoother.power), s_percent)
                display_reading(timestamp, smoother.voltage, smoother.current,
                                smoother.power, s_percent, cpu_temp, cpu_usage,
                                memory_usage, s_remaining, is_charging)

            # CSV always gets raw values
            csv_state["writer"].writerow([timestamp, bus_voltage, current, power,
                                          percent, is_charging, cpu_temp,
                                          cpu_usage, memory_usage, remaining_time])
            csv_state["file"].flush()

            if tray is not None:
                status = "charging" if is_charging else "on battery"
                tray.update(
                    percent=percent,
                    charging=is_charging,
                    tooltip=f"SmartUPS: {percent:.0f}% ({status}) — {bus_voltage:.2f} V",
                )

            if guard.observe(battery_pct=percent, is_charging=is_charging):
                log.critical("Graceful shutdown initiated. Exiting monitor loop.")
                break

            if args.show_plot:
                time_window.append(datetime.now())
                voltage_data.append(bus_voltage)
                current_data.append(current)
                power_data.append(power)
                ax1.clear()
                ax1.plot(time_window, voltage_data, label="Voltage (V)", color="blue")
                ax2.clear()
                ax2.plot(time_window, current_data, label="Current (A)", color="orange")
                ax3.clear()
                ax3.plot(time_window, power_data, label="Power (W)", color="green")
                ax1.set_title("Load Voltage (V)")
                ax2.set_title("Current (A)")
                ax3.set_title("Power (W)")
                plt.pause(0.05)

            time.sleep(args.log_interval)

    except IOError as e:
        log.error("I2C communication error: %s", e)
    except KeyboardInterrupt:
        log.info("Script interrupted by user.")
    finally:
        if csv_state["file"] is not None:
            csv_state["file"].close()
        if tray is not None:
            tray.stop()
        log.info("Script terminated.")
