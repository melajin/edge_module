"""Pure Python reference for the selected V2 feature, baseline, and vote rules.

This module accepts a preconditioned, one-dimensional 1024-sample window at
1000 Hz. It implements the mathematical algorithm; acquisition continuity,
run detection, and calibration-window acceptance remain explicit caller inputs.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import math
from typing import Deque

import numpy as np


SAMPLE_RATE_HZ = 1000.0
FFT_SIZE = 1024
NUM_BINS = FFT_SIZE // 2
BIN_HZ = SAMPLE_RATE_HZ / FFT_SIZE
HARMONIC_BW_HZ = 5.0 + 2.0 * BIN_HZ
HF_CUTOFF_RATIO = 4.5
SENSOR_BW_HZ = 260.0
LOG_EPS = 1.0e-6
FEATURE_NAMES = ("rms", "harmonic1_ratio", "harmonic2_ratio", "harmonic3_ratio", "high_freq_ratio")
SELECTED_SIGMA = 3.0
SELECTED_MIN_DEVIATED_FEATURES = 1
SELECTED_CONFIRM_WINDOW = 5
SELECTED_CONFIRM_HITS = 4
CALIBRATION_WINDOWS = 120
MIN_REL_STD = 0.02
STD_FLOOR = 1.0e-6

_HANN = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(FFT_SIZE, dtype=np.float64) / (FFT_SIZE - 1)))


def fft_spectrum(samples: np.ndarray) -> tuple[float, np.ndarray]:
    """Return centered RMS and one-sided Hann amplitude spectrum.

    This follows ``edge_module/src/feature_extraction_v2.py`` and the
    manifest-pinned C ``em_fft_spectrum`` ordering. The Nyquist bin is omitted,
    as in the C core's 512-bin buffer.
    """
    signal = _as_window(samples)
    centered = signal - float(np.mean(signal))
    rms = float(np.sqrt(np.mean(centered * centered)))
    spectrum = np.fft.rfft(centered * _HANN)[:NUM_BINS]
    magnitude = np.abs(spectrum) * (2.0 / FFT_SIZE) / 0.5
    magnitude[0] *= 0.5
    return rms, magnitude.astype(np.float64, copy=False)


def _as_window(samples: np.ndarray) -> np.ndarray:
    signal = np.asarray(samples, dtype=np.float64)
    if signal.shape != (FFT_SIZE,):
        raise ValueError(f"expected exactly {FFT_SIZE} one-dimensional samples")
    if not np.isfinite(signal).all():
        raise ValueError("samples must be finite")
    return signal


def band_energy(magnitude: np.ndarray, low_hz: float, high_hz: float) -> float:
    """C-compatible sum of squared magnitude bins (floor/ceil, inclusive)."""
    low = max(int(np.floor(low_hz / BIN_HZ)), 1)
    high = min(int(np.ceil(high_hz / BIN_HZ)), NUM_BINS - 1)
    if high < low:
        return 0.0
    return float(np.sum(magnitude[low:high + 1] ** 2))


def rotation_measure(magnitude: np.ndarray, rated_hz: float,
                     low_ratio: float = 0.6, high_ratio: float = 1.4) -> tuple[float, float]:
    """Measure a rotation peak with parabolic sub-bin interpolation and SNR."""
    if not math.isfinite(rated_hz) or rated_hz <= 0:
        raise ValueError("rated_hz must be finite and positive")
    low = max(int(np.floor(rated_hz * low_ratio / BIN_HZ)), 1)
    high = min(int(np.ceil(rated_hz * high_ratio / BIN_HZ)), NUM_BINS - 2)
    if high <= low + 4:
        return rated_hz, 0.0
    peak = low + int(np.argmax(magnitude[low:high + 1]))
    mask = np.ones(high - low + 1, dtype=bool)
    for index in range(peak - 2, peak + 3):
        if low <= index <= high:
            mask[index - low] = False
    floor_bins = magnitude[low:high + 1][mask]
    floor_mean = float(np.mean(floor_bins)) if len(floor_bins) else 0.0
    snr = float(magnitude[peak] / floor_mean) if floor_mean > 1.0e-20 else 0.0
    y0, y1, y2 = magnitude[peak - 1:peak + 2]
    denominator = float(y0 - 2.0 * y1 + y2)
    delta = 0.0
    if abs(denominator) > 1.0e-12:
        delta = float(np.clip(0.5 * (y0 - y2) / denominator, -0.5, 0.5))
    return (peak + delta) * BIN_HZ, snr


def _features_from_spectrum(magnitude: np.ndarray, rms: float, rated_hz: float,
                            feature_rotation_hz: float, snr: float) -> dict[str, float]:
    width = HARMONIC_BW_HZ
    total = float(np.sum(magnitude[1:] ** 2))
    if total < 1.0e-20:
        total = 1.0e-20
    # The C feature layer falls back to rated Hz until a tracker has a value.
    rotation_hz = feature_rotation_hz if feature_rotation_hz > 0.0 else rated_hz
    high_low = rated_hz * HF_CUTOFF_RATIO
    high_high = min(SAMPLE_RATE_HZ * 0.5, SENSOR_BW_HZ - 5.0)
    values = {
        "rms": rms,
        "harmonic1_ratio": band_energy(magnitude, rotation_hz - width, rotation_hz + width) / total,
        "harmonic2_ratio": band_energy(magnitude, 2.0 * rotation_hz - width, 2.0 * rotation_hz + width) / total,
        "harmonic3_ratio": band_energy(magnitude, 3.0 * rotation_hz - width, 3.0 * rotation_hz + width) / total,
        "high_freq_ratio": band_energy(magnitude, high_low, high_high) / total,
        "rotation_hz": float(rotation_hz),
        "rotation_snr": float(snr),
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("feature extraction produced a non-finite value")
    return values


def extract_features(samples: np.ndarray, rated_hz: float = 50.0, *,
                     tracked_hz: float | None = None) -> dict[str, float]:
    """Extract selected features from one conditioned window.

    Without ``tracked_hz`` this is a stateless helper and centers harmonics on
    the current window's peak estimate. The original C pipeline is stateful:
    use :class:`V2FeaturePipeline` when matching its jump rejection/re-lock
    behavior, or pass an already accepted C-compatible ``tracked_hz`` value.
    """
    if not math.isfinite(rated_hz) or rated_hz <= 0:
        raise ValueError("rated_hz must be finite and positive")
    if tracked_hz is not None and (not math.isfinite(tracked_hz) or tracked_hz < 0):
        raise ValueError("tracked_hz must be finite and nonnegative")
    rms, magnitude = fft_spectrum(samples)
    candidate_hz, snr = rotation_measure(magnitude, rated_hz)
    feature_hz = candidate_hz if tracked_hz is None else float(tracked_hz)
    return _features_from_spectrum(magnitude, rms, rated_hz, feature_hz, snr)


class RotationTracker:
    """Python transcription of ``em_rotation_accept`` state and thresholds."""

    def __init__(self, max_jump_hz: float = 5.0, relock_windows: int = 5):
        if not math.isfinite(max_jump_hz) or max_jump_hz <= 0:
            raise ValueError("max_jump_hz must be finite and positive")
        if isinstance(relock_windows, bool) or not isinstance(relock_windows, int) or relock_windows < 1:
            raise ValueError("relock_windows must be a positive integer")
        self.max_jump_hz = float(max_jump_hz)
        self.relock_windows = relock_windows
        self.hz = 0.0
        self.valid = False
        self.pending_hz = 0.0
        self.rejected = 0

    def reset(self) -> None:
        self.hz = 0.0
        self.valid = False
        self.pending_hz = 0.0
        self.rejected = 0

    def accept(self, candidate_hz: float) -> float:
        """Accept a nearby candidate or hold/re-lock after repeated jumps.

        The first candidate is accepted. A candidate within ``max_jump_hz``
        is followed immediately and clears pending rejection state. A larger
        jump holds the previous Hz until candidates stay within that same jump
        distance of the pending candidate for ``relock_windows`` calls.
        """
        candidate = float(candidate_hz)
        if not math.isfinite(candidate) or candidate <= 0:
            raise ValueError("candidate_hz must be finite and positive")
        if not self.valid:
            self.hz = candidate
            self.valid = True
            return self.hz
        if abs(candidate - self.hz) <= self.max_jump_hz:
            self.hz = candidate
            self.rejected = 0
            self.pending_hz = 0.0
            return self.hz
        if self.rejected > 0 and abs(candidate - self.pending_hz) <= self.max_jump_hz:
            self.rejected += 1
        else:
            self.rejected = 1
        self.pending_hz = candidate
        if self.rejected >= self.relock_windows:
            self.hz = candidate
            self.rejected = 0
            self.pending_hz = 0.0
        return self.hz


class V2FeaturePipeline:
    """Stateful run gating, rotation tracking, and feature extraction.

    Mirrors the C pipeline boundary: candidate/SNR is measured each window;
    run/stop state uses consecutive-window hysteresis; rotation updates only
    while running; harmonic features use accepted rotation Hz (or rated-Hz
    fallback before the first accepted estimate). Baseline fitting and fault
    decisions remain separate caller responsibilities.
    """

    def __init__(self, rated_hz: float = 50.0, *, run_snr_threshold: float = 5.0,
                 run_hysteresis: int = 3, max_jump_hz: float = 5.0,
                 relock_windows: int = 5):
        if not math.isfinite(rated_hz) or rated_hz <= 0:
            raise ValueError("rated_hz must be finite and positive")
        if not math.isfinite(run_snr_threshold) or run_snr_threshold < 0:
            raise ValueError("run_snr_threshold must be finite and nonnegative")
        if isinstance(run_hysteresis, bool) or not isinstance(run_hysteresis, int) or run_hysteresis < 1:
            raise ValueError("run_hysteresis must be a positive integer")
        self.rated_hz = float(rated_hz)
        self.run_snr_threshold = float(run_snr_threshold)
        self.run_hysteresis = run_hysteresis
        self.rotation = RotationTracker(max_jump_hz, relock_windows)
        self.running = False
        self.run_streak = 0
        self.stop_streak = 0

    def reset(self) -> None:
        """Reset a stream/session, including run and accepted-rotation state."""
        self.rotation.reset()
        self.running = False
        self.run_streak = 0
        self.stop_streak = 0

    def process_window(self, samples: np.ndarray) -> dict[str, float | bool]:
        """Return this window's candidate, accepted rotation, run flag, features."""
        rms, magnitude = fft_spectrum(samples)
        candidate_hz, snr = rotation_measure(magnitude, self.rated_hz)
        just_started = False
        if snr >= self.run_snr_threshold:
            self.run_streak += 1
            self.stop_streak = 0
            if not self.running and self.run_streak >= self.run_hysteresis:
                self.running = True
                just_started = True
        else:
            self.stop_streak += 1
            self.run_streak = 0
            if self.running and self.stop_streak >= self.run_hysteresis:
                self.running = False

        if self.running:
            self.rotation.accept(candidate_hz)
        tracked_hz = self.rotation.hz
        features = _features_from_spectrum(magnitude, rms, self.rated_hz, tracked_hz, snr)
        return {**features, "candidate_hz": candidate_hz,
                "rotation_hz": tracked_hz, "rotation_snr": snr,
                "running": self.running, "just_started": just_started}


