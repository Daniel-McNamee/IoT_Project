#!/usr/bin/env python3
# test_switch.py - Simple script to read and display the state of a GPIO pin

import time
import board
import digitalio

# --- Configuration (Matching watcher script) ---
SWITCH_PIN = board.D21
# Set PULL_DIRECTION based on the wiring:
# - digitalio.Pull.DOWN if switch connects pin to 3.3V when ON
# - digitalio.Pull.UP if switch connects pin to GND when ON
PULL_DIRECTION = digitalio.Pull.UP

switch = None

print(f"--- Testing GPIO Pin {SWITCH_PIN} ---")
print(f"Using Pull Direction: {PULL_DIRECTION}")
print("Press Ctrl+C to exit.")

try:
    # Initialize the pin
    switch = digitalio.DigitalInOut(SWITCH_PIN)
    switch.direction = digitalio.Direction.INPUT
    switch.pull = PULL_DIRECTION
    print("Pin initialized successfully.")
    print("-" * 20)

    last_state = None
    while True:
        current_state = switch.value # Read the pin state (True or False)

        # If PULL_DOWN: HIGH (True) means ON (connected to 3.3V)
        # If PULL_UP: LOW (False) means ON (connected to GND)
        if PULL_DIRECTION == digitalio.Pull.DOWN:
            on_state = True
            state_str = "ON (HIGH)" if current_state else "OFF (LOW)"
        else: # PULL_UP
            on_state = False
            state_str = "ON (LOW)" if not current_state else "OFF (HIGH)"

        # Print only when the state changes to avoid flooding the screen
        if current_state != last_state:
            print(f"Switch State: {state_str} (Raw value: {current_state})")
            last_state = current_state

        time.sleep(0.1) # Check roughly 10 times per second

except ValueError as e:
    print(f"\nERROR: Failed to initialize pin {SWITCH_PIN}. Is pin number correct? In use? Details: {e}")
except RuntimeError as e:
    print(f"\nERROR: Failed to initialize pin {SWITCH_PIN}. Hardware access issue? Libraries? Details: {e}")
except KeyboardInterrupt:
    print("\nExiting test.")
except Exception as e:
    print(f"\nAn unexpected error occurred: {e}")
finally:
    if switch:
        try:
            switch.deinit()
            print("Pin deinitialized.")
        except Exception as e:
            print(f"Error during deinit: {e}")
