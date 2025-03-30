#!/usr/bin/env python3
# Combined Terrarium Temperature/Humidity Monitor and LCD Display Script (circuitpython_dht.py) & (temp_humidity_display.py)
# Virtual environment used temp_humidity_env
# Command to run program and virtual environment ~/temp_humidity_env/bin/python ~/terrarium_monitor.py

import time
import board
import adafruit_dht
from RPLCD.i2c import CharLCD
import sys

# --- Configuration ---
DHT_PIN = board.D16  # GPIO pin the DHT sensor is connected to 
LCD_I2C_ADDRESS = 0x27 # I2C address of your LCD backpack 
LCD_I2C_EXPANDER = 'PCF8574' # Common I2C chip for LCD backpacks
LCD_COLS = 16
LCD_ROWS = 2
READ_INTERVAL_SECONDS = 5.0 # How often to read the sensor and update display
RETRY_DELAY_SECONDS = 2.0 # How long to wait after a sensor read error before retrying

# --- Global Variables ---
dht_sensor = None
dht_sensor = None
lcd = None

# --- Initialization Functions ---
def initialize_sensor():
    """Initializes the DHT sensor."""
    global dht_sensor
    try:
        dht_sensor = adafruit_dht.DHT22(DHT_PIN)
        print(f"DHT22 sensor initialized on pin {DHT_PIN}")
        # Attempt a quick initial read to check connectivity
        try:
             dht_sensor.temperature
             dht_sensor.humidity
             print("Initial sensor read successful.")
        except RuntimeError as initial_read_error:
             print(f"Warning: Initial sensor read failed: {initial_read_error}. Will re>
        return True
    except Exception as e:
        print(f"FATAL: Failed to initialize DHT sensor: {e}")
        return False

def initialize_lcd():
    """Initializes the I2C LCD display."""
    global lcd
    try:
        lcd = CharLCD(LCD_I2C_EXPANDER, LCD_I2C_ADDRESS, cols=LCD_COLS, rows=LCD_ROWS, >
        lcd.clear()
        lcd.write_string("Initializing...")
        print(f"LCD initialized at address {hex(LCD_I2C_ADDRESS)}")
        time.sleep(2) # Give time to see init message
        lcd.clear()
        return True
    except Exception as e:
        print(f"ERROR: Failed to initialize LCD: {e}")
        print("Script can continue without LCD, but check I2C connections/address.")
        # Decide if you want to exit if LCD fails:
        # sys.exit("Exiting due to LCD initialization failure.")
        return False # Indicate failure but allow script to potentially continue

# --- Main Application Logic ---
def main():
    """Main loop for reading sensor and updating display."""
    global dht_sensor, lcd

    while True:
        temperature_c = None
        humidity = None
        error_message = None

        # --- Read Sensor ---
        try:
            if dht_sensor: # Only read if sensor was initialized
                 temperature_c = dht_sensor.temperature
                 humidity = dht_sensor.humidity
            else:
                error_message = "Sensor not init"
                print("Skipping read: Sensor not initialized.")


# --- Validate Readings ---
            if temperature_c is not None and humidity is not None:
                temperature_f = temperature_c * (9 / 5) + 32
                print(f"Temp: {temperature_c:.1f}C ({temperature_f:.1f}F)  Humidity: {h>

                # --- Update LCD ---
                if lcd: # Only update if LCD was initialized
                    lcd_line1 = f"Temp:{temperature_c:>5.1f}C{temperature_f:>4.0f}F"[:L>
                    lcd_line2 = f"Humid:{humidity:>6.1f}%"[:LCD_COLS]

                    try:
                        lcd.cursor_pos = (0, 0)
                        lcd.write_string(lcd_line1.ljust(LCD_COLS)) # Pad with spaces t>
                        lcd.cursor_pos = (1, 0)
                        lcd.write_string(lcd_line2.ljust(LCD_COLS)) # Pad with spaces t>
                    except Exception as lcd_write_error:
                        print(f"ERROR: Failed to write to LCD: {lcd_write_error}")
                        # Try to re-initialize LCD here? Or just report error
                        # Clear the LCD to indicate an issue
                        try:
  lcd.clear()
                            lcd.write_string("LCD Write Err")
                        except: pass # Ignore errors during error reporting


            else:
                # Handle case where readings are None (but no RuntimeError occurred)
                if dht_sensor and not error_message: # Avoid double reporting if sensor>
                    error_message = "Read Invalid"
                    print("Sensor read returned invalid data (None).")

        except RuntimeError as e:
            # DHT specific read error
            error_message = "Sensor Error"
            print(f"DHT Sensor Read Error: {e}")
            # No need for an extra sleep here, the main loop sleep handles delay

        except Exception as e:
            # Catch other unexpected errors during the loop
            error_message = "System Error"
 print(f"An unexpected error occurred in main loop: {e}")

        # --- Display Error State on LCD (if applicable) ---
        if error_message and lcd:
            try:
                lcd.clear()
                lcd.cursor_pos = (0, 0)
                lcd.write_string(error_message[:LCD_COLS])
                lcd.cursor_pos = (1,0)
                lcd.write_string("Retrying..."[:LCD_COLS])
            except Exception as lcd_error_write:
                 print(f"ERROR: Failed to write error status to LCD: {lcd_error_write}")


        # --- Wait for Next Cycle ---
        if error_message and "Sensor" in error_message : # If it was a sensor error, wa>
             time.sleep(RETRY_DELAY_SECONDS)
        else: # Otherwise wait the normal interval
             time.sleep(READ_INTERVAL_SECONDS)

# --- Script Entry Point ---
if __name__ == "__main__":
    print("--- Terrarium Monitor Starting ---")
    sensor_ok = initialize_sensor()
    lcd_ok = initialize_lcd()

    if not sensor_ok:
         if lcd:
             try:
                 lcd.clear()
                 lcd.write_string("SENSOR FAILED!")
                 lcd.cursor_pos=(1,0)
                 lcd.write_string("Check wiring/pin")
             except: pass # Ignore LCD errors during critical sensor fail msg
         print("Exiting due to critical sensor initialization failure.")
         sys.exit(1) # Exit sensor is absolutely required

    # Start the main monitoring loop
    try:
        main()
  except KeyboardInterrupt:
        print("\nKeyboardInterrupt detected. Exiting gracefully.")
    except Exception as e:
        print(f"\nUnhandled critical error: {e}")
    finally:
        # --- Cleanup ---
        print("Cleaning up resources...")
        if dht_sensor:
            try:
                pass
            except Exception as e:
                print(f"Error during DHT cleanup: {e}")

        if lcd:
            try:
                lcd.clear()
                lcd.backlight_enabled = False # Turn off backlight
                lcd.close(clear=True) # Close I2C connection, clear display
                print("LCD Cleared and Closed.")
            except Exception as e:
               print(f"Error during LCD cleanup: {e}")

        print("--- Terrarium Monitor Stopped ---")
