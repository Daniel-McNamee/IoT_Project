#!/usr/bin/env python3
# --- terrarium_control.py ---

import uuid
import os
import time
from datetime import datetime, time as time_obj # Use alias to avoid name clash with time module
import logging
import requests                 # For sending data to web API AND fetching settings
import json                     # For formatting data as JSON
import board                    # For GPIO pin definitions (DHT Sensor)
import adafruit_dht             # For DHT sensor
import signal                   # For graceful shutdown
import sys                      # For sys.exit
from RPLCD.i2c import CharLCD   # Import LCD library
from gpiozero import OutputDevice # For Relay control
from gpiozero.pins.native import NativeFactory # For non-default pin factory

# --- Configuration ---
# Path for storing the Unique Device ID
DEVICE_ID_FILE = '/home/DanDev/terrarium_device_id.txt'
WEBAPP_URL = 'http://192.168.1.42:5000'
READING_API_ENDPOINT = f'{WEBAPP_URL}/api/device/readings'
SETTINGS_API_ENDPOINT = f'{WEBAPP_URL}/api/device/settings'
SENSOR_READ_INTERVAL = 60 # Seconds between readings/updates
SETTINGS_FETCH_INTERVAL = 300 # Seconds (5 minutes)
MAX_HEATER_ON_DURATION = 15 * 60 # Seconds (15 minutes)
MIN_HEATER_OFF_COOLDOWN = 10 * 60  # Seconds (10 minutes)

# --- Sensor Config ---
DHT_SENSOR_PIN = board.D16 # GPIO Pin for DHT22

# --- Relay Config ---
RELAY_PIN = 18 # GPIO Pin for the relay IN1
# Active-HIGH (HIGH turns relay ON) - Set based on your relay module
RELAY_IS_ACTIVE_HIGH = False # Common (False means LOW activates)

# --- LCD Config ---
LCD_I2C_ADDRESS = 0x27 # Default address, can be checked using `sudo i2cdetect -y 1`
LCD_I2C_EXPANDER = 'PCF8574' # Common expander chip
LCD_COLS = 16
LCD_ROWS = 2
SHUTDOWN_MSG_DELAY = 2.0 # How long to show shutdown message

