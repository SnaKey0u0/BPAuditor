"""PySide6 介面：左側欄（剪貼簿監看 / 手動貼上 / 查對面）+ 右側玩家卡片（房間單欄、遊戲中左右兩隊）或設定頁，
關閉時縮到系統匣。"""
import math
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPainterPath,
                           QImage, QPen, QPixmap)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSpacerItem, QStackedWidget, QSystemTrayIcon, QToolTip, QVBoxLayout, QWidget,
)

from . import autostart, puuids, single
from .analysis import (GameSummary, Mastery, PremadeGroup, detect_premades, game_puuids, game_summaries,
                       split_game, streak)
from .audit import ME, Audit, AuditOutcome, Me, PlayerResult
from .champions import Champions, Icon, download_asset, download_icon, icon_dir, icon_path
from .config import Config
from .keys import BAD, OK, WARMING, mask
from .parsing import RiotId, members_to_query, parse_room_messages, room_change, room_members_in_clipboard
from .riot import KEY_PAGE, RiotClient, RiotError, find_api_key
from .rotation import KeyRotation, key_row_status, run_rotating

GROUP_COLORS = ["#ffb300", "#ab47bc", "#29b6f6", "#ef6c00"]
RED, GREEN, GREY, BLUE, ORANGE = "#ef5350", "#66bb6a", "#9e9e9e", "#90caf9", "#ffb300"
SKELETON = "#323844"
NEON = "#18ffff"  # 送頭場的霓虹框、提示框說明與標籤
KEY_WARMUP = 180  # 新 Key 產生後最多等幾秒讓 Riot 啟用
KEY_RECHECK_MS = 15_000
GAME_POLL_DELAY_MS = 90_000  # 房間稽查完先等 90 秒（排隊＋選角）才開始問觀戰 API
GAME_POLL_MS = 20_000  # 之後每 20 秒問一次，看開局了沒
GAME_POLL_FOR = 15 * 60  # 開始問之後最多問這麼久
GAME_WIDTH = 1100  # 左右兩隊時視窗至少這麼寬
KDA_GOOD, KD_GOOD = 3, 1  # 平均 KDA／KD 達到這個值顯示綠色，否則紅色
ERROR_SHORT = {"not_found": "查無此人", "network": "連線失敗", "server": "伺服器錯誤", "blocked": "連線被擋"}

QSS = """
QWidget{background:#1b1e24;color:#e0e0e0;font-family:'Microsoft JhengHei UI','Segoe UI';font-size:13px;}
QPlainTextEdit,QLineEdit,QSpinBox,QDoubleSpinBox{background:#262a33;border:1px solid #3a3f4b;border-radius:4px;padding:3px;}
QPushButton{background:#3949ab;border:none;border-radius:4px;padding:6px 14px;}
QPushButton:hover{background:#5c6bc0;} QPushButton:disabled{background:#2d323c;color:#757575;}
QPushButton#secondary,QPushButton#icon{background:#2d323c;}
QPushButton#secondary:hover,QPushButton#icon:hover{background:#3a3f4b;}
QPushButton#icon{padding:4px 8px;font-size:15px;} QPushButton#icon:checked{background:#3949ab;}
QScrollArea{border:none;}
#card,#panel{background:#262a33;border-radius:8px;}
#card QLabel,#panel QLabel{background:transparent;}
#keybanner{background:#4a2e2e;border-radius:6px;} #keybanner QLabel{background:transparent;}
QToolTip{background:#262a33;color:#e0e0e0;border:1px solid #3a3f4b;padding:4px;}
QProgressBar{background:#262a33;border:none;border-radius:2px;}
QProgressBar::chunk{background:#3949ab;border-radius:2px;}
QScrollBar:vertical{background:transparent;width:10px;margin:0;}
QScrollBar::handle:vertical{background:#3a3f4b;border-radius:5px;min-height:30px;}
QScrollBar::handle:vertical:hover{background:#4a5060;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:none;}
"""


def wr_color(rate: float) -> str:
    return RED if rate < 0.4 else GREEN if rate > 0.6 else "#e0e0e0"


def grey(text: str) -> str:
    return f"<span style='color:{GREY}'>{text}</span>"


def kda_html(kda: float, kd: float) -> str:
    """平均 KDA < 3、KD < 1 紅色，否則綠色；兩者同字體大小，上下兩行。"""
    def line(label: str, value: float, good: float) -> str:
        return f"{grey(label)} <b style='color:{GREEN if value >= good else RED}'>{value:.2f}</b>"
    return f"<span style='font-size:15px'>{line('KDA', kda, KDA_GOOD)}<br>{line('KD', kd, KD_GOOD)}</span>"


