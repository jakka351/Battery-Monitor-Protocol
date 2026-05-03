import asyncio
import sys
from bleak import BleakClient
from Crypto.Cipher import AES
import binascii

KEY = bytes([(b & 255) for b in [108,101,97,103,101,110,100,-1,-2,49,56,56,50,52,54,54]])
CHAR_UUID = "0000fff4-0000-1000-8000-00805f9b34fb"

def decrypt(data):
    cipher = AES.new(KEY, AES.MODE_CBC, b'\x00' * 16)
    return cipher.decrypt(data)

def parse(data):
    raw = binascii.hexlify(data).decode()
    voltage = int(raw[2:5], 16) / 100.0
    power = int(raw[6:8], 16)
    return voltage, power

def notification_handler(_, data):
    dec = decrypt(data)
    voltage, power = parse(dec)
    print(f"Voltage: {voltage:.2f} V | Charge: {power}%")

async def main(address):
    async with BleakClient(address) as client:
        print("Connected")
        await client.start_notify(CHAR_UUID, notification_handler)
        await asyncio.sleep(999999)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: script.py <MAC>")
    else:
        asyncio.run(main(sys.argv[1]))