# --- Logging Setup ---
log_file = '/home/DanDev/terrarium_control.log' # Log file location
logging.basicConfig(
    level=logging.DEBUG, # Set to DEBUG for detailed logs, INFO for less verbosity
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
relay = None          # Holds the relay object
shutting_down = False # Flag for graceful exit
current_min_temp = None # Store fetched min temp
current_max_temp = None # Store fetched max temp
current_heating_off_start = None # Will store time_obj or None
current_heating_off_end = None   # Will store time_obj or None
last_settings_fetch_time = 0 # Track when settings were last fetched
relay_on_start_time = None       # Track time when relay was turned ON
force_heater_off_until = None    # Track time until forced OFF period ends

# --- Force Native Pin Factory ---
try:
    from gpiozero.pins.native import NativeFactory
    OutputDevice.pin_factory = NativeFactory()
    logging.info("Set gpiozero pin factory to Native.")
except ImportError:
     logging.info("NativeFactory not found, using default pin factory.")
except Exception as factory_ex:
    logging.warning(f"Could not set NativeFactory, using gpiozero default: {factory_ex}")

# --- Initialize Relay ---
def initialize_relay():
    """Initializes the relay GPIO pin."""
    global relay
    try:
        # Correct logic for initial_value based on active_high:
        # active_high=True: initial_value=False means LOW (OFF)
        # active_high=False: initial_value=True means HIGH (OFF)
        initial_pin_state_for_off = not RELAY_IS_ACTIVE_HIGH

        relay = OutputDevice(RELAY_PIN, active_high=RELAY_IS_ACTIVE_HIGH, initial_value=initial_pin_state_for_off)
        logging.info(f"Relay control initialized on GPIO {RELAY_PIN}. Active-High: {RELAY_IS_ACTIVE_HIGH}. Initial state requested: OFF (Pin state should be {'LOW' if RELAY_IS_ACTIVE_HIGH else 'HIGH'})")

        # Verification check
        time.sleep(0.2) # Short pause for state to settle
        try:
            # relay.value returns 1 if the pin is HIGH, 0 if LOW.
            actual_pin_value = relay.value # Read the pin state (0=LOW, 1=HIGH)
            expected_pin_value_for_off = 0 if RELAY_IS_ACTIVE_HIGH else 1 # Pin state expected for OFF

            logging.info(f"Pin {RELAY_PIN} state after init: {'HIGH (1)' if actual_pin_value == 1 else 'LOW (0)'}. Expected pin state for OFF: {'HIGH (1)' if expected_pin_value_for_off == 1 else 'LOW (0)'}.")

            # Check if actual pin state matches the expected state for OFF
            if actual_pin_value != expected_pin_value_for_off:
                 logging.warning(f"Relay pin state ({'HIGH' if actual_pin_value == 1 else 'LOW'}) does NOT match expected state for OFF ({'HIGH' if expected_pin_value_for_off == 1 else 'LOW'}) immediately after initialization!")
            # Check if the relay *thinks* it's off
            if relay.is_active:
                 logging.warning(f"Relay object reports is_active=True immediately after initialization requesting OFF state! Active-High={RELAY_IS_ACTIVE_HIGH}, Initial Value Sent={initial_pin_state_for_off}")

        except Exception as read_err:
             logging.warning(f"Could not read relay pin value after init: {read_err}")

        return True
    except Exception as e:
        logging.critical(f"CRITICAL: Failed to initialize relay on GPIO {RELAY_PIN}: {e}")
        logging.critical("Check GPIO pin number, permissions (run with sudo?), RPi.GPIO installed?, and potential conflicts.")
        relay = None
        return False

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
            if device_id and len(device_id) >= 36:
                 try:
                     uuid.UUID(device_id, version=4)
                     logging.info(f"Read/validated existing ID: {device_id}")
                     return device_id
                 except ValueError:
                     logging.critical(f"CRITICAL: Invalid UUID in file '{DEVICE_ID_FILE}'. Manual fix needed.")
                     return None
            else:
                logging.critical(f"CRITICAL: Invalid content in ID file '{DEVICE_ID_FILE}'. Manual fix needed.")
                return None
        else:
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
                return new_device_id
            except Exception as e:
                logging.error(f"ERROR saving new ID: {e}. Using unsaved ID for session.")
                return new_device_id
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
            # DHT sensors need warm-up time, initial failure is common
            logging.warning(f"Initial sensor read failed: {init_read_err}. Will retry in main loop.")
        except Exception as init_read_generic_err:
             logging.warning(f"Initial sensor read failed (generic): {init_read_generic_err}. Will retry.")
        return True
    except RuntimeError as init_err:
        logging.critical(f"CRITICAL: Failed to initialize DHT22 sensor (RuntimeError): {init_err}")
        logging.critical(f"Check wiring on pin {DHT_SENSOR_PIN}, power, and sensor functionality.")
        dht_device = None # Ensure it's None
        return False
    except NotImplementedError:
        logging.critical("CRITICAL: Failed to initialize DHT22 sensor. 'pulseio' not supported on this platform/kernel?")
        logging.critical("Try running without 'use_pulseio=True' or check kernel/library compatibility.")
        dht_device = None
        return False
    except Exception as e:
        logging.critical(f"CRITICAL: Unexpected error initializing DHT22 sensor: {e}", exc_info=True)
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
        time.sleep(1)
        lcd.clear()
        return True
    except Exception as e:
        logging.error(f"ERROR: Failed to initialize LCD: {e}. Script will continue without LCD.")
        lcd = None
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
    max_retries = 3
    retry_delay = 2.0 # Seconds between retries

    for attempt in range(max_retries):
        try:
            temperature_c = dht_device.temperature
            humidity = dht_device.humidity

            # Basic validation (DHT22 specific ranges)
            if humidity is not None and not (0 <= humidity <= 100):
                logging.warning(f"Discarding improbable humidity reading: {humidity:.1f}% (Attempt {attempt+1})")
                humidity = None # Invalidate humidity but keep temp if valid
            if temperature_c is not None and not (-40 <= temperature_c <= 85): # DHT22 range up to 85C
                logging.warning(f"Discarding improbable temperature reading: {temperature_c:.1f}°C (Attempt {attempt+1})")
                temperature_c = None # Invalidate temp

            # If BOTH are valid after checks, return them
            if temperature_c is not None and humidity is not None:
                logging.info(f"Successful Sensor Read: Temp={temperature_c:.1f}°C, Humidity={humidity:.1f}%")
                return temperature_c, humidity
            else:
                 # If one is None but the other is valid, loop might continue if retries remain
                 logging.debug(f"Sensor read attempt {attempt+1} resulted in partial/invalid data (T:{temperature_c}, H:{humidity}). Retrying if possible.")

        except RuntimeError as error:
            # These are common and typically temporary, log as warning
            logging.warning(f"DHT22 Runtime error reading sensor (Attempt {attempt+1}/{max_retries}): {error.args[0]}")
            # Keep temperature_c and humidity as None if error occurred
            temperature_c = None
            humidity = None
        except Exception as e:
            # Log other errors more severely
            logging.error(f"Unexpected error reading DHT22 sensor (Attempt {attempt+1}): {e}", exc_info=True)
            temperature_c = None
            humidity = None

        # Wait before retrying only if not the last attempt
        if attempt < max_retries - 1:
             time.sleep(retry_delay)

    # If loop finishes without success
    logging.error(f"Failed to get valid sensor reading after {max_retries} attempts.")
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
        logging.debug(f"Sending data to {READING_API_ENDPOINT}: {json.dumps(payload)}")
        response = requests.post(READING_API_ENDPOINT, headers=headers, data=json.dumps(payload), timeout=15)
        response.raise_for_status()
        logging.info(f"Data sent successfully. Server response status: {response.status_code}")
        return True

    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection Error sending data to {WEBAPP_URL}: {e}")
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout sending data to {WEBAPP_URL}: {e}")
    except requests.exceptions.HTTPError as e:
        error_detail = f"Status code: {e.response.status_code}"
        try: error_json = e.response.json(); error_detail += f" - {error_json.get('error', e.response.text)}"
        except json.JSONDecodeError: error_detail += f" - {e.response.text}"
        logging.error(f"HTTP Error sending data: {error_detail}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error during data sending request: {e}")
    except Exception as e:
        logging.error(f"Unexpected error sending data: {e}", exc_info=True)

    return False


# --- Settings Fetch Function ---
def fetch_device_settings(device_id):
    """Fetches settings (temp thresholds, off period) from the web server."""
    # Add new globals to modify
    global current_min_temp, current_max_temp, current_heating_off_start, current_heating_off_end

    if not device_id:
        logging.error("Cannot fetch settings: Device ID is missing.")
        return False

    url = f"{SETTINGS_API_ENDPOINT}/{device_id}"
    logging.debug(f"Attempting to fetch settings from: {url}")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        settings = response.json()
        logging.info(f"Successfully fetched settings: {settings}")

        # --- Temperature Threshold Handling ---
        new_min = settings.get('min_temp_threshold')
        new_max = settings.get('max_temp_threshold')
        # Basic validation: if both are set, min should be less than max
        if new_min is not None and new_max is not None:
             try:
                 if float(new_min) >= float(new_max):
                     logging.warning(f"Fetched settings are invalid (min >= max): Min={new_min}, Max={new_max}. Ignoring threshold update.")
                     # Don't return False yet, time settings might be valid
                     new_min = current_min_temp # Revert to current
                     new_max = current_max_temp
             except (ValueError, TypeError) as conv_err:
                  logging.warning(f"Fetched temp settings have non-numeric values: Min='{new_min}', Max='{new_max}'. Error: {conv_err}. Ignoring threshold update.")
                  new_min = current_min_temp # Revert
                  new_max = current_max_temp

        # Off Period Time Handling
        new_off_start_str = settings.get('heating_off_start_time') # Expects HH:MM:SS or None
        new_off_end_str = settings.get('heating_off_end_time')     # Expects HH:MM:SS or None
        new_off_start_time = None
        new_off_end_time = None

        try:
            if new_off_start_str:
                # Parse HH:MM:SS string into a time object
                new_off_start_time = datetime.strptime(new_off_start_str, '%H:%M:%S').time()
            if new_off_end_str:
                # Parse HH:MM:SS string into a time object
                new_off_end_time = datetime.strptime(new_off_end_str, '%H:%M:%S').time()

            # Add consistency check: If one is set, the other should be too
            if (new_off_start_time is not None and new_off_end_time is None) or \
               (new_off_start_time is None and new_off_end_time is not None):
                 logging.warning(f"Fetched inconsistent time settings: Start='{new_off_start_str}', End='{new_off_end_str}'. Both should be set or neither. Ignoring time update.")
                 # Revert to current stored times
                 new_off_start_time = current_heating_off_start
                 new_off_end_time = current_heating_off_end

        except ValueError as time_parse_error:
             logging.warning(f"Fetched settings contain invalid time format: Start='{new_off_start_str}', End='{new_off_end_str}'. Error: {time_parse_error}. Ignoring time update.")
             # Revert to current stored times
             new_off_start_time = current_heating_off_start
             new_off_end_time = current_heating_off_end

        # --- Check if any settings changed ---
        settings_changed = (
            new_min != current_min_temp or
            new_max != current_max_temp or
            new_off_start_time != current_heating_off_start or # Compare time objects
            new_off_end_time != current_heating_off_end       # Compare time objects
        )

        if settings_changed:
             # Update logging and assignment
             log_start_str = new_off_start_time.strftime('%H:%M:%S') if new_off_start_time else "None"
             log_end_str = new_off_end_time.strftime('%H:%M:%S') if new_off_end_time else "None"
             logging.info(f"Updating stored settings: Min={new_min}, Max={new_max}, OffStart={log_start_str}, OffEnd={log_end_str}")
             current_min_temp = new_min
             current_max_temp = new_max
             current_heating_off_start = new_off_start_time # Store time object
             current_heating_off_end = new_off_end_time     # Store time object
        else:
             logging.debug("Fetched settings are the same as current. No update needed.")

        return True # Indicate success

    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection Error fetching settings from {url}: {e}")
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout fetching settings from {url}: {e}")
    except requests.exceptions.HTTPError as e:
        error_detail = f"Status code: {e.response.status_code}"
        try: error_json = e.response.json(); error_detail += f" - {error_json.get('error', e.response.text)}"
        except json.JSONDecodeError: error_detail += f" - {e.response.text}"
        logging.error(f"HTTP Error fetching settings ({url}): {error_detail}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error during settings fetching request ({url}): {e}")
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding settings JSON response from {url}: {e}")
        logging.error(f"Received content: {response.text[:500]}") # Log raw response on decode error
    except Exception as e:
        logging.error(f"Unexpected error fetching settings ({url}): {e}", exc_info=True)

    # If any exception occurred, return False
    return False

# --- LCD Update Function ---
def update_lcd(temp_c, humid, relay_state_str=None, status_msg=None):
    """Updates the LCD display with sensor data, relay status, or a status message."""
    global lcd
    if not lcd: return

    try:
        lcd.clear()

        if status_msg:
            # Priority status message
            lcd.cursor_pos = (0, 0)
            lcd.write_string(status_msg[:LCD_COLS])
            if len(status_msg) > LCD_COLS :
                 lcd.cursor_pos = (1,0)
                 lcd.write_string(status_msg[LCD_COLS:(LCD_COLS*2)])

        elif temp_c is not None and humid is not None:
            # Normal display: Temp/Humid on Line 1
            try:
                temp_f = temp_c * (9 / 5) + 32
                line1 = f"T:{temp_c:>4.1f}C   H:{humid:>3.0f}%"[:LCD_COLS]
                # Alternative with F: line1 = f"{temp_c:>4.1f}C {temp_f:>4.1f}F"[:LCD_COLS]
            except Exception: # Catch potential float format errors
                line1 = "T: Err H: Err"[:LCD_COLS]
            lcd.cursor_pos = (0, 0)
            lcd.write_string(line1.ljust(LCD_COLS))

            # Relay Status on Line 2
            line2 = (relay_state_str if relay_state_str else "Relay: ---")[:LCD_COLS]
            lcd.cursor_pos = (1, 0)
            lcd.write_string(line2.ljust(LCD_COLS))

        else:
            # Fallback if no error but data is None
            lcd.cursor_pos = (0,0)
            lcd.write_string("Reading...".ljust(LCD_COLS))
            lcd.cursor_pos = (1,0)
            lcd.write_string(" ".ljust(LCD_COLS)) # Clear second line

    except Exception as e:
        logging.error(f"Failed to update LCD: {e}", exc_info=True)


# --- Cleanup Function ---
def cleanup(signum=None, frame=None):
    """Handles resource cleanup on exit."""
    global lcd, shutting_down, dht_device, relay
    if shutting_down: return
    shutting_down = True

    signal_name = signal.Signals(signum).name if signum else "Script Exit"
    print(f"\nReceived {signal_name}. Initiating graceful shutdown...")

    if lcd:
        try:
            print("Attempting to display shutdown message on LCD...")
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Shutting down...".ljust(LCD_COLS))
            time.sleep(SHUTDOWN_MSG_DELAY)
        except Exception as lcd_shutdown_msg_error:
            print(f"Warning: Could not display shutdown message on LCD: {lcd_shutdown_msg_error}")

    if relay:
        try:
            print("Turning relay OFF and closing GPIO...")
            relay.off()
            time.sleep(0.1)
            relay.close()
            print(f"Relay on GPIO {RELAY_PIN} turned OFF and closed.")
        except Exception as e:
            print(f"Warning: Error during relay cleanup: {e}")

    if dht_device:
        try:
            if hasattr(dht_device, 'exit') and callable(dht_device.exit):
                dht_device.exit()
                print("DHT sensor resource released.")
            else:
                print("DHT sensor library may not require explicit exit.")
        except Exception as e:
            print(f"Warning: Error exiting DHT sensor: {e}")

    if lcd:
        try:
            print("Clearing and closing LCD...")
            lcd.clear()
            lcd.backlight_enabled = False
            # Check if close method exists and is callable
            if hasattr(lcd, 'close') and callable(lcd.close):
                lcd.close(clear=True)
            else:
                 logging.warning("LCD object does not have a close method.")
            print("LCD Cleared and Closed (if supported).")
        except Exception as e:
           print(f"Error during final LCD cleanup: {e}")

    print("--- Terrarium Control Script Stopped ---")
    logging.info("--- Terrarium Control Script Stopped ---")
    sys.exit(0)

# --- Main Application Logic ---
if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    logging.info("--- Initializing Device ---")
    DEVICE_UNIQUE_ID = get_or_generate_persistent_device_id()
    sensor_ok = initialize_sensor()
    lcd_ok = initialize_lcd()
    relay_ok = initialize_relay()

    if not DEVICE_UNIQUE_ID or not sensor_ok or not relay_ok:
        critical_msg = "Init Error:"
        if not DEVICE_UNIQUE_ID: critical_msg += " No ID!"
        if not sensor_ok: critical_msg += " Sensor!"
        if not relay_ok: critical_msg += " Relay!"
        logging.critical(f"CRITICAL FAILURE: {critical_msg}. Exiting.")
        if lcd:
             try:
                 lcd.clear(); lcd.cursor_pos = (0, 0)
                 lcd.write_string(critical_msg[:LCD_COLS])
                 if len(critical_msg) > LCD_COLS: lcd.cursor_pos = (1, 0); lcd.write_string(critical_msg[LCD_COLS:(LCD_COLS*2)])
                 time.sleep(5)
             except Exception as lcd_init_err: logging.error(f"Failed to display init error on LCD: {lcd_init_err}")
        exit(1)

    print("\n" + "="*50); print("      TERRARIUM DEVICE ID INFORMATION"); print("="*50)
    print(f" This device's Unique ID is: {DEVICE_UNIQUE_ID}")
    print("\n -> Link this ID in the web app settings."); print("="*50 + "\n")
    logging.info(f"Using Device ID: {DEVICE_UNIQUE_ID}")

    logging.info(f"Web App URL: {WEBAPP_URL}")
    logging.info(f"Reading API endpoint: {READING_API_ENDPOINT}")
    logging.info(f"Settings API endpoint: {SETTINGS_API_ENDPOINT}/<ID>")
    logging.info(f"Sensor read interval: {SENSOR_READ_INTERVAL} seconds")
    logging.info(f"Settings fetch interval: {SETTINGS_FETCH_INTERVAL} seconds")
    logging.info(f"Relay Pin: {RELAY_PIN}, Active-High: {RELAY_IS_ACTIVE_HIGH}")


    # --- Main Loop ---
    logging.info("Starting main control loop...")
    while not shutting_down:
        loop_start_time = time.monotonic()
        error_message_for_lcd = None
        relay_status_str = "Relay: ---" # Default

        try: # *** START OF MAIN TRY BLOCK ***
            # --- Fetch Settings Periodically ---
            current_time = time.monotonic()
            if last_settings_fetch_time == 0 or (current_time - last_settings_fetch_time >= SETTINGS_FETCH_INTERVAL):
                logging.info("Time to fetch device settings...")
                if fetch_device_settings(DEVICE_UNIQUE_ID):
                    last_settings_fetch_time = current_time
                else:
                    logging.warning("Failed to fetch/update settings. Using previous values (if any).")
                    # If fetch fails, we keep using the existing global settings values.

            # --- Read Sensor ---
            temp, humid = read_sensor()

            # --- Send Data to Server ---
            if temp is not None and humid is not None:
                send_data_to_server(DEVICE_UNIQUE_ID, temp, humid)
            else:
                logging.warning("Sensor read failed or returned invalid data this cycle.")
                error_message_for_lcd = "Sensor Error" # Set error message for LCD

            # --- Heating Control Logic (Check Relay & Sensor First) ---
            if relay is None:
                logging.error("Cannot perform heating control: Relay not initialized.")
                relay_status_str = "Relay: ERROR"
                error_message_for_lcd = "Relay Error" # Prioritize relay error
            elif temp is None:
                logging.warning("Cannot perform heating control: Invalid temperature reading.")
                # Turn OFF for safety if sensor fails WHILE relay is ON
                if relay.is_active:
                    logging.warning("Turning relay OFF due to invalid temperature reading.")
                    try: relay.off()
                    except Exception as e: logging.error(f"Failed to turn OFF relay during sensor error: {e}")
                relay_status_str = "Relay: OFF (Safe)"
                if not error_message_for_lcd: error_message_for_lcd = "Sensor Error" # Show sensor error if no other error
            else:
                 # Relay OK, Sensor OK -> Proceed with Time and Temp Logic
                 temp_float = float(temp) # Temp is not None here
                 now_time = datetime.now().time() # Get current time as a time object

                 # Check for Scheduled Off Period
                 is_in_off_period = False
                 # Check only if both start and end times are valid time objects
                 if isinstance(current_heating_off_start, time_obj) and isinstance(current_heating_off_end, time_obj):
                     start_off = current_heating_off_start
                     end_off = current_heating_off_end

                     logging.debug(f"Checking time {now_time.strftime('%H:%M:%S')} against OFF period: {start_off.strftime('%H:%M:%S')} - {end_off.strftime('%H:%M:%S')}")

                     # Handle overnight period (e.g., start 22:00, end 06:00)
                     if start_off > end_off:
                         if now_time >= start_off or now_time < end_off:
                             is_in_off_period = True
                             logging.info(f"Current time is WITHIN overnight OFF period.")
                     # Handle same-day period (e.g., start 10:00, end 17:00)
                     else: # start_off <= end_off
                         if start_off <= now_time < end_off:
                             is_in_off_period = True
                             logging.info(f"Current time is WITHIN same-day OFF period.")

                 if is_in_off_period:
                     # --- Time is within the scheduled OFF period ---
                     if relay.is_active:
                         logging.info("Turning relay OFF due to scheduled off period.")
                         try: relay.off()
                         except Exception as e: logging.error(f"Failed to turn OFF relay during scheduled period: {e}")
                     else:
                          logging.debug("Relay already OFF during scheduled off period.")
                     relay_status_str = "Relay: OFF (Sched)"
                     # Skip the temperature logic below

                 else:
                     # --- Time is outside scheduled OFF period (or period not set) ---
                     if isinstance(current_heating_off_start, time_obj): # Log only if period is defined
                          logging.debug(f"Current time is OUTSIDE OFF period. Applying temperature logic.")

            # --- Heating Control Logic (Check Relay & Sensor First) ---
            if relay is None:
                logging.error("Cannot perform heating control: Relay not initialized.")
                relay_status_str = "Relay: ERROR"
                error_message_for_lcd = "Relay Error" # Prioritize relay error
            elif temp is None:
                logging.warning("Cannot perform heating control: Invalid temperature reading.")
                # Turn OFF for safety if sensor fails WHILE relay is ON
                if relay.is_active:
                    logging.warning("Turning relay OFF due to invalid temperature reading.")
                    try:
                        relay.off()
                        relay_on_start_time = None # Reset timer if forced off by sensor error
                    except Exception as e: logging.error(f"Failed to turn OFF relay during sensor error: {e}")
                relay_status_str = "Relay: OFF (Safe)"
                if not error_message_for_lcd: error_message_for_lcd = "Sensor Error" # Show sensor error if no other error
            else:
                 # Relay OK, Sensor OK -> Proceed with Time and Temp Logic
                 temp_float = float(temp) # Temp is not None here
                 now_time = datetime.now().time() # Get current time for scheduled off check
                 current_monotonic_time = time.monotonic() # Get current time for duration checks

                 # --- Check for forced OFF cooldown period ---
                 is_in_forced_cooldown = False
                 if force_heater_off_until is not None:
                     if current_monotonic_time < force_heater_off_until:
                         logging.info(f"Heater is in forced cooldown period (until {force_heater_off_until:.1f}). Keeping relay OFF.")
                         if relay.is_active:
                             try:
                                 relay.off()
                                 relay_on_start_time = None # Ensure timer is reset
                             except Exception as e: logging.error(f"Failed to turn OFF relay during forced cooldown: {e}")
                         relay_status_str = "Relay: OFF (Cool)"
                         is_in_forced_cooldown = True
                     else:
                         # Cooldown finished
                         logging.info(f"Forced heater cooldown period finished at {current_monotonic_time:.1f}.")
                         force_heater_off_until = None # Clear the cooldown flag

                 # --- Check for Scheduled Off Period (only if not in cooldown) ---
                 is_in_scheduled_off = False
                 if not is_in_forced_cooldown:
                     if isinstance(current_heating_off_start, time_obj) and isinstance(current_heating_off_end, time_obj):
                         start_off = current_heating_off_start
                         end_off = current_heating_off_end
                         logging.debug(f"Checking time {now_time.strftime('%H:%M:%S')} against OFF period: {start_off.strftime('%H:%M:%S')} - {end_off.strftime('%H:%M:%S')}")
                         # Handle overnight period
                         if start_off > end_off:
                             if now_time >= start_off or now_time < end_off: is_in_scheduled_off = True
                         # Handle same-day period
                         else:
                             if start_off <= now_time < end_off: is_in_scheduled_off = True

                         if is_in_scheduled_off:
                             logging.info(f"Current time is WITHIN scheduled OFF period.")
                             if relay.is_active:
                                 logging.info("Turning relay OFF due to scheduled off period.")
                                 try:
                                     relay.off()
                                     relay_on_start_time = None # Reset ON timer
                                 except Exception as e: logging.error(f"Failed to turn OFF relay during scheduled period: {e}")
                             else:
                                 logging.debug("Relay already OFF during scheduled off period.")
                             relay_status_str = "Relay: OFF (Sched)"
                             # Skip remaining logic for this cycle

                 # --- Apply Temperature & Max ON Time Logic (only if NOT in cooldown AND NOT in scheduled off) ---
                 if not is_in_forced_cooldown and not is_in_scheduled_off:
                     if isinstance(current_heating_off_start, time_obj): # Log only if scheduled period exists
                         logging.debug(f"Current time is OUTSIDE OFF period. Applying temperature/limit logic.")
                     else: # Log if no schedule exists
                         logging.debug(f"No scheduled OFF period set. Applying temperature/limit logic.")

                     # --- Check Max ON Time Limit (only if relay is currently ON) ---
                     max_on_time_exceeded = False
                     if relay.is_active and relay_on_start_time is not None:
                         time_on = current_monotonic_time - relay_on_start_time
                         logging.debug(f"Heater ON check: Currently ON for {time_on:.1f}s (Limit: {MAX_HEATER_ON_DURATION}s).")
                         if time_on > MAX_HEATER_ON_DURATION:
                             logging.warning(f"Heater has been ON for {time_on:.1f}s, exceeding MAX limit of {MAX_HEATER_ON_DURATION}s. Forcing OFF and starting cooldown.")
                             max_on_time_exceeded = True
                             force_heater_off_until = current_monotonic_time + MIN_HEATER_OFF_COOLDOWN # Schedule cooldown
                             logging.info(f"Forced cooldown active until monotonic time: {force_heater_off_until:.1f}")
                             try:
                                 relay.off()
                                 relay_on_start_time = None # Reset timer
                             except Exception as e: logging.error(f"Failed to turn OFF relay after max ON time: {e}")
                             relay_status_str = "Relay: OFF (Limit)" # Set status for this cycle
                             # Skip temperature logic below if limit exceeded

                     # --- Apply Temperature Logic (only if max ON time NOT exceeded) ---
                     if not max_on_time_exceeded:
                         min_temp_float = None
                         max_temp_float = None
                         threshold_error = False
                         try:
                             if current_min_temp is not None: min_temp_float = float(current_min_temp)
                             if current_max_temp is not None: max_temp_float = float(current_max_temp)
                         except (ValueError, TypeError) as conv_err:
                             logging.error(f"Invalid threshold values stored: Min='{current_min_temp}', Max='{current_max_temp}'. Error: {conv_err}. Cannot control heating.")
                             if relay.is_active:
                                 logging.warning("Turning relay OFF due to invalid stored thresholds.")
                                 try:
                                     relay.off()
                                     relay_on_start_time = None # Reset timer
                                 except Exception as e: logging.error(f"Failed to turn OFF relay during threshold error: {e}")
                             relay_status_str = "Relay: OFF (Cfg Err)"
                             error_message_for_lcd = "Settings Error"
                             threshold_error = True

                         # Proceed only if thresholds are valid
                         if not threshold_error:
                             relay_is_currently_on = relay.is_active # Re-check state as it might have changed due to errors above
                             logging.debug(f"Temp Control Check: Temp={temp_float:.1f}, Min={min_temp_float}, Max={max_temp_float}, Relay ON={relay_is_currently_on}")
                             action_taken = False

                             # Determine desired state based on temp and thresholds
                             desired_state_on = False
                             if relay_is_currently_on:
                                 # If ON, it should turn OFF if temp >= max (and max is set)
                                 if max_temp_float is not None and temp_float >= max_temp_float:
                                     desired_state_on = False
                                 else:
                                     desired_state_on = True # Stay ON if below max or max not set
                             else:
                                 # If OFF, it should turn ON if temp < min (and min is set)
                                 if min_temp_float is not None and temp_float < min_temp_float:
                                     desired_state_on = True
                                 else:
                                     desired_state_on = False # Stay OFF if above min or min not set

                             # Apply the change if needed
                             if desired_state_on and not relay_is_currently_on:
                                 logging.info(f"Temp ({temp_float:.1f}°C) < Min ({min_temp_float:.1f}°C). Turning relay ON.")
                                 try:
                                     relay.on()
                                     relay_on_start_time = current_monotonic_time # START TIMER 
                                     action_taken = True
                                 except Exception as e: logging.error(f"Failed to turn ON relay: {e}")
                             elif not desired_state_on and relay_is_currently_on:
                                 logging.info(f"Temp ({temp_float:.1f}°C) >= Max ({max_temp_float:.1f}°C) or Min not met. Turning relay OFF.")
                                 try:
                                     relay.off()
                                     relay_on_start_time = None # STOP TIMER 
                                     action_taken = True
                                 except Exception as e: logging.error(f"Failed to turn OFF relay: {e}")

                             # Set Status String based on the ACTUAL relay state after attempting changes
                             # Only set default OFF/ON if no specific status was set earlier
                             final_relay_state = relay.is_active
                             if relay_status_str == "Relay: ---": # Check if status is still default
                                 if final_relay_state:
                                     relay_status_str = "Relay: ON (Heat)"
                                 else:
                                     relay_status_str = "Relay: OFF"

                             if not action_taken and relay_status_str == "Relay: ---": # Log only if no action AND no specific status
                                  logging.debug(f"No temp state change needed. Relay maintained: {'ON' if final_relay_state else 'OFF'}")
                                  # Update status if still default
                                  relay_status_str = "Relay: ON (Heat)" if final_relay_state else "Relay: OFF"


            # --- Update LCD ---
            update_lcd(temp, humid, relay_status_str, error_message_for_lcd)

            # --- Calculate Sleep Time ---
            loop_end_time = time.monotonic()
            time_elapsed = loop_end_time - loop_start_time
            sleep_time = max(0, SENSOR_READ_INTERVAL - time_elapsed) # Ensure non-negative sleep

            logging.debug(f"Loop took {time_elapsed:.2f}s. Sleeping for {sleep_time:.2f} seconds...")

            # Use short sleeps to remain responsive to shutdown signals
            sleep_end_time = time.monotonic() + sleep_time
            while time.monotonic() < sleep_end_time and not shutting_down:
                time.sleep(0.1) # Check for shutdown signal every 100ms

        # --- except blocks ---
        except KeyboardInterrupt:
             logging.info("KeyboardInterrupt detected in main loop. Exiting loop.")
             if not shutting_down: cleanup(signal.SIGINT)
             break

        except Exception as e:
             logging.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
             # Turn off relay on unexpected errors for safety
             if relay and relay.is_active:
                 try:
                     logging.warning("Turning relay OFF due to unexpected error in main loop.")
                     relay.off()
                     relay_on_start_time = None # Reset timer on error too
                     relay_status_str = "Relay: OFF (ERR)"
                 except Exception as relay_err:
                     logging.error(f"Failed to turn off relay during error handling: {relay_err}")
                     relay_status_str = "Relay: ERR!"
             else:
                  # If relay wasn't active or doesn't exist, still indicate error
                  relay_status_str = "Relay: ERR!" if relay else "Relay: ERROR" # Adjust if relay is None

             # Display error on LCD
             update_lcd(None, None, relay_status_str, "System Error")

             # Prevent rapid looping on persistent errors
             logging.info("Sleeping for 15 seconds due to error...")
             time.sleep(15)


    logging.info("Main control loop finished.")
    # Cleanup is normally called by the signal handler, but call just in case loop exited non-standardly
    if not shutting_down:
         logging.warning("Loop exited without shutdown signal. Calling cleanup.")
         cleanup()

