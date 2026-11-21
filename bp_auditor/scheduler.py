"""稽查排隊與自動查對面（GLOSSARY「對局」）：什麼時候開始房間／對局稽查、什麼時候問觀戰 API。

不碰 Qt、不開執行緒、不跑計時器；時間由介面傳入（now：time.monotonic() 的秒數）。介面告訴這裡「發生了什麼」
（來了新房間、查到對局、稽查開始／結束、tick、手動查對面、Key 生效），這裡回傳「該做什麼」（下面的動作）。

- 稽查中來了新房間或查到開局、在等新 Key 生效時來了新房間：先排隊（各只留最新的一筆）。
  稽查結束時排隊的房間優先（Key 還在生效中就先不跑），其次是排隊的對局，都沒有才開始自動查對面。
- 自動查對面：房間稽查完、本人有這組 Key 的 PUUID 才開始。先等 60 秒（排隊＋選角），之後每 10 秒查一次，
  開始查之後最多 5 分鐘；查到開局、手動查過、或開始新的稽查就停。稽查中輪到的那次跳過不查。
"""
from dataclasses import dataclass

from .parsing import RiotId

FIRST_WAIT = 60  # 房間稽查完先等這麼久（秒）才開始問觀戰 API
EVERY = 10  # 之後每這麼久問一次，看開局了沒
POLL_FOR = 5 * 60  # 開始問之後最多問這麼久


# ─── 動作：介面依序執行 ───
@dataclass(frozen=True)
class StartRoom:
    """稽查這些房間成員（名單沒變等情況由介面決定不查）；真的開始稽查時呼叫 started(room=True)。"""
    members: list[RiotId]
    auto: bool
    force: bool


@dataclass(frozen=True)
class StartGame:
    """稽查這場對局；key 是查到它的 Key。真的開始稽查時呼叫 started(room=False)。"""
    game: dict
    key: str


@dataclass(frozen=True)
class CheckGame:
    """自動查一次對面（手動查由介面直接發起），結果交回 spectated。"""


@dataclass(frozen=True)
class PollBar:
    """右下角讀條：left 秒後查對面；progress 是這段等待已過的比例（0–1）；first：還在第一次的 60 秒。"""
    left: float
    progress: float
    first: bool


@dataclass(frozen=True)
class StopPolling:
    """停止自動查對面：收起讀條。"""


@dataclass(frozen=True)
class Message:
    """狀態列訊息。"""
    text: str


Action = StartRoom | StartGame | CheckGame | PollBar | StopPolling | Message


class AuditScheduler:
    """稽查排隊與自動查對面的唯一擁有者；只在介面執行緒呼叫。warming：沒有可用的 Key、有新 Key 在等 Riot 啟用。"""

    def __init__(self):
        self.running = False  # 有稽查在跑
        self._room = False  # 正在跑（或剛跑完）的是房間稽查
        self._room_queue: tuple[list[RiotId], bool, bool] | None = None  # (成員, 自動, 強制重查)
        self._game_queue: tuple[dict, str] | None = None  # (對局, 查到它的 Key)
        self._next: float | None = None  # 下一次自動查對面的時間；None 代表沒在自動查
        self._until = 0.0  # 到這個時間就不再自動查
        self._first = False  # 還在第一次的等待

    @property
    def polling(self) -> bool:
        return self._next is not None

    # ─── 稽查 ───
    def room(self, members: list[RiotId], auto: bool, force: bool = False, *, warming: bool) -> list[Action]:
        """來了一組房間成員（auto：剪貼簿自動偵測；force：手動按稽查，名單沒變也重查）。"""
        if self.running or warming:
            self._room_queue = (members, auto, force)
            return [Message("新 Key 生效後自動稽查")] if warming else []
        return [StartRoom(members, auto, force)]

    def requeue(self, members: list[RiotId]):
        """這次稽查用的 Key 還沒生效、每個人都查失敗：Key 生效後整房重查。"""
        self._room_queue = (members, False, True)

    def key_ready(self, *, warming: bool) -> list[Action]:
        """有 Key 可用了：排隊的房間可以跑了（還在稽查或仍在等 Key 生效時照舊排隊）。"""
        if self._room_queue and not self.running and not warming:
            return self._pop_room()
        return []

    def game(self, game: dict, key: str) -> list[Action]:
        """要稽查這場對局；稽查中就等這次查完。"""
        if self.running:
            self._game_queue = (game, key)
            return []
        return [StartGame(game, key)]

    def started(self, *, room: bool) -> list[Action]:
        """介面真的開始一次稽查了（room：房間稽查，否則是對局）。新的稽查開始就不再自動查對面。"""
        self.running, self._room = True, room
        return self._stop()

    def finished(self, now: float, *, warming: bool, me_ready: bool) -> list[Action]:
        """稽查結束。me_ready：本人有使用中那組 Key 的 PUUID（觀戰 API 要用）。"""
        self.running = False
        if self._room_queue and not warming:
            return self._pop_room()
        if self._game_queue:
            (game, key), self._game_queue = self._game_queue, None
            return [StartGame(game, key)]
        if self._room and me_ready and not self.polling:
            self._next, self._until, self._first = now + FIRST_WAIT, now + FIRST_WAIT + POLL_FOR, True
            return [self._bar(now)]
        return []

    def _pop_room(self) -> list[Action]:
        (members, auto, force), self._room_queue = self._room_queue, None
        return [StartRoom(members, auto, force)]

    # ─── 查對面 ───
    def tick(self, now: float) -> list[Action]:
        """讀條計時器每 200 ms 呼叫一次：更新讀條，到時間就查一次對面，超過 5 分鐘就停。"""
        if not self.polling:
            return []
        if now < self._next:
            return [self._bar(now)]
        if now >= self._until:
            return self._stop()
        self._first = False
        while self._next <= now:  # 電腦睡過頭時不要一口氣補查好幾次
            self._next += EVERY
        return ([] if self.running else [CheckGame()]) + [self._bar(now)]

    def checked_manually(self) -> list[Action]:
        """使用者手動查對面：之後不再自動查。"""
        return self._stop()

    def spectated(self, game: dict | None, error: str, key: str, *, manual: bool,
                  current_game_id: object) -> list[Action]:
        """觀戰 API 的結果：error 為空字串代表查詢成功、game 為 None 代表還沒開局；key 是查詢所用的 Key。
        查到新的一場就停止自動查、稽查這場；與畫面上同一場就不理。訊息只在手動查時顯示。"""
        def say(text: str) -> list[Action]:
            return [Message(text)] if manual else []

        if error:
            return say(error)
        if not game:
            return say("你還沒進遊戲（載入畫面開始才查得到）")
        if game.get("gameId") == current_game_id:
            return say("已經是這場了")
        return self._stop() + self.game(game, key)

    def _stop(self) -> list[Action]:
        if not self.polling:
            return []
        self._next = None
        return [StopPolling()]

    def _bar(self, now: float) -> PollBar:
        total = FIRST_WAIT if self._first else EVERY
        left = max(round(self._next - now, 3), 0.0)  # 取到毫秒：浮點誤差不要讓 60 秒顯示成 61 秒
        return PollBar(left, (total - left) / total, self._first)
