# --- terrarium_control.py (Generate ID on First Run) ---

import uuid
import os
import time
import logging
import requests
import json
import board
import adafruit_dht

# --- Configuration ---
DEVICE_ID_FILE = '/home/DanDev/terrarium_device_id.txt'
WEBAPP_URL = 'http://192.168.1.42:5000/'
READING_API_ENDPOINT = f'{WEBAPP_URL}/api/device/readings'
SENSOR_READ_INTERVAL = 60 # Seconds

# --- Logging Setup ---
log_file = '/home/DanDev/terrarium_control.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logging.info("Terrarium Control Script Starting Up")

# --- Device ID Management (Get or Generate) ---
def get_or_generate_persistent_device_id():
    """
    Gets the device's unique ID.
    Generates and stores it using UUIDv4 on the first run if the file doesn't exist.
    Reads the ID from the file on subsequent runs.
    Returns the device ID string or None if a critical error occurs.
    """
    device_id = None
    try:
        if os.path.exists(DEVICE_ID_FILE):
            logging.debug(f"Device ID file found at {DEVICE_ID_FILE}")
            with open(DEVICE_ID_FILE, 'r') as f: device_id = f.read().strip()
            if device_id and len(device_id) >= 36:
                 try:
                     uuid.UUID(device_id, version=4); logging.info(f"Read/validated existing ID: {device_id}"); return device_id
                 except ValueError: logging.critical(f"CRITICAL: Invalid UUID in file '{DEVICE_ID_FILE}'. Manual fix needed."); return None
            else: logging.critical(f"CRITICAL: Invalid content in ID file '{DEVICE_ID_FILE}'. Manual fix needed."); return None
        else:
            logging.info(f"Device ID file not found. Generating new ID...")
            new_device_id = str(uuid.uuid4()); logging.info(f"Generated new ID: {new_device_id}")
            try:
                with open(DEVICE_ID_FILE, 'w') as f: f.write(new_device_id)
                logging.info(f"Saved new ID to '{DEVICE_ID_FILE}'"); return new_device_id
            except IOError as e: logging.error(f"ERROR saving new ID file '{DEVICE_ID_FILE}': {e}. Using unsaved ID."); return new_device_id
            except Exception as e: logging.error(f"ERROR saving new ID: {e}. Using unsaved ID."); return new_device_id
    except Exception as e: logging.critical(f"CRITICAL ERROR during ID retrieval: {e}"); return None

# --- Sensor Configuration ---
DHT_SENSOR_PIN = board.D16

# --- Initialize DHT Sensor ---
dht_device = None
try:
    # Create the sensor object with the specified pin
    dht_device = adafruit_dht.DHT22(DHT_SENSOR_PIN, use_pulseio=False)
    logging.info(f"DHT22 sensor successfully initialized on pin: {DHT_SENSOR_PIN}")
except RuntimeError as error:
    logging.critical(f"CRITICAL: Failed to initialize DHT22 sensor: {error.args[0]}")
    logging.critical(f"Check wiring on pin {DHT_SENSOR_PIN}, power, and sensor functionality.")
except Exception as e:
    logging.critical(f"CRITICAL: Unexpected error initializing DHT22 sensor: {e}")

# --- Sensor Reading Function ---
def read_sensor():
    """Reads Temp & Humidity from the connected DHT22 sensor."""
    global dht_device
    if dht_device is None: logging.error("DHT sensor not initialized."); return None, None

    temperature_c = None; humidity = None
    try:
        temperature_c = dht_device.temperature
        humidity = dht_device.humidity

        if humidity is not None and not (0 <= humidity <= 100):
            logging.warning(f"Discarding improbable humidity: {humidity:.1f}%")
            humidity = None
        if temperature_c is not None and not (-40 <= temperature_c <= 85):
            logging.warning(f"Discarding improbable temperature: {temperature_c:.1f}°C")
            temperature_c = None

        if temperature_c is not None and humidity is not None:
            logging.info(f"Successful Read: Temp={temperature_c:.1f}°C, Humidity={humidity:.1f}%")
            return temperature_c, humidity
        else:
            logging.warning("Sensor read valid but data None/invalid after checks.")
            return None, None
    except RuntimeError as error:
        logging.warning(f"DHT22 Runtime error reading sensor: {error.args[0]}")
        return None, None
    except Exception as e:
        logging.error(f"Unexpected error reading DHT22: {e}", exc_info=True)
        return None, None

