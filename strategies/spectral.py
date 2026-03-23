import numpy as np
import pandas as pd

class DominantCycleAnalyzer:
    """
    Identifies dominant cyclical frequencies in price data using FFT.
    Helps tune indicator periods to the current market 'heartbeat'.
    """
    @staticmethod
    def get_dominant_period(prices, min_period=14, max_period=50):
        """
        Returns the dominant cycle period (in bars) using FFT.
        """
        if len(prices) < max_period * 2:
            return None
        
        # 1. Detrend the data (crucial for FFT accuracy)
        # Using log returns or differencing
        data = np.diff(np.log(prices))
        
        # 2. Apply Hanning window to prevent spectral leakage
        window = np.hanning(len(data))
        windowed_data = data * window
        
        # 3. Fast Fourier Transform
        fft_values = np.fft.rfft(windowed_data)
        amplitudes = np.abs(fft_values)
        
        # 4. Map FFT bins to periods
        # Frequency = bin_index / num_samples
        # Period = 1 / Frequency
        num_samples = len(data)
        frequencies = np.fft.rfftfreq(num_samples)
        
        # Filter for periods within our target range
        valid_indices = []
        for i, freq in enumerate(frequencies):
            if freq == 0: continue
            period = 1 / freq
            if min_period <= period <= max_period:
                valid_indices.append(i)
        
        if not valid_indices:
            return None
            
        # Find index with max amplitude in the valid range
        max_amp_idx = valid_indices[np.argmax(amplitudes[valid_indices])]
        dominant_period = 1 / frequencies[max_amp_idx]
        
        return int(round(dominant_period))

    @staticmethod
    def get_signal_to_noise(prices):
        """
        Rough estimate of how 'cyclical' the market is vs random noise.
        Uses Hanning window (same as get_dominant_period) to prevent spectral
        leakage from artificially inflating peak amplitudes.
        """
        if len(prices) < 50:
            return 0.0

        data = np.diff(np.log(prices))

        # Apply Hanning window to match get_dominant_period and prevent leakage
        window = np.hanning(len(data))
        windowed_data = data * window

        fft_values = np.abs(np.fft.rfft(windowed_data))

        # Signal = Top 3 peaks / Total energy
        total_energy = np.sum(fft_values)
        if total_energy == 0: return 0.0

        peaks = np.sort(fft_values)[-3:]
        snr = np.sum(peaks) / total_energy

        return snr
