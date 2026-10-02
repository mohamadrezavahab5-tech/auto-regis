"""One visual language for every page: palette, type scale, shapes. Pages only use object names / properties from here.

Palette: a deep petrol rail and a cool green-grey canvas (SnappPay is a fintech brand in the green family); decision colours
are fixed app-wide - approve green, edit amber, cancel red, manual indigo - and appear identically in tiles, chips, charts
and the Excel/Sheet output."""
from PySide6.QtCore import QLocale
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "canvas": "#EEF2F1", "surface": "#FFFFFF", "surface2": "#F6F8F7", "border": "#DCE4E1", "border2": "#C5D1CC",
    "text": "#12201D", "text2": "#52625C", "text3": "#83928C",
    "accent": "#0D9C82", "accent_hover": "#0A876F", "accent_press": "#07705C", "accent_soft": "#DFF3EE", "accent_text": "#086A57",
    "accent_top": "#12AE91", "on_accent": "#FFFFFF", "disabled_accent": "#A9D8CD", "disabled_on_accent": "#F4FBF9",
    "rail": "#0E292D", "rail2": "#123338", "rail_text": "#C3D8D2", "rail_muted": "#7B9C95", "rail_active": "#1D454A", "rail_hover": "#173A3F",
    "approve": "#1C9553", "approve_soft": "#E2F3E8", "edit": "#B27206", "edit_soft": "#FBEFD8",
    "cancel": "#C93A3A", "cancel_soft": "#FAE3E2", "manual": "#535FC2", "manual_soft": "#E8E9FA",
    "pending": "#9AA8A3", "pending_soft": "#EDF1EF", "done": "#8A92D8", "duplicate": "#8E3B9C",
    "warn": "#A8620B", "warn_soft": "#FFF3DE", "danger": "#BF3737", "danger_soft": "#FBE7E6", "info": "#2E6BB8", "info_soft": "#E5EEF9",
    "banner_border": "#EFD9A8", "banner_text": "#6E4A00", "banner_icon": "#8A5A00",
    "toast_bg": "#12201D", "toast_text": "#FFFFFF", "knob": "#FFFFFF",
    "hero_from": "#0E292D", "hero_to": "#0D7F6B", "hero_text": "#FFFFFF", "hero_muted": "#A9D3C9",
}
# Dark: the same petrol family, lifted for contrast; decision colours keep their meaning, just brighter on a dark ground.
DARK = {
    "canvas": "#0A1315", "surface": "#101C1F", "surface2": "#152428", "border": "#1F3237", "border2": "#2A434A",
    "text": "#E5EEEB", "text2": "#A3B6B0", "text3": "#6E8781",
    "accent": "#17B496", "accent_hover": "#1FC6A6", "accent_press": "#119279", "accent_soft": "#11302B", "accent_text": "#5ED6BC",
    "accent_top": "#22C9A8", "on_accent": "#06201B", "disabled_accent": "#1E4A42", "disabled_on_accent": "#7FA79D",
    "rail": "#060E10", "rail2": "#0B181B", "rail_text": "#B6CCC6", "rail_muted": "#5B7872", "rail_active": "#12302F", "rail_hover": "#0E2426",
    "approve": "#3CC27A", "approve_soft": "#12301F", "edit": "#E3A43C", "edit_soft": "#33280F",
    "cancel": "#EE6E6E", "cancel_soft": "#3A1B1B", "manual": "#8F98F2", "manual_soft": "#1E2243",
    "pending": "#6E8781", "pending_soft": "#1A2A2E", "done": "#8A92D8", "duplicate": "#C77BD6",
    "warn": "#E7A43E", "warn_soft": "#33280F", "danger": "#F07272", "danger_soft": "#3A1B1B", "info": "#6CA9F2", "info_soft": "#132A40",
    "banner_border": "#4A3A15", "banner_text": "#F1C977", "banner_icon": "#F1C977",
    "toast_bg": "#E5EEEB", "toast_text": "#0A1315", "knob": "#E5EEEB",
    "hero_from": "#0B2A2B", "hero_to": "#0F5E52", "hero_text": "#FFFFFF", "hero_muted": "#9FD0C4",
}
C = dict(LIGHT)
MODE = "light"
ACTION, STATE, WORKFLOW = {}, {}, {}


