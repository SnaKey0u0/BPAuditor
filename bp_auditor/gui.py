"""PySide6 介面：左側欄（剪貼簿監看 / 手動貼上 / 查對面）+ 右側玩家卡片（房間單欄、遊戲中左右兩隊）或設定頁，
關閉時縮到系統匣。"""
import math
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QStackedWidget, QSystemTrayIcon, QToolTip, QVBoxLayout, QWidget,
)

from . import autostart, single
from .analysis import Mastery, PremadeGroup, detect_premades, participant, split_game
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
GAME_POLL_DELAY_MS = 90_000  # 房間稽查完先等 90 秒（排隊＋選角）才開始問觀戰 API
GAME_POLL_MS = 20_000  # 之後每 20 秒問一次，看開局了沒
GAME_POLL_FOR = 15 * 60  # 開始問之後最多問這麼久
GAME_WIDTH = 1100  # 左右兩隊時視窗至少這麼寬
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
    player = Signal(object)
    outcome = Signal(object)
    me = Signal(object)
    done = Signal()
    champions = Signal(object)
    key_checked = Signal(str, str, str)  # (key, 錯誤種類, 錯誤訊息)；種類為空字串代表有效
    me_lookup = Signal(str, str, str)  # (Riot ID, puuid, 錯誤訊息)
    icon_ready = Signal()
    game = Signal(object, str, bool)  # (進行中對局或 None, 錯誤訊息, 是否手動)
    mastery = Signal(str, object)  # (標籤, Mastery 或 None)


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
        self.is_me = False
        self.hidden = False
        self.champion_key: int | None = None  # 本場英雄；只在遊戲中有
        self.mastery: Mastery | None = None
        self.mastery_loaded = False
        self.set_stripe(None)
        v = QVBoxLayout(self); v.setContentsMargins(12, 7, 10, 7); v.setSpacing(3)
        self.title = QLabel(); self.title.setWordWrap(True)
        self.title.setToolTip("在 op.gg 開啟個人頁")
        self.title.linkActivated.connect(lambda _: QDesktopServices.openUrl(QUrl(opgg_profile(self.rid))))
        v.addWidget(self.title)
        stats = QHBoxLayout(); stats.setSpacing(12)
        self.big = QLabel(); self.kda = QLabel(); self.dots = ResultBars()
        stats.addWidget(self.big); stats.addWidget(self.kda); stats.addWidget(self.dots); stats.addStretch()
        v.addLayout(stats)
        self.rank = QLabel(); v.addWidget(self.rank)
        self.chip = QLabel(); self.chip.hide()
        v.addWidget(self.chip, alignment=Qt.AlignLeft)
        self.champ = QLabel(); self.champ.hide()
        v.addWidget(self.champ)
        v.addStretch()  # 左右兩隊同一列等高時，多出的空間放在底部
        self.set_title("")
        self.set_skeleton()

    def show_hidden(self, alias: str = ""):
        """Riot 隱藏了此人身分：沒有 Riot ID／PUUID 查不到戰績，只顯示化名或本場英雄。"""
        name = f"{alias} " if alias else ""
        self.title.setText(f"<b style='font-size:15px'>{name}</b>{grey('身分被隱藏')}"); self.title.setToolTip("")
        self.clear_skeleton(); self.dots.hide(); self.rank.hide(); self.kda.hide()
        self.big.setText(grey("Riot 沒有提供這位玩家的身分，無法查詢"))
        self.hidden = True
        self.show_champion()

    def set_title(self, extra: str = ""):
        self.title.setText(f"<a href='opgg' style='color:#e0e0e0;text-decoration:none'>"
                           f"<b style='font-size:15px'>{self.rid}</b></a> {extra}")

    def mark_me(self):
        self.is_me = True

    def set_stripe(self, color):
        self.setStyleSheet(f"#card{{border-left:6px solid {color or '#262a33'};}}")

    def set_skeleton(self):
        """查詢中：灰色佔位方塊。"""
        for w, size in ((self.big, (100, 24)), (self.kda, (110, 18))):
            w.setFixedSize(*size); w.setStyleSheet(f"background:{SKELETON};border-radius:4px;")
        self.dots.set_items(None)

    def clear_skeleton(self):
        for w in (self.big, self.kda):
            w.setMinimumSize(0, 0); w.setMaximumSize(16777215, 16777215); w.setStyleSheet("background:transparent;")

    def show_result(self, r: PlayerResult):
        if self.champion_key is not None and r is not self.result:  # 遊戲中新查的人：熟練度跟著戰績一起回來
            self.mastery, self.mastery_loaded = r.mastery, True
        self.result = r
        self.clear_skeleton()
        self.dots.set_items([])
        self.show_champion()
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
            self.big.setText(f"<span style='font-size:20px;color:{wr_color(s.win_rate)}'>{round(s.win_rate * 100)}%</span>"
                             f" {grey(f'{s.wins}勝{s.games - s.wins}敗')}")
            self.kda.setText(f"<span style='font-size:15px'>KDA {s.kda:.1f}</span> {grey(f'KD {s.kd:.1f}')}")
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

    def _dot_tip(self, match: dict, p: dict) -> str:
        cid = p["championName"]
        queue = QUEUES.get(match["info"].get("queueId"), match["info"].get("gameMode", ""))
        return (f"{self.win.icon_html(cid, 32)}<br><b>{self.win.champions.name(cid)}</b> "
                f"{p['kills']}/{p['deaths']}/{p['assists']}<br>{grey(queue)}")

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
        self.games_hint.setText(grey(f"5 人房最多約 {4 * n + 15} 次請求，進遊戲查對面再約 {5 * n + 22} 次。"
                                     "開發 Key 上限每 2 分鐘 100 次。"))

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
        b.icon_ready.connect(self.refresh_cards)
        b.game.connect(self.on_game); b.mastery.connect(self.on_mastery)

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
        self.poll_timer = QTimer(self); self.poll_timer.timeout.connect(self.poll_game)
        self.poll_tick = QTimer(self); self.poll_tick.timeout.connect(self.update_poll_bar)
        self.poll_until = 0.0
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
        self.refresh_cards()

    def refresh_cards(self):
        for c in self.cards.values():
            if c.result:
                c.show_result(c.result)
            else:
                c.show_champion()

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
        members = [m for m in self.room or parse_room_messages(self.box.toPlainText()).members if not m.hidden]
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
        change = "new" if force or self.view == "game" else room_change(self.room, members)
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
        if self.view == "game":
            self.game_rows[side] += 1
            self.game_grid.addWidget(c, self.game_rows[side], side)
        else:
            c.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)  # 單欄時維持內容高度，不被拉長
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

    def _run_audit(self, query: list[RiotId], **game):
        self.running, self.done_count, self.total, self.t0 = True, 0, len(query), time.monotonic()
        self.me_note = ""
        self.audit_btn.setEnabled(False)
        self.update_progress()
        self.progress_timer.start(500)
        b = self.bridge
        audit = Audit(self.client(), self.room, self.me(), self.cfg.recent_games, query=query, prior=self.histories,
                      on_player=b.player.emit, on_me=b.me.emit, on_outcome=b.outcome.emit, on_done=b.done.emit,
                      on_mastery=b.mastery.emit, **game)
        threading.Thread(target=audit.run, daemon=True).start()

    def on_player(self, r: PlayerResult):
        k = key_of(r.riot_id)
        if card := self.cards.get(k):
            card.show_result(r)
            if r.is_me:
                self._mark_me(card, k)
        self.done_count += 1
        self.update_progress()
        if r.error and r.error.kind == "key" and self.key_is_fresh():
            if not self.key_warming and self.view == "room":
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
        elif self.pending_game:
            game, self.pending_game = self.pending_game, None
            self.start_game(game)
        elif self.view == "room" and self.room:
            self.start_polling()

    # ─── 查對面（Riot 觀戰 API，不讀遊戲客戶端） ───
    def start_polling(self):
        """房間稽查完：等 90 秒後每 20 秒查一次；查到開局或手動查過就停。"""
        if self.cfg.my_puuid and not self.poll_timer.isActive():
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
        puuid = self.cfg.my_puuid
        if not puuid or not self.cfg.api_key:
            if manual:
                self.status.setText("先在設定填好 API Key 和你的 Riot ID")
            return
        if manual:
            self.poll_timer.stop()  # 手動查過就不再自動查
            self.status.setText("查詢對局中…")

        def run():
            try:
                self.bridge.game.emit(self.client().active_game(puuid), "", manual)
            except RiotError as e:
                self.bridge.game.emit(None, str(e), manual)
        threading.Thread(target=run, daemon=True).start()

    def on_game(self, game: dict | None, error: str, manual: bool):
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
        self.start_game(game)

    def start_game(self, game: dict):
        if self.running:
            self.pending_game = game; return
        me = self.cfg.my_puuid
        allies, enemies = split_game(game, me)
        if not any(p.puuid == me for p in allies):
            self.status.setText("對局資料裡找不到你"); return
        self.game_id = game.get("gameId")
        in_room = {key_of(m): m for m in self.room}

        def rid_of(p) -> RiotId:  # 沿用房間裡的寫法，房間成員的近期戰績才對得上標籤
            r = RiotId.parse(p.label) or RiotId(p.puuid[:8], "?")
            return in_room.get(key_of(r), r)

        old = self.cards
        for c in old.values():
            c.setParent(None)  # 先拿下來，換版面時才不會被一起刪掉
        self._reset_game_view()
        query, room, puuids, champs = [], [], {}, {}
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
                room.append(rid); puuids[str(rid)] = p.puuid
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
        self._run_audit(query, puuids=puuids, champions=champs, sides=sides)

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