def resource(name: str) -> Path:
    """打包後的資源在 sys._MEIPASS，開發時在專案根目錄的 assets/。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


def app_icon() -> QIcon:
    p = resource("icon.ico")
    return QIcon(str(p)) if p.exists() else QIcon()


def key_of(m: RiotId) -> str:
    return str(m).casefold()


def opgg_profile(m: RiotId) -> str:
    q = lambda t: urllib.parse.quote(t, safe="")
    return f"https://op.gg/lol/summoners/tw/{q(m.name)}-{q(m.tag)}"


def ago(epoch: float) -> str:
    secs = time.time() - epoch
    if secs < 3600:
        return "剛剛"
    if secs < 86400:
        return f"{secs / 3600:.0f} 小時前"
    return f"{secs / 86400:.0f} 天前"


def points(n: int) -> str:
    return f"{n / 10000:.1f} 萬點" if n >= 10000 else f"{n} 點"


class Bridge(QObject):
    """把背景執行緒的結果安全地送回 UI 執行緒。"""
    player = Signal(object, str)  # (PlayerResult, 這次稽查所用的 Key：PUUID 依 Key 加密)
    outcome = Signal(object)
    me = Signal(object, str)  # (Me, 查出這個 PUUID 的 Key)
    done = Signal()
    champions = Signal(object)
    key_checked = Signal(str, str, str)  # (key, 錯誤種類, 錯誤訊息)；種類為空字串代表有效
    me_lookup = Signal(str, object, str)  # (Riot ID, {Key: puuid}, 錯誤訊息)；某組查不到時其他組照樣存
    versions = Signal(object)  # list[Version]：黑名單其他 Key 的版本，背景換算回來
    icon_ready = Signal()
    game = Signal(object, str, bool, str)  # (進行中對局或 None, 錯誤訊息, 是否手動, 查詢所用的 Key)
    mastery = Signal(str, object)  # (標籤, Mastery 或 None)
    restart = Signal(str, int)  # (新 Key, 要查的人數)：換 Key 了，整次稽查從頭重跑，舊結果丟掉（ADR 0002）
    key_failed = Signal(str)  # 稽查中回 401/403 的 Key（已標成不可用），換下一組重跑
    audit_error = Signal(str)  # 稽查沒辦法開始（例如沒有可用的 Key）


class Switch(QAbstractButton):
    """開關樣式的切換鈕，取代勾選框。"""

    def __init__(self, text: str = ""):
        super().__init__()
        self.setCheckable(True); self.setText(text); self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        w = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(40 + (w + 10 if w else 0), 22)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        on, enabled = self.isChecked(), self.isEnabled()
        track = QColor("#3949ab" if on else "#3a3f4b") if enabled else QColor("#2d323c")
        p.setPen(Qt.NoPen); p.setBrush(track); p.drawRoundedRect(QRectF(0, 2, 36, 18), 9, 9)
        p.setBrush(QColor("#e0e0e0" if enabled else "#757575"))
        p.drawEllipse(QRectF(20 if on else 2, 4, 14, 14))
        if self.text():
            p.setPen(QColor("#e0e0e0" if enabled else "#757575"))
            p.drawText(QRectF(46, 0, self.width() - 46, self.height()), Qt.AlignVCenter, self.text())


class FlagButton(QAbstractButton):
    """「!」黑名單切換鈕。圓圈與驚嘆號都自己畫，不靠字型，驚嘆號才會在圓圈正中心。"""

    def __init__(self):
        super().__init__()
        self.setCheckable(True); self.setFixedSize(24, 24); self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            ring = QColor("#30343d"); mark = QColor("#4a5060"); fill = Qt.NoBrush
        elif self.isChecked():
            ring = QColor(RED); fill = QColor(RED); mark = QColor("#1b1e24")
        elif self.underMouse():
            ring = mark = QColor(RED); fill = Qt.NoBrush
        else:
            ring = QColor("#4a5060"); mark = QColor(GREY); fill = Qt.NoBrush
        p.setPen(QPen(ring, 1)); p.setBrush(fill); p.drawEllipse(QRectF(0.5, 0.5, 23, 23))
        # 驚嘆號：一條直槓加一個點，整體高 12、以 (12, 12) 為中心
        p.setPen(Qt.NoPen); p.setBrush(mark)
        p.drawRoundedRect(QRectF(11, 6, 2, 7.5), 1, 1)
        p.drawEllipse(QRectF(10.75, 15.5, 2.5, 2.5))


class ChampionTiles(QWidget):
    """近期戰績的英雄頭像方塊：由新到舊，底部綠／紅條表示該場勝敗；每場有各自的提示框。

    每列最多 PER_ROW 個，超過換列；卡片比較窄時（視窗縮小、對局的兩隊畫面）也會提早換列，
    方塊列不會把卡片撐寬。四周留 MARGIN 邊距，送頭場的光暈畫在這裡。
    """
    PER_ROW = 10
    MARGIN = 6
    PLACEHOLDER = 5  # 查詢中顯示幾個灰色佔位方塊
    ROOM, GAME = 32, 24  # 方塊邊長：房間畫面、對局的兩隊畫面
    _pixmaps: dict[str, QPixmap] = {}  # 頭像檔案路徑 → 已載入的圖

    def __init__(self, icon_file: Callable[[str], Path | None], tip: Callable[[GameSummary], str]):
        """icon_file：英雄 id → 本機頭像檔（還沒下載好回 None）；tip：單場摘要 → 提示框內容。"""
        super().__init__()
        self.icon_file, self.tip = icon_file, tip
        self.games: list[GameSummary] | None = None  # None = 查詢中（灰色佔位）
        self.tile = self.ROOM
        self.rects: list[QRectF] = []
        self.setMouseTracking(True)

    def set_games(self, games: list[GameSummary] | None):
        self.games = games
        self.setVisible(games is None or bool(games))
        self.updateGeometry(); self.update()

    def set_tile_size(self, px: int):
        if px != self.tile:
            self.tile = px
            self.updateGeometry(); self.update()

    def _gap(self) -> int:
        return 6 if self.tile >= 32 else 4

    def _count(self) -> int:
        return self.PLACEHOLDER if self.games is None else len(self.games)

    def _per_row(self, width: int) -> int:
        """給定寬度下每列放幾個：最多 PER_ROW 個，至少 1 個。"""
        step = self.tile + self._gap()
        fit = (width - 2 * self.MARGIN + self._gap()) // step
        return max(1, min(self.PER_ROW, self._count(), fit))

    def _extent(self, n: int) -> int:
        """n 個方塊排成一列（或一欄）的長度，含兩端邊距。"""
        return n * (self.tile + self._gap()) - self._gap() + 2 * self.MARGIN

    def sizeHint(self):
        n = self._count()
        cols = min(n, self.PER_ROW)
        return QSize(self._extent(cols), self._extent(math.ceil(n / cols))) if n else QSize(0, 0)

    def minimumSizeHint(self):
        return QSize(self._extent(1), self._extent(1))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w: int) -> int:
        n = self._count()
        return self._extent(math.ceil(n / self._per_row(w))) if n else 0

    def _pixmap(self, champion: str, grey: bool = False) -> QPixmap | None:
        """grey=True：去色版本（重開局用），另外快取。"""
        path = self.icon_file(champion)
        if path is None:
            return None
        key = str(path)
        if key not in self._pixmaps:
            pm = QPixmap(key)
            if pm.isNull():
                return None
            self._pixmaps[key] = pm
        if grey and "grey:" + key not in self._pixmaps:
            img = self._pixmaps[key].toImage().convertToFormat(QImage.Format_Grayscale8)
            self._pixmaps["grey:" + key] = QPixmap.fromImage(img)
        return self._pixmaps[("grey:" if grey else "") + key]

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        p.setPen(Qt.NoPen)
        s, gap, radius = self.tile, self._gap(), 4 if self.tile >= 32 else 3
        bar = max(s // 7, 3)  # 底部勝敗色條的高度
        cols = self._per_row(self.width())
        self.rects = [QRectF(self.MARGIN + i % cols * (s + gap), self.MARGIN + i // cols * (s + gap), s, s)
                      for i in range(self._count())]
        feeding = [r for r, g in zip(self.rects, self.games or []) if g.feeding]
        for r in feeding:  # 光暈先畫，相鄰方塊再蓋上去，光暈只留在間隙與邊距裡
            self._glow(p, r, radius)
        for i, r in enumerate(self.rects):
            if self.games is None:
                p.setBrush(QColor(SKELETON)); p.drawRoundedRect(r, radius, radius)
                continue
            g = self.games[i]
            clip = QPainterPath(); clip.addRoundedRect(r, radius, radius)
            p.save(); p.setClipPath(clip)
            pm = self._pixmap(g.champion, grey=g.remake)
            if pm:
                p.drawPixmap(r, pm, QRectF(pm.rect()))
            else:  # 頭像還沒下載好：灰底加英雄 id 前兩個字母
                p.fillRect(r, QColor("#3a3f4b"))
                p.setPen(QColor(GREY)); p.drawText(r.adjusted(0, 0, 0, -bar), Qt.AlignCenter, g.champion[:2])
                p.setPen(Qt.NoPen)
            if g.remake:  # 重開局：去色頭像再調暗，底條灰色
                p.fillRect(r, QColor(27, 30, 36, 110))
            p.fillRect(QRectF(r.left(), r.bottom() - bar, s, bar), QColor(GREY if g.remake else GREEN if g.win else RED))
            p.restore()
        p.setPen(QPen(QColor(NEON), 1.5)); p.setBrush(Qt.NoBrush)
        for r in feeding:  # 送頭場的實線外框，緊貼方塊外緣
            p.drawRoundedRect(r.adjusted(-0.75, -0.75, 0.75, 0.75), radius + 0.75, radius + 0.75)

    def _glow(self, p: QPainter, r: QRectF, radius: float):
        """送頭場的靜態青色光暈：由外而內 5 層，越內層越不透明；最外層剛好到 MARGIN 邊距。"""
        c = QColor(NEON)
        for grow, alpha in ((6, 18), (5, 26), (4, 38), (3, 55), (2, 80)):
            c.setAlpha(alpha); p.setBrush(c)
            p.drawRoundedRect(r.adjusted(-grow, -grow, grow, grow), radius + grow, radius + grow)

    def event(self, e):
        if e.type() == QEvent.ToolTip and self.games:
            pad = self._gap() / 2
            i = next((i for i, r in enumerate(self.rects) if r.adjusted(-pad, -pad, pad, pad).contains(e.pos())), None)
            if i is not None and i < len(self.games):
                QToolTip.showText(e.globalPos(), self.tip(self.games[i]), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)


class PlayerCard(QFrame):
    """一位玩家的卡片，分三段：
    - 標題列：名稱、等級、牌位；右上角的 header_right 放「!」黑名單按鈕。
    - 中段：勝率、英雄頭像方塊、KDA／KD；對局中再加一行本場英雄的熟練度。
    - 標籤列 tags：相遇等小標籤，靠左排。
    """

    def __init__(self, rid: RiotId, win: "MainWindow"):
        super().__init__()
        self.setObjectName("card")
        self.rid, self.win, self.result = rid, win, None
        self.is_me = False
        self.hidden = False
        self.champion_key: int | None = None  # 本場英雄；只在遊戲中有
        self.mastery: Mastery | None = None
        self.mastery_loaded = False
        self.puuid = ""  # 稽查結果回來才有；黑名單以此判定
        self.puuid_key = ""  # self.puuid 是用哪一組 Key 查到的（PUUID 依 Key 加密，黑名單要比對同一組的版本）
        self.blacklisted = False
        self.set_stripe(None)
        v = QVBoxLayout(self); v.setContentsMargins(12, 8, 10, 8); v.setSpacing(4)

        head = QHBoxLayout(); head.setSpacing(10)
        self.title = QLabel()
        self.title.setToolTip("在 op.gg 開啟個人頁")
        self.title.linkActivated.connect(lambda _: QDesktopServices.openUrl(QUrl(opgg_profile(self.rid))))
        self.rank = QLabel(); self.rank.setWordWrap(True)  # 窄卡片時牌位換行，不撐寬卡片
        head.addWidget(self.title, 0, Qt.AlignTop); head.addWidget(self.rank, 1)
        self.header_right = QHBoxLayout(); self.header_right.setSpacing(4)
        head.addLayout(self.header_right)
        # 「!」黑名單切換鈕：取得 PUUID 後才可按；本人與身分被隱藏的玩家不顯示
        self.flag = FlagButton(); self.flag.setEnabled(False)
        self.flag.clicked.connect(self.toggle_blacklist)
        self.header_right.addWidget(self.flag, 0, Qt.AlignTop)
        v.addLayout(head)

        mid = QHBoxLayout(); mid.setSpacing(14)
        self.big = QLabel(); self.kda = QLabel()
        self.tiles = ChampionTiles(win.icon_file, self._tile_tip)
        for w in (self.big, self.tiles, self.kda):
            mid.addWidget(w, 0, Qt.AlignVCenter)
        mid.addStretch()
        v.addLayout(mid)
        self.champ = QLabel(); self.champ.hide()
        v.addWidget(self.champ)

        self.tags = QHBoxLayout(); self.tags.setSpacing(6)
        self.black_tag = QLabel("! 黑名單"); self.black_tag.hide()
        self.black_tag.setStyleSheet(f"background:{RED};color:#1b1e24;font-weight:bold;border-radius:10px;padding:2px 9px;")
        self.streak = QLabel(); self.streak.hide()  # 連勝／連敗
        self.chip = QLabel(); self.chip.hide()
        self.feed_tag = QLabel("送頭"); self.feed_tag.hide()  # 近期戰績有任一場送頭場；排在相遇之前
        self.feed_tag.setStyleSheet(f"background:#12303a;color:{NEON};border:1px solid {NEON};"
                                    "border-radius:10px;padding:2px 9px;")
        self.feed_tag.setToolTip("有青色霓虹框的那幾場；滑鼠移到方塊上看細節")
        self.tags.addWidget(self.black_tag); self.tags.addWidget(self.streak); self.tags.addWidget(self.feed_tag)
        self.tags.addWidget(self.chip); self.tags.addStretch()
        v.addLayout(self.tags)
        self.bottom = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Minimum)
        v.addItem(self.bottom)
        self.set_title("")
        self.set_skeleton()

    def set_compact(self, on: bool):
        """對局的兩隊畫面：卡片較窄，改用小方塊；同一列兩張卡片等高，多出的空間放在底部。
        房間畫面的卡片維持內容高度（方塊換列時跟著長高），不被拉長。"""
        self.tiles.set_tile_size(ChampionTiles.GAME if on else ChampionTiles.ROOM)
        self.bottom.changeSize(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding if on else QSizePolicy.Minimum)
        self.layout().invalidate()

    def show_hidden(self, alias: str = ""):
        """Riot 隱藏了此人身分：沒有 Riot ID／PUUID 查不到戰績，只顯示化名或本場英雄。"""
        name = f"{alias} " if alias else ""
        self.title.setText(f"<b style='font-size:15px'>{name}</b>{grey('身分被隱藏')}"); self.title.setToolTip("")
        self.clear_skeleton(); self.tiles.hide(); self.rank.hide(); self.kda.hide()
        self.big.setText(grey("Riot 沒有提供這位玩家的身分，無法查詢"))
        self.hidden = True
        self.flag.hide()
        self.show_champion()

    def set_title(self, extra: str = ""):
        self.title.setText(f"<a href='opgg' style='color:#e0e0e0;text-decoration:none'>"
                           f"<b style='font-size:15px'>{self.rid}</b></a> {extra}")

    def mark_me(self):
        self.is_me = True
        self.flag.hide()
        self.show_blacklist()

    def set_stripe(self, color):
        """可能多排的左側色條；與黑名單的紅框暗紅底一起組成卡片樣式。"""
        self.stripe = color
        self._restyle()

    def _restyle(self):
        if self.blacklisted:  # 黑名單：紅框＋暗紅底，可能多排的色條照樣蓋在左邊
            self.setStyleSheet(f"#card{{background:#3a2226;border:1px solid {RED};"
                               f"border-left:6px solid {self.stripe or RED};}}")
        else:
            self.setStyleSheet(f"#card{{border-left:6px solid {self.stripe or '#262a33'};}}")

    def toggle_blacklist(self):
        if self.puuid and not self.is_me:
            self.win.toggle_blacklist(self.puuid_key, self.puuid)
        self.show_blacklist()

    def show_blacklist(self):
        """依設定檔的黑名單更新「!」按鈕、標籤與卡片顏色；沒有 PUUID（查詢中／失敗）時按鈕不可用。"""
        usable = bool(self.puuid) and not self.is_me and not self.hidden
        self.blacklisted = usable and self.win.cfg.is_blacklisted(self.puuid_key, self.puuid)
        self.flag.setEnabled(usable)
        self.flag.setChecked(self.blacklisted)
        self.flag.setToolTip("已在黑名單（再點一次移除）" if self.blacklisted else "加入黑名單")
        self.black_tag.setVisible(self.blacklisted)
        self._restyle()

    def set_skeleton(self):
        """查詢中：灰色佔位方塊。"""
        for w, size in ((self.big, (64, 44)), (self.kda, (64, 40))):
            w.setFixedSize(*size); w.setStyleSheet(f"background:{SKELETON};border-radius:4px;")
        self.tiles.set_games(None)

    def reset(self):
        """換 Key 重跑：舊 Key 查到的結果全部清掉，回到查詢中（ADR 0002）。"""
        self.result, self.puuid, self.puuid_key = None, "", ""
        self.mastery, self.mastery_loaded = None, False
        self.big.setText(""); self.big.setToolTip(""); self.kda.setText(""); self.rank.setText("")
        for w in (self.streak, self.feed_tag, self.chip):
            w.hide()
        self.set_title("")
        self.set_skeleton()
        self.show_champion()
        self.show_blacklist()

    def clear_skeleton(self):
        for w in (self.big, self.kda):
            w.setMinimumSize(0, 0); w.setMaximumSize(16777215, 16777215); w.setStyleSheet("background:transparent;")

    def show_result(self, r: PlayerResult):
        if self.champion_key is not None and r is not self.result:  # 遊戲中新查的人：熟練度跟著戰績一起回來
            self.mastery, self.mastery_loaded = r.mastery, True
        self.result = r
        self.clear_skeleton()
        self.tiles.set_games([])
        self.streak.hide()
        self.feed_tag.hide()
        self.show_champion()
        self.puuid = "" if r.error else (r.puuid or "")
        self.show_blacklist()
        if r.error:
            if r.error.kind == "key":
                self.big.setText(grey("—")); self.big.setToolTip("")
            else:
                self.big.setText(f"<span style='color:{RED}'>{ERROR_SHORT.get(r.error.kind, '查詢失敗')}</span>")
                self.big.setToolTip(str(r.error))
            self.kda.setText("")
            return
        self.big.setToolTip("")
        s = r.stats
        self.set_title(grey(f"Lv {s.level if s.level is not None else '?'}"))
        self.show_ranks(r.ranks)
        if s.games:
            self.big.setText(f"<b style='font-size:24px;color:{wr_color(s.win_rate)}'>{round(s.win_rate * 100)}%</b>"
                             f"<br>{grey(f'{s.wins}勝{s.games - s.wins}敗')}")
            self.kda.setText(kda_html(s.kda, s.kd))
        else:
            self.big.setText(grey("近期沒有對戰")); self.kda.setText("")
        games = game_summaries(r.puuid, r.matches, self.win.feeding())
        self.tiles.set_games(games)  # 全是重開局時統計為空，方塊仍列出
        self.feed_tag.setVisible(any(x.feeding for x in games))
        self.win.prefetch_tip_icons(games)
        if st := streak(games):
            color, bg = (GREEN, "#2e4a3a") if st.win else (RED, "#4a2e2e")
            self.streak.setText(f"{'連勝中' if st.win else '連敗中'}({st.games})")
            self.streak.setStyleSheet(f"background:{bg};color:{color};border-radius:10px;padding:2px 8px;")
            self.streak.show()
        e = r.encounter
        if e:
            when = "上一場" if e.games_ago == 0 else f"{e.games_ago + 1} 場前"
            side = "隊友" if e.ally else "敵人"
            self.chip.setText(f"{self.win.icon_html(e.champion_id, 16)} {when}的{side} · "
                              f"{self.win.champions.name(e.champion_id)} {e.kills}/{e.deaths}/{e.assists}")
            self.chip.setStyleSheet(f"background:{'#2e4a3a' if e.ally else '#4a2e2e'};border-radius:10px;padding:2px 8px;")
            self.chip.show()

    def show_ranks(self, ranks):
        if ranks is None:
            self.rank.setText(grey("牌位查不到")); self.rank.setToolTip(""); return
        parts, tips = [], []
        for q in ("單雙", "彈性"):
            r = ranks.get(q)
            parts.append(f"{grey(q)} {r if r else grey('未定級')}")
            if r:
                games = r.wins + r.losses
                tips.append(f"{q} {r.wins}勝{r.losses}敗" + (f"（{round(r.wins / games * 100)}%）" if games else ""))
        self.rank.setText(" · ".join(parts)); self.rank.setToolTip("\n".join(tips))

    def _tile_tip(self, g: GameSummary) -> str:
        """單場提示框：英雄頭像、K/D/A 與勝敗、英雄與模式、裝備列、主符文與召喚師技能列；圖示缺少時以文字替代。
        rows 是表格的列，單場說明（重開局、送頭場）接在最後。"""
        w, c = self.win, self.win.champions
        result = f"<span style='color:{GREEN if g.win else RED}'>{'勝' if g.win else '敗'}</span>"
        head = w.icon_html(g.champion, 44)
        rows = [f"<tr>{f'<td rowspan=2>{head}</td>' if head else ''}"
                f"<td><b style='font-size:15px'>{g.kills} / {g.deaths} / {g.assists}</b> {result}</td></tr>",
                f"<tr><td><b>{c.name(g.champion)}</b> {grey(g.mode)}</td></tr>"]
        if g.items:
            rows.append(f"<tr><td colspan='2' style='padding-top:4px'>"
                        f"{'&nbsp;'.join(w.asset_html(c.item(i), 26) for i in g.items)}</td></tr>")
        rune = c.rune(g.keystone) if g.keystone else None
        icons = ([w.asset_html(rune, 24, fallback="")] if rune else []) + [w.asset_html(c.spell(s), 22) for s in g.spells]
        if rune or g.spells:
            rows.append(f"<tr><td colspan='2'>{'&nbsp;'.join(i for i in icons if i)}"
                        f"{'&nbsp;' + grey(rune.name) if rune else ''}</td></tr>")
        if g.remake:
            rows.append(f"<tr><td colspan='2'>{grey('重開局（不計入統計）')}</td></tr>")
        if g.feeding:
            rows.append(f"<tr><td colspan='2' style='color:{NEON};padding-top:4px'>"
                        f"送頭場：死亡 {g.deaths} · 每分鐘死亡 {g.death_rate:.2f}</td></tr>")
        return f"<table cellspacing='0' cellpadding='2'>{''.join(rows)}</table>"

    def set_champion(self, key: int | None, m: Mastery | None = None, *, loaded: bool = False):
        """設定本場英雄；loaded=True 代表熟練度已查完（m 為 None 即沒玩過）。"""
        self.champion_key, self.mastery, self.mastery_loaded = key, m, loaded
        self.show_champion()

    def show_champion(self):
        """本場英雄的熟練度（只在遊戲中）。"""
        if self.champion_key is None:
            self.champ.hide(); return
        cid = self.win.champions.by_key(self.champion_key)
        head = f"{self.win.icon_html(cid, 18) if cid else ''} {self.win.champions.name(cid) if cid else '本場英雄'}"
        m = self.mastery
        if self.hidden:
            body = ""
        elif m is not None:
            body = f"熟練 {m.level} 級 · {points(m.points)} · {grey(ago(m.last_play) + '玩過')}"
        elif self.mastery_loaded:
            body = grey("沒玩過（或查不到）")
        else:
            body = grey("查詢中…")
        self.champ.setText(f"{head}　{body}"); self.champ.show()


ROW_COLORS = {"active": GREEN, "ok": GREEN, "cooling": ORANGE, "busy": BLUE, "error": RED}


def key_row_html(text: str, tone: str) -> str:
    """設定頁 Key 清單每一列的狀態（文字與色調見 rotation.key_row_status）。"""
    if tone == "grey":
        return grey(text)
    weight = "font-weight:bold;" if tone == "active" else ""
    return f"<span style='color:{ROW_COLORS[tone]};{weight}'>{text}</span>"


class KeyList(QWidget):
    """設定頁的 Key 清單（版型 A）：一列一組 Key（#序號、遮罩過的 Key、狀態、✕），下方輸入框新增。
    清單本身在 cfg.api_keys（keys.KeyPool）；新增、刪除、驗證都交給 MainWindow。"""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(12); self.grid.setColumnStretch(2, 1)
        v.addLayout(self.grid)
        add = QHBoxLayout()
        self.input = QLineEdit(); self.input.setPlaceholderText("貼上 RGAPI-… 後按 Enter")
        self.input.returnPressed.connect(self.add)
        btn = QPushButton("新增"); btn.setObjectName("secondary"); btn.clicked.connect(self.add)
        add.addWidget(self.input, 1); add.addWidget(btn)
        v.addLayout(add)
        self.error = QLabel(); self.error.hide()
        v.addWidget(self.error)

    def add(self):
        text = self.input.text().strip()
        if not text:
            return
        if self.win.accept_key(find_api_key(text) or text):
            self.input.clear(); self.show_error("")
        else:
            self.show_error("已經在清單裡")

    def show_error(self, text: str):
        self.error.setText(f"<span style='color:{RED}'>{text}</span>"); self.error.setVisible(bool(text))

    def refresh(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.setParent(None); w.deleteLater()
        self.rows: list[tuple[str, QLabel, QLabel]] = []  # (Key, #序號, 狀態)：每秒更新使用中與冷卻倒數
        for i, k in enumerate(self.win.cfg.api_keys):
            num, status = QLabel(), QLabel()
            rm = QPushButton("✕"); rm.setObjectName("icon"); rm.setToolTip("刪除這組 Key")
            rm.clicked.connect(lambda _=False, key=k.key: self.win.remove_key(key))
            for col, w in enumerate((num, QLabel(mask(k.key)), status, rm)):
                self.grid.addWidget(w, i, col)
            self.rows.append((k.key, num, status))
        self.tick()

    def tick(self):
        """只換文字、不重建列：倒數每秒跑一次，滑鼠停在 ✕ 上也不會閃。"""
        active, pool, rot = self.win.active_key(), self.win.cfg.api_keys, self.win.rotation
        for i, (key, num, status) in enumerate(getattr(self, "rows", [])):
            if not (entry := pool.get(key)):
                continue
            num.setText(f"<b>#{i + 1}</b>" if key == active else grey(f"#{i + 1}"))
            status.setText(key_row_html(*key_row_status(entry, key == active, rot.cooldown(key))))


class SettingsPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win, cfg = win, win.cfg
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("<b style='font-size:16px'>設定</b>"), 1)
        back = QPushButton("← 返回"); back.setObjectName("secondary"); back.clicked.connect(win.show_results)
        top.addWidget(back)
        v.addLayout(top)
        panel = QFrame(); panel.setObjectName("panel"); f = self.form = QFormLayout(panel)
        v.addWidget(panel); v.addStretch()

        self.keys = KeyList(win)
        f.addRow("API Key", self.keys)
        self.key_state = QLabel(); self.key_state.setWordWrap(True)
        upd = QPushButton("取得新 Key"); upd.setObjectName("secondary"); upd.clicked.connect(win.start_key_refresh)
        row = QHBoxLayout(); row.addWidget(self.key_state, 1); row.addWidget(upd)
        f.addRow("", row)
        f.addRow("", QLabel(grey("在網站複製 Key，程式會自動加入清單")))

        self.me = QLineEdit(cfg.my_riot_id); self.me.setPlaceholderText("名稱#標籤")
        self.me.editingFinished.connect(self.save_me)
        f.addRow("你的 Riot ID", self.me)
        self.me_state = QLabel(); self.me_state.setWordWrap(True)
        f.addRow("", self.me_state); f.setRowVisible(self.me_state, False)

        self.games = QSpinBox(); self.games.setRange(5, 20); self.games.setSuffix(" 場")
        self.games.setValue(cfg.recent_games); self.games.valueChanged.connect(self.save_games)
        self.games_hint = QLabel()
        col = QVBoxLayout(); col.setSpacing(2); col.addWidget(self.games); col.addWidget(self.games_hint)
        f.addRow("查詢場數", col)
        self.update_games_hint()

        self.boot = Switch("縮在系統匣"); self.boot.setChecked(cfg.autostart)
        self.boot.toggled.connect(self.save_autostart)
        if not autostart.supported():
            self.boot.setEnabled(False); self.boot.setToolTip("只有打包後的 exe 能用")
        f.addRow("開機時啟動", self.boot)

        # 送頭場判定：死亡速率（死亡數 ÷ 整局分鐘）≥ 門檻；改了立即存檔並重畫卡片，不重新稽查
        head = QLabel("<b>送頭場判定</b>"); head.setStyleSheet("padding-top:10px;"); f.addRow(head)
        self.feed_rate = QDoubleSpinBox(); self.feed_rate.setRange(0.05, 5); self.feed_rate.setDecimals(2)
        self.feed_rate.setSingleStep(0.05); self.feed_rate.setSuffix(" 次/分")
        self.feed_rate.setValue(cfg.feeding_deaths_per_minute)
        self.feed_rate.setKeyboardTracking(False); self.feed_rate.valueChanged.connect(self.save_feeding)
        f.addRow("每分鐘死亡 ≥", self.feed_rate)
        f.addRow("", QLabel(grey("死亡數 ÷ 整局時長（分鐘）≥ 門檻 → 送頭場")))

    def save_me(self):
        text = self.me.text().strip()
        cfg = self.win.cfg
        if text == cfg.my_riot_id:
            return
        rid = RiotId.parse(text) if text else None
        if text and (rid is None or "進入組隊房間" in text):
            self._me_state(f"<span style='color:{RED}'>格式是 名稱#標籤</span>"); return
        cfg.my_riot_id, cfg.my_puuids = text, {}  # 換了本人：每組 Key 都用新的 Riot ID 重查
        cfg.save()
        self._me_state("")
        if rid and self.win.active_key():
            self._me_state(grey("確認中…"))
            self.win.lookup_me(rid)

    def _me_state(self, html: str):
        self.me_state.setText(html); self.form.setRowVisible(self.me_state, bool(html))

    def show_me_lookup(self, error: str):
        self._me_state(f"<span style='color:{RED}'>{error}</span>" if error
                              else f"<span style='color:{GREEN}'>✓ 已確認</span>")

    def save_games(self, n: int):
        self.win.cfg.recent_games = n; self.win.cfg.save()
        self.update_games_hint()

    def update_games_hint(self):
        n = self.games.value()
        self.games_hint.setText(grey(f"5 人房最多約 {4 * n + 15} 次請求，進遊戲查對面再約 {5 * n + 22} 次。"
                                     "每組 Key 上限每 2 分鐘 100 次。"))

    def save_autostart(self, on: bool):
        self.win.cfg.autostart = on; self.win.cfg.save()
        autostart.apply(on)

    def save_feeding(self):
        cfg = self.win.cfg
        cfg.feeding_deaths_per_minute = self.feed_rate.value()
        cfg.save()
        self.win.refresh_cards()  # 用記憶體裡的對戰重算，不發 Riot API 請求


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("血壓稽查器")
        self.setWindowIcon(app_icon())
        self.resize(880, 600)  # 房間畫面一列放得下 10 個英雄頭像方塊
        self.cfg = Config.load()
        self.champions = Champions()
        # 每組 Key 各自的限速（跨次稽查也遵守）與使用中的 Key；冷卻時輪替到下一組（#11）
        self.rotation = KeyRotation(lambda: self.cfg.api_keys)
        self.cards: dict[str, PlayerCard] = {}
        self.room: list[RiotId] = []
        self.histories: dict = {}
        self.running = False
        self.pending: tuple[list[RiotId], bool, bool] | None = None  # (成員, 自動, 強制重查)
        self.icons_loading: set[str] = set()
        self.assets_ready: set[str] = set()  # 已在本機的提示框圖示，免得每次重畫都查檔案
        self.asset_slots = threading.Semaphore(4)  # 提示框圖示一次可能上百個，限制同時下載數
        self.waiting_key = False
        self.audit_key = ""  # 上一次稽查用的 Key；Key 有問題時知道是哪一組
        self.me_note = ""
        self.switch_note = ""  # 稽查中換了 Key、從頭重查時顯示在狀態列
        self.quitting = False
        self.view = "room"  # "room"：房間成員單欄；"game"：遊戲中左右兩隊
        self.game_id = None  # 已經查過的對局，避免重複查
        self.enemies: set[str] = set()  # 對面的標籤
        self.pending_game: dict | None = None
        self.me_key = ""  # 本人卡片的 key；可能多排裡本人的標籤是「你」

        self.bridge = Bridge()
        b = self.bridge
        b.player.connect(self.on_player); b.outcome.connect(self.on_outcome); b.me.connect(self.on_me)
        b.done.connect(self.on_done); b.champions.connect(self.on_champions)
        b.key_checked.connect(self.on_key_checked); b.me_lookup.connect(self.on_me_lookup)
        b.versions.connect(self.cfg.add_versions)
        b.icon_ready.connect(self.refresh_cards)
        b.game.connect(self.on_game); b.mastery.connect(self.on_mastery)
        b.restart.connect(self.on_restart); b.key_failed.connect(self.on_key_failed)
        b.audit_error.connect(self.on_audit_error)

        root = QWidget(); self.setCentralWidget(root)
        h = QHBoxLayout(root)
        sidebar = self._build_sidebar()
        self.right = QStackedWidget()
        self.right.addWidget(self._build_results())
        self.settings = SettingsPage(self)
        self.right.addWidget(self.settings)
        h.addWidget(self.right, 1); h.addWidget(sidebar)

        self._build_tray()
        threading.Thread(target=self._load_champions, daemon=True).start()
        QGuiApplication.clipboard().dataChanged.connect(self.on_clipboard)
        self.progress_timer = QTimer(self); self.progress_timer.timeout.connect(self.update_progress)
        self.poll_timer = QTimer(self); self.poll_timer.timeout.connect(self.poll_game)
        self.poll_tick = QTimer(self); self.poll_tick.timeout.connect(self.update_poll_bar)
        self.poll_until = 0.0
        self.key_tick = QTimer(self); self.key_tick.timeout.connect(self.tick_keys)
        self.key_tick.start(1000)  # 設定頁的「使用中」與冷卻倒數
        self.update_key_status()
        if self.cfg.autostart:
            autostart.apply(True)  # exe 搬過位置時更新路徑
        if not self.cfg.api_keys:
            self.show_settings()

    # ─── 版面 ───
    def _build_sidebar(self) -> QWidget:
        side = QWidget(); side.setFixedWidth(230)
        v = QVBoxLayout(side); v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel(f"<span style='color:{BLUE}'>● 正在監看剪貼簿</span>"))
        v.addWidget(QLabel("或手動貼上房間訊息"))
        self.box = QPlainTextEdit(); self.box.setPlaceholderText("名稱 #標籤 進入組隊房間")
        self.box.setFixedHeight(80)
        v.addWidget(self.box)
        self.audit_btn = QPushButton("稽查"); self.audit_btn.clicked.connect(self.audit_box)
        v.addWidget(self.audit_btn)
        opgg = QPushButton("在 op.gg 開啟"); opgg.setObjectName("secondary"); opgg.clicked.connect(self.open_opgg)
        v.addWidget(opgg)
        self.game_btn = QPushButton("查對面"); self.game_btn.setObjectName("secondary")
        self.game_btn.setToolTip("進入載入畫面後才查得到。房間稽查完 90 秒後會每 20 秒自動查，手動查過就不再自動查")
        self.game_btn.clicked.connect(lambda: self.check_game(manual=True))
        v.addWidget(self.game_btn)
        self.err = QLabel(); self.err.setWordWrap(True); self.err.setStyleSheet(f"color:{RED}")
        v.addWidget(self.err)
        v.addStretch()
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setStyleSheet(f"color:{GREY}")
        v.addWidget(self.status)
        self.poll_label = QLabel(); self.poll_label.setStyleSheet(f"color:{GREY}")
        self.poll_bar = QProgressBar(); self.poll_bar.setTextVisible(False); self.poll_bar.setFixedHeight(4)
        self.poll_bar.setRange(0, 1000)
        for w in (self.poll_label, self.poll_bar):
            w.hide(); v.addWidget(w)
        bottom = QHBoxLayout()
        self.key_status = QLabel(); self.key_status.setWordWrap(True)
        bottom.addWidget(self.key_status, 1)
        self.gear = QPushButton("⚙"); self.gear.setObjectName("icon"); self.gear.setCheckable(True)
        self.gear.setToolTip("設定"); self.gear.clicked.connect(self.toggle_settings)
        bottom.addWidget(self.gear)
        v.addLayout(bottom)
        return side

    def _build_results(self) -> QWidget:
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0)
        self.key_banner = QFrame(); self.key_banner.setObjectName("keybanner")
        kb = QHBoxLayout(self.key_banner)
        self.key_banner_text = QLabel(); self.key_banner_text.setWordWrap(True)
        self.key_banner_btn = QPushButton()
        self.key_banner_btn.clicked.connect(self._key_banner_action)
        kb.addWidget(self.key_banner_text, 1); kb.addWidget(self.key_banner_btn)
        self.key_banner.hide()
        v.addWidget(self.key_banner)
        self.banner = QVBoxLayout(); self.banner.setSpacing(2); v.addLayout(self.banner)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.addWidget(self.scroll, 1)
        self._show_empty()
        return page

    def _show_empty(self):
        empty = QLabel("在遊戲聊天室複製\n「名稱 #標籤 進入組隊房間」\n就會自動稽查")
        empty.setAlignment(Qt.AlignCenter); empty.setStyleSheet(f"color:{GREY};font-size:15px")
        self.scroll.setWidget(empty)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("血壓稽查器")
        menu = QMenu()
        show = QAction("顯示", self); show.triggered.connect(self.show_window)
        quit_ = QAction("結束", self); quit_.triggered.connect(self.quit)
        menu.addAction(show); menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def show_window(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def quit(self):
        self.quitting = True
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, e):
        if self.quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            return super().closeEvent(e)
        e.ignore(); self.hide()  # ✕ 縮到系統匣，剪貼簿監看繼續

    def notify(self):
        """自動稽查時提醒：縮在系統匣就放回工作列（最小化），再閃爍工作列，不搶焦點。"""
        if not self.isVisible():
            self.showMinimized()
        if not self.isActiveWindow():
            QApplication.alert(self)

    def toggle_settings(self):
        self.show_results() if self.right.currentIndex() == 1 else self.show_settings()

    def show_settings(self):
        self.right.setCurrentIndex(1); self.gear.setChecked(True)

    def show_results(self):
        self.right.setCurrentIndex(0); self.gear.setChecked(False)

    # ─── API Key ───
    def active_key(self) -> str:
        """使用中的 Key：一直用同一組，冷卻了才依序換下一組（rotation.KeyRotation）；沒有可用的時是空字串。"""
        return self.rotation.current()

    def client_for(self, key: str) -> RiotClient:
        """指定 Key 的客戶端（遵守它自己的限速，冷卻就等）：補查本人、換算黑名單等只能用這組 Key 的請求。"""
        return self.rotation.client(key)

    def tick_keys(self):
        if self.right.currentIndex() == 1:
            self.settings.keys.tick()

    def usable_keys(self, but: str = "") -> list[str]:
        return [k.key for k in self.cfg.api_keys if k.usable and k.key != but]

    def key_warming(self) -> bool:
        """沒有可用的 Key，但有新 Key 在等 Riot 啟用：這段期間的稽查先排隊。"""
        return not self.active_key() and any(k.status == WARMING for k in self.cfg.api_keys)

    def update_key_status(self):
        pool = self.cfg.api_keys
        if self.waiting_key:
            text = f"<span style='color:{BLUE}'>等你複製新 Key</span>"
        else:
            summary = pool.summary()
            color = {"ok": GREY, "busy": BLUE, "error": RED}[summary.tone]
            text = f"<span style='color:{color}'>{summary.text}</span>"
        self.key_status.setText(text)
        self.settings.key_state.setText(text)
        self.settings.keys.refresh()
        if pool.all_bad:
            self.show_key_banner("所有 API Key 都失效了，請重新產生", "取得新 Key")

    def on_keys_changed(self):
        """清單或狀態變了：存檔、重畫。本人與黑名單每組 Key 各有一份 PUUID，換使用中的 Key 不必重查。"""
        self.cfg.save()
        self.update_key_status()

    def start_key_refresh(self):
        self.waiting_key = True
        self.update_key_status()
        QDesktopServices.openUrl(QUrl(KEY_PAGE))

    def accept_key(self, key: str) -> bool:
        """加入 Key 清單（加在最後）並驗證；已經在清單裡就不加，回 False。"""
        self.waiting_key = False
        entry = self.cfg.api_keys.add(key, time.time())
        if entry is None:
            self.update_key_status(); return False
        self.key_banner.hide()
        self.on_keys_changed()
        self.check_key(entry.key)
        return True

    def remove_key(self, key: str):
        if self.cfg.remove_key(key):  # 本人與黑名單中它那個版本一併刪掉
            self.key_banner.hide()
            self.on_keys_changed()

    def check_key(self, key: str):
        def check():
            try:
                self.client_for(key).check_key(); self.bridge.key_checked.emit(key, "", "")
            except RiotError as e:
                self.bridge.key_checked.emit(key, e.kind, str(e))
        threading.Thread(target=check, daemon=True).start()

    def key_is_fresh(self, key: str) -> bool:
        entry = self.cfg.api_keys.get(key)
        return bool(entry) and time.time() - entry.added_at < KEY_WARMUP

    def warm_up_key(self, key: str):
        """剛產生的 Key 要等 Riot 啟用（約 1–2 分鐘）：每 15 秒重驗一次；沒有其他可用的 Key 時稽查先排隊。"""
        self.cfg.api_keys.set_status(key, WARMING)
        self.key_banner.hide()
        self.on_keys_changed()
        QTimer.singleShot(KEY_RECHECK_MS, lambda: self.check_key(key) if self.cfg.api_keys.get(key) else None)

    def on_key_checked(self, key: str, kind: str, error: str):
        if not self.cfg.api_keys.get(key):
            return  # 驗證期間被刪掉了
        if kind == "key" and self.key_is_fresh(key):
            self.warm_up_key(key); return
        if kind == "key":
            self.cfg.api_keys.set_status(key, BAD)  # 不自動刪除，由使用者決定
        else:
            # 驗證通過；或是連線／伺服器問題，分不出 Key 好壞，先當作可用
            self.cfg.api_keys.set_status(key, OK)
            if error:
                self.status.setText(f"驗證 Key 時發生錯誤：{error}")
        self.on_keys_changed()
        if kind != "key":
            self.backfill_key(key)
        if not error:
            if self.pending and not self.running and not self.key_warming():
                members, auto, force = self.pending
                self.pending = None
                self.start_room(members, auto, force)
        QApplication.alert(self)

    def backfill_key(self, key: str):
        """Key 可用之後：本人用 Riot ID 查一次這組 Key 的 PUUID；黑名單每筆用既有的可用 Key 換算出這組的版本。
        只補還沒有這組版本的，重複呼叫不會多查。"""
        rid = RiotId.parse(self.cfg.my_riot_id) if self.cfg.my_riot_id else None
        if rid and not self.cfg.my_puuid(key):
            self.lookup_me(rid, [key])
        entries, sources = self.cfg.blacklist.snapshot(), self.usable_keys(but=key)
        if entries and sources:
            threading.Thread(target=lambda: self.bridge.versions.emit(puuids.backfill(entries, key, sources, self.client_for)),
                             daemon=True).start()

    def show_key_banner(self, text: str, button: str):
        self.key_banner_text.setText(text); self.key_banner_btn.setText(button)
        self.key_banner.show()

    def _key_banner_action(self):
        if self.cfg.api_keys:
            self.start_key_refresh()
        else:
            self.show_settings()

    # ─── 本人 ───
    def lookup_me(self, rid: RiotId, keys: list[str] | None = None):
        """用本人的 Riot ID 在每組 Key（預設：所有可用的 Key）下各查一次 PUUID。"""
        keys = self.usable_keys() if keys is None else keys

        def run():
            found, e = puuids.lookup_me(rid, keys, self.client_for)
            error = "" if found or e is None else "找不到這個 Riot ID" if e.kind == "not_found" else str(e)
            self.bridge.me_lookup.emit(str(rid), found, error)
        threading.Thread(target=run, daemon=True).start()

    def on_me_lookup(self, rid: str, found: dict, error: str):
        if rid != self.cfg.my_riot_id:
            return
        found = {k: v for k, v in found.items() if self.cfg.api_keys.get(k)}  # 查詢期間被刪掉的 Key 不存
        if found:
            self.cfg.my_puuids.update(found); self.cfg.save()
        self.settings.show_me_lookup(error)

    def me(self, key: str) -> Me:
        """本人；PUUID 取 key（這次稽查所用的 Key）的版本。"""
        return Me(RiotId.parse(self.cfg.my_riot_id) if self.cfg.my_riot_id else None, self.cfg.my_puuid(key))

    def is_me(self, m: RiotId) -> bool:
        me = RiotId.parse(self.cfg.my_riot_id) if self.cfg.my_riot_id else None
        return bool(me and m.same_as(me))

    def on_me(self, me: Me, key: str):
        """稽查中查到本人改名，或補查到 key 的 PUUID。改名不影響其他 Key 的 PUUID（帳號不變）；
        其他還沒有版本的 Key 用新的 Riot ID 補查。"""
        old = self.cfg.my_riot_id
        self.cfg.my_riot_id = str(me.riot_id)
        if self.cfg.api_keys.get(key):
            self.cfg.my_puuids[key] = me.puuid
        self.cfg.save()
        self.settings.me.setText(self.cfg.my_riot_id)
        if old and RiotId.parse(old) != me.riot_id:
            self.me_note = f"你的 Riot ID 已更新為 {me.riot_id}"
        if missing := [k for k in self.usable_keys() if not self.cfg.my_puuid(k)]:
            self.lookup_me(me.riot_id, missing)

    def toggle_blacklist(self, key: str, puuid: str):
        """切換黑名單（puuid 出自 key）；加入時用其他可用的 Key 各換算一份，換 Key 稽查也認得。"""
        if self.cfg.toggle_blacklist(key, puuid) and (targets := self.usable_keys(but=key)):
            threading.Thread(target=lambda: self.bridge.versions.emit(puuids.spread(key, puuid, targets, self.client_for)),
                             daemon=True).start()

    # ─── 雜項 ───
    def _load_champions(self):
        try:
            self.bridge.champions.emit(Champions.load())
        except Exception:
            pass  # 沒有英雄中文名時仍可用英文 id 查

    def on_champions(self, champs: Champions):
        self.champions = champs
        self.refresh_cards()

    def refresh_cards(self):
        for c in self.cards.values():
            if c.result:
                c.show_result(c.result)
            else:
                c.show_champion()

    def feeding(self) -> float:
        """設定頁的送頭場門檻：每分鐘死亡次數。"""
        return self.cfg.feeding_deaths_per_minute

    def icon_html(self, champion_id: str, size: int) -> str:
        """英雄頭像的 <img>；還沒下載好時是空字串。"""
        path = self.icon_file(champion_id)
        if path is None:
            return ""
        return f"<img src='{QUrl.fromLocalFile(str(path)).toString()}' width={size} height={size} align=middle>"

    def icon_file(self, champion_id: str) -> Path | None:
        """英雄頭像的本機檔案；本機還沒有就在背景下載（好了再重畫卡片），先回 None。"""
        cid = self.champions.canonical(champion_id)
        path = icon_path(cid)
        if path.exists():
            return path
        if self.champions.version and cid not in self.icons_loading:
            self.icons_loading.add(cid)

            def load():
                try:
                    download_icon(self.champions, cid); self.bridge.icon_ready.emit()
                except Exception:
                    pass
            threading.Thread(target=load, daemon=True).start()
        return None

    def asset_html(self, icon: Icon, size: int, fallback: str | None = None) -> str:
        """裝備／符文／召喚師技能的 <img>；還沒下載好時是 fallback（預設為灰色的名稱）。"""
        path = self.asset_file(icon)
        if path is None:
            return grey(icon.name) if fallback is None else fallback
        return f"<img src='{QUrl.fromLocalFile(str(path)).toString()}' width={size} height={size} align=middle>"

    def asset_file(self, icon: Icon) -> Path | None:
        """裝備／符文／召喚師技能圖示的本機檔案；本機還沒有就排進背景下載，先回 None。
        這些圖示只出現在提示框（滑鼠停上去才產生），下載好不必重畫卡片。"""
        path = icon_dir() / icon.file
        if icon.file in self.assets_ready or path.exists():
            self.assets_ready.add(icon.file)
            return path
        if icon.url and icon.file not in self.icons_loading:
            self.icons_loading.add(icon.file)

            def load():
                with self.asset_slots:
                    try:
                        download_asset(icon)
                    except Exception:
                        pass  # 斷網或下載失敗：提示框以文字替代
            threading.Thread(target=load, daemon=True).start()
        return None

    def prefetch_tip_icons(self, games: list[GameSummary]):
        """稽查結果回來後，在背景先下載單場提示框要用的圖示；已在本機的不重新下載。"""
        c = self.champions
        for g in games:
            for icon in ([c.item(i) for i in g.items] + [c.spell(s) for s in g.spells]
                         + ([c.rune(g.keystone)] if g.keystone else [])):
                self.asset_file(icon)

    def open_opgg(self):
        members = [m for m in self.room or parse_room_messages(self.box.toPlainText()).members if not m.hidden]
        if members:
            q = ",".join(str(m) for m in members)
            QDesktopServices.openUrl(QUrl("https://op.gg/lol/multisearch/tw?summoners=" + urllib.parse.quote(q)))

    # ─── 稽查 ───
    def on_clipboard(self):
        text = QGuiApplication.clipboard().text()
        key = find_api_key(text)
        if key:  # 新 Key 加入清單，不取代原本的
            if not self.accept_key(key):
                self.settings.keys.show_error("剛複製的 Key 已經在清單裡")
            return
        members = room_members_in_clipboard(text)
        if members:
            self.start_room(members, auto=True)

    def audit_box(self):
        pr = parse_room_messages(self.box.toPlainText())
        self.err.setText("看不懂這幾行：\n" + "\n".join(pr.bad_lines) if pr.bad_lines else "")
        if not pr.members:
            self.status.setText("沒有可稽查的 Riot ID"); return
        self.start_room(pr.members, auto=False, force=True)

    def start_room(self, members: list[RiotId], auto: bool, force: bool = False):
        if self.running or self.key_warming():
            self.pending = (members, auto, force)
            if self.key_warming():
                self.status.setText("新 Key 生效後自動稽查")
            return
        # 使用中的 Key 換了：舊結果的 PUUID 出自別組 Key，不能沿用，整房重查（ADR 0002）
        key_changed = bool(self.audit_key) and self.active_key() != self.audit_key
        change = "new" if force or key_changed or self.view == "game" else room_change(self.room, members)
        if change == "same":
            self.status.setText("名單沒變，不重查"); return
        if auto:
            self.box.setPlainText("\n".join(f"{m.name} #{m.tag} 進入組隊房間" for m in members))
            self.err.setText("")
            self.notify()
        if not self.cfg.api_keys:
            self.show_key_banner("尚未設定 API Key", "設定"); return
        if not self.active_key():
            self.show_key_banner("所有 API Key 都失效了，請重新產生", "取得新 Key"); return
        self.show_results()
        if change == "shrink":
            self._shrink(members); return
        self.poll_timer.stop()
        if change == "new":
            self._reset_cards()
            self.histories = {}
        new = members_to_query(self.room, members, change)  # 本人也查，也有卡片
        self.room = members
        for m in new:
            c = self._add_card(m)
            if m.hidden:
                c.show_hidden(m.name)
            elif self.is_me(m):
                self._mark_me(c, key_of(m))
        query = [m for m in new if not m.hidden]
        self._run_audit(query)

    def _reset_cards(self):
        self._clear_banner()
        self.cards = {}
        self.view, self.enemies = "room", set()
        inner = QWidget(); self.cards_box = QVBoxLayout(inner); self.cards_box.addStretch()
        self.scroll.setWidget(inner)

    def _reset_game_view(self):
        """遊戲中：左邊我方、右邊對面。"""
        self._clear_banner()
        self.cards = {}
        self.view = "game"
        inner = QWidget(); g = self.game_grid = QGridLayout(inner)
        g.setVerticalSpacing(6)
        for col, (title, color) in enumerate((("我方", GREEN), ("對面", RED))):
            g.addWidget(QLabel(f"<b style='color:{color};font-size:15px'>{title}</b>"), 0, col)
            g.setColumnStretch(col, 1)
        g.setRowStretch(6, 1)  # 兩隊各最多 5 人，多的空間留在最下面
        self.game_rows = [0, 0]
        self.scroll.setWidget(inner)
        if self.width() < GAME_WIDTH:
            self.resize(GAME_WIDTH, self.height())

    def _add_card(self, m: RiotId, side: int | None = None, card: PlayerCard | None = None) -> PlayerCard:
        """房間：加到單欄最後；遊戲中：side 0 = 我方、1 = 對面，依序往下排。"""
        c = card or PlayerCard(m, self)
        self.cards[key_of(m)] = c
        c.set_compact(self.view == "game")  # 房間階段的卡片搬進對局畫面時也換成小方塊
        if self.view == "game":
            self.game_rows[side] += 1
            self.game_grid.addWidget(c, self.game_rows[side], side)
        else:
            self.cards_box.insertWidget(self.cards_box.count() - 1, c)
        return c

    def _mark_me(self, card: PlayerCard, k: str):
        """標示本人；房間中把本人的卡片移到最前面（遊戲中開局時就已排在我方第一個）。"""
        card.mark_me(); self.me_key = k
        if self.view == "room":
            self.cards_box.removeWidget(card)
            self.cards_box.insertWidget(0, card)

    def _remove_card(self, k: str):
        c = self.cards.pop(k, None)
        if c:
            c.setParent(None); c.deleteLater()

    def _shrink(self, members: list[RiotId]):
        keep = {key_of(m) for m in members}
        for k in list(self.cards):
            if k not in keep:
                self._remove_card(k)
        self.room = members
        labels = {str(m) for m in members} | {ME}
        self.histories = {l: h for l, h in self.histories.items() if l in labels}
        self.show_premades(detect_premades(self.histories))
        self.status.setText("有人離開，已移除")

    def _run_audit(self, query: list[RiotId], key: str = "", **game):
        """在背景跑一次稽查。key：指定要用的 Key（查對面時是查到對局的那組）；空字串用使用中的 Key。
        途中 Key 冷卻或失效而換 Key 時，整次稽查用新 Key 從頭重跑（rotation.run_rotating、ADR 0002）。"""
        self.running, self.done_count, self.total, self.t0 = True, 0, len(query), time.monotonic()
        self.me_note = self.switch_note = ""
        self.audit_btn.setEnabled(False)
        self.update_progress()
        self.progress_timer.start(500)
        b = self.bridge
        if key:
            self.rotation.set_current(key)
        first = self.audit_key = key or self.active_key()  # 一次稽查的所有資料都出自同一組 Key（ADR 0002）
        room, prior, games = list(self.room), self.histories, self.cfg.recent_games
        everyone = [m for m in room if not m.hidden]
        tries: list[str] = []

        def job(k: str, client: RiotClient):
            as_given = not tries and k == first
            tries.append(k)
            kw = dict(game)
            if not as_given:  # 換 Key 了：舊 Key 查到的都不要，房間（或對局）裡每個人都用新 Key 重查
                b.restart.emit(k, len(everyone))
                if "puuids" in kw:  # 查對面：觀戰資料的 PUUID 是舊 Key 的版本，用新 Key 重查一次對局
                    kw["puuids"] = self._game_puuids(k, client, room)
            Audit(client, room, self.me(k), games, query=query if as_given else everyone,
                  prior=prior if as_given else {}, on_player=lambda r: b.player.emit(r, k),
                  on_me=lambda m: b.me.emit(m, k), on_outcome=b.outcome.emit, on_mastery=b.mastery.emit,
                  **kw).execute()

        def run():
            try:
                run_rotating(self.rotation, job, on_key_error=self._key_failed_in_audit)
            except RiotError as e:
                b.audit_error.emit(str(e))
            finally:
                b.done.emit()
        threading.Thread(target=run, daemon=True).start()

    def _game_puuids(self, key: str, client: RiotClient, room: list[RiotId]) -> dict[str, str]:
        """（背景執行緒）用 key 重查一次進行中的對局，取得 room 每個人在這組 Key 的 PUUID；
        查不到的人由稽查改用 Riot ID 查帳號。"""
        me = self.cfg.my_puuid(key)
        try:
            return game_puuids(client.active_game(me) if me else None, room)
        except RiotError:
            return {}

    def _key_failed_in_audit(self, key: str):
        """（背景執行緒）稽查中回 401/403：馬上標成不可用，輪替才會跳過它；剛產生的 Key 當作還沒生效。"""
        self.cfg.api_keys.set_status(key, WARMING if self.key_is_fresh(key) else BAD)
        self.bridge.key_failed.emit(key)

    def on_key_failed(self, key: str):
        entry = self.cfg.api_keys.get(key)
        if entry and entry.status == WARMING:
            self.warm_up_key(key)  # 15 秒後重驗
        else:
            self.on_keys_changed()

    def on_restart(self, key: str, total: int):
        """換 Key 了：整次稽查用新 Key 從頭重跑，畫面上舊 Key 查到的結果全部清掉（ADR 0002）。"""
        self.audit_key, self.done_count, self.total = key, 0, total
        n = next((i + 1 for i, k in enumerate(self.cfg.api_keys) if k.key == key), "?")
        self.switch_note = f"換用 Key #{n}，從頭重查"
        for m in self.room:
            if not m.hidden and (c := self.cards.get(key_of(m))):
                c.reset()
        self._clear_banner()
        self.update_progress()
        self.update_key_status()

    def on_audit_error(self, error: str):
        self.me_note = error  # 稽查結束時顯示在狀態列
        self.update_key_status()

    def on_player(self, r: PlayerResult, key: str):
        k = key_of(r.riot_id)
        if card := self.cards.get(k):
            card.puuid_key = key
            card.show_result(r)
            if r.is_me:
                self._mark_me(card, k)
        self.done_count += 1
        self.update_progress()
        entry = self.cfg.api_keys.get(key) if r.error and r.error.kind == "key" else None
        if entry and self.key_is_fresh(entry.key):
            if entry.status != WARMING and self.view == "room":
                self.pending = (self.room, False, True)  # Key 生效後整房重查
                self.warm_up_key(entry.key)
        elif entry and entry.status != BAD:
            entry.status = BAD  # 全部失效時 update_key_status 會顯示錯誤橫幅；否則下次稽查改用下一組
            self.on_keys_changed()

    def _clear_banner(self):
        while self.banner.count():
            w = self.banner.takeAt(0).widget()
            if w:
                w.deleteLater()

    def on_outcome(self, o: AuditOutcome):
        self.histories = o.histories
        self.show_premades(o.premades)

    def show_premades(self, groups: list[PremadeGroup]):
        self._clear_banner()
        for c in self.cards.values():
            c.set_stripe(None)
        for i, g in enumerate(groups):
            color = GROUP_COLORS[i % len(GROUP_COLORS)]
            members = sorted(g.members, key=lambda m: m != ME)  # 「你」放最前面
            names = "、".join(m.split("#")[0] for m in members)
            how = f"最近連續同隊 {g.streak} 場" if g.streak >= 2 else f"近期同隊 {g.total} 場"
            side = "對面 " if g.members[0] in self.enemies else ""
            lab = QLabel(f"{side}{names} 可能多排，{how}")
            lab.setStyleSheet(f"color:{color};font-weight:bold;padding:1px 4px;"); lab.setWordWrap(True)
            self.banner.addWidget(lab)
            for m in g.members:
                if c := self.cards.get(self.me_key if m == ME else m.casefold()):
                    c.set_stripe(color)

    def update_progress(self):
        """查詢進度；被 Riot 限流時顯示倒數，免得看起來像當掉。"""
        text = f"查詢中 {self.done_count}/{self.total}"
        wait = self.rotation.limiter(self.audit_key).wait_remaining() if self.audit_key else 0
        if self.switch_note:
            text += f"\n{self.switch_note}"
        if wait >= 1:
            text += f"\n請求太多，Riot 要求等 {wait:.0f} 秒"
        self.status.setText(text)

    def on_done(self):
        self.running, self.switch_note = False, ""
        self.progress_timer.stop()
        self.audit_btn.setEnabled(True)
        msg = f"完成，用時 {time.monotonic() - self.t0:.1f} 秒"
        self.status.setText(msg + (f"\n{self.me_note}" if self.me_note else ""))
        if self.pending and not self.key_warming():
            members, auto, force = self.pending
            self.pending = None
            self.start_room(members, auto, force)
        elif self.pending_game:
            (game, key), self.pending_game = self.pending_game, None
            self.start_game(game, key)
        elif self.view == "room" and self.room:
            self.start_polling()

    # ─── 查對面（Riot 觀戰 API，不讀遊戲客戶端） ───
    def start_polling(self):
        """房間稽查完：等 90 秒後每 20 秒查一次；查到開局或手動查過就停。"""
        if self.cfg.my_puuid(self.active_key()) and not self.poll_timer.isActive():
            self.poll_until = time.monotonic() + GAME_POLL_DELAY_MS / 1000 + GAME_POLL_FOR
            self.poll_timer.start(GAME_POLL_DELAY_MS)
            self.poll_tick.start(200)
            self.update_poll_bar()

    def update_poll_bar(self):
        """右下角讀條：距離下一次自動查對面還有多久；停止自動查時隱藏。"""
        active = self.poll_timer.isActive()
        for w in (self.poll_label, self.poll_bar):
            w.setVisible(active)
        if not active:
            self.poll_tick.stop(); return
        total, left = self.poll_timer.interval(), max(self.poll_timer.remainingTime(), 0)
        self.poll_bar.setValue(round((total - left) / total * 1000))
        first = total == GAME_POLL_DELAY_MS
        self.poll_label.setText(f"{math.ceil(left / 1000)} 秒後{'開始' if first else '再'}查對面")

    def poll_game(self):
        if time.monotonic() > self.poll_until:
            self.poll_timer.stop(); return
        self.poll_timer.setInterval(GAME_POLL_MS)  # 第一次等 90 秒，之後每 20 秒
        if not self.running:
            self.check_game(manual=False)

    def check_game(self, manual: bool):
        key = self.active_key()
        puuid = self.cfg.my_puuid(key)  # 觀戰資料的 PUUID 也是這組 Key 的版本，對面要用同一組查
        if not puuid:
            if manual:
                self.status.setText("先在設定填好 API Key 和你的 Riot ID")
            return
        if manual:
            self.poll_timer.stop()  # 手動查過就不再自動查
            self.status.setText("查詢對局中…")

        def job(k: str, client: RiotClient):  # 冷卻或失效時換 Key 重查；觀戰資料的 PUUID 是查詢所用那組 Key 的版本
            if not (me := self.cfg.my_puuid(k)):
                raise RiotError("not_found", "還沒查到你在這組 Key 的 PUUID，請確認設定裡你的 Riot ID")
            return client.active_game(me), k

        def run():
            try:
                game, k = run_rotating(self.rotation, job, on_key_error=self._key_failed_in_audit)
                self.bridge.game.emit(game, "", manual, k)
            except RiotError as e:
                self.bridge.game.emit(None, str(e), manual, key)
        threading.Thread(target=run, daemon=True).start()

    def on_game(self, game: dict | None, error: str, manual: bool, key: str):
        if error:
            if manual:
                self.status.setText(error)
            return
        if not game:
            if manual:
                self.status.setText("你還沒進遊戲（載入畫面開始才查得到）")
            return
        if game.get("gameId") == self.game_id:
            if manual:
                self.status.setText("已經是這場了")
            return
        self.poll_timer.stop()
        self.start_game(game, key)

    def start_game(self, game: dict, key: str):
        """key：查到這場對局的 Key；觀戰資料裡的 PUUID 是它的版本，整次稽查都用它。"""
        if self.running:
            self.pending_game = (game, key); return
        me = self.cfg.my_puuid(key)
        allies, enemies = split_game(game, me)
        if not any(p.puuid == me for p in allies):
            self.status.setText("對局資料裡找不到你"); return
        self.game_id = game.get("gameId")
        in_room = {key_of(m): m for m in self.room}
        if key != self.audit_key:  # 房間階段的結果出自別組 Key，PUUID 對不上，不沿用（ADR 0002）
            self.histories = {}

        def rid_of(p) -> RiotId:  # 沿用房間裡的寫法，房間成員的近期戰績才對得上標籤
            r = RiotId.parse(p.label) or RiotId(p.puuid[:8], "?")
            return in_room.get(key_of(r), r)

        old = self.cards
        for c in old.values():
            c.setParent(None)  # 先拿下來，換版面時才不會被一起刪掉
        self._reset_game_view()
        query, room, known, champs = [], [], {}, {}
        sides = [[ME], []]
        hidden = 0
        allies.sort(key=lambda p: p.puuid != me)  # 本人排在我方第一個
        for i, team in enumerate((allies, enemies)):
            for p in team:
                if not p.puuid:
                    hidden += 1
                    card = self._add_card(RiotId(f"隱藏玩家{hidden}", "?"), i)
                    card.set_champion(p.champion_key); card.show_hidden()
                    continue
                rid = rid_of(p)
                room.append(rid); known[str(rid)] = p.puuid
                champs[str(rid)] = p.champion_key
                is_me = p.puuid == me
                if not is_me:
                    sides[i].append(str(rid))
                prev = old.pop(key_of(rid), None) if i == 0 else None
                label = ME if is_me else str(rid)  # 本人的近期戰績以「你」存放
                if prev and prev.result and not prev.result.error and label in self.histories:
                    card = self._add_card(rid, i, prev)  # 房間階段已查過，只補熟練度
                else:
                    card = self._add_card(rid, i)
                    query.append(rid)
                card.set_champion(p.champion_key)
                if is_me:
                    self._mark_me(card, key_of(rid))
        for c in old.values():
            c.deleteLater()
        self.room, self.enemies = room, set(sides[1])
        self.notify()
        self.show_results()
        self._run_audit(query, key, puuids=known, champions=champs, sides=sides)

    def on_mastery(self, label: str, m: Mastery | None):
        if c := self.cards.get(label.casefold()):
            c.set_champion(c.champion_key, m, loaded=True)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    app.setQuitOnLastWindowClosed(False)
    if single.notify_running():  # 已經開著：叫出那個視窗並通知使用者
        if autostart.TRAY_ARG not in sys.argv:
            box = QMessageBox(QMessageBox.Information, "血壓稽查器", "血壓稽查器已在執行")
            box.setWindowIcon(app_icon()); box.addButton("確定", QMessageBox.AcceptRole)
            box.exec()
        return
    instance = single.SingleInstance()
    w = MainWindow()
    instance.show_requested.connect(w.show_window)
    if autostart.TRAY_ARG not in sys.argv:
        w.show()
    sys.exit(app.exec())
