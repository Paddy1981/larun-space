import numpy as np
import pytest
from larun_space.features.extractor import LarunSpaceFeatureExtractor

@pytest.fixture
def feature_extractor():
    """Provides a LarunSpaceFeatureExtractor instance."""
    return LarunSpaceFeatureExtractor()

def test_extraction_happy_path(feature_extractor):
    """
    Tests feature extraction with valid, simple data.
    """
    time = np.arange(10)
    flux = np.array([1, 2, 1, 2, 1, 2, 1, 2, 1, 2])
    
    features = feature_extractor.extract(time, flux)
    
    assert features.shape == (10,)
    assert not np.isnan(features).any()
    
    # Check a few specific, easy-to-calculate values
    assert features[0] == 1.5  # Mean flux
    assert features[5] == 10   # Number of points
    assert features[6] == 9    # Time baseline

def test_extraction_empty_input(feature_extractor):
    """
    Tests feature extraction with empty input arrays.
    """
    time = np.array([])
    flux = np.array([])
    
    features = feature_extractor.extract(time, flux)
    
    assert features.shape == (10,)
    assert np.isnan(features).all()

def test_extraction_single_point(feature_extractor):
    """
    Tests feature extraction with a single data point.
    """
    time = np.array([1])
    flux = np.array([100])
    
    features = feature_extractor.extract(time, flux)
    
    assert features.shape == (10,)
    assert not np.isnan(features).any()
    assert features[0] == 100.0 # Mean
    assert features[1] == 0.0   # Std Dev
    assert features[4] == 0.0   # Amplitude
    assert features[5] == 1     # Num points
    assert features[6] == 0     # Time baseline
