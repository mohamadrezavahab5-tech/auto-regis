"""Bits several pages share: Persian names of NBO statuses and decisions, a scrolling page frame."""
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ...texts import ACTION_FA
from ...workboard import STATE_FA
from .. import theme
from ..theme import C

# NBO's own status names, written the way NBO shows them (owner 2026-10-02: English where Persian is not needed)
NBO_STATUS_FA = {
    "PENDING": "Pending", "COMMERCIAL_IN_PROGRESS": "Commercial in progress", "COMMERCIAL_APPROVED": "Approved",
    "ACTIVATING": "Activating", "PENDING_ACTIVATION": "Pending activation", "COMPLETED": "Completed",
    "REQUIRED_EDITING": "Required editing", "CANCELLED": "Cancelled", "DRAFT": "Draft",
    "LEFT_QUEUE": "از صف بیرون رفته — وضعیت دقیق در دریافت کامل بعدی",
}
STATE_COLORS = {k: v[0] for k, v in theme.STATE.items()}
STATE_COLORS["DUPLICATE"] = theme.C["duplicate"]
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


def related_card(rel):
    """'Same owner / same bank account' for one request (reference.related), or None when it has no siblings."""
    import html
    from ..widgets import Card, label, ltr, num
    if not rel or not (rel["same_owner"] or rel["same_iban"]):
        return None
    card = Card(soft=True)
    card.lay.addWidget(label("درخواست‌های مرتبط", "h3"))

    def lines(title, items):
        if not items:
            return
        card.lay.addWidget(label(f"{title}: {num(len(items))}", "muted"))
        for smr, status, site in items:
            card.lay.addWidget(label(f"• {ltr(smr)} — {nbo_status_fa(status)} — {ltr(html.escape(site or ''))}", "caption", wrap=True,
                                     selectable=True))
    lines("همین صاحب (کد ملی)", rel["same_owner"])
    lines("همین حساب بانکی (شبا)", rel["same_iban"])
    if rel.get("iban_other_owner"):
        card.lay.addWidget(label(f"<span style='color:{theme.C['danger']}'>این شبا در درخواست صاحب دیگری هم آمده؛ دقیق‌تر بررسی شود.</span>",
                                 wrap=True))
    return card
