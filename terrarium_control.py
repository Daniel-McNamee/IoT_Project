#!/usr/bin/env python3
# --- terrarium_control.py ---

import uuid
import os
import time
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

# --- Sensor Config ---
DHT_SENSOR_PIN = board.D16 # GPIO Pin for DHT22

# --- Relay Config ---
RELAY_PIN = 18 # GPIO Pin for the relay IN1
# Active-HIGH (HIGH turns relay ON)
RELAY_IS_ACTIVE_HIGH = False

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
last_settings_fetch_time = 0 # Track when settings were last fetched

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
        # Determine initial pin state for OFF based on active high/low
        # Active-HIGH needs LOW pin for OFF.
        initial_pin_state_for_off = not RELAY_IS_ACTIVE_HIGH # False (LOW) for Active-High

        relay = OutputDevice(RELAY_PIN, active_high=RELAY_IS_ACTIVE_HIGH, initial_value=initial_pin_state_for_off)
        logging.info(f"Relay control initialized on GPIO {RELAY_PIN}. Active-High: {RELAY_IS_ACTIVE_HIGH}. Initial state requested: OFF (Pin {'HIGH' if initial_pin_state_for_off else 'LOW'})")

        # Verification check
        time.sleep(0.2) # Short pause for state to settle
        try:
            actual_pin_value = relay.value # Read the pin state (0=LOW, 1=HIGH)
            expected_pin_value = 1 if initial_pin_state_for_off else 0
            logging.info(f"Pin {RELAY_PIN} state after init: {'HIGH (1)' if actual_pin_value == 1 else 'LOW (0)'}. Expected for OFF state: {'HIGH (1)' if expected_pin_value == 1 else 'LOW (0)'}.")
            if actual_pin_value != expected_pin_value:
                 logging.warning(f"Relay pin state ({actual_pin_value}) does not match expected state for OFF ({expected_pin_value}) immediately after initialization!")
        except Exception as read_err:
             logging.warning(f"Could not read relay pin value after init: {read_err}")

        return True
    except Exception as e:
        logging.critical(f"CRITICAL: Failed to initialize relay on GPIO {RELAY_PIN}: {e}")
        logging.critical("Check GPIO pin number, permissions (run with sudo?), and potential conflicts.")
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

        # Basic validation (DHT22 specific ranges)
        if humidity is not None and not (0 <= humidity <= 100):
            logging.warning(f"Discarding improbable humidity reading: {humidity:.1f}%")
            humidity = None
        if temperature_c is not None and not (-40 <= temperature_c <= 80): # DHT22 typical range
            logging.warning(f"Discarding improbable temperature reading: {temperature_c:.1f}°C")
            temperature_c = None

        if temperature_c is not None and humidity is not None:
            logging.info(f"Successful Sensor Read: Temp={temperature_c:.1f}°C, Humidity={humidity:.1f}%")
            return temperature_c, humidity
        else:
            logging.warning("Sensor read attempt resulted in invalid/None data after validation.")
            return None, None

    except RuntimeError as error:
        # Log DHT Sensor errors as warning
        logging.warning(f"DHT22 Runtime error reading sensor: {error.args[0]}")
        return None, None
    except Exception as e:
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
        logging.debug(f"Sending data to {READING_API_ENDPOINT}: {json.dumps(payload)}") # Log actual payload
        response = requests.post(READING_API_ENDPOINT, headers=headers, data=json.dumps(payload), timeout=15) # 15s timeout
        response.raise_for_status() # Raise HTTPError for bad responses 
        logging.info(f"Data sent successfully. Server response status: {response.status_code}")
        return True

    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection Error sending data to {WEBAPP_URL}: {e}")
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout sending data to {WEBAPP_URL}: {e}")
    except requests.exceptions.HTTPError as e:
        error_detail = f"Status code: {e.response.status_code}"
        try:
            error_json = e.response.json() # Try parsing server's JSON error
            error_detail += f" - {error_json.get('error', e.response.text)}"
        except json.JSONDecodeError:
            error_detail += f" - {e.response.text}" # Use raw text if not JSON
        logging.error(f"HTTP Error sending data: {error_detail}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error during data sending request: {e}") # Catch other request errors
    except Exception as e:
        logging.error(f"Unexpected error sending data: {e}", exc_info=True)

    return False # Return False if any exception occurred

