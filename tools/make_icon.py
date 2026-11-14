"""產生 App 圖示 assets/icon.png 與多尺寸 assets/icon.ico。

圖示：血條分格的血壓錶（綠→黃→紅，指針打爆）+ 金框深紅底 + LOL 字樣。
不使用任何 Riot 商標或美術素材；字樣用一般字型。
執行：.venv\Scripts\python tools\make_icon.py
"""
import math
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

GOLD, WHITE, DARK, RED = QColor("#c8aa6e"), QColor("#f0e6d2"), QColor("#3c0a0a"), QColor("#e53935")
SEGMENTS = [QColor("#43a047")] * 3 + [QColor("#fdd835")] * 2 + [RED] * 3
ASSETS = Path(__file__).resolve().parent.parent / "assets"


def draw(p: QPainter, s: int):
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 0, s); g.setColorAt(0, QColor("#c62828")); g.setColorAt(1, QColor("#5a0b0b"))
    m = s * .02
    p.setBrush(g); p.setPen(QPen(GOLD, s * .035))
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), s * .2, s * .2)

    cx, cy, r = s / 2, s * .55, s * .36
    p.setPen(QPen(GOLD, s * .03)); p.setBrush(QColor("#1e1e24"))
    dial = QPainterPath(); dial.moveTo(cx - r, cy)
    dial.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 180, -180); dial.closeSubpath()
    p.drawPath(dial)
    rr, step = r * .78, 180 / len(SEGMENTS)
    for i, col in enumerate(SEGMENTS):
        p.setPen(QPen(col, r * .26, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr), int((180 - (i + 1) * step + 3) * 16), int((step - 6) * 16))
    t = math.radians(12)
    p.setPen(QPen(WHITE, s * .045, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(t) * r * .95, cy - math.sin(t) * r * .95))
    p.setPen(QPen(GOLD, s * .015)); p.setBrush(DARK); p.drawEllipse(QPointF(cx, cy), s * .05, s * .05)

    f = QFont("Georgia"); f.setBold(True); f.setPixelSize(max(1, int(s * .2)))
    f.setLetterSpacing(QFont.PercentageSpacing, 110)
    p.setFont(f); p.setPen(GOLD)
    p.drawText(QRectF(0, s * .62, s, s * .3), Qt.AlignCenter, "LOL")


def render(s: int) -> QImage:
    img = QImage(s, s, QImage.Format_ARGB32); img.fill(Qt.transparent)
    p = QPainter(img); draw(p, s); p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def bmp_bytes(img: QImage) -> bytes:
    """ICO 用的 32 位元 DIB：BITMAPINFOHEADER（高度 ×2）+ 由下而上的 BGRA 像素 + 全透明的 AND 遮罩。"""
    img = img.convertToFormat(QImage.Format_ARGB32)  # 記憶體中即為 BGRA
    w, h = img.width(), img.height()
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    rows = [bytes(img.constScanLine(y))[: w * 4] for y in range(h - 1, -1, -1)]
    mask_row = ((w + 31) // 32) * 4
    return header + b"".join(rows) + b"\0" * (mask_row * h)


def write_ico(path: Path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """多尺寸 ICO，每個尺寸各自繪製，小尺寸才會清楚。
    256px 用 PNG 壓縮；較小尺寸用傳統 BMP，Windows 檔案總管與工作列在小尺寸只認得這種。"""
    pngs = [png_bytes(render(s)) if s >= 256 else bmp_bytes(render(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(pngs))


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    ASSETS.mkdir(exist_ok=True)
    render(256).save(str(ASSETS / "icon.png"))
    write_ico(ASSETS / "icon.ico")
    print("ok")
