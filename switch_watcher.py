#!/usr/bin/env python3
# switch_watcher.py - This script watches a physical switch connected to the computer
#                     and turns a specific background program (service) on or off based
#                     on the switch's position.

import time         # Lets the script pause or wait
import board        # Helps use the computer's pins (like on a Raspberry Pi) by name
import digitalio    # Used to read the state (on/off) of the pins
import subprocess   # Lets this script run other commands on the computer, like starting/stopping services
import sys          # Allows the script to exit cleanly
import signal       # Helps the script shut down gracefully if asked to stop (e.g., by Ctrl+C)

# Which pin the switch is physically connected to.
# 'board.D21' means digital pin 21.
SWITCH_PIN = board.D21

# The exact name of the background program being controlled.
SERVICE_NAME = "terrarium-monitor.service"

# How the switch is wired up. This tells the script whether the pin should normally
# be 'low' (OFF) or 'high' (ON) when the switch isn't being pressed/flipped.
# - Use digitalio.Pull.DOWN if your switch connects the pin to POWER (like 3.3V) when ON.
#   The pin will be OFF by default.
# - Use digitalio.Pull.UP if your switch connects the pin to GROUND (GND) when ON.
#   The pin will be ON by default.
PULL_DIRECTION = digitalio.Pull.DOWN

# How long (in seconds) to wait after the switch is flipped before reacting.
# Physical switches can be 'bouncy' and send multiple quick signals. This pause
# helps make sure we only react once to a single flip. 0.3 seconds is usually enough.
DEBOUNCE_TIME_SEC = 0.3

# How often (in seconds) the script checks the switch's state.
# Checking too often uses more compute power, too slowly makes it feel unresponsive.
POLL_INTERVAL_SEC = 0.2

# --- Internal Tracking Variables ---

# This will hold the object representing the switch pin after setup. Starts as empty.
switch = None
# Remembers what the switch state was the last time we checked. Starts as unknown.
previous_switch_state = None
# A flag to know if the script is currently trying to shut down.
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

    # Put together the command to run, e.g., "/bin/systemctl start my_service.service"
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

def cleanup(signum=None, frame=None):
    """
    This function is called when the script is asked to stop.
    It cleans up neatly, mainly by releasing the pin connection.
    """
    global shutting_down
    # Make sure we only run the cleanup steps once.
    if shutting_down:
        return
    shutting_down = True # Set the flag so the main loop stops
    print("\nShutting down the switch watcher...")
    # If we successfully set up the switch pin earlier...
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
    global switch, previous_switch_state # We need to modify the global variables
    try:
        # Create an object to represent the physical pin connection.
        switch = digitalio.DigitalInOut(SWITCH_PIN)
        # Set the pin as an input (we want to read from it, not send power out).
        switch.direction = digitalio.Direction.INPUT
        # Configure the pull-up or pull-down resistor based on the setting at the top.
        # This gives the pin a default state when the switch isn't actively connecting it.
        switch.pull = PULL_DIRECTION

        # Give the pin a tiny moment to stabilize after setting the pull resistor.
        time.sleep(0.1)
        # Read the switch's current state (True for ON/HIGH, False for OFF/LOW)
        # and store it as the initial state.
        previous_switch_state = switch.value
        # Show a user-friendly message about the initial state.
        state_str = "ON (connected)" if previous_switch_state else "OFF (disconnected)"
        pull_str = "Pull Down (expects 3.3V when ON)" if PULL_DIRECTION == digitalio.Pull.DOWN else "Pull Up (expects Ground when ON)"
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
    global previous_switch_state # We need to update the remembered state
    # Keep running until the 'shutting_down' flag becomes True.
    while not shutting_down:
        try:
            # Read the current state of the switch (True or False).
            current_switch_state = switch.value

            # Check if the state has changed since the last time we looked.
            if current_switch_state != previous_switch_state:
                # Print the raw change detected.
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
                desired_state_str = "RUNNING" if stable_state else "STOPPED"
                print(f"Switch is now stable in {state_str} state. Service should be {desired_state_str}.")
                print(f"(Service is currently {'running' if service_is_currently_active else 'stopped'})")

                # If the switch is now ON (True)...
                if stable_state is True:
                    # ...and the service is NOT running...
                    if not service_is_currently_active:
                        # ...then start the service.
                        print(f"Switch is ON, starting the service '{SERVICE_NAME}'...")
                        run_systemctl("start")
                    else:
                        # ...but if the service is already running, do nothing.
                        print(f"Switch is ON, but service '{SERVICE_NAME}' is already running. No action needed.")
                # If the switch is now OFF (False)...
                else:
                    # ...and the service IS running...
                    if service_is_currently_active:
                        # ...then stop the service.
                        print(f"Switch is OFF, stopping the service '{SERVICE_NAME}'...")
                        run_systemctl("stop")
                    else:
                        # ...but if the service is already stopped, do nothing.
                        print(f"Switch is OFF, but service '{SERVICE_NAME}' is already stopped. No action needed.")

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

    try:
        # Start the main loop to watch the switch.
        watch_switch()
    except SystemExit:
        # This is expected if the 'cleanup' function was called successfully. Do nothing.
        pass
    except Exception as e:
        # If a major unexpected error happens that wasn't caught inside the loop...
        print(f"\nA critical error occurred: {e}")
        # ...try to clean up before exiting.
        cleanup()