# --- Data Sending Function ---
def send_data_to_server(device_id, temperature, humidity):
    """Sends sensor data to the web application API."""
    payload = {'device_unique_id': device_id, 'temperature': temperature, 'humidity': humidity}
    headers = {'Content-Type': 'application/json'}
    try:
        logging.debug(f"Sending data to {READING_API_ENDPOINT}: {payload}")
        response = requests.post(READING_API_ENDPOINT, headers=headers, data=json.dumps(payload), timeout=10)
        response.raise_for_status()
        logging.info(f"Data sent successfully: {response.status_code}")
        return True
    except requests.exceptions.ConnectionError as e: logging.error(f"Connection Error: {e}")
    except requests.exceptions.Timeout as e: logging.error(f"Timeout Error: {e}")
    except requests.exceptions.HTTPError as e:
        # Log specific API errors
        error_detail = e.response.text # Get the raw text first as a fallback
        try:
            # Attempt to parse the response as JSON to get a structured error
            error_json = e.response.json()
            # Use the 'error' field from the JSON if it exists, otherwise keep raw text
            error_detail = error_json.get('error', error_detail)
        except json.JSONDecodeError:
            # If the response wasn't valid JSON, just use the raw text obtained earlier
            pass
        logging.error(f"HTTP Error sending data: {e.response.status_code} - {error_detail}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending data: {e}")

    return False

# --- Main Application Logic ---
if __name__ == "__main__":
    logging.info("--- Initializing Device ---")
    DEVICE_UNIQUE_ID = get_or_generate_persistent_device_id()

    if not DEVICE_UNIQUE_ID:
        logging.critical("Initialization failed: Could not obtain valid Device Unique ID.")
        exit(1)

    # Print ID for user to record (matches the generate-on-first-run method)
    print("\n" + "="*50)
    print("      TERRARIUM DEVICE ID INFORMATION")
    print("="*50)
    print(f" This device's Unique ID is: {DEVICE_UNIQUE_ID}")
    print("\n -> Write this ID down accurately.")
    print(" -> Enter this ID in the web application's")
    print("    'Device Settings' section to link this device.")
    print("="*50 + "\n")
    logging.info(f"Device Initialized. Using Device ID: {DEVICE_UNIQUE_ID}")

    # Check if sensor initialized before starting loop
    if dht_device is None:
         logging.critical("DHT Sensor failed to initialize. Exiting.")
         exit(1)

    logging.info(f"Will send data to: {READING_API_ENDPOINT}")
    logging.info(f"Sensor read interval: {SENSOR_READ_INTERVAL} seconds")

    # --- Main Loop ---
    while True:
        try:
            temp, humid = read_sensor()
            if temp is not None and humid is not None:
                send_data_to_server(DEVICE_UNIQUE_ID, temp, humid)
            else:
                logging.debug("Sensor read failed or returned invalid data.")

            logging.debug(f"Sleeping for {SENSOR_READ_INTERVAL} seconds...")
            time.sleep(SENSOR_READ_INTERVAL)

        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt received. Exiting gracefully.")
            if dht_device: dht_device.exit() # Clean up sensor object
            break
        except Exception as e:
            logging.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
            time.sleep(10)

    logging.info("--- Terrarium Control Script Shutting Down ---")
    if dht_device: dht_device.exit() # Ensure cleanup on normal exit too
