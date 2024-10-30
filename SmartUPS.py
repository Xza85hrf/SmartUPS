import smbus2 as smbus
import time
import csv
import psutil
import matplotlib.pyplot as plt
from collections import deque
from datetime import datetime
import os
import argparse
from colorama import Fore, Style, init

# Initialize colorama for colored terminal output
init()

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

# Plot initialization and data buffers for optional plotting
plt.ion()
fig, (ax1, ax2, ax3) = plt.subplots(3, 1)
time_window = deque(maxlen=50)
voltage_data = deque(maxlen=50)
current_data = deque(maxlen=50)
power_data = deque(maxlen=50)

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




if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="SmartUPS Monitoring")
    parser.add_argument("--show-plot", action="store_true", help="Display real-time plot of metrics")
    parser.add_argument("--log-interval", type=int, default=SAMPLE_INTERVAL, help="Interval for logging data in seconds")
    args = parser.parse_args()

    ina219 = INA219()
    log_file = "ina219_data_log.csv"
    file_exists = os.path.isfile(log_file)

    # Setup CSV logging with headers if the file is new
    try:
        with open(log_file, mode="a", newline="") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(["Timestamp", "Load Voltage (V)", "Current (A)", "Power (W)", "Percent (%)",
                                 "CPU Temp (°C)", "CPU Usage (%)", "Memory Usage (%)", "Remaining Time (min)"])

            while True:
                # Retrieve data from INA219 and system metrics
                bus_voltage = ina219.getBusVoltage_V()
                current = ina219.getCurrent_mA() / 1000
                power = ina219.getPower_W()
                percent = ina219.getPercent(bus_voltage)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                is_on_battery = bus_voltage < 12.0  # Detect if running on battery
                cpu_temp = psutil.sensors_temperatures().get('cpu_thermal', [])[0].current if psutil.sensors_temperatures().get('cpu_thermal') else None
                cpu_usage = psutil.cpu_percent()
                memory_usage = psutil.virtual_memory().percent
                remaining_time = ina219.estimate_remaining_time(power)

                # Display readings in a clear format
                display_reading(timestamp, bus_voltage, current, power, percent, cpu_temp, cpu_usage, memory_usage, remaining_time)

                # Log data to CSV file
                writer.writerow([timestamp, bus_voltage, current, power, percent, cpu_temp, cpu_usage, memory_usage])
                file.flush()

                # Display plot if requested
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
        print("I2C communication error:", e)
    except KeyboardInterrupt:
        print("\nScript interrupted by user.")
    finally:
        print("Script terminated.")
