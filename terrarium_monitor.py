#!/usr/bin/env python3
# Combined Terrarium Temperature/Humidity Monitor and LCD Display Script

import time
import board
import adafruit_dht
from RPLCD.i2c import CharLCD
import sys
import signal # Import signal module required to run cleanup before reboot
import mysql.connector # Added for Database access
from mysql.connector import Error # Added for DB specific error handling

# --- Configuration ---
DHT_PIN = board.D16  # GPIO pin the DHT sensor is connected to
LCD_I2C_ADDRESS = 0x27 # I2C address of your LCD backpack
LCD_I2C_EXPANDER = 'PCF8574' # Common I2C chip for LCD backpacks
LCD_COLS = 16
LCD_ROWS = 2
READ_INTERVAL_SECONDS = 5.0 # How often to read the sensor and update display/loop speed
RETRY_DELAY_SECONDS = 2.0 # How long to wait after a sensor read error before retrying
SHUTDOWN_MSG_DELAY = 2.0 # Shutdown Message Delay

# --- Database Configuration ---
DB_HOST = 'localhost'
DB_USER = 'terrarium_user' # Specific user for this script
DB_PASSWORD = 'Life4588'
DB_NAME = 'terrarium_data'
DB_LOG_INTERVAL_SECONDS = 300 # Log sensor data every 5 minutes

# --- Global Variables ---
dht_sensor = None
lcd = None
db_connection = None # Holds the database connection object
db_cursor = None     # Holds the database cursor object
last_db_log_time = 0 # Tracks time of last DB sensor data insert
shutting_down = False # Flagging to prevent loop from restarting during cleanup

# --- Database Connection Function ---
def connect_database():
    """Establishes connection to the MySQL/MariaDB database."""
    global db_connection, db_cursor
    # Avoid reconnecting if already connected
    if db_connection and db_connection.is_connected():
        print("DB connection already active.")
        return True
    try:
        print("Connecting to database...")
        db_connection = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            connect_timeout=5 # Add a connection timeout
        )
        if db_connection.is_connected():
            db_cursor = db_connection.cursor()
            print("Database connection successful.")
            return True
        else:
             # This case is unlikely with mysql.connector but handled defensively
             print("Database connection failed (is_connected() returned False).")
             db_connection = None # Ensure it's None
             db_cursor = None
             return False
    except Error as e:
        print(f"Error connecting to MySQL database: {e}")
        db_connection = None # Ensure connection is None on error
        db_cursor = None
        return False

# --- DB System Event Logging Function ---
def log_system_event(event_type, details=None):
    """Logs an event (like startup, shutdown) to the system_events table."""
    # Only log if connected
    if db_connection and db_connection.is_connected() and db_cursor:
        try:
            sql = "INSERT INTO system_events (event_type, details) VALUES (%s, %s)"
            val = (event_type, details)
            db_cursor.execute(sql, val)
            db_connection.commit() # Commit the change
            print(f"Event logged: {event_type}")
        except Error as e:
            print(f"Failed to log system event '{event_type}': {e}")
            # Reconnection strategy if connection is lost during runtime
            if not db_connection.is_connected(): connect_database()
    else:
        print(f"Cannot log event '{event_type}', database not connected.")

# --- Cleanup Function ---
def cleanup(signum=None, frame=None):
    """Handles resource cleanup on exit (LCD, Database)."""
    global lcd, shutting_down, db_connection, db_cursor # Add DB variables here
    if shutting_down: # Avoid running cleanup multiple times if signals overlap
        return
    shutting_down = True

    signal_name = signal.Signals(signum).name if signum else "Normal Exit"
    print(f"\nReceived signal {signal_name}. Initiating graceful shutdown...")

    # --- Log Shutdown Event to Database ---
    print("Logging system shutdown event...")
    log_system_event('SYSTEM_SHUTDOWN') # Log this event before closing DB

    # --- Close Database Connection ---
    print("Closing database connection...")
    if db_cursor:
        try:
            db_cursor.close()
            print("Database cursor closed.")
        except Error as e:
             print(f"Error closing DB cursor: {e}")
    if db_connection and db_connection.is_connected():
        try:
            db_connection.close()
            print("Database connection closed.")
        except Error as e:
            print(f"Error closing DB connection: {e}")
    db_connection = None # Clear global variables
    db_cursor = None

    # --- Display Shutdown Message on LCD ---
    if lcd:
        try:
            print("Attempting to display shutdown message on LCD...")
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            # Keep message short for speed
            lcd.write_string("Shutting down..".ljust(LCD_COLS))
            time.sleep(SHUTDOWN_MSG_DELAY) # Wait
        except Exception as lcd_shutdown_msg_error:
            print(f"Warning: Could not display shutdown message on LCD: {lcd_shutdown_msg_error}")

    # --- Close LCD Resources ---
    if lcd:
        try:
            print("Clearing and closing LCD...")
            lcd.clear()
            lcd.backlight_enabled = False
            lcd.close(clear=True)
            print("LCD Cleared and Closed.")
        except Exception as e:
           print(f"Error during final LCD cleanup: {e}")

    print("--- Terrarium Monitor Stopped ---")
    sys.exit(0) # Exit the script cleanly

