"""Bits several pages share: Persian names of NBO statuses and decisions, a scrolling page frame."""
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ...texts import ACTION_FA
from ...workboard import STATE_FA
from .. import theme
from ..theme import C

NBO_STATUS_FA = {
    "PENDING": "در انتظار بررسی", "COMMERCIAL_IN_PROGRESS": "در حال بررسی تجاری", "COMMERCIAL_APPROVED": "تایید تجاری",
    "ACTIVATING": "در حال فعال‌سازی", "PENDING_ACTIVATION": "در انتظار فعال‌سازی", "COMPLETED": "تکمیل‌شده",
    "REQUIRED_EDITING": "نیاز به اصلاح", "CANCELLED": "لغوشده", "DRAFT": "پیش‌نویس",
}
STATE_COLORS = {k: v[0] for k, v in theme.STATE.items()}
STATE_COLORS["DUPLICATE"] = "#8E3B9C"
ACTION_COLORS = {k: v[0] for k, v in theme.ACTION.items()}


def nbo_status_fa(code):
    return f"{NBO_STATUS_FA.get(code, code)}"


def state_items(counts):
    order = ("NOT_REVIEWED", "DUPLICATE", "APPROVE", "EDIT", "CANCEL", "MANUAL_OPEN", "MANUAL_DONE")
    return [(STATE_FA[k], counts.get(k, 0), STATE_COLORS[k]) for k in order]


class ScrollPage(QWidget):
    """A page whose content scrolls; self.body is the column to fill."""

    title = ""
    subtitle = ""

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("page")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(0, 0, 6, 12)
        self.body.setSpacing(16)
        area.setWidget(inner)
        outer.addWidget(area)


__all__ = ["ACTION_FA", "ACTION_COLORS", "C", "NBO_STATUS_FA", "STATE_COLORS", "ScrollPage", "nbo_status_fa", "state_items"]
