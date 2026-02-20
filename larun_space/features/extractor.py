import numpy as np
from typing import Tuple

class LarunSpaceFeatureExtractor:
    """
    Extracts 10 statistical features from a light curve.

    The light curve is provided as time and flux values.
    """

    def extract(self, time: np.ndarray, flux: np.ndarray) -> np.ndarray:
        """
        Calculates the 10 statistical features for the given light curve.

        Args:
            time: A NumPy array of time values.
            flux: A NumPy array of flux values.

        Returns:
            A NumPy array containing the 10 extracted features.
            Returns an array of NaNs if the input is invalid.
        """
        if flux is None or len(flux) == 0 or time is None or len(time) == 0:
            return np.full(10, np.nan)

        try:
            # 1. Mean flux
            mean_flux = np.mean(flux)

            # 2. Standard deviation
            std_dev = np.std(flux)

            # 3. Skewness
            skewness = np.mean(((flux - mean_flux) / std_dev) ** 3) if std_dev > 0 else 0

            # 4. Kurtosis
            kurtosis = np.mean(((flux - mean_flux) / std_dev) ** 4) - 3 if std_dev > 0 else 0

            # 5. Peak-to-peak amplitude
            amplitude = np.ptp(flux)

            # 6. Number of points
            num_points = len(flux)

            # 7. Time baseline
            time_baseline = time[-1] - time[0] if len(time) > 1 else 0

            # 8. Median absolute deviation (MAD)
            median_flux = np.median(flux)
            mad = np.median(np.abs(flux - median_flux))

            # 9. Beyond 1-sigma fraction
            sigma_fraction = np.sum(np.abs(flux - mean_flux) > std_dev) / num_points if std_dev > 0 else 0
            
            # 10. Auto-correlation lag-1
            autocorr_lag1 = np.corrcoef(flux[:-1], flux[1:])[0, 1] if len(flux) > 1 else 0

            return np.array([
                mean_flux,
                std_dev,
                skewness,
                kurtosis,
                amplitude,
                num_points,
                time_baseline,
                mad,
                sigma_fraction,
                autocorr_lag1
            ])
        except Exception:
            return np.full(10, np.nan)
