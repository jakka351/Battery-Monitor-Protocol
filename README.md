# Battery-Monitor-Protocol BM2 Battery Monitor
Vehicle Battery Monitor purchased from Supercheap Auto comes bundled with an App that *tracks user location data and violates privacy*. Shame on Supercheap Auto
for selling this crap to their customers and profiting from users being flagrantly tracked and their data sent to chinese servers. That's the last time I'll ever buy 
a product from a company that supports that sort of behaviour and subscribes to those kinds of ethics. It really shows you how much they don't care about their customers. 
Absolutely appalling on their behalf.  
  
This project provides a Python script to read **battery voltage** and **charge level** from a BM2 Bluetooth battery monitor by reverse engineering its BLE protocol. 
It connects to the device over Bluetooth Low Energy (BLE), decrypts the broadcast data, and prints readable values in real time.

---
<img width="4080" height="2296" alt="image" src="https://github.com/user-attachments/assets/a0e771b9-e761-4ab7-ba41-6e9ac953282d" />



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
python monitor.py B0:D2:78:00:11:22 # the OUI for the device is B0:D2:78 - Texas Instruments, with the last 6 digits being a unique identifier.
```
<img width="585" height="663" alt="image" src="https://github.com/user-attachments/assets/4b4e92f3-1526-47e9-9e7c-21e96fd904f1" />


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

<img width="359" height="190" alt="image" src="https://github.com/user-attachments/assets/18e2aac3-f421-4eee-b534-17a0a3e4711b" />


The reverse engineering article linked above reports that the official BM2 mobile application may collect and transmit various types of device and network information (such as location-related data and network identifiers) to remote servers.

While these findings are based on analysis by a third party, users should be aware that:

* The official app may request broad permissions
* Network traffic behavior may not be transparent
* Data handling practices are not clearly documented

Users concerned about privacy should:

* Review app permissions carefully
A note on this - Here is a screenshot of the actual permissions this app asks for - keep in mind this is an app for monitoring battery voltage via bluetooth low energy, yet they are asking for (And have no need for) fine location, nearby devices, notifications, camera, microphone, music and audio, photos and videos permissions. If thats not indicative of what sinister motives the creators of this application/product have then I don't know what is.
      
<img width="25%" height="25%" alt="image" src="https://github.com/user-attachments/assets/d0aad781-df1f-4a50-9e7f-8623c176a510" />  
  
* Monitor network activity if possible
* Consider avoiding or isolating the official app, or as I did - uninstalling it and fucking it off, don't accept this bullshit from companies or products - buy an alternative and vote with your money.
      
<img width="25%" height="25%" alt="image" src="https://github.com/user-attachments/assets/425cc441-8d8d-4e1f-b6ef-e38358e14363" />  


This Python script provides a minimal alternative that reads data directly from the device without relying on the vendor’s cloud services. In the GUI folder you will find a full display that can be easily 
implemented on a raspberry pi and displayed on a headunit - don't accept spyware as the cost of functionality - now there is a better alternative and its essentially free. This is why I publish open source
stuff, because I get really fucked off by shit like this, so this is my reaction to said bullshit. Supercheap Auto I am fucking talking about you here as well, you're the supplier of the product and either
you are knowingly agreeing to this happening or you haven't done proper due diligence on the product that you are selling which indicates to me Supercheap Auto is either incompetent or malicous in its actions 
regarding its customers.
  
`/rant.`   
  

---

## Disclaimer

This project is provided for educational and interoperability purposes.
Use at your own risk. No affiliation with the device manufacturer.
(Fuck the device manufacturer, evil fucks. Thats why this repo was made.)
---