# --- Initialization Functions ---
def initialize_sensor():
    """Initializes the DHT sensor."""
    global dht_sensor
    try:
        dht_sensor = adafruit_dht.DHT22(DHT_PIN)
        print(f"DHT22 sensor initialized on pin {DHT_PIN}")
        # Attempt a quick initial read to check connectivity
        try:
             dht_sensor.temperature; dht_sensor.humidity # Semicolon allows both on one line
             print("Initial sensor read successful.")
        except RuntimeError as initial_read_error:
             print(f"Warning: Initial sensor read failed: {initial_read_error}. Will retry in main loop.")
        return True
    except Exception as e:
        print(f"FATAL: Failed to initialize DHT sensor: {e}")
        return False

def initialize_lcd():
    """Initializes the I2C LCD display."""
    global lcd
    try:
        lcd = CharLCD(LCD_I2C_EXPANDER, LCD_I2C_ADDRESS, cols=LCD_COLS, rows=LCD_ROWS, auto_linebreaks=False)
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
        return False # Indicate failure but allow script to continue

# --- Main Application Logic ---
def main():
    """Main loop for reading sensor, updating display, and logging to DB every 5 minutes."""
    global dht_sensor, lcd, db_connection, db_cursor, shutting_down, last_db_log_time # Include DB globals

    while not shutting_down: # Check flag to ensure we don't loop after cleanup starts
        current_monotonic_time = time.monotonic() # Get time once per loop
        temperature_c = None
        humidity = None
        error_message = None
        db_logged_this_cycle = False # Track DB log status for this specific loop iteration

        # --- Check DB connection status ---
        if not (db_connection and db_connection.is_connected()):
             print("DB connection lost or unavailable. Attempting reconnect...")
             connect_database() # Attempt to reconnect if needed

        # --- Read Sensor ---
        try:
            if dht_sensor: # Only read if sensor was initialized
                 temperature_c = dht_sensor.temperature
                 humidity = dht_sensor.humidity
            else:
                # This case should ideally be prevented by startup checks
                error_message = "Sensor object not initialized!"
                print(error_message)

            # --- Validate Readings ---
            if temperature_c is not None and humidity is not None:
                # Calculation and console print for every valid read
                temperature_f = temperature_c * (9 / 5) + 32
                print(f"Sensor: Temp={temperature_c:.1f}C ({temperature_f:.1f}F), Humid={humidity:.1f}%")

                # --- Update LCD (Runs every valid read cycle) ---
                if lcd: # Only update if LCD was initialized
                    try:
                        lcd_line1 = f"Temp:{temperature_c:>5.1f}C{temperature_f:>4.0f}F"[:LCD_COLS] # Format for columns
                        lcd_line2 = f"Humid:{humidity:>6.1f}%"[:LCD_COLS]
                        lcd.cursor_pos = (0, 0)
                        lcd.write_string(lcd_line1.ljust(LCD_COLS)) # Pad with spaces to clear line
                        lcd.cursor_pos = (1, 0)
                        lcd.write_string(lcd_line2.ljust(LCD_COLS)) # Pad with spaces to clear line
                    except Exception as lcd_write_error:
                        print(f"ERROR: Failed to write to LCD: {lcd_write_error}")
                        # Report error
                        # Clear the LCD to indicate an issue
                        try:
                            lcd.clear()
                            lcd.write_string("LCD Write Err")
                        except: pass # Ignore errors during error reporting

                # --- Log Sensor Data to Database (Conditional based on time) ---
                time_since_last_log = current_monotonic_time - last_db_log_time
                if time_since_last_log >= DB_LOG_INTERVAL_SECONDS:
                    if db_connection and db_connection.is_connected() and db_cursor:
                        try:
                            sql = "INSERT INTO readings (temperature, humidity) VALUES (%s, %s)"
                            val = (temperature_c, humidity)
                            db_cursor.execute(sql, val)
                            db_connection.commit() # Commit the transaction to save the data
                            db_logged_this_cycle = True
                            print(f"DB Log: Record inserted (Temp={temperature_c:.1f}, Humid={humidity:.1f}).")
                            last_db_log_time = current_monotonic_time # Reset timer ONLY after successful log
                        except Error as e:
                            print(f"Database Insert Error (readings): {e}")
                            # Reconnect attempt already at top of loop
                    else:
                         print("DB Log: Cannot log readings, connection unavailable.")
                # End of DB logging check

            else: # Handle case where sensor readings are None
                if dht_sensor and not error_message: # Avoid double reporting if sensor init failed
                    error_message = "Sensor Read Invalid"
                    print("Sensor read returned invalid data (None).")

        except RuntimeError as e:
            # DHT specific read error
            error_message = "Sensor Read Error"
            print(f"DHT Sensor Read Error: {e}")
            log_system_event('SENSOR_ERROR', str(e)[:254])

        except Exception as e:
            # Catch other unexpected errors during the loop
            error_message = "System Error"
            print(f"An unexpected error occurred in main loop: {e}")
            log_system_event('RUNTIME_ERROR', str(e)[:254])

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
        # Use a loop with short sleeps to check the shutdown flag more often
        # This makes the script slightly more responsive to shutdown signals
        # Wait time depends on whether there was a sensor error or not
        wait_duration = RETRY_DELAY_SECONDS if error_message else READ_INTERVAL_SECONDS
        loop_end_time = time.monotonic() + wait_duration
        while time.monotonic() < loop_end_time and not shutting_down:
             time.sleep(0.1) # Check for shutdown signal every 0.1s

