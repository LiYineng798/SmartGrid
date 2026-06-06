# Smart Grid Edge Monitoring and Cloud Warning System

This repository contains the embedded firmware and web service code for a smart-grid distribution-equipment monitoring project. The system is designed as an edge acquisition node with local warning and cloud-side visualization. It collects environmental and equipment-state data, performs alarm judgement on the STM32 side, uploads data through Wi-Fi and MQTT, and displays real-time alarms on a web dashboard.

## Project Overview

The project targets the operating environment of distribution cabinets and similar smart-grid equipment. Instead of only showing raw sensor values, the final system works as a closed-loop warning chain:

1. Sensors collect temperature, humidity, smoke/gas level, flame state, human presence, and posture data.
2. The STM32 edge node reads the data through GPIO, ADC, UART, and I2C interfaces.
3. Firmware performs local threshold judgement, anti-jitter filtering, and alarm-state packaging.
4. OLED and buzzer provide local feedback.
5. ESP8266 connects to a mobile-phone hotspot and uploads MQTT data to a self-built cloud server.
6. The Flask service normalizes device data and provides a web dashboard for real-time monitoring and alarm history.

## Main Features

- Multi-sensor monitoring for distribution-equipment operating risk.
- STM32-side alarm judgement instead of relying only on the web page.
- Local OLED display and buzzer warning.
- MQTT communication through ESP8266-01S.
- Mobile-phone hotspot network access for classroom demonstration.
- Cloud-side web dashboard with real-time status and alarm history.
- Alarm-priority upload when warning state changes.
- Sliding-average filtering and threshold hysteresis to reduce false alarms.

## Hardware Modules

| Module | Function |
|---|---|
| STM32F103C8T6 development board | Main controller, sensor acquisition, alarm judgement, MQTT packet generation |
| DHT11 | Temperature and humidity monitoring |
| MPU6050 | Three-axis posture and tilt detection |
| MQ-2 | Smoke/gas-risk analog input |
| Flame sensor | Fire-risk digital signal |
| Human-presence sensor | Nearby activity detection |
| OLED display | Local real-time values and device state |
| Buzzer | Local alarm output |
| ESP8266-01S | Wi-Fi and MQTT communication |

## Repository Structure

```text
.
+-- flask_service.py             # Web dashboard service; rename from the original script if needed
+-- AgriTrace/
    +-- Core/                     # STM32CubeMX generated core files
    +-- Drivers/                  # STM32 HAL/CMSIS drivers
    +-- code/                     # User modules: sensors, OLED, MQTT, cloud service
    +-- MDK-ARM/                  # Keil project files
    +-- SafeGuard.ioc             # STM32CubeMX configuration
```

Key embedded files:

| File | Description |
|---|---|
| `Core/Src/main.c` | STM32 initialization and main loop |
| `code/user.c` | Periodic sensor tasks and alarm logic |
| `code/dht11.c` | DHT11 driver |
| `code/mpu6050.c` | MPU6050 driver |
| `code/oled.c` | OLED display driver |
| `code/mqtt.c` | MQTT packet construction |
| `code/cloud_service.c` | ESP8266 AT command state machine, Wi-Fi connection, upload logic |
| `flask_service.py` | Flask web service and dashboard backend |

## Embedded Firmware Setup

1. Open `AgriTrace/MDK-ARM` with Keil MDK.
2. Check the target MCU and debugger settings.
3. Update the Wi-Fi and MQTT configuration in:

```c
AgriTrace/code/cloud_service.h
```

Important parameters:

```c
#define WIFI_SSID       "your_hotspot_name"
#define WIFI_PASSWORD   "your_hotspot_password"
#define MQTT_BROKER_IP  "your_cloud_server_or_broker"
#define MQTT_BROKER_PORT 1883
```

During the demonstration, the board connects to a mobile-phone hotspot through ESP8266. Make sure the phone hotspot name and password match the firmware configuration.

4. Build the project in Keil.
5. Flash the firmware to the STM32 board through ST-Link.
6. Power the board and check the OLED Wi-Fi state.

## Cloud/Web Service Setup

The Python service provides the web dashboard and server-side data processing.

Install dependencies:

```bash
pip install flask
```

Run the service:

```bash
python flask_service.py
```

Default settings can be changed through environment variables inside the script. In deployment, the web service is expected to run on a cloud server and expose the dashboard through a public domain.

Project demo dashboard:

```text
https://smartgrid.np5.top/
```

## Alarm Logic

The firmware does not upload only raw values. It performs edge-side warning decisions first:

- Temperature or humidity outside the selected range triggers environmental warning.
- Smoke/gas ADC value above threshold triggers smoke-risk warning.
- Flame and human-presence inputs require consecutive active samples before confirmation.
- Pitch or roll beyond the posture threshold triggers device-error warning.
- When alarm state changes, the firmware forces several immediate uploads to improve cloud capture.

To reduce false alarms, the system uses simple filtering and hysteresis rules. Short sensor spikes are not treated as valid alarms unless they persist across repeated samples.

## Demonstration Tests

The final prototype was tested with the following cases:

- Homepage normal monitoring state.
- Tilt/device-error detection with MPU6050.
- Human-presence detection.
- Flame alarm detection.
- Temperature and humidity alarm.
- Smoke/gas-risk alarm.
- Historical alarm log display.

Each physical test was compared with the corresponding web-side alarm state to verify the edge-to-cloud warning path.

## Notes

- The firmware contains project-specific MQTT topics and credentials. Replace them before using your own server.
- The ESP8266 communication depends on stable UART wiring and power supply.
- If Wi-Fi connection fails, first check hotspot name, password, 2.4 GHz compatibility, and ESP8266 power stability.
- This project is intended for course demonstration and prototype validation, not direct industrial deployment.