def _rebuild():
    """Colour sets derived from C - refilled in place, so every module that imported them sees the active palette."""
    ACTION.clear()
    ACTION.update({"APPROVE": (C["approve"], C["approve_soft"]), "EDIT": (C["edit"], C["edit_soft"]),
                   "CANCEL": (C["cancel"], C["cancel_soft"]), "MANUAL": (C["manual"], C["manual_soft"])})
    STATE.clear()
    STATE.update({"NOT_REVIEWED": (C["pending"], C["pending_soft"]), "APPROVE": ACTION["APPROVE"], "EDIT": ACTION["EDIT"],
                  "CANCEL": ACTION["CANCEL"], "MANUAL_OPEN": ACTION["MANUAL"], "MANUAL_DONE": (C["done"], C["manual_soft"])})
    # The two-team workflow (workflow.STATES): open states first, then what NBO finally did. Same colours everywhere.
    WORKFLOW.clear()
    WORKFLOW.update({"WAIT_ONLINE": (C["text2"], C["pending_soft"]), "MANUAL": ACTION["MANUAL"], "WAIT_INSTORE": (C["info"], C["info_soft"]),
                     "CONFLICT": (C["danger"], C["danger_soft"]), "EDIT": ACTION["EDIT"], "CANCEL": ACTION["CANCEL"],
                     "READY": (C["accent_text"], C["accent_soft"]), "DONE_APPROVED": ACTION["APPROVE"],
                     "DONE_CLOSED": (C["text3"], C["surface2"]), "OUT_OF_SCOPE": (C["text3"], C["surface2"])})


def set_mode(mode: str) -> str:
    """'light' | 'dark'. Must run before any window is built (widgets read the palette when they are created)."""
    global MODE
    MODE = "dark" if mode == "dark" else "light"
    C.clear()
    C.update(DARK if MODE == "dark" else LIGHT)
    _rebuild()
    return MODE


_rebuild()
FAMILY = "Segoe UI"                    # replaced by Vazirmatn once load_fonts() found the bundled files