# --- Script Entry Point ---
if __name__ == "__main__":
    # --- Register Signal Handlers ---
    # Register the cleanup function to be called on SIGTERM and SIGINT
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    print("--- Terrarium Monitor Service Starting ---")

    # --- Initialize All Components ---
    # This function now handles sensor, LCD, and DB connection, plus initial event logging
    critical_init_ok = False
    try:
        # Connect to DB, Log startup event, check sensor/LCD
        sensor_ok = initialize_sensor() # Check sensor first, as it's critical
        if not sensor_ok:
             print("Exiting due to critical sensor initialization failure.")
             # Attempt to log if DB connects
             if connect_database(): log_system_event('SYSTEM_SHUTDOWN', 'Sensor init failed')
             sys.exit(1) # Exit if sensor failed

        # Sensor OK, proceed with LCD and DB
        initialize_lcd() # Log warning if it fails, but don't exit
        if connect_database(): # Attempt DB connection
             log_system_event('SYSTEM_STARTUP') # Log startup event
        else:
             print("Warning: Database connection failed on startup. Logging disabled.")

        critical_init_ok = True # Reached here means sensor is okay
        global last_db_log_time
        last_db_log_time = time.monotonic() # Set initial time for DB logging interval

    except Exception as init_err:
        print(f"Unexpected error during initialization phase: {init_err}")
        sys.exit(1) # Exit if overall init fails unexpectedly

    # --- Start Main Loop ---
    if critical_init_ok:
        try:
            main() # Start the main monitoring loop
        except SystemExit:
            # Expected exit via cleanup()
            print("Exiting cleanly via SystemExit from cleanup.")
        except Exception as e:
            # Catch any other unexpected errors during main execution
            print(f"\nUnhandled critical error during main execution: {e}")
            # Attempt cleanup even on unexpected error
            # Pass None as signal number to indicate non-signal exit
            cleanup(signum=None)
    else:
         print("Could not start main loop due to initialization failures.")
