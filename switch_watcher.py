#!/usr/bin/env python3
# switch_watcher.py - This script watches a physical switch connected to the computer
#                     and turns a specific background program (service) on or off based
#                     on the switch's position. Also logs events to database.

import time         # Lets the script pause or wait
import board        # Helps use the computer's pins (like on a Raspberry Pi) by name
import digitalio    # Used to read the state (on/off) of the pins
import subprocess   # Lets this script run other commands on the computer, like starting/stopping services
import sys          # Allows the script to exit cleanly
import signal       # Helps the script shut down gracefully if asked to stop (e.g., by Ctrl+C)
import mysql.connector # Added for Database access
from mysql.connector import Error # Added for DB specific error handling

# --- Configuration ---

# Which pin the switch is physically connected to.
# 'board.D21' means digital pin 21.
SWITCH_PIN = board.D21

# The name of the background program being controlled.
SERVICE_NAME = "terrarium-control.service"

# How the switch is wired up. This tells the script whether the pin should normally
# be 'low' (OFF) or 'high' (ON) when the switch isn't being flipped.
# - Use digitalio.Pull.DOWN if your switch connects the pin to POWER (like 3.3V) when ON.
#   The pin will be OFF by default.
# - Use digitalio.Pull.UP if your switch connects the pin to GROUND (GND) when ON.
#   The pin will be ON by default.
PULL_DIRECTION = digitalio.Pull.UP # Set according to current wiring

# How long (in seconds) to wait after the switch is flipped before reacting.
# Physical switches can be 'bouncy' and send multiple quick signals. This pause
# helps make sure we only react once to a single flip. 0.3 seconds is usually enough.
DEBOUNCE_TIME_SEC = 0.3

# How often (in seconds) the script checks the switch's state.
# Checking too often uses more compute power, too slowly makes it feel unresponsive.
POLL_INTERVAL_SEC = 0.2

# --- Database Configuration ---
DB_HOST = 'localhost'
DB_USER = 'root' # Using root DB user as script runs as system root
DB_PASSWORD = 'Life4588'
DB_NAME = 'terrarium_data'

# --- Internal Tracking Variables ---

# This will hold the object representing the switch pin after setup. Starts as empty.
switch = None
# Remembers what the switch state was the last time we checked. Starts as unknown.
previous_switch_state = None
# Holds the database connection object
db_connection = None
# Holds the database cursor object
db_cursor = None
# Flagged to track if the script is currently trying to shut down.
shutting_down = False

# --- Helper Functions ---

def run_systemctl(action):
    """
    Tells the systemctl to do something
    (like 'start' or 'stop') the program specified in SERVICE_NAME.
    Returns True if the command seemed to work, False otherwise.
    """
    # Only allow specific safe actions
    if action not in ["start", "stop", "is-active"]:
        print(f"Error: Tried to run an invalid action '{action}' on the service.")
        return None # Indicate an invalid action was requested

    # Put together the command to run, e.g., "/bin/systemctl start terrarium_monitor.service"
    command = ["/bin/systemctl", action, SERVICE_NAME]
    try:
        # Run the command. We capture the output text and don't crash if it fails
        # (because 'is-active' fails when the service is stopped, which is normal).
        # Set a timeout so it doesn't hang forever if systemctl gets stuck.
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
        # Show what happened (useful for debugging)
        print(f"Ran command: '{' '.join(command)}' | Result code: {result.returncode} | Output: {result.stdout.strip()} {result.stderr.strip()}")
        # A result code of 0 usually means success.
        return result.returncode == 0
    except FileNotFoundError:
        # This means the 'systemctl' command itself wasn't found.
        print(f"Error: Could not find the 'systemctl' command.")
        return False
    except subprocess.TimeoutExpired:
        # The command took too long to finish.
        print(f"Error: The command '{' '.join(command)}' timed out.")
        return False
    except Exception as e:
        # Catch any other unexpected problems running the command.
        print(f"Error trying to run '{action}' on the service: {e}")
        return False

