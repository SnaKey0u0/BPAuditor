"""PySide6 介面：左側欄（剪貼簿監看 / 手動貼上）+ 右側玩家卡片或設定頁，關閉時縮到系統匣。"""
import math
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCompleter, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QStackedWidget, QSystemTrayIcon, QToolTip, QVBoxLayout, QWidget,
)

from . import autostart, single
from .analysis import PremadeGroup, champion_stats, detect_premades, participant
from .audit import ME, Audit, AuditOutcome, Me, PlayerResult
from .champions import Champions, download_icon, icon_path
from .config import Config
from .parsing import RiotId, members_to_query, parse_room_messages, room_change, room_members_in_clipboard
from .riot import DEV_KEY_LIFETIME, KEY_PAGE, RateLimiter, RiotClient, RiotError, find_api_key

GROUP_COLORS = ["#ffb300", "#ab47bc", "#29b6f6", "#ef6c00"]
RED, GREEN, GREY, BLUE, ORANGE = "#ef5350", "#66bb6a", "#9e9e9e", "#90caf9", "#ffb300"
SKELETON = "#323844"
KEY_WARMUP = 180  # 新 Key 產生後最多等幾秒讓 Riot 啟用
KEY_RECHECK_MS = 15_000
QUEUES = {450: "ARAM", 420: "單雙", 440: "彈性", 400: "一般", 430: "一般", 490: "快速", 1700: "競技場",
          900: "URF", 1900: "URF", 1300: "極限"}
ERROR_SHORT = {"not_found": "查無此人", "network": "連線失敗", "server": "伺服器錯誤", "blocked": "連線被擋"}

QSS = """
QWidget{background:#1b1e24;color:#e0e0e0;font-family:'Microsoft JhengHei UI','Segoe UI';font-size:13px;}
QPlainTextEdit,QLineEdit,QSpinBox{background:#262a33;border:1px solid #3a3f4b;border-radius:4px;padding:3px;}
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
"""


def wr_color(rate: float) -> str:
    return RED if rate < 0.4 else GREEN if rate > 0.6 else "#e0e0e0"


def grey(text: str) -> str:
    return f"<span style='color:{GREY}'>{text}</span>"


