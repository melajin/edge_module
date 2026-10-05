"""Label, input, and abstention contracts shared by the reference replays."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AIHUB_LABELS = {
    "정상": "normal",
    "베어링불량": "bearing",
    "회전체불평형": "imbalance",
    "축정렬불량": "misalignment",
    "벨트느슨함": "belt_looseness",
}
TAXONOMY = ("imbalance", "misalignment", "mechanical_looseness", "bearing", "belt_looseness")
UNSUPPORTED_LABELS = {
    "mechanical_looseness": "공개 trace에 독립 라벨과 별도 평가가 없음; 혼합 rotate type2/type3로 대체하지 않음"
}

Status = Literal["normal", "confirmed", "suspected", "inspection_required", "unavailable"]


@dataclass(frozen=True)
class Evidence:
    """A verdict slot whose invalid state is an abstention, never a negative."""

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


def ai_hub_label(source_label: str) -> str | None:
    """Map the four independent AI-Hub faults; mechanical looseness stays unmapped."""
    return AIHUB_LABELS.get(source_label)

