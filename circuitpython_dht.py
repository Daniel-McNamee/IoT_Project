import adafruit_dht
import board
import time

dht_device = adafruit_dht.DHT22(board.D16)  # Adjust if using a different port

while True:
    try:
        temperature = dht_device.temperature
        humidity = dht_device.humidity
        print(f"Temp: {temperature:.1f} C  Humidity: {humidity:.1f} %")
    except RuntimeError as e:
        print(f"Error: {e}")
    time.sleep(2)