def load_fonts() -> str:
    """Vazirmatn (SIL Open Font License, assets/fonts/OFL.txt): made for Persian interfaces, with clean Latin letters and
    digits. Bundled with the app, never installed into Windows. Falls back to Segoe UI if anything is missing."""
    global FAMILY
    from PySide6.QtGui import QFontDatabase
    from ..paths import assets_dir
    families = set()
    for f in sorted((assets_dir() / "fonts").glob("Vazirmatn-*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(f))
        if fid >= 0:
            families.update(QFontDatabase.applicationFontFamilies(fid))
    if "Vazirmatn" in families:
        FAMILY = "Vazirmatn"
    return FAMILY


def font(size=13, weight=QFont.Weight.Normal) -> QFont:
    f = QFont(FAMILY)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def qss() -> str:
    c = C
    return f"""
* {{ font-family: '{FAMILY}'; font-size: 13px; color: {c['text']}; outline: none; }}
QMainWindow, QDialog, QWidget#canvas {{ background: {c['canvas']}; }}
QWidget#page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QFrame#rail {{ background: {c['rail']}; }}
QLabel#brand {{ color: #FFFFFF; font-size: 17px; font-weight: 700; }}
QLabel#brandSub {{ color: {c['rail_muted']}; font-size: 11px; }}
QPushButton#railItem {{ background: transparent; color: {c['rail_text']}; border: none; border-radius: 10px; padding: 10px 14px;
    text-align: right; font-size: 13px; }}
QPushButton#railItem:hover {{ background: {c['rail_hover']}; color: #FFFFFF; }}
QPushButton#railItem:checked {{ background: {c['rail_active']}; color: #FFFFFF; font-weight: 600; }}
QPushButton#railItem:focus {{ border: 1px solid {c['accent']}; }}
QLabel#railSection {{ color: {c['rail_muted']}; font-size: 10px; font-weight: 600; padding: 12px 14px 4px 14px; }}
QFrame#railFoot {{ background: {c['rail2']}; border-radius: 12px; }}
QLabel#railFootText {{ color: {c['rail_text']}; font-size: 12px; }}

QFrame#topbar {{ background: transparent; }}
QLabel#pageTitle {{ font-size: 22px; font-weight: 700; color: {c['text']}; }}
QLabel#pageSub {{ color: {c['text2']}; font-size: 12px; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; color: {c['text']}; }}
QLabel#h3 {{ font-size: 13px; font-weight: 600; color: {c['text']}; }}
QLabel#muted {{ color: {c['text2']}; }}
QLabel#caption {{ color: {c['text3']}; font-size: 12px; }}
QLabel#bigNumber {{ font-size: 30px; font-weight: 700; }}
QLabel#mono {{ font-family: 'Consolas'; font-size: 12px; color: {c['text2']}; }}

QFrame[card="true"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 16px; }}
QFrame[card="soft"] {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#banner {{ background: {c['warn_soft']}; border: 1px solid {c['banner_border']}; border-radius: 12px; }}
QLabel#bannerText {{ color: {c['banner_text']}; }}
QFrame#hline {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}

QPushButton {{ background: {c['surface']}; border: 1px solid {c['border2']}; border-radius: 10px; padding: 7px 14px; min-height: 20px; }}
QPushButton:hover {{ background: {c['surface2']}; border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['accent_soft']}; }}
QPushButton:focus {{ border: 2px solid {c['accent']}; padding: 6px 13px; }}
QPushButton:disabled {{ color: {c['text3']}; background: {c['surface2']}; border-color: {c['border']}; }}
QPushButton[kind="primary"] {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {c['accent_top']}, stop:1 {c['accent']}); color: {c['on_accent']};
    border: none; font-weight: 600; padding: 8px 18px; }}
QPushButton[kind="primary"]:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {c['accent']}, stop:1 {c['accent_hover']}); }}
QPushButton[kind="primary"]:pressed {{ background: {c['accent_press']}; }}
QPushButton[kind="primary"]:focus {{ border: 2px solid {c['text']}; padding: 6px 16px; }}
QPushButton[kind="primary"]:disabled {{ background: {c['disabled_accent']}; color: {c['disabled_on_accent']}; }}
QPushButton[kind="danger"] {{ color: {c['danger']}; border-color: #EBC2BF; }}
QPushButton[kind="danger"]:hover {{ background: {c['danger_soft']}; border-color: {c['danger']}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {c['text2']}; padding: 6px 10px; }}
QPushButton[kind="ghost"]:hover {{ background: {c['surface2']}; color: {c['text']}; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {c['accent_text']}; padding: 2px 4px; text-decoration: underline; }}
QPushButton[kind="chip"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; padding: 4px 12px; color: {c['text2']}; }}
QPushButton[kind="chip"]:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; color: {c['accent_text']}; font-weight: 600; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{ background: {c['surface']}; border: 1px solid {c['border2']}; border-radius: 9px;
    padding: 7px 10px; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 2px solid {c['accent']}; padding: 6px 9px; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ background: {c['surface2']}; color: {c['text3']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border2']}; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 17px; height: 17px; }}
QCheckBox::indicator {{ border: 1px solid {c['border2']}; border-radius: 5px; background: {c['surface']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QCheckBox::indicator:focus {{ border: 2px solid {c['accent']}; }}

QProgressBar {{ background: {c['pending_soft']}; border: none; border-radius: 6px; max-height: 10px; min-height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}

QTableView, QListWidget, QTreeWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px;
    gridline-color: transparent; alternate-background-color: {c['surface2']}; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QTableView::item, QListWidget::item {{ padding: 4px 8px; }}
QHeaderView::section {{ background: {c['surface2']}; border: none; border-bottom: 1px solid {c['border']}; padding: 8px 10px; font-weight: 600; color: {c['text2']}; }}
QTableCornerButton::section {{ background: {c['surface2']}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border2']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['text3']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border2']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {c['toast_bg']}; color: {c['toast_text']}; border: none; border-radius: 6px; padding: 6px 8px; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border2']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; border: none; padding: 8px 14px; color: {c['text2']}; }}
QTabBar::tab:selected {{ color: {c['accent_text']}; font-weight: 600; border-bottom: 2px solid {c['accent']}; }}
QStatusBar {{ background: {c['surface']}; border-top: 1px solid {c['border']}; }}
QStatusBar QLabel {{ color: {c['text2']}; font-size: 12px; }}
QLabel#pill {{ border-radius: 10px; padding: 2px 10px; font-size: 12px; }}
"""


def apply(app: QApplication, mode: str = None) -> None:
    if mode is None:
        from .. import settings
        try:
            mode = settings.load_rules().get("appearance", {}).get("theme", "light")
        except Exception:                                  # a broken settings file never stops the app from opening
            mode = "light"
    set_mode(mode)
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))   # number boxes: English digits (owner 2026-10-02)
    load_fonts()
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(C["canvas"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(C["surface"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(C["surface2"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(C["text"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(C["text"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(C["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(C["surface"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(C["accent_soft"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(C["text"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(C["text3"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(C["text"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#FFFFFF"))
    app.setPalette(pal)
    app.setFont(font(13))
    app.setStyleSheet(qss())
