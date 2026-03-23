#!/usr/bin/env python3
"""
LAUNCHER
Starts the Control Tower (Market Scanner) and the Trading Engine in parallel.
"""
import subprocess
import time
import os
import sys
from termcolor import colored

def main():
    print(colored("🚀 STARTING QUANT SYSTEM (Control Tower Mode)", "cyan", attrs=['bold']))
    
    # Get the directory where launcher.py represents
    base_path = os.path.dirname(os.path.abspath(__file__))
    
    # 1. Start Scanner (Background)
    scanner = subprocess.Popen([sys.executable, "scan_market.py"], cwd=base_path, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    
    print(colored("Initializing Control Tower...", "yellow"))
    time.sleep(3)
    
    # 2. Start Engine (Foreground)
    print(colored("2. Launching Trading Engine...", "green"))
    try:
        # We run engine in foreground so we can see its output directly
        subprocess.run([sys.executable, "main.py"], cwd=base_path)
    except KeyboardInterrupt:
        print("\nStopping subsystem...")
    finally:
        print(colored("Shutting down Scanner...", "red"))
        scanner.terminate()
        scanner.wait()
        print("System Halted.")

if __name__ == "__main__":
    main()