def feature_vector(features: dict[str, float] | np.ndarray) -> np.ndarray:
    """Return the configured 5-feature order and reject malformed values."""
    if isinstance(features, dict):
        try:
            vector = np.asarray([features[name] for name in FEATURE_NAMES], dtype=np.float64)
        except KeyError as error:
            raise ValueError(f"missing feature: {error.args[0]}") from error
    else:
        vector = np.asarray(features, dtype=np.float64)
    if vector.shape != (len(FEATURE_NAMES),) or not np.isfinite(vector).all():
        raise ValueError("features must contain exactly five finite values")
    if (vector < 0).any():
        raise ValueError("V2 features must be nonnegative")
    return vector


def to_z_space(features: dict[str, float] | np.ndarray) -> np.ndarray:
    """Apply C detector transform ``log(max(x, 0) + 1e-6)``."""
    return np.log(feature_vector(features) + LOG_EPS)


@dataclass(frozen=True)
class NormalBaseline:
    mean: tuple[float, ...]
    std: tuple[float, ...]
    sample_count: int
    accepted_class: str = "normal"


def fit_normal_baseline(accepted_normal_features: np.ndarray,
                        expected_windows: int = CALIBRATION_WINDOWS) -> NormalBaseline:
    """Fit sample mean/std in log space from accepted NORMAL feature rows only.

    Running-state and warmup filtering must happen before this function. For the
    selected AI-Hub path, the target was 120 accepted normal windows.
    """
    if expected_windows < 2:
        raise ValueError("expected_windows must be at least two")
    values = np.asarray(accepted_normal_features, dtype=np.float64)
    if values.shape != (expected_windows, len(FEATURE_NAMES)):
        raise ValueError(f"expected {expected_windows} accepted normal feature rows")
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("normal feature rows must be finite and nonnegative")
    transformed = np.log(values + LOG_EPS)
    mean = transformed.mean(axis=0)
    std = transformed.std(axis=0, ddof=1)
    floor = np.maximum(np.abs(mean) * MIN_REL_STD, STD_FLOOR)
    std = np.maximum(std, floor)
    return NormalBaseline(tuple(map(float, mean)), tuple(map(float, std)), expected_windows)


