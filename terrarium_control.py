# --- terrarium_control.py (Merged ID, API Sending, and LCD) ---

import uuid
import os
import time
import logging
import requests                 # For sending data to web API
import json                     # For formatting data as JSON
import board                    # For GPIO pin definitions
import adafruit_dht             # For DHT sensor
import signal                   # For graceful shutdown
import sys                      # For sys.exit
from RPLCD.i2c import CharLCD   # Import LCD library

# --- Configuration ---
# Path for storing the Unique Device ID
DEVICE_ID_FILE = '/home/DanDev/terrarium_device_id.txt'
WEBAPP_URL = 'http://192.168.1.42:5000' 
READING_API_ENDPOINT = f'{WEBAPP_URL}/api/device/readings'
SENSOR_READ_INTERVAL = 60 # Seconds between readings/updates

# --- Sensor Config ---
DHT_SENSOR_PIN = board.D16 # GPIO Pin for DHT22

# --- LCD Config ---
LCD_I2C_ADDRESS = 0x27       # Default address, can be checked using `sudo i2cdetect -y 1`
LCD_I2C_EXPANDER = 'PCF8574' # Common expander chip
LCD_COLS = 16
LCD_ROWS = 2
SHUTDOWN_MSG_DELAY = 2.0 # How long to show shutdown message

# --- Logging Setup ---
log_file = '/home/DanDev/terrarium_control.log' # Log file location
logging.basicConfig(
    level=logging.INFO, # Set to DEBUG for more detail if needed
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler() # Also output to console/journal
    ]
)
logging.info("Terrarium Control Script Starting Up")

# --- Global Variables ---
dht_device = None     # Holds the sensor object
lcd = None            # Holds the LCD object
shutting_down = False # Flag for graceful exit

# --- Device ID Management (Get or Generate) ---
def get_or_generate_persistent_device_id():
    """
    Gets the device's unique ID. Generates/stores UUIDv4 on first run.
    Returns ID string or None on critical error.
    """
    device_id = None
    try:
        if os.path.exists(DEVICE_ID_FILE):
            logging.debug(f"Device ID file found at {DEVICE_ID_FILE}")
            with open(DEVICE_ID_FILE, 'r') as f:
                device_id = f.read().strip()
            # Validate
            if device_id and len(device_id) >= 36:
                 try:
                     uuid.UUID(device_id, version=4) # Check if valid v4 UUID
                     logging.info(f"Read/validated existing ID: {device_id}")
                     return device_id
                 except ValueError:
                     logging.critical(f"CRITICAL: Invalid UUID in file '{DEVICE_ID_FILE}'. Manual fix needed.")
                     return None # Do not proceed
            else:
                logging.critical(f"CRITICAL: Invalid content in ID file '{DEVICE_ID_FILE}'. Manual fix needed.")
                return None # Do not proceed
        else:
            # Generate New ID
            logging.info(f"Device ID file not found. Generating new ID...")
            new_device_id = str(uuid.uuid4())
            logging.info(f"Generated new ID: {new_device_id}")
            try:
                with open(DEVICE_ID_FILE, 'w') as f:
                    f.write(new_device_id)
                logging.info(f"Saved new ID to '{DEVICE_ID_FILE}'")
                return new_device_id
            except IOError as e:
                logging.error(f"ERROR saving new ID file '{DEVICE_ID_FILE}': {e}. Using unsaved ID for session.")
                return new_device_id # Return unsaved ID
            except Exception as e:
                logging.error(f"ERROR saving new ID: {e}. Using unsaved ID for session.")
                return new_device_id # Return unsaved ID
    except Exception as e:
        logging.critical(f"CRITICAL ERROR during ID retrieval: {e}")
        return None

# --- Sensor Initialization ---
def initialize_sensor():
    """Initializes the DHT sensor object."""
    global dht_device
    try:
        dht_device = adafruit_dht.DHT22(DHT_SENSOR_PIN, use_pulseio=True)
        logging.info(f"DHT22 sensor successfully initialized on pin: {DHT_SENSOR_PIN}")
        # Attempt initial read check
        try:
            _ = dht_device.temperature # Read temperature
            _ = dht_device.humidity    # Read humidity
            logging.info("Initial sensor read check successful.")
        except RuntimeError as init_read_err:
            logging.warning(f"Initial sensor read failed: {init_read_err}. Will retry in main loop.")
        except Exception as init_read_generic_err:
             logging.warning(f"Initial sensor read failed (generic): {init_read_generic_err}. Will retry.")
        return True
    except RuntimeError as init_err:
        logging.critical(f"CRITICAL: Failed to initialize DHT22 sensor (RuntimeError): {init_err}")
        logging.critical(f"Check wiring on pin {DHT_SENSOR_PIN}, power, and sensor functionality.")
        dht_device = None # Ensure it's None
        return False
    except Exception as e:
        logging.critical(f"CRITICAL: Unexpected error initializing DHT22 sensor: {e}")
        dht_device = None # Ensure it's None
        return False

