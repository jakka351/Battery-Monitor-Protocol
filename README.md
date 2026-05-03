# Battery-Monitor-Protocol BM2 Battery Monitor
Vehicle Battery Monitor purchased from Supercheap Auto comes bundled with an App that tracks user location data and violates privacy. 
This project provides a Python script to read **battery voltage** and **charge level** from a BM2 Bluetooth battery monitor by reverse engineering its BLE protocol. 
It connects to the device over Bluetooth Low Energy (BLE), decrypts the broadcast data, and prints readable values in real time.

---

## Source / Background

This implementation is based on the reverse engineering work described here:

👉 https://haxrob.net/bm2-reversing-the-ble-protocol-of-the-bm2-battery-monitor/

The article documents:

* BLE characteristics used by the device
* AES encryption parameters (key + IV)
* Data packet structure
* Example code for decrypting and parsing telemetry

---

## Features

* Connects to BM2 device over BLE
* Subscribes to notification characteristic
* Decrypts AES-CBC encrypted payloads
* Extracts:

  * Battery voltage (V)
  * Charge level (%)
* Prints values in real time

---

## Requirements

* Python 3.8+
* BLE-compatible system (Linux, macOS, or Windows)

### Install dependencies

```bash
pip install bleak pycryptodome
```

---

## Usage

1. Find your BM2 device MAC address (via BLE scan tools)
2. Run the script:

```bash
python script.py AA:BB:CC:DD:EE:FF
```

Example output:

```
Connected
Voltage: 12.68 V | Charge: 76%
```

---

## Technical Details

* **BLE Characteristic UUID:** `0000fff4-0000-1000-8000-00805f9b34fb`
* **Encryption:** AES-CBC
* **Key:** `leagendÿþ1882466`
* **IV:** 16 bytes of zero

Decoded payload format:

* Byte 0: Header (`0xF5`)
* Bytes 2–4: Voltage (divide by 100)
* Byte 6–7: Charge (%)

---

## ⚠️ Privacy Notice

The reverse engineering article linked above reports that the official BM2 mobile application may collect and transmit various types of device and network information (such as location-related data and network identifiers) to remote servers.

While these findings are based on analysis by a third party, users should be aware that:

* The official app may request broad permissions
* Network traffic behavior may not be transparent
* Data handling practices are not clearly documented

Users concerned about privacy should:

* Review app permissions carefully
* Monitor network activity if possible
* Consider avoiding or isolating the official app

This Python script provides a minimal alternative that reads data directly from the device without relying on the vendor’s cloud services.

---

## Disclaimer

This project is provided for educational and interoperability purposes.
Use at your own risk. No affiliation with the device manufacturer.
(Fuck the device manufacture, evil fucks. Thats why this repo was made.)
---
