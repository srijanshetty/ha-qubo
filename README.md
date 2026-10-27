# Qubo for Home Assistant

A custom integration for Home Assistant that allows you to control and monitor your Hero Electronix **Qubo** smart switches and plugs via their cloud API.

This integration connects directly to Qubo's cloud servers and establishes a persistent MQTT connection to give you instant switch control and real-time energy metrics.

## ✨ Features
* **UI Setup:** Fully configure the integration via the Home Assistant UI (Config Flow). No `configuration.yaml` editing required!
* **Instant Control:** Toggle your smart plug on and off instantly.
* **Energy Monitoring:** Live tracking of Power, Current, Voltage, and total energy consumption.
* **Stable Connection:** Proactive background token refreshing prevents the connection from silently dropping.
* **Auto-Discovery:** Automatically fetches your Device UUIDs and sets everything up.

## 🔌 Supported Devices
* Qubo Smart Plug 10A (Model: HSP02 / HSP02A) — tested, with energy monitoring
* Other Qubo smart switches and plugs — **experimental**

Every device on your account that exposes the `lcSwitchControl` service is added automatically. Multi-gang switches get one switch entity per gang. Energy sensors are only created for devices that report metering.

*(Only the 10A plug has been tested. If another device doesn't work, enable debug logging for `custom_components.qubo` and open an issue with the logged service names. Redact UUIDs and personal details first.)*

## 📦 Installation

### Method 1: HACS (Recommended)
This is the easiest way to install and keep the integration updated.

1. Open Home Assistant and navigate to **HACS**.
2. Go to **Integrations**, click the three dots in the top right corner, and select **Custom repositories**.
3. Paste the URL of this repository: [https://github.com/Anonymous-AAA/ha-qubo](https://github.com/Anonymous-AAA/ha-qubo)
4. Select **Integration** as the category and click **Add**.
5. Click on the newly added **Qubo** integration and click **Download**.
6. Restart Home Assistant.

### Method 2: Manual Installation
1. Download the latest release from this repository.
2. Extract the zip file.
3. Copy the entire `custom_components/qubo` directory into your Home Assistant's `config/custom_components/` directory.
4. Restart Home Assistant.

## ⚙️ Configuration

1. In the Home Assistant UI, go to **Settings** -> **Devices & Services**.
2. Click the **+ Add Integration** button in the bottom right corner.
3. Search for **Qubo**.
4. Enter your Qubo app **Email address** and **Password**.
5. Click Submit. The integration will automatically discover your switches and plugs and create a device for each!

## 📊 Entities Created
Each Qubo device on your account gets its own Device in Home Assistant.

| Entity Type | Created for | Description | Unit |
| :--- | :--- | :--- | :--- |
| `switch` | All devices (one per gang) | Toggles the channel On/Off | - |
| `sensor` | Metering plugs | Current Power Draw | `W` |
| `sensor` | Metering plugs | Electrical Current | `mA` |
| `sensor` | Metering plugs | Electrical Voltage | `V` |
| `sensor` | Metering plugs | Energy Consumption Today | `kWh` |
| `sensor` | Metering plugs | Time On Today | `m` |

*Metrics are pushed from the Qubo cloud every 5 seconds while the plug is active.*

## ⚠️ Disclaimer
This is a custom, unofficial integration. It is not affiliated with, endorsed by, or supported by Hero Electronix or Qubo. It relies on a reverse-engineered cloud API. Because of this, Qubo could change their API structure at any time, which may cause this integration to temporarily or permanently stop working. Use at your own risk!