# --- Settings Fetch Function ---
def fetch_device_settings(device_id):
    """Fetches min/max temperature settings from the web server."""
    global current_min_temp, current_max_temp # Allow updating globals

    if not device_id:
        logging.error("Cannot fetch settings: Device ID is missing.")
        return False # Indicate failure

    url = f"{SETTINGS_API_ENDPOINT}/{device_id}"
    logging.debug(f"Attempting to fetch settings from: {url}")

    try:
        response = requests.get(url, timeout=10) # 10 second timeout
        response.raise_for_status() # Raise HTTPError for bad responses

        settings = response.json()
        logging.info(f"Successfully fetched settings: {settings}")

        # Update global variables, converting None carefully
        new_min = settings.get('min_temp_threshold')
        new_max = settings.get('max_temp_threshold')

        # Basic validation: if both are set, min should be less than max
        if new_min is not None and new_max is not None:
             try:
                 if float(new_min) >= float(new_max):
                     logging.warning(f"Fetched settings are invalid (min >= max): Min={new_min}, Max={new_max}. Ignoring update.")
                     return False # Indicate invalid settings received
             except (ValueError, TypeError) as conv_err:
                  logging.warning(f"Fetched settings have non-numeric values: Min='{new_min}', Max='{new_max}'. Error: {conv_err}. Ignoring update.")
                  return False


        # Only update if different to avoid unnecessary logs
        if new_min != current_min_temp or new_max != current_max_temp:
             logging.info(f"Updating stored settings: Min={new_min}, Max={new_max}")
             current_min_temp = new_min # Store as fetched (could be None or string/number)
             current_max_temp = new_max
        else:
             logging.debug("Fetched settings are the same as current. No update needed.")

        return True # Indicate success

    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection Error fetching settings from {url}: {e}")
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout fetching settings from {url}: {e}")
    except requests.exceptions.HTTPError as e:
        error_detail = f"Status code: {e.response.status_code}"
        try:
            error_json = e.response.json()
            error_detail += f" - {error_json.get('error', e.response.text)}"
        except json.JSONDecodeError:
            error_detail += f" - {e.response.text}"
        logging.error(f"HTTP Error fetching settings ({url}): {error_detail}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error during settings fetching request ({url}): {e}")
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding settings JSON response from {url}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error fetching settings ({url}): {e}", exc_info=True)

    # If any exception occurred, return False
    return False

# --- LCD Update Function ---
def update_lcd(temp_c, humid, relay_state_str=None, status_msg=None):
    """Updates the LCD display with sensor data, relay status, or a status message."""
    global lcd
    if not lcd: return # Do nothing if LCD failed to initialize

    try:
        lcd.clear() # Clear previous content

        if status_msg:
            # Display priority status message (e.g. "Sensor Error")
            lcd.cursor_pos = (0, 0)
            lcd.write_string(status_msg[:LCD_COLS])
            if len(status_msg) > LCD_COLS :
                 lcd.cursor_pos = (1,0)
                 lcd.write_string(status_msg[LCD_COLS:(LCD_COLS*2)])

        elif temp_c is not None and humid is not None:
            # Display Temp and Humidity on Line 1
            temp_f = temp_c * (9 / 5) + 32
            line1 = f"T:{temp_c:>4.1f}C H:{humid:>3.0f}%"[:LCD_COLS] # Compact format
            lcd.cursor_pos = (0, 0)
            lcd.write_string(line1.ljust(LCD_COLS))

            # Display Relay Status on Line 2
            if relay_state_str:
                 line2 = relay_state_str[:LCD_COLS]
                 lcd.cursor_pos = (1, 0)
                 lcd.write_string(line2.ljust(LCD_COLS))
            else: # Fallback if no relay status
                 lcd.cursor_pos = (1,0)
                 lcd.write_string("Relay: ---".ljust(LCD_COLS))

        else:
            # Fallback message if no error but data is None
            lcd.cursor_pos = (0,0)
            lcd.write_string("Reading...".ljust(LCD_COLS))
            lcd.cursor_pos = (1,0)
            lcd.write_string(" ".ljust(LCD_COLS)) # Clear second line

    except Exception as e:
        logging.error(f"Failed to update LCD: {e}")

