import smbus2 as smbus
import time
import csv
import psutil
import matplotlib.pyplot as plt
from collections import deque
from datetime import datetime
import os

# Configuration constants
_REG_CONFIG = 0x00
_REG_SHUNTVOLTAGE = 0x01
_REG_BUSVOLTAGE = 0x02
_REG_POWER = 0x03
_REG_CURRENT = 0x04
_REG_CALIBRATION = 0x05

# Default INA219 setup
I2C_BUS = 1
I2C_ADDRESS = 0x41
SAMPLE_INTERVAL = 2  # seconds

# Battery and UPS configuration
BATTERY_CAPACITY_WH = (
    100  # Example: 100 watt-hours, adjust this to your battery capacity
)
is_on_battery = False

# Thresholds for alerts
MAX_VOLTAGE = 15.0
MAX_CURRENT = 2.0
MAX_POWER = 10.0

# Initialize plot
plt.ion()
fig, (ax1, ax2, ax3) = plt.subplots(3, 1)
time_window = deque(maxlen=50)
voltage_data = deque(maxlen=50)
current_data = deque(maxlen=50)
power_data = deque(maxlen=50)


class INA219:
    def __init__(self, i2c_bus=I2C_BUS, addr=I2C_ADDRESS, shunt_resistance=0.1):
        self.bus = smbus.SMBus(i2c_bus)
        self.addr = addr
        self.shunt_resistance = shunt_resistance
        self._current_lsb = 0.1  # Current LSB = 100uA per bit
        self._power_lsb = 0.002  # Power LSB = 2mW per bit
        self.set_calibration_32V_2A()

    def write(self, address, data):
        temp = [data >> 8, data & 0xFF]
        self.bus.write_i2c_block_data(self.addr, address, temp)

    def read(self, address):
        data = self.bus.read_i2c_block_data(self.addr, address, 2)
        return (data[0] << 8) | data[1]

    def set_calibration_32V_2A(self):
        self._cal_value = int(0.04096 / (self._current_lsb * self.shunt_resistance))
        self.write(_REG_CALIBRATION, self._cal_value)
        self.config = 0x2000 | 0x1800 | 0x07  # 32V, 320mV gain, continuous mode
        self.write(_REG_CONFIG, self.config)

    def getShuntVoltage_mV(self):
        value = self.read(_REG_SHUNTVOLTAGE)
        return ((value - 65536) if value > 32767 else value) * 0.01

    def getBusVoltage_V(self):
        value = self.read(_REG_BUSVOLTAGE)
        return (value >> 3) * 0.004

    def getCurrent_mA(self):
        value = self.read(_REG_CURRENT)
        return ((value - 65536) if value > 32767 else value) * self._current_lsb

    def getPower_W(self):
        value = self.read(_REG_POWER)
        return ((value - 65536) if value > 32767 else value) * self._power_lsb

    def getPercent(self, bus_voltage):
        percent = ((bus_voltage - 9) / 3.6) * 100
        return min(max(percent, 0), 100)

    def check_thresholds(self, bus_voltage, current, power):
        if bus_voltage > MAX_VOLTAGE:
            print(
                f"Warning: Voltage {bus_voltage:.2f}V exceeds max limit of {MAX_VOLTAGE}V"
            )
        if current > MAX_CURRENT:
            print(
                f"Warning: Current {current:.2f}A exceeds max limit of {MAX_CURRENT}A"
            )
        if power > MAX_POWER:
            print(f"Warning: Power {power:.2f}W exceeds max limit of {MAX_POWER}W")

    def get_system_metrics(self):
        cpu_temp = (
            psutil.sensors_temperatures().get("cpu_thermal", [])[0].current
            if psutil.sensors_temperatures().get("cpu_thermal")
            else None
        )
        cpu_usage = psutil.cpu_percent()
        memory_usage = psutil.virtual_memory().percent
        return cpu_temp, cpu_usage, memory_usage

    def log_to_file(
        self,
        writer,
        timestamp,
        bus_voltage,
        current,
        power,
        percent,
        cpu_temp,
        cpu_usage,
        memory_usage,
    ):
        writer.writerow(
            [
                timestamp,
                bus_voltage,
                current,
                power,
                percent,
                cpu_temp,
                cpu_usage,
                memory_usage,
            ]
        )

    def estimate_remaining_time(self, current_power_draw):
        if is_on_battery and current_power_draw > 0:
            remaining_time_hours = BATTERY_CAPACITY_WH / current_power_draw
            return max(0, remaining_time_hours * 60)  # Convert hours to minutes
        return None


if __name__ == "__main__":
    ina219 = INA219()

    # Prepare log file
    log_file = "ina219_data_log.csv"
    file_exists = os.path.isfile(log_file)

    try:
        with open(log_file, mode="a", newline="") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(
                    [
                        "Timestamp",
                        "Load Voltage (V)",
                        "Current (A)",
                        "Power (W)",
                        "Percent (%)",
                        "CPU Temp (°C)",
                        "CPU Usage (%)",
                        "Memory Usage (%)",
                        "Remaining Time (min)",
                    ]
                )

            while True:
                # Read INA219 data
                bus_voltage = ina219.getBusVoltage_V()
                shunt_voltage = ina219.getShuntVoltage_mV() / 1000
                current = ina219.getCurrent_mA() / 1000
                power = ina219.getPower_W()
                percent = ina219.getPercent(bus_voltage)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Detect if running on battery
                is_on_battery = (
                    bus_voltage < 12.0
                )  # Example threshold for detecting UPS mode

                # Check thresholds
                ina219.check_thresholds(bus_voltage, current, power)

                # Estimate remaining battery time
                remaining_time = ina219.estimate_remaining_time(power)

                # System metrics
                cpu_temp, cpu_usage, memory_usage = ina219.get_system_metrics()

                # Output readings to console
                print(
                    f"{timestamp} - Load Voltage: {bus_voltage:.3f} V, Current: {current:.6f} A, "
                    f"Power: {power:.3f} W, Percent: {percent:.1f}%, "
                    f"CPU Temp: {cpu_temp}°C, CPU Usage: {cpu_usage}%, Memory Usage: {memory_usage}%, "
                    f"Remaining Time: {remaining_time:.2f} min"
                    if remaining_time
                    else "Calculating..."
                )

                # Write to log file
                ina219.log_to_file(
                    writer,
                    timestamp,
                    bus_voltage,
                    current,
                    power,
                    percent,
                    cpu_temp,
                    cpu_usage,
                    memory_usage,
                )
                file.flush()

                # Real-time plotting
                time_window.append(datetime.now())
                voltage_data.append(bus_voltage)
                current_data.append(current)
                power_data.append(power)

                ax1.clear()
                ax1.plot(time_window, voltage_data)
                ax1.set_title("Load Voltage (V)")
                ax2.clear()
                ax2.plot(time_window, current_data)
                ax2.set_title("Current (A)")
                ax3.clear()
                ax3.plot(time_window, power_data)
                ax3.set_title("Power (W)")
                plt.pause(0.05)

                # Sampling rate
                time.sleep(SAMPLE_INTERVAL)

    except IOError as e:
        print("I2C communication error:", e)
    except KeyboardInterrupt:
        print("\nScript interrupted by user.")
    finally:
        print("Script terminated.")
