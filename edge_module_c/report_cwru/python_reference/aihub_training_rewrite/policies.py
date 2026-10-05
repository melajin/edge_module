"""Pure stateful vote and V3 evidence policy, independent of signal features."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Deque, Dict, Literal, Optional


Status = Literal["normal", "confirmed", "suspected", "inspection_required", "unavailable"]
CONFIRMABLE_FAULTS = ("bearing", "misalignment", "belt")


@dataclass(frozen=True)
class Evidence:
    """Generic single stream evidence. Invalid is abstention, never a negative."""
    valid: bool
    flag: bool | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.valid) is not bool:
            raise TypeError("valid must be bool")
        if self.valid and type(self.flag) is not bool:
            raise ValueError("valid evidence requires a bool flag")
        if not self.valid and self.flag is not None:
            raise ValueError("invalid evidence must not carry a verdict")


@dataclass(frozen=True)
class VoteResult:
    status: Status
    votes_positive: int
    votes_valid: int
    window_size: int
    abstention_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class FourOfFive:
    """Reusable rolling vote; invalid inputs preserve votes and return unavailable."""
    def __init__(self, window_size: int = 5, required_votes: int = 4):
        if window_size <= 0 or not 0 < required_votes <= window_size:
            raise ValueError("required_votes must be within the positive window_size")
        self.window_size, self.required_votes = window_size, required_votes
        self._history: Deque[bool] = deque(maxlen=window_size)

    def reset(self) -> None:
        self._history.clear()

    def update(self, evidence: Evidence) -> VoteResult:
        if evidence.valid:
            self._history.append(bool(evidence.flag))
        if not evidence.valid:
            return VoteResult("unavailable", sum(self._history), len(self._history), self.window_size,
                              evidence.reason or "current_window_invalid")
        if len(self._history) < self.window_size:
            return VoteResult("unavailable", sum(self._history), len(self._history), self.window_size,
                              "confirmation_buffer_incomplete")
        positive = sum(self._history) >= self.required_votes
        return VoteResult("confirmed" if positive else "normal", sum(self._history), len(self._history), self.window_size)


@dataclass(frozen=True)
class InstantEvidence:
    """One upstream per-fault instant verdict; flag is ignored only when invalid."""
    valid: bool
    flag: Optional[bool] = None

    def __post_init__(self) -> None:
        if type(self.valid) is not bool:
            raise TypeError("valid must be a bool")
        if self.valid and self.flag is None:
            raise ValueError("valid instant evidence requires a boolean flag")
        if self.flag is not None and type(self.flag) is not bool:
            raise TypeError("flag must be a bool or None")


@dataclass(frozen=True)
class ImbalanceEvidence:
    """One RPM-synchronous 1x observation against an equipment normal baseline."""
    rpm_available: bool
    baseline_available: bool
    amplitude_ratio_1x: Optional[float] = None
    phase_concentration: Optional[float] = None

    @property
    def valid(self) -> bool:
        return (self.rpm_available and self.baseline_available
                and self.amplitude_ratio_1x is not None and self.phase_concentration is not None)

    def __post_init__(self) -> None:
        if type(self.rpm_available) is not bool or type(self.baseline_available) is not bool:
            raise TypeError("availability fields must be bools")
        if self.amplitude_ratio_1x is not None:
            if isinstance(self.amplitude_ratio_1x, bool) or not isinstance(self.amplitude_ratio_1x, (int, float)):
                raise TypeError("amplitude_ratio_1x must be a number or None")
            if not isfinite(self.amplitude_ratio_1x) or self.amplitude_ratio_1x < 0:
                raise ValueError("amplitude_ratio_1x must be finite and non-negative")
        if self.phase_concentration is not None:
            if isinstance(self.phase_concentration, bool) or not isinstance(self.phase_concentration, (int, float)):
                raise TypeError("phase_concentration must be a number or None")
            if not isfinite(self.phase_concentration) or not 0 <= self.phase_concentration <= 1:
                raise ValueError("phase_concentration must be finite and in [0, 1]")


@dataclass(frozen=True)
class FaultResult:
    status: Status
    votes_positive: int
    votes_valid: int
    window_size: int
    evidence: dict

    def to_dict(self) -> dict:
        return asdict(self)


class V3Diagnostic:
    """The existing V3 policy over caller evidence; it does not extract features."""
    def __init__(self, *, window_size: int = 5, required_votes: int = 4,
                 imbalance_amplitude_ratio: float = 1.1,
                 imbalance_phase_concentration: float = 0.0):
        if window_size <= 0 or not 0 < required_votes <= window_size:
            raise ValueError("required_votes must be between 1 and window_size")
        if isinstance(imbalance_amplitude_ratio, bool) or not isinstance(imbalance_amplitude_ratio, (int, float)):
            raise TypeError("imbalance_amplitude_ratio must be a number")
        if not isfinite(imbalance_amplitude_ratio) or imbalance_amplitude_ratio <= 0:
            raise ValueError("imbalance_amplitude_ratio must be finite and positive")
        if isinstance(imbalance_phase_concentration, bool) or not isinstance(imbalance_phase_concentration, (int, float)):
            raise TypeError("imbalance_phase_concentration must be a number")
        if not isfinite(imbalance_phase_concentration) or not 0 <= imbalance_phase_concentration <= 1:
            raise ValueError("imbalance_phase_concentration must be finite and in [0, 1]")
        self.window_size = window_size
        self.required_votes = required_votes
        self.imbalance_amplitude_ratio = float(imbalance_amplitude_ratio)
        self.imbalance_phase_concentration = float(imbalance_phase_concentration)
        self._history: Dict[tuple[str, str], Dict[str, Deque[bool]]] = {}

    def reset_session(self, equipment_id: str, session_id: str) -> None:
        self._history.pop(self._key(equipment_id, session_id), None)

    def reset_equipment(self, equipment_id: str) -> None:
        for key in [key for key in self._history if key[0] == equipment_id]:
            del self._history[key]

    def update(self, *, equipment_id: str, session_id: str,
               bearing: InstantEvidence, misalignment: InstantEvidence,
               belt: InstantEvidence, imbalance: ImbalanceEvidence) -> dict[str, FaultResult]:
        key = self._key(equipment_id, session_id)
        histories = self._history.setdefault(
            key, {name: deque(maxlen=self.window_size) for name in (*CONFIRMABLE_FAULTS, "imbalance")})
        inputs = {"bearing": bearing, "misalignment": misalignment, "belt": belt}
        results: dict[str, FaultResult] = {}
        for name, instant in inputs.items():
            if instant.valid:
                histories[name].append(bool(instant.flag))
            results[name] = self._confirmed_result(name, histories[name], instant)

        if imbalance.valid:
            positive = (float(imbalance.amplitude_ratio_1x) >= self.imbalance_amplitude_ratio
                        and float(imbalance.phase_concentration) >= self.imbalance_phase_concentration)
            histories["imbalance"].append(positive)
        results["imbalance"] = self._imbalance_result(
            histories["imbalance"], imbalance, results["misalignment"].status)
        return results

    def _confirmed_result(self, name: str, history: Deque[bool], instant: InstantEvidence) -> FaultResult:
        votes = sum(history)
        mature = len(history) == self.window_size
        if not instant.valid or not mature:
            status: Status = "unavailable"
        else:
            status = "confirmed" if votes >= self.required_votes else "normal"
        return FaultResult(status, votes, len(history), self.window_size, {
            "fault": name,
            "instant_valid": instant.valid,
            "instant_flag": bool(instant.flag) if instant.valid else None,
            "rule": f"{self.required_votes}-of-{self.window_size} valid windows",
        })

    def _imbalance_result(self, history: Deque[bool], evidence: ImbalanceEvidence,
                          misalignment_status: Status) -> FaultResult:
        votes = sum(history)
        mature = len(history) == self.window_size
        if not evidence.valid or not mature:
            status: Status = "unavailable"
        elif votes < self.required_votes:
            status = "normal"
        elif misalignment_status == "confirmed":
            status = "inspection_required"
        else:
            status = "suspected"
        return FaultResult(status, votes, len(history), self.window_size, {
            "auto_confirmation_allowed": False,
            "rpm_available": evidence.rpm_available,
            "baseline_available": evidence.baseline_available,
            "amplitude_ratio_1x": evidence.amplitude_ratio_1x,
            "phase_concentration": evidence.phase_concentration,
            "amplitude_ratio_threshold": self.imbalance_amplitude_ratio,
            "phase_concentration_threshold": self.imbalance_phase_concentration,
            "misalignment_status": misalignment_status,
            "rule": f"warning only after {self.required_votes}-of-{self.window_size} valid windows",
        })

    @staticmethod
    def _key(equipment_id: str, session_id: str) -> tuple[str, str]:
        if not equipment_id or not session_id:
            raise ValueError("equipment_id and session_id are required")
        return equipment_id, session_id


def imbalance_policy(*, amplitude_ratio_1x: float | None, phase_concentration: float | None,
                     rpm_available: bool, baseline_available: bool, recent_positive_votes: int,
                     vote_count: int = 5, required_votes: int = 4,
                     amplitude_threshold: float = 1.1, phase_threshold: float = 0.0,
                     misalignment_status: Status = "unavailable") -> VoteResult:
    """Single-result helper when the caller owns the rolling positive-vote count."""
    if not rpm_available or not baseline_available or amplitude_ratio_1x is None or phase_concentration is None:
        return VoteResult("unavailable", recent_positive_votes, 0, vote_count,
                          "rpm_baseline_or_1x_evidence_unavailable")
    values = (amplitude_ratio_1x, phase_concentration, amplitude_threshold, phase_threshold)
    if not all(isinstance(v, (float, int)) and not isinstance(v, bool) and isfinite(v) for v in values):
        raise ValueError("imbalance evidence and thresholds must be finite numbers")
    if amplitude_ratio_1x < 0 or not 0 <= phase_concentration <= 1 or not 0 <= phase_threshold <= 1:
        raise ValueError("amplitude must be nonnegative and phase concentration in [0, 1]")
    if not 0 < required_votes <= vote_count or not 0 <= recent_positive_votes <= vote_count:
        raise ValueError("invalid vote counts")
    current_positive = amplitude_ratio_1x >= amplitude_threshold and phase_concentration >= phase_threshold
    if not current_positive or recent_positive_votes < required_votes:
        state: Status = "normal"
    elif misalignment_status == "confirmed":
        state = "inspection_required"
    else:
        state = "suspected"
    return VoteResult(state, recent_positive_votes, vote_count, vote_count,
                      "imbalance_is_advisory_only" if state in ("suspected", "inspection_required") else None)