# --- LCD Initialization ---
def initialize_lcd():
    """Initializes the I2C LCD display."""
    global lcd
    try:
        lcd = CharLCD(i2c_expander=LCD_I2C_EXPANDER, address=LCD_I2C_ADDRESS, port=1,
                      cols=LCD_COLS, rows=LCD_ROWS, auto_linebreaks=False)
        lcd.clear()
        lcd.write_string("Initializing...")
        logging.info(f"LCD initialized at address {hex(LCD_I2C_ADDRESS)}")
        time.sleep(1) # Short delay
        lcd.clear()
        return True
    except Exception as e:
        logging.error(f"ERROR: Failed to initialize LCD: {e}. Script will continue without LCD.")
        lcd = None # Ensure lcd is None if init fails
        return False

# --- Sensor Reading Function ---
def read_sensor():
    """Reads Temp & Humidity from the sensor. Returns (temp_c, humidity) or (None, None)."""
    global dht_device
    if dht_device is None:
        logging.error("DHT sensor object not available for reading.")
        return None, None

    temperature_c = None
    humidity = None
    try:
        temperature_c = dht_device.temperature
        humidity = dht_device.humidity

        # Validate readings
        if humidity is not None and not (0 <= humidity <= 100):
            logging.warning(f"Discarding improbable humidity reading: {humidity:.1f}%")
            humidity = None
        if temperature_c is not None and not (-40 <= temperature_c <= 85): # Range for DHT22
            logging.warning(f"Discarding improbable temperature reading: {temperature_c:.1f}°C")
            temperature_c = None

        if temperature_c is not None and humidity is not None:
            logging.info(f"Successful Sensor Read: Temp={temperature_c:.1f}°C, Humidity={humidity:.1f}%")
            return temperature_c, humidity
        else:
            logging.warning("Sensor read attempt resulted in invalid/None data after validation.")
            return None, None

    except RuntimeError as error:
        logging.warning(f"DHT22 Runtime error reading sensor: {error.args[0]}")
        return None, None
    except Exception as e:
        # Catch other unexpected errors during reading
        logging.error(f"Unexpected error reading DHT22 sensor: {e}", exc_info=True)
        return None, None

# --- Data Sending Function ---
def send_data_to_server(device_id, temperature, humidity):
    """Sends sensor data via HTTP POST to the web application API."""
    if not device_id:
        logging.error("Cannot send data: Device ID is missing.")
        return False

    payload = {
        'device_unique_id': device_id,
        'temperature': temperature,
        'humidity': humidity
    }
    headers = {'Content-Type': 'application/json'}

    try:
        logging.debug(f"Sending data to {READING_API_ENDPOINT}: {payload}")
        response = requests.post(READING_API_ENDPOINT, headers=headers, data=json.dumps(payload), timeout=15)
        response.raise_for_status()
        logging.info(f"Data sent successfully. Server response status: {response.status_code}")
        return True

    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection Error sending data to {WEBAPP_URL}: {e}")
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout sending data to {WEBAPP_URL}: {e}")
    except requests.exceptions.HTTPError as e:
        # Log specific API errors returned by the server
        error_detail = e.response.text # Get raw text as fallback
        try:
            # Attempt to parse JSON response for structured error
            error_json = e.response.json()
            error_detail = error_json.get('error', error_detail)
        except json.JSONDecodeError:
            # Response was not JSON, stick with raw text
            pass
        logging.error(f"HTTP Error sending data: {e.response.status_code} - {error_detail}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending data request: {e}")

    return False # Return False if any exception occurred

# --- LCD Update Function ---
def update_lcd(temp_c, humid, status_msg=None):
    """Updates the LCD display with sensor data or a status message."""
    global lcd
    if not lcd: return # Do nothing if LCD failed to initialize

    try:
        lcd.clear() # Clear previous content
        if status_msg:
            # Display status message (e.g., "Sensor Error")
            lcd.cursor_pos = (0, 0)
            lcd.write_string(status_msg[:LCD_COLS]) # Show first line
            if len(status_msg) > LCD_COLS : # Show second line if needed
                 lcd.cursor_pos = (1,0)
                 lcd.write_string(status_msg[LCD_COLS:(LCD_COLS*2)])
        elif temp_c is not None and humid is not None:
            # Display Temp and Humidity
            temp_f = temp_c * (9 / 5) + 32
            # Format strings to fit columns
            line1 = f"Temp:{temp_c:>5.1f}C{temp_f:>4.0f}F"[:LCD_COLS]
            line2 = f"Hum:{humid:>6.1f}%"[:LCD_COLS]
            lcd.cursor_pos = (0, 0)
            lcd.write_string(line1.ljust(LCD_COLS)) # Pad with spaces
            lcd.cursor_pos = (1, 0)
            lcd.write_string(line2.ljust(LCD_COLS)) # Pad with spaces
        else:
            # Fallback message if no error but data is None
            lcd.cursor_pos = (0,0)
            lcd.write_string("Reading..."[:LCD_COLS])

    except Exception as e:
        logging.error(f"Failed to update LCD: {e}")