def resource(name: str) -> Path:
    """打包後的資源在 sys._MEIPASS，開發時在專案根目錄的 assets/。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


def app_icon() -> QIcon:
    p = resource("icon.ico")
    return QIcon(str(p)) if p.exists() else QIcon()


def key_of(m: RiotId) -> str:
    return str(m).casefold()


class Bridge(QObject):
    """把背景執行緒的結果安全地送回 UI 執行緒。"""
    player = Signal(object)
    outcome = Signal(object)
    me = Signal(object)
    done = Signal()
    champions = Signal(object)
    key_checked = Signal(str, str, str)  # (key, 錯誤種類, 錯誤訊息)；種類為空字串代表有效
    me_lookup = Signal(str, str, str)  # (Riot ID, puuid, 錯誤訊息)
    icon_ready = Signal()


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


class ResultBars(QWidget):
    """近期戰績的勝敗長條：贏的朝上（綠）、輸的朝下（紅），由新到舊；每場有各自的提示框。

    用上下方向表示勝敗，不靠顏色也看得懂。位置以實際螢幕像素計算：125% 這類非整數縮放下，
    邏輯座標會落在半個像素上，反鋸齒的淡邊會讓長條寬窄、間距不一。
    """
    BAR, GAP, HALF = 5, 2, 8  # 邏輯像素：長條寬、間距、基準線上下各自的高度

    def __init__(self):
        super().__init__()
        self.items: list[tuple[bool, str]] | None = None  # None = 查詢中（灰色佔位）
        self.setMouseTracking(True)

    def set_items(self, items):
        self.items = items
        self.updateGeometry(); self.update()

    def _metrics(self, dpr: float) -> tuple[int, int, int]:
        """(長條寬, 間距, 半高)，以實際像素計。"""
        return tuple(max(round(v * dpr), 1) for v in (self.BAR, self.GAP, self.HALF))

    def sizeHint(self):
        n = 10 if self.items is None else len(self.items)
        dpr = self.devicePixelRatioF(); bar, gap, _ = self._metrics(dpr)
        return QSize(math.ceil(max(n * (bar + gap) - gap, 0) / dpr), 2 * self.HALF + 2)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, e):
        p = QPainter(self)
        t = p.deviceTransform(); dpr = t.m11()
        bar, gap, half = self._metrics(dpr)
        x0 = round(t.dx()); base = round(t.dy() + (self.height() * dpr - 1) / 2)  # 基準線那一列像素
        width = round(self.width() * dpr)

        def rect(x: int, y: int, w: int, h: int) -> QRectF:  # 實際像素 → 邏輯座標，四邊都落在整數像素上
            return QRectF((x - t.dx()) / dpr, (y - t.dy()) / dpr, w / dpr, h / dpr)

        p.setPen(Qt.NoPen)
        if self.items is None:
            p.setBrush(QColor(SKELETON)); p.drawRect(rect(x0, base - half, width, 2 * half + 1))
            return
        p.setBrush(QColor("#3a3f4b")); p.drawRect(rect(x0, base, width, 1))
        for i, (win, _) in enumerate(self.items):
            x = x0 + i * (bar + gap)
            p.setBrush(QColor(GREEN if win else RED))
            p.drawRect(rect(x, base - half, bar, half) if win else rect(x, base + 1, bar, half))

    def event(self, e):
        if e.type() == QEvent.ToolTip and self.items:
            dpr = self.devicePixelRatioF(); bar, gap, _ = self._metrics(dpr)
            i = int(e.pos().x() * dpr // (bar + gap))
            if 0 <= i < len(self.items):
                QToolTip.showText(e.globalPos(), self.items[i][1], self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)


class PlayerCard(QFrame):
    def __init__(self, rid: RiotId, win: "MainWindow"):
        super().__init__()
        self.setObjectName("card")
        self.rid, self.win, self.result = rid, win, None
        self.set_stripe(None)
        g = QGridLayout(self)
        g.setColumnStretch(1, 1)
        self.title = QLabel(f"<b style='font-size:15px'>{rid}</b>"); self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        g.addWidget(self.title, 0, 0, 1, 2)
        self.chip = QLabel(); self.chip.hide()
        g.addWidget(self.chip, 1, 0, 1, 2, alignment=Qt.AlignLeft)
        self.big = QLabel(); self.big.setMinimumWidth(80)
        g.addWidget(self.big, 2, 0, 2, 1)
        self.kda = QLabel(); g.addWidget(self.kda, 2, 1)
        self.dots = ResultBars()
        g.addWidget(self.dots, 3, 1, 1, 2, alignment=Qt.AlignLeft)  # 跨到右欄：20 場才放得下
        self.champ = QLineEdit(); self.champ.setPlaceholderText("查詢特定英雄"); self.champ.setFixedWidth(130)
        self.champ.textChanged.connect(self.update_champion)
        g.addWidget(self.champ, 0, 2, alignment=Qt.AlignRight)
        self.champ_res = QLabel(); g.addWidget(self.champ_res, 1, 2, 2, 1, alignment=Qt.AlignRight | Qt.AlignTop)
        self.set_completer()
        self.set_skeleton()

    def set_completer(self):
        c = QCompleter(self.win.champions.names()); c.setFilterMode(Qt.MatchContains)
        c.setCaseSensitivity(Qt.CaseInsensitive)
        self.champ.setCompleter(c)

    def set_stripe(self, color):
        self.setStyleSheet(f"#card{{border-left:6px solid {color or '#262a33'};}}")

    def set_skeleton(self):
        """查詢中：灰色佔位方塊。"""
        for w, size in ((self.big, (64, 40)), (self.kda, (80, 18))):
            w.setFixedSize(*size); w.setStyleSheet(f"background:{SKELETON};border-radius:4px;")
        self.dots.set_items(None)

    def clear_skeleton(self):
        for w in (self.big, self.kda):
            w.setMinimumSize(0, 0); w.setMaximumSize(16777215, 16777215); w.setStyleSheet("background:transparent;")
        self.big.setMinimumWidth(80)

    def show_result(self, r: PlayerResult):
        self.result = r
        self.clear_skeleton()
        self.dots.set_items([])
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
        self.title.setText(f"<b style='font-size:15px'>{r.riot_id}</b> "
                           + grey(f"Lv {s.level if s.level is not None else '?'}"))
        if s.games:
            self.big.setText(f"<span style='font-size:24px;color:{wr_color(s.win_rate)}'>{round(s.win_rate * 100)}%</span>"
                             f"<br>{grey(f'{s.wins}勝{s.games - s.wins}敗')}")
            self.kda.setText(f"<span style='font-size:17px'>KDA {s.kda:.1f}</span>")
            self.dots.set_items([(p["win"], self._dot_tip(m, p)) for m in r.matches
                                 if (p := participant(m, r.puuid))])
        else:
            self.big.setText(grey("近期沒有對戰")); self.kda.setText("")
        e = r.encounter
        if e:
            when = "上一場" if e.games_ago == 0 else f"{e.games_ago + 1} 場前"
            side = "隊友" if e.ally else "敵人"
            self.chip.setText(f"{self.win.icon_html(e.champion_id, 16)} {when}的{side} · "
                              f"{self.win.champions.name(e.champion_id)} {e.kills}/{e.deaths}/{e.assists}")
            self.chip.setStyleSheet(f"background:{'#2e4a3a' if e.ally else '#4a2e2e'};border-radius:10px;padding:2px 8px;")
            self.chip.show()
        self.update_champion()

    def _dot_tip(self, match: dict, p: dict) -> str:
        cid = p["championName"]
        queue = QUEUES.get(match["info"].get("queueId"), match["info"].get("gameMode", ""))
        return (f"{self.win.icon_html(cid, 32)}<br><b>{self.win.champions.name(cid)}</b> "
                f"{p['kills']}/{p['deaths']}/{p['assists']}<br>{grey(queue)}")

    def update_champion(self):
        text = self.champ.text().strip()
        r = self.result
        if not text or not r or not r.matches:
            self.champ_res.setText(""); return
        cid = self.win.champions.lookup(text)
        if not cid:
            self.champ_res.setText(grey("找不到英雄")); return
        cs = champion_stats(r.puuid, r.matches, cid)
        icon = self.win.icon_html(cid, 18)
        if not cs.games:
            self.champ_res.setText(f"{icon} {grey(f'近 {r.stats.games} 場沒玩')}"); return
        rate = cs.wins / cs.games
        self.champ_res.setText(f"{icon} {cs.games} 場 <span style='color:{wr_color(rate)}'>{round(rate * 100)}%</span>"
                               f"<br>KDA {cs.kda:.1f}")


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

        self.key = QLineEdit(cfg.api_key); self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("RGAPI-…")
        self.key.editingFinished.connect(self.save_key)
        show = QPushButton("👁"); show.setObjectName("icon"); show.setCheckable(True); show.setToolTip("顯示 Key")
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        row = QHBoxLayout(); row.addWidget(self.key, 1); row.addWidget(show)
        f.addRow("API Key", row)
        self.key_state = QLabel(); self.key_state.setWordWrap(True)
        upd = QPushButton("更新 API Key"); upd.setObjectName("secondary"); upd.clicked.connect(win.start_key_refresh)
        row = QHBoxLayout(); row.addWidget(self.key_state, 1); row.addWidget(upd)
        f.addRow("", row)
        f.addRow("", QLabel(grey("在網站複製 Key，程式會自動存入")))
        self.personal = Switch("Personal Key（不會過期）"); self.personal.setChecked(cfg.personal_key)
        self.personal.toggled.connect(self.save_personal)
        f.addRow("", self.personal)

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

    def save_key(self):
        key = self.key.text().strip()
        if key != self.win.cfg.api_key:
            self.win.accept_key(key)

    def save_me(self):
        text = self.me.text().strip()
        cfg = self.win.cfg
        if text == cfg.my_riot_id:
            return
        rid = RiotId.parse(text) if text else None
        if text and (rid is None or "進入組隊房間" in text):
            self._me_state(f"<span style='color:{RED}'>格式是 名稱#標籤</span>"); return
        cfg.my_riot_id, cfg.my_puuid = text, ""
        cfg.save()
        self._me_state("")
        if rid and cfg.api_key:
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
        self.games_hint.setText(grey(f"5 人房最多約 {4 * n + 10} 次請求。開發 Key 上限每 2 分鐘 100 次。"))

    def save_personal(self, on: bool):
        self.win.cfg.personal_key = on; self.win.cfg.save()
        self.win.update_key_status()

    def save_autostart(self, on: bool):
        self.win.cfg.autostart = on; self.win.cfg.save()
        autostart.apply(on)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("血壓稽查器")
        self.setWindowIcon(app_icon())
        self.resize(720, 600)
        self.cfg = Config.load()
        self.champions = Champions()
        self.limiter = RateLimiter()  # 全程共用，跨次稽查也遵守開發 Key 限速
        self.cards: dict[str, PlayerCard] = {}
        self.room: list[RiotId] = []
        self.histories: dict = {}
        self.running = False
        self.pending: tuple[list[RiotId], bool, bool] | None = None  # (成員, 自動, 強制重查)
        self.key_warming = False  # 新 Key 還沒被 Riot 啟用
        self.icons_loading: set[str] = set()
        self.key_bad = False
        self.key_note = ""
        self.waiting_key = False
        self.me_note = ""
        self.quitting = False

        self.bridge = Bridge()
        b = self.bridge
        b.player.connect(self.on_player); b.outcome.connect(self.on_outcome); b.me.connect(self.on_me)
        b.done.connect(self.on_done); b.champions.connect(self.on_champions)
        b.key_checked.connect(self.on_key_checked); b.me_lookup.connect(self.on_me_lookup)
        b.icon_ready.connect(self.refresh_cards)

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
        self.key_timer = QTimer(self); self.key_timer.timeout.connect(self.update_key_status)
        self.key_timer.start(60_000)
        self.progress_timer = QTimer(self); self.progress_timer.timeout.connect(self.update_progress)
        self.update_key_status()
        if self.cfg.autostart:
            autostart.apply(True)  # exe 搬過位置時更新路徑
        if not self.cfg.api_key:
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
        self.err = QLabel(); self.err.setWordWrap(True); self.err.setStyleSheet(f"color:{RED}")
        v.addWidget(self.err)
        v.addStretch()
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setStyleSheet(f"color:{GREY}")
        v.addWidget(self.status)
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
        self.banner = QVBoxLayout(); v.addLayout(self.banner)
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
    def client(self) -> RiotClient:
        return RiotClient(self.cfg.api_key, self.limiter)

    def update_key_status(self):
        if self.waiting_key:
            text = f"<span style='color:{BLUE}'>等你複製新 Key</span>"
        elif self.key_warming:
            text = f"<span style='color:{BLUE}'>新 Key 生效中…</span>"
        elif not self.cfg.api_key:
            text = f"<span style='color:{RED}'>未設定 Key</span>"
        elif self.key_note:
            text = self.key_note
        elif self.key_bad:
            text = f"<span style='color:{RED}'>Key 無法使用</span>"
        else:
            left = DEV_KEY_LIFETIME - (time.time() - self.cfg.key_saved_at)
            if self.cfg.personal_key or not self.cfg.key_saved_at:
                text = grey("Key 已設定")
            elif left <= 0:
                text = f"<span style='color:{RED}'>Key 可能過期了</span>"
            else:
                color = ORANGE if left < 2 * 3600 else GREY
                span = f"{left / 3600:.0f} 小時" if left >= 3600 else f"{left / 60:.0f} 分鐘"
                text = f"<span style='color:{color}'>Key 剩 {span}</span>"
        self.key_status.setText(text)
        self.settings.key_state.setText(text)

    def start_key_refresh(self):
        self.waiting_key = True
        self.update_key_status()
        QDesktopServices.openUrl(QUrl(KEY_PAGE))

    def accept_key(self, key: str):
        self.waiting_key = False
        self.cfg.set_key(key, time.time()); self.cfg.save()
        self.settings.key.setText(self.cfg.api_key)
        if not self.cfg.api_key:
            self.key_note = ""; self.update_key_status(); return
        self.key_bad, self.key_warming = False, False
        self.key_banner.hide()
        self.key_note = grey("驗證 Key 中…")
        self.update_key_status()
        self.check_key(key)

    def check_key(self, key: str):
        def check():
            try:
                RiotClient(key, self.limiter).check_key(); self.bridge.key_checked.emit(key, "", "")
            except RiotError as e:
                self.bridge.key_checked.emit(key, e.kind, str(e))
        threading.Thread(target=check, daemon=True).start()

    def key_is_fresh(self) -> bool:
        return time.time() - self.cfg.key_saved_at < KEY_WARMUP

    def warm_up_key(self):
        """剛產生的 Key 要等 Riot 啟用（約 1–2 分鐘）：每 15 秒重驗一次，期間的稽查先排隊。"""
        self.key_warming, self.key_note = True, ""
        self.key_banner.hide()
        self.update_key_status()
        key = self.cfg.api_key
        QTimer.singleShot(KEY_RECHECK_MS, lambda: self.check_key(key) if key == self.cfg.api_key else None)

    def on_key_checked(self, key: str, kind: str, error: str):
        if key != self.cfg.api_key:
            return
        if kind == "key" and self.key_is_fresh():
            self.warm_up_key(); return
        self.key_warming = False
        self.key_bad = bool(error)
        if error:
            self.key_note = ""
            self.show_key_banner(error, "更新 API Key")
        else:
            self.key_note = f"<span style='color:{GREEN}'>✓ Key 可用</span>"
            self.key_banner.hide()
            if self.pending and not self.running:
                members, auto, force = self.pending
                self.pending = None
                self.start_room(members, auto, force)
            QTimer.singleShot(8000, lambda: (setattr(self, "key_note", ""), self.update_key_status()))
            if self.cfg.my_riot_id and not self.cfg.my_puuid:
                rid = RiotId.parse(self.cfg.my_riot_id)
                if rid:
                    self.lookup_me(rid)
        self.update_key_status()
        QApplication.alert(self)

    def show_key_banner(self, text: str, button: str):
        self.key_banner_text.setText(text); self.key_banner_btn.setText(button)
        self.key_banner.show()

    def _key_banner_action(self):
        if self.cfg.api_key:
            self.start_key_refresh()
        else:
            self.show_settings()

    # ─── 本人 ───
    def lookup_me(self, rid: RiotId):
        def run():
            try:
                puuid = self.client().account_puuid(rid.name, rid.tag)
                self.bridge.me_lookup.emit(str(rid), puuid, "")
            except RiotError as e:
                self.bridge.me_lookup.emit(str(rid), "", "找不到這個 Riot ID" if e.kind == "not_found" else str(e))
        threading.Thread(target=run, daemon=True).start()

    def on_me_lookup(self, rid: str, puuid: str, error: str):
        if rid != self.cfg.my_riot_id:
            return
        if puuid:
            self.cfg.my_puuid = puuid; self.cfg.save()
        self.settings.show_me_lookup(error)

    def me(self) -> Me:
        return Me(RiotId.parse(self.cfg.my_riot_id) if self.cfg.my_riot_id else None, self.cfg.my_puuid)

    def is_me(self, m: RiotId) -> bool:
        me = self.me().riot_id
        return bool(me and m.same_as(me))

    def on_me(self, me: Me):
        old = self.cfg.my_riot_id
        self.cfg.my_riot_id, self.cfg.my_puuid = str(me.riot_id), me.puuid
        self.cfg.save()
        self.settings.me.setText(self.cfg.my_riot_id)
        if old and RiotId.parse(old) != me.riot_id:
            self.me_note = f"你的 Riot ID 已更新為 {me.riot_id}"

    # ─── 雜項 ───
    def _load_champions(self):
        try:
            self.bridge.champions.emit(Champions.load())
        except Exception:
            pass  # 沒有英雄中文名時仍可用英文 id 查

    def on_champions(self, champs: Champions):
        self.champions = champs
        for c in self.cards.values():
            c.set_completer()
        self.refresh_cards()

    def refresh_cards(self):
        for c in self.cards.values():
            if c.result:
                c.show_result(c.result)

    def icon_html(self, champion_id: str, size: int) -> str:
        """英雄頭像的 <img>；本機還沒有就在背景下載，好了再重畫卡片。"""
        cid = self.champions.canonical(champion_id)
        path = icon_path(cid)
        if path.exists():
            return f"<img src='{QUrl.fromLocalFile(str(path)).toString()}' width={size} height={size} align=middle>"
        if self.champions.version and cid not in self.icons_loading:
            self.icons_loading.add(cid)

            def load():
                try:
                    download_icon(self.champions, cid); self.bridge.icon_ready.emit()
                except Exception:
                    pass
            threading.Thread(target=load, daemon=True).start()
        return ""

    def open_opgg(self):
        members = self.room or parse_room_messages(self.box.toPlainText()).members
        if members:
            q = ",".join(str(m) for m in members)
            QDesktopServices.openUrl(QUrl("https://op.gg/lol/multisearch/tw?summoners=" + urllib.parse.quote(q)))

    # ─── 稽查 ───
    def on_clipboard(self):
        text = QGuiApplication.clipboard().text()
        key = find_api_key(text)
        if key:
            if key != self.cfg.api_key:
                self.accept_key(key)
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
        if self.running or self.key_warming:
            self.pending = (members, auto, force)
            if self.key_warming:
                self.status.setText("新 Key 生效後自動稽查")
            return
        change = "new" if force else room_change(self.room, members)
        if change == "same":
            self.status.setText("名單沒變，不重查"); return
        if auto:
            self.box.setPlainText("\n".join(f"{m.name} #{m.tag} 進入組隊房間" for m in members))
            self.err.setText("")
            self.notify()
        if not self.cfg.api_key:
            self.show_key_banner("尚未設定 API Key", "設定"); return
        self.show_results()
        if change == "shrink":
            self._shrink(members); return
        if change == "new":
            self._reset_cards()
            self.histories = {}
        query = [m for m in members_to_query(self.room, members, change) if not self.is_me(m)]
        self.room = members
        for m in query:
            self._add_card(m)
        self._run_audit(query)

    def _reset_cards(self):
        self._clear_banner()
        self.cards = {}
        inner = QWidget(); self.cards_box = QVBoxLayout(inner); self.cards_box.addStretch()
        self.scroll.setWidget(inner)

    def _add_card(self, m: RiotId):
        c = PlayerCard(m, self)
        self.cards[key_of(m)] = c
        self.cards_box.insertWidget(self.cards_box.count() - 1, c)

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

    def _run_audit(self, query: list[RiotId]):
        self.running, self.done_count, self.total, self.t0 = True, 0, len(query), time.monotonic()
        self.me_note = ""
        self.audit_btn.setEnabled(False)
        self.update_progress()
        self.progress_timer.start(500)
        b = self.bridge
        audit = Audit(self.client(), self.room, self.me(), self.cfg.recent_games, query=query, prior=self.histories,
                      on_player=b.player.emit, on_me=b.me.emit, on_outcome=b.outcome.emit, on_done=b.done.emit)
        threading.Thread(target=audit.run, daemon=True).start()

    def on_player(self, r: PlayerResult):
        k = key_of(r.riot_id)
        if r.is_me:
            self._remove_card(k)
        elif card := self.cards.get(k):
            card.show_result(r)
        self.done_count += 1
        self.update_progress()
        if r.error and r.error.kind == "key" and self.key_is_fresh():
            if not self.key_warming:
                self.pending = (self.room, False, True)  # Key 生效後整房重查
                self.warm_up_key()
        elif r.error and r.error.kind == "key" and not self.key_bad:
            self.key_bad = True
            self.show_key_banner(str(r.error), "更新 API Key")
            self.update_key_status()

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
            names = "、".join(m.split("#")[0] for m in g.members)
            how = f"最近連續同隊 {g.streak} 場" if g.streak >= 2 else f"近期同隊 {g.total} 場"
            lab = QLabel(f"{names} 疑似多排，{how}")
            lab.setStyleSheet(f"color:{color};font-weight:bold;padding:4px;"); lab.setWordWrap(True)
            self.banner.addWidget(lab)
            for m in g.members:
                if c := self.cards.get(m.casefold()):
                    c.set_stripe(color)

    def update_progress(self):
        """查詢進度；被 Riot 限流時顯示倒數，免得看起來像當掉。"""
        text = f"查詢中 {self.done_count}/{self.total}"
        wait = self.limiter.wait_remaining()
        if wait >= 1:
            text += f"\n請求太多，Riot 要求等 {wait:.0f} 秒"
        self.status.setText(text)

    def on_done(self):
        self.running = False
        self.progress_timer.stop()
        self.audit_btn.setEnabled(True)
        msg = f"完成，用時 {time.monotonic() - self.t0:.1f} 秒"
        self.status.setText(msg + (f"\n{self.me_note}" if self.me_note else ""))
        if self.pending and not self.key_warming:
            members, auto, force = self.pending
            self.pending = None
            self.start_room(members, auto, force)


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
