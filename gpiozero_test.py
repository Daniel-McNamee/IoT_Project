#!/usr/bin/env python3
from gpiozero import OutputDevice
from time import sleep
import sys

RELAY_PIN = 18  # GPIO pin number

print(f"Attempting to use gpiozero for pin {RELAY_PIN}")

# Basic settings for your relay
IS_ACTIVE_HIGH = False  # False = relay turns ON with LOW signal
START_STATE_HIGH = True  # True = start with relay OFF for active-low relays

try:
    # Setup the relay
    relay = OutputDevice(RELAY_PIN, active_high=IS_ACTIVE_HIGH, initial_value=START_STATE_HIGH)
    
    # Display the initial state
    if IS_ACTIVE_HIGH:
        initial_state_str = "HIGH (ON)" if START_STATE_HIGH else "LOW (OFF)"
    else:
        initial_state_str = "HIGH (OFF)" if START_STATE_HIGH else "LOW (ON)"
    
    print(f"GPIOZero initialized pin {RELAY_PIN}. Relay Active-High: {IS_ACTIVE_HIGH}. Initial State: {initial_state_str}")
    print("-" * 30)
    print("Starting gpiozero relay test cycle. Press Ctrl+C to exit.")
    print(f"Observe the relay module LED and listen for a click. (Pin {RELAY_PIN})")
    print("-" * 30)
    
    # Main loop - turn relay on and off repeatedly
    while True:
        print(f"Turning relay ON (activating pin {RELAY_PIN})...")
        relay.on()
        sleep(5)
        
        print(f"Turning relay OFF (deactivating pin {RELAY_PIN})...")
        relay.off()
        sleep(5)
        print("-" * 30)
        
except Exception as e:
    print(f"\nError initializing or controlling GPIO with gpiozero: {e}")
    
    # Show helpful error messages
    if "permission denied" in str(e).lower() or "unable to open" in str(e).lower():
         print(">>> Did you run with sudo? <<<")
    elif "pin is already in use" in str(e).lower():
         print(">>> Is another script using this pin? <<<")
    elif "no such pin" in str(e).lower():
         print(f">>> Is GPIO {RELAY_PIN} the correct pin number? <<<")
         
finally:
    # Clean up before exiting
    if 'relay' in locals() and hasattr(relay, 'off'):
        print("Ensuring relay is OFF before exiting...")
        relay.off()
    if 'relay' in locals() and hasattr(relay, 'close'):
        relay.close()
    print("\nExiting gpiozero test.")
