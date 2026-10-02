"""Shared building blocks. Every page is assembled from these, so the app has one visual language.

Charts are painted directly (not QtCharts): Persian right-to-left labels, the app's fonts and colours, and short grow-in
animations that replay only when the numbers change."""
from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..jalali import fa_digits
from . import icons, theme
from .theme import C


# ---- small helpers ---------------------------------------------------------------------------------------------------------
RLM = "‏"


def rtl(text) -> str:
    """Force a right-to-left paragraph: Qt picks the direction from the first strong letter, so 'NBO: همین حالا' would
    otherwise be laid out left-to-right and read in the wrong order."""
    s = "" if text is None else str(text)
    return s if not s or s.startswith(RLM) else RLM + s


def ltr(text) -> str:
    """Isolate a left-to-right value (URL, code, e-mail) inside right-to-left text, so 'https://shop.ir/' keeps its order."""
    s = "" if text is None else str(text)
    return f"⁦{s}⁩" if s else s


class FaLabel(QLabel):
    def __init__(self, text=""):
        super().__init__(rtl(text))

    def setText(self, text):
        super().setText(rtl(text))


def label(text="", role=None, wrap=False, selectable=False) -> QLabel:
    lab = FaLabel(text)
    if role:
        lab.setObjectName(role)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def button(text="", kind=None, icon=None, tooltip=None, icon_color=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon:
        color = icon_color or (C["on_accent"] if kind == "primary" else C["text2"])
        b.setIcon(icons.icon(icon, color, 16))
        b.setIconSize(QSize(16, 16))
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("hline")
    return f


def num(n, decimals=0) -> str:
    """12345 -> '12,345', 9.8 -> '9.8' (Persian digits and separators only when jalali.DIGITS = 'persian')."""
    from .. import jalali
    if n is None:
        return "—"
    s = f"{n:,.{decimals}f}" if decimals else f"{int(round(n)):,}"
    if jalali.DIGITS == "persian":
        s = s.replace(",", "٬").replace(".", "٫")
    return fa_digits(s)


class Card(QFrame):
    def __init__(self, parent=None, soft=False, padding=18, spacing=12):
        super().__init__(parent)
        self.setProperty("card", "soft" if soft else "true")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding - 2, padding, padding)
        self.lay.setSpacing(spacing)

    def header(self, title, subtitle=None, right=None):
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title, "h2"))
        if subtitle:
            col.addWidget(label(subtitle, "caption", wrap=True))
        row.addLayout(col, 1)
        if right is not None:
            if isinstance(right, (list, tuple)):
                for w in right:
                    row.addWidget(w)
            else:
                row.addWidget(right)
        self.lay.addLayout(row)
        return row


class Pill(FaLabel):
    """A small coloured status label ('وصل', 'قدیمی', 'تایید' ...)."""

    def __init__(self, text="", fg=None, bg=None):
        super().__init__(text)
        self.setObjectName("pill")
        self.set(text, fg, bg)

    def set(self, text, fg=None, bg=None):
        self.setText(text)
        self.setStyleSheet(f"color: {fg or C['text2']}; background: {bg or C['surface2']};")


def action_pill(action: str, text: str) -> Pill:
    fg, bg = theme.ACTION.get(action, (C["text2"], C["surface2"]))
    return Pill(text, fg, bg)


class IconLabel(QLabel):
    def __init__(self, name, color=None, size=18):
        super().__init__()
        self.setPixmap(icons.pixmap(name, color or C["text2"], size))
        self.setFixedSize(size + 2, size + 2)


# ---- animation mixin -------------------------------------------------------------------------------------------------------
class _Animated(QWidget):
    """progress 0 -> 1 when new data arrives (never on every repaint)."""

    def __init__(self, parent=None, duration=650):
        super().__init__(parent)
        self._p = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(duration)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._p = float(v)
        self.update()

    def animate(self):
        self._anim.stop()
        self._p = 0.0
        self._anim.start()