@dataclass(frozen=True)
class InstantVerdict:
    valid: bool
    flag: bool | None
    z: tuple[float, ...] | None
    deviated_features: int | None
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def instant_from_z(z: dict[str, float] | list[float] | tuple[float, ...] | np.ndarray,
                   sigma: float = SELECTED_SIGMA,
                   minimum_deviated_features: int = SELECTED_MIN_DEVIATED_FEATURES) -> InstantVerdict:
    """Apply the selected strict ``abs(z) > sigma`` instant rule."""
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError("sigma must be finite and positive")
    if minimum_deviated_features < 1 or minimum_deviated_features > len(FEATURE_NAMES):
        raise ValueError("minimum_deviated_features must be in [1, 5]")
    if isinstance(z, dict):
        try:
            vector = np.asarray([z[name] for name in FEATURE_NAMES], dtype=np.float64)
        except KeyError as error:
            raise ValueError(f"missing z feature: {error.args[0]}") from error
    else:
        vector = np.asarray(z, dtype=np.float64)
    if vector.shape != (len(FEATURE_NAMES),) or not np.isfinite(vector).all():
        raise ValueError("z must contain exactly five finite values")
    count = int(np.count_nonzero(np.abs(vector) > sigma))
    return InstantVerdict(True, count >= minimum_deviated_features, tuple(map(float, vector)), count)