def is_service_active():
    """
    Checks if the background program (SERVICE_NAME) is currently running.
    Returns True if it's running, False if it's stopped.
    """
    # Prepare the command to check the service status quietly (we only care if it's active or not).
    command = ["/bin/systemctl", "is-active", "--quiet", SERVICE_NAME]
    try:
        # Run the command. 'is-active' returns 0 if active, non-zero otherwise.
        result = subprocess.run(command, check=False, timeout=3)
        # Return True if the command succeeded (exit code 0), meaning the service is active.
        return result.returncode == 0
    except Exception as e:
        # If anything goes wrong trying to check, assume it's not running for safety.
        print(f"Error checking if service is active: {e}")
        return False # Assume inactive if we hit an error

# --- Database Connection Function (uses root credentials) ---
def connect_database():
    """Establishes connection to the MySQL/MariaDB database as root."""
    global db_connection, db_cursor
    if db_connection and db_connection.is_connected():
        return True # Already connected
    try:
        print("Watcher connecting to database (as root)...") # Differentiate logs
        db_connection = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            connect_timeout=5
        )
        if db_connection.is_connected():
            db_cursor = db_connection.cursor()
            print("Watcher database connection successful.")
            return True
        else:
             print("Watcher database connection failed (is_connected False).")
             db_connection = None; db_cursor = None
             return False
    except Error as e:
        print(f"Error connecting watcher to MySQL database: {e}")
        db_connection = None
        db_cursor = None
        return False

# --- DB System Event Logging Function ---
def log_system_event(event_type, details=None):
    """Logs an event (like startup, shutdown) to the system_events table."""
    # Attempt to connect if necessary
    if not (db_connection and db_connection.is_connected()):
        print("DB connection needed for event log, trying to connect...")
        if not connect_database():
             print(f"Failed to connect to DB. Cannot log event: {event_type}")
             return # Exit function if connection fails

    # Proceed if connected
    if db_cursor:
        try:
            sql = "INSERT INTO system_events (event_type, details) VALUES (%s, %s)"
            val = (event_type, details)
            db_cursor.execute(sql, val)
            db_connection.commit()
            print(f"Event logged: {event_type}")
        except Error as e:
            print(f"Failed to log system event '{event_type}': {e}")
            # If connection lost mid-log, can try reconnecting here too
    else:
         print(f"Cannot log event '{event_type}', DB cursor unavailable.")


def cleanup(signum=None, frame=None):
    """
    This function is called when the script is asked to stop.
    It cleans up neatly, releasing the pin connection and DB connection.
    """
    global shutting_down, db_connection, db_cursor, switch # Add DB/switch variables
    # Make sure we only run the cleanup steps once.
    if shutting_down:
        return
    shutting_down = True # Set flag so the main loop stops
    signal_name = signal.Signals(signum).name if signum else "Normal Exit"
    print(f"\nShutting down the switch watcher (Signal: {signal_name})...")

    # --- Close Database Connection ---
    print("Watcher closing database connection...")
    if db_cursor:
        try: db_cursor.close(); print("Watcher DB cursor closed.")
        except Error as e: print(f"Error closing watcher DB cursor: {e}")
    if db_connection and db_connection.is_connected():
        try: db_connection.close(); print("Watcher DB connection closed.")
        except Error as e: print(f"Error closing watcher DB connection: {e}")
    db_connection = None # Clear globals
    db_cursor = None

    # If switch pin is setup correctly
    if switch:
        try:
            # Release the pin so other programs can use it.
            switch.deinit()
            print("Switch pin connection released.")
        except Exception as e:
            # Just report errors during cleanup, but try to exit anyway.
            print(f"Error trying to release the switch pin: {e}")

    print("Switch watcher stopped.")
    # Exit the script cleanly.
    sys.exit(0)