# --- Cleanup Function ---
def cleanup(signum=None, frame=None):
    """Handles resource cleanup on exit."""
    global lcd, shutting_down, dht_device, relay
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

    # Clean up Relay object FIRST (to ensure heater is OFF)
    if relay:
        try:
            print("Turning relay OFF and closing GPIO...")
            relay.off() # Ensure heater is off
            time.sleep(0.1) # Short pause
            relay.close() # Release GPIO resources
            print(f"Relay on GPIO {RELAY_PIN} turned OFF and closed.")
        except Exception as e:
            print(f"Warning: Error during relay cleanup: {e}")

    # Clean up DHT sensor object
    if dht_device:
        try:
            if hasattr(dht_device, 'exit') and callable(dht_device.exit):
                dht_device.exit()
                print("DHT sensor resource released.")
            else:
                print("DHT sensor library does not have explicit exit method.")
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
    logging.info("--- Terrarium Control Script Stopped ---") # Log it
    sys.exit(0) # Exit cleanly

# --- Main Application Logic ---
if __name__ == "__main__":
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup) # Catches Ctrl+C

    logging.info("--- Initializing Device ---")
    DEVICE_UNIQUE_ID = get_or_generate_persistent_device_id()
    sensor_ok = initialize_sensor()
    lcd_ok = initialize_lcd()
    relay_ok = initialize_relay()

    # Critical check: Need at least ID, Sensor, and Relay to function
    if not DEVICE_UNIQUE_ID or not sensor_ok or not relay_ok:
        critical_msg = "Init Error:"
        if not DEVICE_UNIQUE_ID: critical_msg += " No ID!"
        if not sensor_ok: critical_msg += " Sensor!"
        if not relay_ok: critical_msg += " Relay!"
        logging.critical(f"CRITICAL FAILURE: {critical_msg}. Exiting.")

        # Attempt to display error on LCD if it initializes
        if lcd:
             try:
                 lcd.clear()
                 lcd.cursor_pos = (0, 0)
                 lcd.write_string(critical_msg[:LCD_COLS])
                 if len(critical_msg) > LCD_COLS:
                     lcd.cursor_pos = (1, 0)
                     lcd.write_string(critical_msg[LCD_COLS:(LCD_COLS*2)])
                 time.sleep(5)
             except Exception as lcd_init_err:
                 logging.error(f"Failed to display init error on LCD: {lcd_init_err}")

        exit(1) # Exit with error code

    # Print Device ID for user convenience
    print("\n" + "="*50)
    print("      TERRARIUM DEVICE ID INFORMATION")
    print("="*50)
    print(f" This device's Unique ID is: {DEVICE_UNIQUE_ID}")
    print("\n -> Link this ID in the web app settings.")
    print("="*50 + "\n")
    logging.info(f"Using Device ID: {DEVICE_UNIQUE_ID}")

    # Log configuration details
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
        error_message_for_lcd = None # Store specific error message for LCD display
        relay_status_str = "Relay: ---" # Default relay status string

        try: # *** START OF MAIN TRY BLOCK ***
            # --- Fetch Settings Periodically ---
            current_time = time.monotonic()
            # Fetch settings on first run OR if interval has passed
            if last_settings_fetch_time == 0 or (current_time - last_settings_fetch_time >= SETTINGS_FETCH_INTERVAL):
                logging.info("Time to fetch device settings...")
                if fetch_device_settings(DEVICE_UNIQUE_ID):
                    last_settings_fetch_time = current_time # Update time only on success
                else:
                    logging.warning("Failed to fetch/update settings. Using previous values (if any).")
                    # Consider what happens if fetch fails repeatedly - should we disable heating?
                    # For now, it uses old values or None if never fetched.

            # --- Read Sensor ---
            temp, humid = read_sensor()

            # --- Send Data to Server ---
            if temp is not None and humid is not None:
                send_data_to_server(DEVICE_UNIQUE_ID, temp, humid)
            else:
                logging.warning("Sensor read failed or returned invalid data this cycle.")
                error_message_for_lcd = "Sensor Error" # Set error message for LCD

            # --- Heating Control Logic (Hysteresis) ---
            # Check if relay was initialized successfully
            if relay is None:
                logging.error("Cannot perform heating control: Relay not initialized.")
                relay_status_str = "Relay: ERROR"
                error_message_for_lcd = "Relay Error" # Prioritize relay error on LCD
            # Check if we have a valid temperature reading
            elif temp is None:
                logging.warning("Cannot perform heating control: Invalid temperature reading.")
                # Turn OFF for safety if sensor fails
                if relay.is_active:
                    logging.warning("Turning relay OFF due to invalid temperature reading.")
                    relay.off()
                relay_status_str = "Relay: OFF (Safe)"
                error_message_for_lcd = "Sensor Error" # Show sensor error on LCD
            # Proceed with logic if relay and temp are valid
            else:
                temp_float = float(temp) # Ensure temp is float for comparison
                min_temp_float = None
                max_temp_float = None

                # Safely convert thresholds to float, handle potential errors/None
                try:
                    if current_min_temp is not None:
                        min_temp_float = float(current_min_temp)
                    if current_max_temp is not None:
                        max_temp_float = float(current_max_temp)
                except (ValueError, TypeError) as conv_err:
                     logging.error(f"Invalid threshold values stored: Min='{current_min_temp}', Max='{current_max_temp}'. Error: {conv_err}. Cannot control heating.")
                     # Turn OFF if thresholds are invalid?
                     if relay.is_active:
                         logging.warning("Turning relay OFF due to invalid stored thresholds.")
                         relay.off()
                     relay_status_str = "Relay: OFF (Cfg Err)"
                     error_message_for_lcd = "Settings Error"
                else:
                    # Thresholds are valid numbers (or None)
                    relay_is_currently_on = relay.is_active # Check current state BEFORE making decisions

                    # Log current state for debugging
                    logging.debug(f"Control Logic Check: Temp={temp_float}, Min={min_temp_float}, Max={max_temp_float}, Relay Currently ON={relay_is_currently_on}")

                    # --- Decision Making ---
                    action_taken = False # Flag to track if we changed state

                    if not relay_is_currently_on:
                        # --- Heater is OFF: Check if we need to turn ON ---
                        # Requires min_temp to be set and temp to be below it
                        if min_temp_float is not None and temp_float < min_temp_float:
                            logging.info(f"Temperature ({temp_float:.1f}°C) is BELOW minimum ({min_temp_float:.1f}°C) and relay is OFF. Turning relay ON.")
                            relay.on()
                            action_taken = True
                        else:
                            # Heater is OFF and Temp is NOT below minimum (or min not set) -> Keep OFF
                            logging.debug(f"Heater is OFF. Temp ({temp_float:.1f}°C) not below min ({min_temp_float}). Keeping relay OFF.")
                            # No action needed, relay stays OFF

                    else: # relay_is_currently_on is True
                        # --- Heater is ON: Check if we need to turn OFF ---
                        # Requires max_temp to be set and temp to be at or above it
                        if max_temp_float is not None and temp_float >= max_temp_float:
                            logging.info(f"Temperature ({temp_float:.1f}°C) is AT or ABOVE maximum ({max_temp_float:.1f}°C) and relay is ON. Turning relay OFF.")
                            relay.off()
                            action_taken = True
                        else:
                            # Heater is ON and Temp is NOT above maximum (or max not set) -> Keep ON
                            logging.debug(f"Heater is ON. Temp ({temp_float:.1f}°C) not above max ({max_temp_float}). Keeping relay ON.")
                            # No action needed, relay stays ON

                    # --- Set Status String for LCD ---
                    final_relay_state = relay.is_active # Check state AFTER potential changes
                    if final_relay_state:
                        relay_status_str = "Relay: ON (Heat)"
                    else:
                        relay_status_str = "Relay: OFF"
                    # Log if no action was taken but state is maintained
                    if not action_taken:
                         logging.debug(f"No state change required. Maintained relay state: {'ON' if final_relay_state else 'OFF'}")


            # --- Update LCD ---
            # Pass relay status string to the LCD function
            # Status_msg (like "Sensor Error") will override normal display if present
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

        except KeyboardInterrupt:
             # Already handled by signal handler, but good practice to have also in loop
             logging.info("KeyboardInterrupt detected in main loop. Exiting loop.")
             if not shutting_down: # If signal handler didn't run first
                 cleanup(signal.SIGINT)
             break # Exit while loop

        except Exception as e:
             logging.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
             # Turn off relay on unexpected errors for safety
             if relay and relay.is_active:
                 try:
                     logging.warning("Turning relay OFF due to unexpected error in main loop.")
                     relay.off()
                     relay_status_str = "Relay: OFF (ERR)"
                 except Exception as relay_err:
                     logging.error(f"Failed to turn off relay during error handling: {relay_err}")
                     relay_status_str = "Relay: ERR!"

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
