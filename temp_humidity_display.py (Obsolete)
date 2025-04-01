import time
import board
import adafruit_dht
from RPLCD.i2c import CharLCD

# Initialize DHT22 sensor on GPIO16
dht_sensor = adafruit_dht.DHT22(board.D16)

# Initialize 16x2 I2C LCD at address 0x27
lcd = CharLCD('PCF8574', 0X27, cols=16, rows=2)

def main():
    print("Starting temperature and humidity monitoring...")
    last_reading = 0  # Last successful reading time

    while True:
        current_time = time.time()

        if current_time - last_reading >= 2.0:
            try:
                temperature = dht_sensor.temperature
                humidity = dht_sensor.humidity

                if temperature is not None and humidity is not None:
                    print(f"Temperature: {temperature:.1f}C, Humidity: {humidity:.1f}%")

                    lcd.clear()
                    lcd.cursor_pos = (0, 0)
                    lcd.write_string(f"Temp: {temperature:.1f}C")
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string(f"Humidity: {humidity:.1f}%")

                    last_reading = current_time  # Update last successful reading time
                else:
                    print("Failed to get reading. Try again!")
                    
            except RuntimeError as e:
                print(f"Reading error: {e}")
                lcd.clear()
                lcd.cursor_pos = (0, 0)
                lcd.write_string("Sensor error")
                time.sleep(2)  # Give some delay to avoid spamming errors

            except Exception as e:
                print(f"Other error: {e}")
                lcd.clear()
                lcd.cursor_pos = (0, 0)
                lcd.write_string("Error")
                time.sleep(2)

        time.sleep(0.1)  # Small delay to reduce CPU usage

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Program terminated by user")
        lcd.clear()
        lcd.close()