def initialize_switch():
    """
    Sets up the computer pin to read the switch's state.
    Returns True if setup was successful, False otherwise.
    """
    global switch, previous_switch_state # Modified the global variables
    try:
        # Object to represent the physical pin connection.
        switch = digitalio.DigitalInOut(SWITCH_PIN)
        # Pin set as an input (we want to read from it, not send power out).
        switch.direction = digitalio.Direction.INPUT
        # Configure the pull-up or pull-down resistor based on the setting at the top.
        # This gives the pin a default state when the switch isn't actively connecting it.
        switch.pull = PULL_DIRECTION

        # Give the pin a tiny moment to stabilize after setting the pull resistor.
        time.sleep(0.1)
        # Read the switch's current state (True for ON/HIGH, False for OFF/LOW)
        # and store it as the initial state.
        previous_switch_state = switch.value
        # Show a message about the initial state.
        # Adjusted logic to correctly display ON/OFF based on pull direction
        if PULL_DIRECTION == digitalio.Pull.UP:
             state_str = "OFF (HIGH)" if previous_switch_state else "ON (LOW)" # PullUP: LOW=ON
        else: # PULL_DOWN
             state_str = "ON (HIGH)" if previous_switch_state else "OFF (LOW)" # PullDOWN: HIGH=ON
        pull_str = "Pull Up (expects Ground when ON)" if PULL_DIRECTION == digitalio.Pull.UP else "Pull Down (expects 3.3V when ON)"
        print(f"Switch pin {SWITCH_PIN} is ready. Wiring mode: {pull_str}. Initial state is: {state_str}")
        return True # Signal success
    except ValueError as e:
        # Common error if the pin doesn't exist or is already in use.
        print(f"FATAL ERROR: Failed to set up the switch pin {SWITCH_PIN}. Is the pin number correct? Is it already used by another program? Details: {e}")
        return False # Signal failure
    except RuntimeError as e:
        # Might happen if the hardware libraries aren't installed or can't access the hardware.
        print(f"FATAL ERROR: Failed to set up the switch pin {SWITCH_PIN}. Do you need to run as root, or are hardware libraries missing? Details: {e}")
        return False # Signal failure
    except Exception as e:
        # Catch any other unexpected problems during setup.
        print(f"FATAL ERROR: Failed to set up the switch: {e}")
        return False # Signal failure

