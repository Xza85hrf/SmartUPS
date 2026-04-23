import smbus2 as smbus
import time
import csv
import logging
import signal
import sys
import psutil
from collections import deque
from datetime import datetime
import os
import argparse
from colorama import Fore, Style, init

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

# Configurable Constants
I2C_BUS = 1
I2C_ADDRESS = 0x41
SAMPLE_INTERVAL = 2  # Data sampling interval in seconds
BATTERY_CAPACITY_WH = 100  # UPS battery capacity in watt-hours

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

    def __init__(self, i2c_bus=I2C_BUS, addr=I2C_ADDRESS, shunt_resistance=0.1):
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
        self._cal_value = int(0.04096 / (self._current_lsb * self.shunt_resistance))
        self.write(_REG_CALIBRATION, self._cal_value)
        self.config = (0x2000 | 0x1800 | 0x07)  # 32V, 320mV gain, continuous mode
        self.write(_REG_CONFIG, self.config)

    def getShuntVoltage_mV(self):
        """Returns the shunt voltage in mV."""
        value = self.read(_REG_SHUNTVOLTAGE)
        return ((value - 65536) if value > 32767 else value) * 0.01

    def getBusVoltage_V(self):
        """Returns the bus voltage in V."""
        value = self.read(_REG_BUSVOLTAGE)
        return (value >> 3) * 0.004

    def getCurrent_mA(self):
        """Returns the current in mA."""
        value = self.read(_REG_CURRENT)
        return ((value - 65536) if value > 32767 else value) * self._current_lsb

    def getPower_W(self):
        """Returns the power in W."""
        value = self.read(_REG_POWER)
        return ((value - 65536) if value > 32767 else value) * self._power_lsb

    def getPercent(self, bus_voltage):
        """Calculates battery percentage based on bus voltage."""
        percent = ((bus_voltage - 9) / 3.6) * 100
        return min(max(percent, 0), 100)

    def estimate_remaining_time(self, current_power_draw):
        """
        Estimates the remaining time based on current power draw.

        Parameters:
        current_power_draw (float): Current power draw in W.

        Returns:
        float: Estimated remaining time in minutes.
        """
        if current_power_draw > 0:
            remaining_time_hours = BATTERY_CAPACITY_WH / current_power_draw
            return min(10000, remaining_time_hours * 60)  # Limits time to avoid impractical values
        return None