def instant_from_features(features: dict[str, float] | np.ndarray, baseline: NormalBaseline,
                          sigma: float = SELECTED_SIGMA,
                          minimum_deviated_features: int = SELECTED_MIN_DEVIATED_FEATURES) -> InstantVerdict:
    """Transform current features, standardize against the frozen normal model, and judge."""
    vector = feature_vector(features)
    if len(baseline.mean) != 5 or len(baseline.std) != 5 or baseline.sample_count < 2:
        raise ValueError("invalid normal baseline")
    mean = np.asarray(baseline.mean, dtype=np.float64)
    std = np.asarray(baseline.std, dtype=np.float64)
    if (std <= 0).any() or not np.isfinite(mean).all() or not np.isfinite(std).all():
        raise ValueError("baseline statistics must be finite with positive standard deviations")
    z = (np.log(vector + LOG_EPS) - mean) / std
    return instant_from_z(z, sigma, minimum_deviated_features)


@dataclass(frozen=True)
class V2WindowVerdict:
    instant: InstantVerdict
    confirmed_valid: bool
    is_anomaly: bool | None
    votes_positive: int
    votes_valid: int


class V2FourOfFive:
    """Selected rolling 4/5 rule; invalid windows do not vote or erase history.

    Call ``reset`` at an explicit new stream/file boundary or a run-state restart,
    matching the C pipeline. This is separate from the belt 20-window candidate.
    """
    def __init__(self, window_size: int = SELECTED_CONFIRM_WINDOW,
                 required_hits: int = SELECTED_CONFIRM_HITS):
        if window_size <= 0 or not 0 < required_hits <= window_size:
            raise ValueError("required_hits must be within window_size")
        self.window_size = window_size
        self.required_hits = required_hits
        self._history: Deque[bool] = deque(maxlen=window_size)

    def reset(self) -> None:
        self._history.clear()

    def update(self, instant: InstantVerdict) -> V2WindowVerdict:
        if not instant.valid:
            return V2WindowVerdict(instant, False, None, sum(self._history), len(self._history))
        if instant.flag is None:
            raise ValueError("valid instant verdict requires a boolean flag")
        self._history.append(instant.flag)
        mature = len(self._history) == self.window_size
        return V2WindowVerdict(instant, mature,
                               sum(self._history) >= self.required_hits if mature else None,
                               sum(self._history), len(self._history))


def unavailable(reason: str = "upstream_window_invalid") -> InstantVerdict:
    """Construct an explicit abstention for a non-running, warmup, or invalid window."""
    if not reason:
        raise ValueError("unavailable reason must be non-empty")
    return InstantVerdict(False, None, None, None, reason)