# ---- big numbers -----------------------------------------------------------------------------------------------------------
class StatTile(QFrame):
    """A number that counts up to its new value, with a caption and an optional small line under it."""

    def __init__(self, caption, color=None, soft=None, icon=None, parent=None):
        super().__init__(parent)
        self.setProperty("card", "true")
        self.setMinimumWidth(150)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        if icon:
            badge = QLabel()
            badge.setFixedSize(28, 28)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setPixmap(icons.pixmap(icon, color or C["accent"], 16))
            badge.setStyleSheet(f"background: {soft or C['accent_soft']}; border-radius: 14px;")
            top.addWidget(badge)
        cap = label(caption, "muted")
        top.addWidget(cap, 1)
        lay.addLayout(top)
        self.value = label("—", "bigNumber")
        self.value.setStyleSheet(f"color: {color or C['text']};")
        lay.addWidget(self.value)
        self.sub = label("", "caption", wrap=True)
        lay.addWidget(self.sub)
        self._shown = 0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.value.setText(num(int(v))))

    def set_value(self, n, sub=None):
        if sub is not None:
            self.sub.setText(sub)
        if n is None:
            self._anim.stop()
            self.value.setText("—")
            return
        if n == self._shown:
            self.value.setText(num(int(n)))                 # a real 0 must read 0, never the 'no data' dash
            return
        self._anim.stop()
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(int(n))
        self._shown = int(n)
        self._anim.start()