# --- Cleanup Function ---
def cleanup(signum=None, frame=None):
    """Handles resource cleanup on exit."""
    global lcd, shutting_down, dht_device
    if shutting_down: return # Prevent double execution
    shutting_down = True

    signal_name = signal.Signals(signum).name if signum else "Script Exit"
    print(f"\nReceived {signal_name}. Initiating graceful shutdown...") # Use print during shutdown

    # Display shutdown message on LCD
    if lcd:
        try:
            print("Attempting to display shutdown message on LCD...")
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Shutting down...".ljust(LCD_COLS))
            time.sleep(SHUTDOWN_MSG_DELAY)
        except Exception as lcd_shutdown_msg_error:
            print(f"Warning: Could not display shutdown message on LCD: {lcd_shutdown_msg_error}")

    # Clean up DHT sensor object
    if dht_device:
        try:
            dht_device.exit()
            print("DHT sensor resource released.")
        except Exception as e:
            print(f"Warning: Error exiting DHT sensor: {e}")

    # Clean up LCD
    if lcd:
        try:
            print("Clearing and closing LCD...")
            lcd.clear()
            lcd.backlight_enabled = False
            lcd.close(clear=True) # Ensure LCD is cleared on close
            print("LCD Cleared and Closed.")
        except Exception as e:
           print(f"Error during final LCD cleanup: {e}")

    print("--- Terrarium Control Script Stopped ---")
    sys.exit(0) # Exit cleanly

# --- Main Application Logic ---
if __name__ == "__main__":
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup) # Catches Ctrl+C

    logging.info("--- Initializing Device ---")
    DEVICE_UNIQUE_ID = get_or_generate_persistent_device_id()
    sensor_ok = initialize_sensor()
    lcd_ok = initialize_lcd() # Initialize LCD

    # Critical check: Need ID and working Sensor
    if not DEVICE_UNIQUE_ID or not sensor_ok:
        logging.critical("CRITICAL FAILURE: Could not obtain Device ID or initialize sensor. Exiting.")
        if lcd: update_lcd(None, None, "Init Error!")
        exit(1) # Exit

    # Print Device ID for record (useful on first run)
    print("\n" + "="*50)
    print("      TERRARIUM DEVICE ID INFORMATION")
    print("="*50)
    print(f" This device's Unique ID is: {DEVICE_UNIQUE_ID}")
    print("\n -> Enter this ID in the web app settings to link.")
    print("="*50 + "\n")
    logging.info(f"Using Device ID: {DEVICE_UNIQUE_ID}")

    logging.info(f"Will send data to: {READING_API_ENDPOINT}")
    logging.info(f"Sensor read interval: {SENSOR_READ_INTERVAL} seconds")

    # --- Main Loop ---
    while not shutting_down:
        loop_start_time = time.monotonic()
        error_message_for_lcd = None # Store potential error for LCD update

        try:
            temp, humid = read_sensor()

            if temp is not None and humid is not None:
                logging.info(f"Sensor values valid. Calling send_data_to_server...")
                send_data_to_server(DEVICE_UNIQUE_ID, temp, humid)
                # Update LCD with data
                update_lcd(temp, humid)
            else:
                logging.debug("Sensor read failed or returned invalid data this cycle.")
                error_message_for_lcd = "Sensor Error" # Set error message for LCD
                update_lcd(None, None, error_message_for_lcd) # Update LCD to show error

            # Calculate time elapsed and sleep for the remaining interval
            loop_end_time = time.monotonic()
            time_elapsed = loop_end_time - loop_start_time
            sleep_time = max(0, SENSOR_READ_INTERVAL - time_elapsed) # Ensure sleep is not negative

            logging.debug(f"Loop took {time_elapsed:.2f}s. Sleeping for {sleep_time:.2f} seconds...")

            # Use short sleeps to remain responsive to shutdown signals
            sleep_end_time = time.monotonic() + sleep_time
            while time.monotonic() < sleep_end_time and not shutting_down:
                time.sleep(0.1) # Check every 100ms

        except KeyboardInterrupt: # Should be caught by signal handler, but good safety net
             logging.info("KeyboardInterrupt in main loop. Initiating cleanup.")
             cleanup(signal.SIGINT) # Explicitly call cleanup
        except Exception as e:
             logging.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
             update_lcd(None, None, "System Error") # Show error on LCD
             # Prevent rapid looping on persistent errors
             time.sleep(10)

    # Cleanup is normally handled by the signal handler, but call JIC if loop exits otherwise
    if not shutting_down:
         cleanup()