def display_reading(timestamp, bus_voltage, current, power, percent, cpu_temp, cpu_usage, memory_usage, remaining_time):
    """
    Displays a formatted summary of key metrics with color highlights for easy readability.

    Parameters:
    timestamp (str): Timestamp for the reading.
    bus_voltage (float): Voltage reading in V.
    current (float): Current reading in A.
    power (float): Power reading in W.
    percent (float): Battery percentage.
    cpu_temp (float): CPU temperature in °C.
    cpu_usage (float): CPU usage percentage.
    memory_usage (float): Memory usage percentage.
    remaining_time (float): Estimated remaining time in minutes.
    """
    # Determine power consumption stage based on power level
    if power < 0.005:
        power_stage = "System Idle - Low Power Consumption"
    elif power < 0.5:
        power_stage = "Low Power Consumption"
    elif power < 2.0:
        power_stage = "Moderate Power Consumption"
    else:
        power_stage = "High Power Consumption"

    # Format remaining time for better readability
    if remaining_time and remaining_time > 1440:  # Cap at 24 hours
        remaining_time_display = "More than 24 hrs"
    elif remaining_time and remaining_time > 60:
        hours = int(remaining_time // 60)
        minutes = int(remaining_time % 60)
        remaining_time_display = f"{hours} hrs {minutes} min"
    else:
        remaining_time_display = f"{remaining_time:.2f} min" if remaining_time else "Calculating..."

    # Display output with power stage and remaining time
    print(f"{Fore.CYAN}[{timestamp}]{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Load Voltage:{Style.RESET_ALL}   {bus_voltage:.3f} V")
    print(f"{Fore.YELLOW}Current:{Style.RESET_ALL}        {current:.6f} A")
    print(f"{Fore.MAGENTA}Power:{Style.RESET_ALL}          {power:.3f} W")
    print(f"{Fore.LIGHTBLUE_EX}Battery:{Style.RESET_ALL}       {percent:.1f}%")
    print(f"{Fore.RED}CPU Temp:{Style.RESET_ALL}       {cpu_temp:.1f}°C")
    print(f"{Fore.CYAN}CPU Usage:{Style.RESET_ALL}      {cpu_usage:.1f}%")
    print(f"{Fore.LIGHTYELLOW_EX}Memory Usage:{Style.RESET_ALL} {memory_usage:.1f}%")
    print(f"{Fore.LIGHTGREEN_EX}Status:{Style.RESET_ALL}       {power_stage}")
    print(f"{Fore.LIGHTGREEN_EX}Remaining Time:{Style.RESET_ALL} {remaining_time_display}")




def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SmartUPS Monitoring")
    parser.add_argument("--show-plot", action="store_true", help="Display real-time plot of metrics")
    parser.add_argument("--log-interval", type=int, default=SAMPLE_INTERVAL,
                        help="Interval for logging data in seconds")
    parser.add_argument("--daemon", action="store_true",
                        help="Run in background mode: suppress terminal output and log to file")
    parser.add_argument("--tray", action="store_true",
                        help="Show a system-tray icon reflecting battery status (needs pystray + Pillow)")
    parser.add_argument("--shutdown-threshold", type=float, default=20.0,
                        help="Battery %% at or below which graceful shutdown is armed (default: 20)")
    parser.add_argument("--shutdown-consecutive", type=int, default=3,
                        help="Consecutive critical readings required to fire shutdown (default: 3)")
    parser.add_argument("--no-shutdown", action="store_true",
                        help="Disable the automatic graceful-shutdown guard (monitoring only)")
    parser.add_argument("--log-file", default=None,
                        help="Path for the rotating text log (default: ~/.local/share/smartups/smartups.log)")
    parser.add_argument("--csv-file", default="ina219_data_log.csv",
                        help="CSV data log path (default: ./ina219_data_log.csv)")
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
    fh = logging.FileHandler(log_file)
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
    _configure_logging(daemon=args.daemon, log_file=args.log_file)
    log.info("SmartUPS starting (daemon=%s, tray=%s, shutdown=%s)",
             args.daemon, args.tray, not args.no_shutdown)

    stop_requested = False

    def _on_signal(*_a):
        global stop_requested
        stop_requested = True
        log.info("Stop requested — finishing current sample then exiting.")

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    ina219 = INA219()

    guard = ShutdownGuard(
        threshold_pct=args.shutdown_threshold,
        consecutive_required=args.shutdown_consecutive,
        enabled=not args.no_shutdown,
    )
    log.info("ShutdownGuard: threshold=%.1f%% consecutive=%d enabled=%s",
             guard.threshold_pct, guard.consecutive_required, guard.enabled)

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

    file_exists = os.path.isfile(args.csv_file)

    try:
        with open(args.csv_file, mode="a", newline="") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(["Timestamp", "Load Voltage (V)", "Current (A)", "Power (W)", "Percent (%)",
                                 "Charging", "CPU Temp (°C)", "CPU Usage (%)", "Memory Usage (%)",
                                 "Remaining Time (min)"])

            while not stop_requested:
                bus_voltage = ina219.getBusVoltage_V()
                current = ina219.getCurrent_mA() / 1000
                power = ina219.getPower_W()
                percent = ina219.getPercent(bus_voltage)
                is_charging = detect_charging(current, bus_voltage)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cpu_temp = _get_cpu_temp()
                cpu_usage = psutil.cpu_percent()
                memory_usage = psutil.virtual_memory().percent
                remaining_time = ina219.estimate_remaining_time(power)

                if not args.daemon:
                    display_reading(timestamp, bus_voltage, current, power, percent,
                                    cpu_temp, cpu_usage, memory_usage, remaining_time)

                writer.writerow([timestamp, bus_voltage, current, power, percent, is_charging,
                                 cpu_temp, cpu_usage, memory_usage, remaining_time])
                file.flush()

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
        if tray is not None:
            tray.stop()
        log.info("Script terminated.")
