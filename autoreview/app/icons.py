"""One line-icon set for the whole app (24x24 grid, 1.8 stroke, round caps), drawn from inline SVG so it scales crisply and
takes any colour. Own simple shapes - no external icon package."""
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

_P = {
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "dashboard": '<rect x="3" y="3" width="7.5" height="9" rx="2"/><rect x="13.5" y="3" width="7.5" height="5.5" rx="2"/>'
                 '<rect x="13.5" y="11.5" width="7.5" height="9.5" rx="2"/><rect x="3" y="15" width="7.5" height="6" rx="2"/>',
    "review": '<circle cx="12" cy="12" r="9"/><path d="M10 8.6v6.8l5.6-3.4z"/>',
    "results": '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.6" cy="6.5" r="1.1"/><circle cx="4.6" cy="12" r="1.1"/><circle cx="4.6" cy="17.5" r="1.1"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-4.6-4.6"/>',
    "nbo": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/><path d="M6.5 6.6h.01M9.3 6.6h.01"/>',
    "crm": '<path d="M4 21V5.5A1.5 1.5 0 0 1 5.5 4h8A1.5 1.5 0 0 1 15 5.5V21M15 9.5h3.5A1.5 1.5 0 0 1 20 11v10M7.5 8h4M7.5 12h4M7.5 16h4M3 21h18"/>',
    "plug": '<path d="M9 7.5V3M15 7.5V3M6.5 7.5h11V11a5.5 5.5 0 0 1-11 0zM12 16.5V21"/>',
    "logs": '<rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "settings": '<path d="M4 6.5h9M17 6.5h3M4 12h3M11 12h9M4 17.5h11M19 17.5h1"/><circle cx="15" cy="6.5" r="2"/>'
                '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="17.5" r="2"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c1.4-3.8 4.3-5.7 7.5-5.7s6.1 1.9 7.5 5.7"/>',
    "logout": '<path d="M14.5 4H18a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3.5M10 16.5 5.5 12 10 7.5M5.5 12H15"/>',
    "refresh": '<path d="M20 11.5A8 8 0 1 1 17.7 6M20 4v5.5h-5.5"/>',
    "download": '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 20h14"/>',
    "upload": '<path d="M12 20V9M7.5 13.5 12 9l4.5 4.5M5 4h14"/>',
    "pause": '<path d="M9 5.5v13M15 5.5v13"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
    "play": '<path d="M8 5.5v13l10.5-6.5z"/>',
    "check": '<path d="M5 12.5 9.5 17 19 7.5"/>',
    "x": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "alert": '<path d="M12 3.5 21 19.5H3z"/><path d="M12 10v4M12 16.8h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8h.01"/>',
    "external": '<path d="M14 4h6v6M20 4l-8.5 8.5M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M15.5 8.5V5.5A1.5 1.5 0 0 0 14 4H5.5A1.5 1.5 0 0 0 4 5.5V14a1.5 1.5 0 0 0 1.5 1.5h3"/>',
    "sheet": '<rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M4 9h16M4 15h16M10 3v18"/>',
    "folder": '<path d="M3 7.5A2 2 0 0 1 5 5.5h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.6-3.4 8.1-8 9-4.6-.9-8-4.4-8-9V6z"/><path d="M9 12.2l2.1 2.1L15.2 10"/>',
    "home": '<path d="M4 11 12 4l8 7v8.5a1.5 1.5 0 0 1-1.5 1.5H15v-6H9v6H5.5A1.5 1.5 0 0 1 4 19.5z"/>',
    "back": '<path d="M9.5 18 15.5 12 9.5 6"/>',
    "forward": '<path d="M14.5 18 8.5 12l6-6"/>',
    "filter": '<path d="M4 5h16l-6 7.5V19l-4 1.5v-8z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3"/>',
    "list-check": '<path d="M10 6.5h10M10 12h10M10 17.5h10"/><path d="M3.5 6.5l1.5 1.5 2.5-3M3.5 12l1.5 1.5 2.5-3M3.5 17.5l1.5 1.5 2.5-3"/>',
    "send": '<path d="M20.5 3.5 10 14M20.5 3.5l-6.5 17-4-6.5-6.5-4z"/>',
}
_FILLED = {"play", "stop"}


def svg(name: str, color: str) -> bytes:
    body = _P.get(name, _P["info"])
    fill = color if name in _FILLED else "none"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode("utf-8")


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#52625C", size: int = 18, dpr: float = 2.0) -> QPixmap:
    px = int(size * dpr)
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg(name, color))).render(p, QRectF(0, 0, px, px))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = "#52625C", size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


# SnappPay's own marks (owner 2026-10-02: "the look of SnappPay - logo, everything"), as published on snapppay.ir:
# the 'Snapp! Pay' wordmark (SVG, one colour #007DFA) and the '!' mark (192 px PNG).
BRAND_BLUE = "#007DFA"


def _brand_file(name):
    from ..paths import assets_dir
    return assets_dir() / "brand" / name


def _tinted(img: QImage, colour: str) -> QImage:
    """The same shape in one colour (alpha kept)."""
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(colour))
    p.end()
    return out


def wordmark_pixmap(height: int, colour: str = BRAND_BLUE) -> QPixmap:
    """The 'Snapp! Pay' wordmark, height px tall (its file is square), in the brand blue or e.g. white on the navy rail."""
    dpr = 2.0
    px = int(height * dpr)
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(_brand_file("snapppay-logo.svg").read_bytes())).render(p, QRectF(0, 0, px, px))
    p.end()
    pm = QPixmap.fromImage(_tinted(img, colour) if colour.upper() != BRAND_BLUE else img)
    pm.setDevicePixelRatio(dpr)
    return pm


def logo_image(px: int) -> QImage:
    """The app mark at exactly px x px: SnappPay's '!' in white on a rounded square of the brand blue."""
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), px * 0.24, px * 0.24)
    p.fillPath(path, QColor(BRAND_BLUE))
    mark = _tinted(QImage(str(_brand_file("snapppay-fav-192.png"))), "#FFFFFF")
    inner = QRectF(px * 0.12, px * 0.12, px * 0.76, px * 0.76)
    p.drawImage(inner, mark)
    p.end()
    return img


def logo_pixmap(size: int = 64) -> QPixmap:
    dpr = 2.0
    pm = QPixmap.fromImage(logo_image(int(size * dpr)))
    pm.setDevicePixelRatio(dpr)
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(logo_pixmap(s))
    return ic