# --- Main Part of the Script ---
def watch_switch():
    """
    The main loop that continuously checks the switch and acts on changes.
    """
    print("DEBUG: Entered watch_switch() function.") # Debugging
    loop_counter = 0 # Initialize loop counter
    global previous_switch_state # We need to update the remembered state
    # Keep running until the 'shutting_down' flag becomes True.
    print("Entering watch_switch main loop. shutting_down =", shutting_down) # Debugging
    while not shutting_down:
        loop_counter += 1 # Loop counter
        print(f"DEBUG: Top of watch_switch loop #{loop_counter}") # Debugging
        try:
            # Read the current state of the switch (True or False).
            print(f"DEBUG: Reading switch value...") # Debugging
            current_switch_state = switch.value
            print(f"DEBUG: Switch value read: {current_switch_state}") # Debugging

            # Check if the state has changed since the last time we looked.
            if current_switch_state != previous_switch_state:
                # Determine ON/OFF string based on pull direction
                if PULL_DIRECTION == digitalio.Pull.UP:
                    state_str = "OFF" if current_switch_state else "ON"
                else:
                    state_str = "ON" if current_switch_state else "OFF"
                print(f"Switch flipped! New state: {state_str}")

                # --- Handle Switch Bounce ---
                # Wait for the debounce time to ignore electrical noise.
                time.sleep(DEBOUNCE_TIME_SEC)
                # Read the switch state AGAIN after the pause.
                stable_state = switch.value
                # If the state flipped back during the pause, it was probably noise.
                if stable_state != current_switch_state:
                    print("Detected switch bounce (state changed back quickly). Ignoring the flip.")
                    # Update the 'previous' state to the actual stable state we just read.
                    previous_switch_state = stable_state
                    # Skip the rest of the actions and check again next loop.
                    continue

                # --- End of Bounce Handling ---

                # If we get here, the switch state seems stable after the debounce delay.
                # Update the 'previous' state to this new, stable state.
                previous_switch_state = current_switch_state # Note: using current_switch_state here is correct

                # --- Decide What To Do ---
                # Check if the background service is running right now.
                service_is_currently_active = is_service_active()
                # Determine if the service *should* be running based on stable state and pull direction
                should_be_running = (stable_state is False if PULL_DIRECTION == digitalio.Pull.UP else stable_state is True)
                desired_state_str = "RUNNING" if should_be_running else "STOPPED"
                print(f"Switch is now stable in {state_str} state. Service should be {desired_state_str}.")
                print(f"(Service is currently {'running' if service_is_currently_active else 'stopped'})")

                # --- Perform Action and Log Event ---
                action_taken = False
                event_to_log = None
                event_details = 'Triggered by switch'

                # If the service should be running...
                if should_be_running:
                    # ...and it's NOT running...
                    if not service_is_currently_active:
                        print(f"Switch wants service ON, starting '{SERVICE_NAME}'...")
                        if run_systemctl("start"):
                            event_to_log = 'MONITOR_START'
                            action_taken = True
                    else:
                        # ...but if it's already running, do nothing.
                        print(f"Switch wants service ON, but '{SERVICE_NAME}' is already running. No action needed.")
                # If the service should be stopped...
                else: # should_be_running is False
                    # ...and it IS running...
                    if service_is_currently_active:
                        print(f"Switch wants service OFF, stopping '{SERVICE_NAME}'...")
                        if run_systemctl("stop"):
                             event_to_log = 'MONITOR_STOP'
                             action_taken = True
                    else:
                        # ...but if it's already stopped, do nothing.
                        print(f"Switch wants service OFF, but '{SERVICE_NAME}' is already stopped. No action needed.")

                # Log the event AFTER the systemctl command seems successful
                if event_to_log:
                    log_system_event(event_to_log, event_details)
                    print(f"Logged event: {event_to_log}")

                if action_taken:
                    print("Systemctl action sequence completed.")


            # Wait a short time before checking the switch again.
            # This prevents the script from using too much computer power.
            time.sleep(POLL_INTERVAL_SEC)

        # --- Handle Specific Errors Gracefully ---
        except (OSError, IOError) as e:
            # These errors often mean a problem reading the physical pin.
            print(f"Problem reading the switch pin: {e}. Check the wiring/connection.")
            # Wait a bit longer before trying again, in case it's a temporary issue.
            time.sleep(5)
        except Exception as e:
            # Catch any other unexpected problems during the main loop.
            print(f"An unexpected error occurred: {e}")
            # Wait a short moment before trying again.
            time.sleep(1)

    print("DEBUG: Exited watch_switch loop.") # Debugging

# --- Script Starts Running Here ---
if __name__ == "__main__":
    # Tell the script how to shut down cleanly if it receives a 'terminate' signal
    # (e.g., from the system shutting down) or an 'interrupt' signal (e.g., Ctrl+C).
    signal.signal(signal.SIGTERM, cleanup) # Standard terminate signal
    signal.signal(signal.SIGINT, cleanup)  # Ctrl+C signal

    print("--- Starting Switch Watcher ---")
    # Try to set up the switch pin. If it fails, stop the script.
    if not initialize_switch():
        sys.exit(1) # Exit with an error code

    # Connect to database after switch init
    if not connect_database():
        print("Warning: Watcher failed initial DB connection. Event logging disabled until reconnect.")
        # Script will continue to control service, but won't log events initially

    print("DEBUG: Initialization complete. Calling watch_switch().") # Debugging
    try:
        # Start the main loop to watch the switch.
        watch_switch()
    except SystemExit:
        # This is expected if the 'cleanup' function was called successfully. Do nothing.
        print("DEBUG: Caught SystemExit.") # Debugging
        pass
    except Exception as e:
        # If a major unexpected error happens that wasn't caught inside the loop...
        print(f"\nDEBUG: Caught unexpected error in main block: {e}") # Debugging
        # Clean up before exiting.
        cleanup()
    print("DEBUG: Script execution finished.") # Debugging