# ---- charts ----------------------------------------------------------------------------------------------------------------
class SegmentBar(_Animated):
    """One horizontal bar split into coloured parts (right-to-left), e.g. today's backlog by state."""

    def __init__(self, height=16, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.parts = []                                     # [(count, color)]

    def set_parts(self, parts):
        if parts != self.parts:
            self.parts = list(parts)
            self.animate()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setClipPath(clip)
        p.fillRect(r, QColor(C["pending_soft"]))
        total = sum(c for c, _ in self.parts)
        if total:
            x = r.right()
            for count, color in self.parts:
                w = r.width() * count / total * self._p
                p.fillRect(QRectF(x - w, r.top(), w, r.height()), QColor(color))
                x -= w
        p.end()


class Legend(QWidget):
    """Colour dot + label + count chips that wrap onto the next line when narrow."""

    def __init__(self, parent=None, show_counts=True):
        super().__init__(parent)
        self.show_counts = show_counts
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(16)

    def set_items(self, items):                             # [(label, count, color)]
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for text, count, color in items:
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
            h.addWidget(dot)
            h.addWidget(label(text, "muted"))
            if self.show_counts:
                h.addWidget(label(num(count), "h3"))
            self.lay.addWidget(box)
        self.lay.addStretch(1)


class Ring(_Animated):
    """Progress ring with the percentage in the middle (and a caption under it)."""

    def __init__(self, size=132, color=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.value, self.caption = 0.0, ""
        self.color = color or C["accent"]

    def set_value(self, percent, caption=""):
        changed = abs(percent - self.value) > 0.05 or caption != self.caption
        self.value, self.caption = percent, caption
        if changed:
            self.animate()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = 11
        r = QRectF(w / 2 + 2, w / 2 + 2, self.width() - w - 4, self.height() - w - 4)
        p.setPen(QPen(QColor(C["pending_soft"]), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(r, 0, 360 * 16)
        span = -int(360 * 16 * min(self.value, 100) / 100 * self._p)
        if span:
            p.setPen(QPen(QColor(self.color), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(r, 90 * 16, span)
        p.setPen(QColor(C["text"]))
        p.setFont(theme.font(24, QFont.Weight.Bold))
        p.drawText(QRectF(0, self.height() / 2 - 26, self.width(), 32), Qt.AlignmentFlag.AlignCenter,
                   fa_digits(f"{self.value * self._p:.0f}") + "%")
        if self.caption:
            p.setPen(QColor(C["text3"]))
            p.setFont(theme.font(11))
            p.drawText(QRectF(0, self.height() / 2 + 6, self.width(), 18), Qt.AlignmentFlag.AlignCenter, self.caption)
        p.end()


class Donut(_Animated):
    """Share of each decision; the total sits in the middle."""

    def __init__(self, size=170, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.parts, self.center_caption = [], "مجموع"

    def set_parts(self, parts, caption="مجموع"):          # [(count, color)]
        if parts != self.parts or caption != self.center_caption:
            self.parts, self.center_caption = list(parts), caption
            self.animate()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = 22
        r = QRectF(w / 2 + 2, w / 2 + 2, self.width() - w - 4, self.height() - w - 4)
        total = sum(c for c, _ in self.parts)
        p.setPen(QPen(QColor(C["pending_soft"]), w))
        p.drawArc(r, 0, 360 * 16)
        if total:
            start = 90 * 16
            sweep_all = 360 * 16 * self._p
            for count, color in self.parts:
                if not count:
                    continue
                span = sweep_all * count / total
                p.setPen(QPen(QColor(color), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawArc(r, int(start), -int(span))
                start -= span
        p.setPen(QColor(C["text"]))
        p.setFont(theme.font(26, QFont.Weight.Bold))
        p.drawText(QRectF(0, self.height() / 2 - 28, self.width(), 34), Qt.AlignmentFlag.AlignCenter, num(total))
        p.setPen(QColor(C["text3"]))
        p.setFont(theme.font(11))
        p.drawText(QRectF(0, self.height() / 2 + 6, self.width(), 18), Qt.AlignmentFlag.AlignCenter, self.center_caption)
        p.end()


class BarList(_Animated):
    """Ranked horizontal bars: label on the right, bar growing to the left, value at the bar's end."""

    ROW = 30

    def __init__(self, color=None, parent=None):
        super().__init__(parent)
        self.items, self.color = [], color or C["accent"]
        self.empty_text = "داده‌ای نیست"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.ROW * 3)

    def set_items(self, items):                             # [(label, value, color|None)]
        if items != self.items:
            self.items = list(items)
            self.setFixedHeight(max(3, len(self.items)) * self.ROW)
            self.animate()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.items:
            p.setPen(QColor(C["text3"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            p.end()
            return
        font = theme.font(12)
        fm = QFontMetrics(font)
        label_w = min(int(self.width() * 0.46), max(fm.horizontalAdvance(t) for t, _, _ in self.items) + 12)
        top_v = max(v for _, v, _ in self.items) or 1
        num_w = 46
        bar_max = self.width() - label_w - num_w - 8
        for i, (text, value, color) in enumerate(self.items):
            y = i * self.ROW
            p.setFont(font)
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(self.width() - label_w, y, label_w - 4, self.ROW), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideLeft, label_w - 6))
            bw = max(3.0, bar_max * value / top_v * self._p)
            bar = QRectF(self.width() - label_w - 6 - bw, y + 9, bw, self.ROW - 18)
            path = QPainterPath()
            path.addRoundedRect(bar, 5, 5)
            p.fillPath(path, QColor(color or self.color))
            p.setPen(QColor(C["text2"]))
            p.drawText(QRectF(bar.left() - num_w - 2, y, num_w, self.ROW), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, num(value))
        p.end()


class DailyBars(_Animated):
    """Stacked columns, one per day (newest on the left, as dates read right-to-left)."""

    def __init__(self, keys, colors, parent=None):
        super().__init__(parent)
        self.keys, self.colors = keys, colors
        self.days = []                                      # [(label, {key: n})]
        self.setMinimumHeight(190)

    def set_days(self, days):
        if days != self.days:
            self.days = list(days)
            self.animate()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.days:
            p.end()
            return
        h_lab = 22
        area = QRectF(8, 8, self.width() - 16, self.height() - h_lab - 12)
        top = max((sum(d.values()) for _, d in self.days), default=0) or 1
        n = len(self.days)
        slot = area.width() / n
        bw = min(26.0, slot * 0.6)
        p.setPen(QPen(QColor(C["border"]), 1))
        p.drawLine(QPointF(area.left(), area.bottom()), QPointF(area.right(), area.bottom()))
        p.setFont(theme.font(10))
        for i, (lab, counts) in enumerate(self.days):
            cx = area.right() - slot * (i + 0.5)              # day 0 (oldest) at the right
            y = area.bottom()
            for k in self.keys:
                v = counts.get(k, 0)
                if not v:
                    continue
                hgt = area.height() * v / top * self._p
                p.fillRect(QRectF(cx - bw / 2, y - hgt, bw, hgt), QColor(self.colors[k]))
                y -= hgt
            p.setPen(QColor(C["text3"]))
            p.drawText(QRectF(cx - slot / 2, area.bottom() + 4, slot, h_lab - 4), Qt.AlignmentFlag.AlignCenter, lab)
        p.end()


# ---- status / steps --------------------------------------------------------------------------------------------------------
class StepList(QWidget):
    """Connection-check steps: ✓ / ✗ / … with a detail line each."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)

    def set_steps(self, steps):                             # [(text, ok: True/False/None, detail)]
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for text, ok, detail in steps:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            name, color = ("check", C["approve"]) if ok is True else (("x", C["danger"]) if ok is False else ("clock", C["text3"]))
            h.addWidget(IconLabel(name, color, 16), 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(text, "h3"))
            if detail:
                col.addWidget(label(str(detail), "caption", wrap=True, selectable=True))
            h.addLayout(col, 1)
            self.lay.addWidget(row)


class EmptyState(QWidget):
    def __init__(self, icon_name, title, text, action=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 32, 24, 32)
        lay.setSpacing(8)
        lay.addWidget(IconLabel(icon_name, C["text3"], 34), 0, Qt.AlignmentFlag.AlignHCenter)
        t = label(title, "h2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        s = label(text, "muted", wrap=True)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(s)
        if action is not None:
            lay.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)


class SearchBox(QLineEdit):
    def __init__(self, placeholder="جستجو…", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icons.icon("search", C["text3"], 16), QLineEdit.ActionPosition.LeadingPosition)
        self.setMinimumWidth(240)


class Switch(QAbstractButton):
    """An on/off switch (checkable), with keyboard focus ring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pos = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.valueChanged.connect(self._move)
        self.toggled.connect(self._toggled)

    def _move(self, v):
        self._pos = float(v)
        self.update()

    def _toggled(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on):
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        on = QColor(C["accent"]) if self.isEnabled() else QColor(C["disabled_accent"])
        off = QColor(C["border2"])
        track = QColor(off.red() + (on.red() - off.red()) * self._pos, off.green() + (on.green() - off.green()) * self._pos,
                       off.blue() + (on.blue() - off.blue()) * self._pos)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, track)
        if self.hasFocus():
            p.setPen(QPen(QColor(C["text"]), 1.5))
            p.drawPath(path)
        d = r.height() - 6
        # RTL: "on" moves the knob to the left
        x = r.right() - 3 - d - (r.width() - d - 6) * self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["knob"]))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))
        p.end()

    def sizeHint(self):
        return QSize(42, 24)


class Toast(QFrame):
    """A short message that fades in at the bottom of the window and leaves by itself."""

    KINDS = {"ok": (C["approve"], "check"), "warn": (C["warn"], "alert"), "error": (C["danger"], "x"), "info": (C["info"], "info")}

    def __init__(self, parent, text, kind="ok", ms=3800):
        super().__init__(parent)
        color, ic = self.KINDS.get(kind, self.KINDS["info"])
        self.setStyleSheet(f"QFrame {{ background: {C['toast_bg']}; border-radius: 12px; }} QLabel {{ color: {C['toast_text']}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 16, 10)
        lay.setSpacing(10)
        lay.addWidget(IconLabel(ic, color if kind != "error" else "#FF8A8A", 18))
        msg = FaLabel(text)
        msg.setWordWrap(True)
        msg.setMaximumWidth(460)
        lay.addWidget(msg)
        self.adjustSize()
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(220)
        self._fade.valueChanged.connect(lambda v: self._fx.setOpacity(float(v)))
        self._place()
        self.show()
        self.raise_()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QTimer.singleShot(ms, self._leave)

    def _place(self):
        par = self.parentWidget()
        self.move(28, par.height() - self.height() - 52)

    def _leave(self):
        self._fade.stop()
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.deleteLater)
        self._fade.start()


def toast(window, text, kind="ok"):
    Toast(window, text, kind)
