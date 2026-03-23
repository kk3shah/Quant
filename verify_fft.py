import numpy as np
import pandas as pd
from strategies.spectral import DominantCycleAnalyzer

def test_fft_precision():
    print("--- Testing FFT Precision ---")
    
    # 1. Generate a synthetic wave with a clear period of 20 bars
    t = np.arange(256)
    period = 20
    freq = 1 / period
    signal = np.sin(2 * np.pi * freq * t) + np.random.normal(0, 0.1, 256)
    
    # Convert to log-price-like series
    prices = np.exp(np.cumsum(signal * 0.01) + 10)
    
    detected_period = DominantCycleAnalyzer.get_dominant_period(prices)
    snr = DominantCycleAnalyzer.get_signal_to_noise(prices)
    
    print(f"Target Period: {period}")
    print(f"Detected Period: {detected_period}")
    print(f"SNR: {snr:.2f}")
    
    if abs(detected_period - period) <= 2:
        print("✅ SUCCESS: FFT accurately identified the frequency.")
    else:
        print("❌ FAILURE: FFT accuracy out of range.")

if __name__ == "__main__":
    test_fft_precision()
