# SmartUPS - UPS Monitoring for Raspberry Pi

**SmartUPS** is a real-time monitoring solution for the [Waveshare UPS Module 3S](https://www.waveshare.com/wiki/UPS_Module_3S) on a Raspberry Pi running Linux. It provides detailed insights into your UPS status, including battery voltage, power consumption, CPU metrics, and more. With customizable logging intervals and optional real-time plotting, SmartUPS is adaptable and easily extendable to meet various needs.

## Features

- **Real-Time UPS Monitoring**: Track voltage, current, power consumption, and battery percentage.
- **Power Consumption Stages**: Displays clear stages from "Idle" to "High Power Consumption" based on load.
- **Estimated Remaining Time**: Dynamically calculates UPS runtime based on current power consumption.
- **Customizable Logging**: Choose your logging interval and store data in CSV format.
- **Optional Plotting**: Real-time plots of voltage, current, and power usage.
- **Extensible Design**: Customize power stages or adapt for other hardware configurations.

## Hardware Requirements

- **Raspberry Pi** (tested on Raspberry Pi 4 and 5)
- **Waveshare UPS Module 3S** (required for current setup)

### Compatibility Note

This code is specifically designed for the **Waveshare UPS Module 3S**. For other UPS modules, you may need to adjust sensor register addresses or other configurations.

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/Xza85hrf/SmartUPS.git
cd SmartUPS
```

### Step 2: Install Required Packages

SmartUPS requires Python 3 and several Python libraries. Use the following command to install them with `pip3`:

```bash
pip3 install -r requirements.txt
```

**Note**: If `pip3` is not installed, you can install it with:

```bash
sudo apt update
sudo apt install python3-pip
```

### Step 3: Enable I2C on Raspberry Pi

Ensure I2C is enabled on your Raspberry Pi to communicate with the UPS Module.

```bash
sudo raspi-config
```

- Go to **Interfacing Options** -> **I2C** -> **Enable**

### Step 4: Run SmartUPS

You can start SmartUPS with the following command. Use optional flags for customized behavior:

```bash
python3 SmartUPS.py --show-plot --log-interval 5
```

## Usage

### Command-Line Arguments

- `--show-plot`: Enables real-time plotting of voltage, current, and power usage.
- `--log-interval`: Specifies the logging interval in seconds (default is 2 seconds).

### Example Command

```bash
python3 SmartUPS.py --show-plot --log-interval 5
```

### Displayed Information

- **Voltage, Current, and Power**: Core UPS metrics displayed in real-time.
- **Battery Status and Remaining Time**: Shows battery percentage and an estimated remaining time in hours and minutes.
- **Power Consumption Stages**:
  - **Idle**: Minimal power consumption
  - **Low Power**: Light load
  - **Moderate Power**: Standard load
  - **High Power**: Heavy load

## Example Output

```plaintext
[2024-10-30 23:58:19]
Load Voltage:   11.312 V
Current:        -0.000400 A
Power:          0.004 W
Battery:       64.2%
CPU Temp:       56.2°C
CPU Usage:      39.4%
Memory Usage:  36.2%
Status:        System Idle - Low Power Consumption
Remaining Time: More than 24 hrs
```

### CSV Logging

SmartUPS logs all metrics into a CSV file (`ina219_data_log.csv`) for further analysis.

## Extending SmartUPS

SmartUPS is configured to work with the **Waveshare UPS Module 3S**. If you are using a different UPS module, you may need to modify:

- **I2C Address**: Update the `I2C_ADDRESS` constant if your module has a different I2C address.
- **Sensor Registers**: Adjust INA219 register settings in `INA219.py` to match your module's specifications.
- **Power Calculation Constants**: Modify voltage and current thresholds for customized power stages.

## License

SmartUPS is licensed under the MIT License. See `LICENSE` for more details.

## Contributions

Feel free to open issues or submit pull requests to contribute to the project!
