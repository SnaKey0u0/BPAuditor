"""稽查場次：從房間稽查到查對面的一連串稽查，共用的狀態與「這次要查誰、沿用誰」的決定。不碰 Qt。

ADR 0002：一次稽查（含查對面）的所有資料都出自同一組 Key。`key` 是目前這些結果所用的 Key；
使用中的 Key 換了，舊的近期戰績一律不沿用，房間整個重查。

介面執行緒呼叫 plan_room／enter_room、plan_game／enter_game 決定要查誰，再用 request() 交給背景執行緒跑
（rotation.run_rotating）；背景的結果經由介面執行緒回報 started／restarted／finished。
"""
from collections.abc import Callable, Container
from dataclasses import dataclass, field

from .analysis import GamePlayer, Mastery, PremadeGroup, detect_premades, game_puuids, split_game
from .audit import ME, Audit, AuditOutcome, History, Me, PlayerResult
from .parsing import RiotId, members_to_query, room_change
from .riot import RiotClient, RiotError

ROOM, GAME = "room", "game"  # 畫面：房間成員單欄／遊戲中左右兩隊


def card_key(m: RiotId) -> str:
    """一位玩家在畫面上的鍵（Riot ID 不分大小寫）。"""
    return str(m).casefold()


@dataclass
class RoomPlan:
    change: str             # 'grow'（只查新的人）、'shrink'（有人離開，不用查）、'new'（全部重查）
    members: list[RiotId]
    added: list[RiotId]     # 要新增卡片的人；身分被隱藏的人也有卡片，但不查

    @property
    def query(self) -> list[RiotId]:
        return [m for m in self.added if not m.hidden]


@dataclass
class Seat:
    """對局中的一個位置（畫面上一張卡片），依顯示順序排：我方本人第一個。"""
    rid: RiotId
    side: int             # 0 = 我方、1 = 對面
    champion: int         # 本場英雄的數字 key
    hidden: bool = False  # 身分被隱藏：沒有 PUUID，不查
    is_me: bool = False
    reused: bool = False  # 房間階段已用同一組 Key 查過：沿用原本的卡片，只補熟練度


@dataclass
class GamePlan:
    game_id: object
    seats: list[Seat]
    room: list[RiotId]             # 兩隊所有查得到的人
    query: list[RiotId]            # 要查近期戰績的人（沒被沿用的）
    puuids: dict[str, str]         # 觀戰資料已給的 PUUID（查對面那組 Key 的版本）
    champions: dict[str, int]
    sides: list[list[str]]         # 可能多排分隊判斷；我方含「你」


@dataclass
class Listener:
    """背景稽查的回報；GUI 傳入 Qt 訊號的 emit，在介面執行緒處理。"""
    player: Callable[[PlayerResult, str], None]  # (結果, 這次稽查所用的 Key)
    me: Callable[[Me, str], None]
    outcome: Callable[[AuditOutcome], None]
    mastery: Callable[[str, Mastery | None], None]
    restart: Callable[[str, int], None]          # (新 Key, 要查的人數)：換 Key 了，整次從頭重跑


@dataclass
class AuditRequest:
    """一次稽查要用的全部資料，都是開始時的複本：背景執行緒不讀會變的設定或畫面。"""
    room: list[RiotId]
    query: list[RiotId]
    prior: dict[str, History]
    games: int
    me_riot_id: RiotId | None
    my_puuids: dict[str, str]   # 本人在每組 Key 的 PUUID
    first_key: str
    game: dict = field(default_factory=dict)  # 查對面時：puuids、champions、sides

    def job(self, listener: Listener) -> Callable[[str, RiotClient], None]:
        """給 rotation.run_rotating 的 job(key, client)。第一次照請求查；換了 Key（或一開始就不是 first_key）時
        舊 Key 查到的都不要，房間（或對局）裡每個人都用新 Key 重查（ADR 0002）。"""
        everyone = [m for m in self.room if not m.hidden]
        tries: list[str] = []

        def run(k: str, client: RiotClient):
            as_given = not tries and k == self.first_key
            tries.append(k)
            game = dict(self.game)
            if not as_given:
                listener.restart(k, len(everyone))
                if "puuids" in game:  # 觀戰資料的 PUUID 是舊 Key 的版本：用新 Key 重查一次對局
                    game["puuids"] = rerun_game_puuids(self.my_puuids.get(k, ""), client, self.room)
            Audit(client, self.room, Me(self.me_riot_id, self.my_puuids.get(k, "")), self.games,
                  query=self.query if as_given else everyone, prior=self.prior if as_given else {},
                  on_player=lambda r: listener.player(r, k), on_me=lambda m: listener.me(m, k),
                  on_outcome=listener.outcome, on_mastery=listener.mastery, **game).execute()
        return run


def rerun_game_puuids(me_puuid: str, client: RiotClient, room: list[RiotId]) -> dict[str, str]:
    """用 client 的 Key 重查一次進行中的對局，取得 room 每個人在這組 Key 的 PUUID；
    查不到的人由稽查改用 Riot ID 查帳號。"""
    try:
        return game_puuids(client.active_game(me_puuid) if me_puuid else None, room)
    except RiotError:
        return {}


class AuditSession:
    """一連串稽查的狀態：房間名單、近期戰績（同一組 Key 才沿用）、目前結果所用的 Key、畫面與對局。"""

    def __init__(self):
        self.room: list[RiotId] = []
        self.histories: dict[str, History] = {}  # 標籤（含「你」）→ 近期戰績；只有查成功的人
        self.key = ""                            # 目前結果所用的 Key
        self.view = ROOM
        self.game_id = None                      # 已經查過的對局，避免重複查
        self.enemies: set[str] = set()           # 對面的標籤
        self.me_card = ""                        # 本人卡片的鍵；可能多排裡本人的標籤是「你」

    # ─── 房間 ───
    def plan_room(self, members: list[RiotId], key: str, force: bool = False) -> RoomPlan | None:
        """這份房間名單要怎麼查；名單沒變時回 None。使用中的 Key 換了，或剛從對局回來，整房重查。"""
        key_changed = bool(self.key) and key != self.key
        change = "new" if force or key_changed or self.view == GAME else room_change(self.room, members)
        if change == "same":
            return None
        return RoomPlan(change, list(members), members_to_query(self.room, members, change))

    def enter_room(self, plan: RoomPlan):
        """套用 plan：'new' 丟掉舊結果、回到房間畫面；'shrink' 拿掉離開的人的近期戰績。"""
        if plan.change == "new":
            self.histories, self.view, self.enemies, self.me_card = {}, ROOM, set(), ""
        elif plan.change == "shrink":
            labels = {str(m) for m in plan.members} | {ME}
            self.histories = {l: h for l, h in self.histories.items() if l in labels}
        self.room = plan.members

    def premades(self) -> list[PremadeGroup]:
        return detect_premades(self.histories)

    # ─── 對局 ───
    def plan_game(self, game: dict, key: str, me_puuid: str, cards: Container[str] = ()) -> GamePlan | None:
        """查到的對局要怎麼查；對局資料裡找不到本人時回 None。key 是查到這場對局的 Key，me_puuid 是本人在它的版本。
        我方在房間階段已用同一組 Key 查過、畫面上還有卡片（cards：卡片的鍵）的人沿用，只補熟練度。"""
        allies, enemies = split_game(game, me_puuid)
        if not any(p.puuid == me_puuid for p in allies):
            return None
        histories = self.histories if key == self.key else {}  # 房間階段的結果出自別組 Key 時不沿用（ADR 0002）
        in_room = {card_key(m): m for m in self.room}

        def rid_of(p: GamePlayer, is_me: bool) -> RiotId:  # 沿用房間裡的寫法，近期戰績才對得上標籤
            if is_me and self.me_card in in_room:
                return in_room[self.me_card]
            r = RiotId.parse(p.label) or RiotId(p.puuid[:8], "?")
            return in_room.get(card_key(r), r)

        plan = GamePlan(game.get("gameId"), [], [], [], {}, {}, [[ME], []])
        hidden = 0
        allies = sorted(allies, key=lambda p: p.puuid != me_puuid)  # 本人排在我方第一個
        for side, team in enumerate((allies, enemies)):
            for p in team:
                if not p.puuid:
                    hidden += 1
                    plan.seats.append(Seat(RiotId(f"隱藏玩家{hidden}", "?"), side, p.champion_key, hidden=True))
                    continue
                is_me = p.puuid == me_puuid
                rid = rid_of(p, is_me)
                plan.room.append(rid)
                plan.puuids[str(rid)], plan.champions[str(rid)] = p.puuid, p.champion_key
                if not is_me:
                    plan.sides[side].append(str(rid))
                label = ME if is_me else str(rid)  # 本人的近期戰績以「你」存放
                reused = side == 0 and label in histories and card_key(rid) in cards
                if not reused:
                    plan.query.append(rid)
                plan.seats.append(Seat(rid, side, p.champion_key, is_me=is_me, reused=reused))
        return plan

    def enter_game(self, plan: GamePlan, key: str):
        if key != self.key:
            self.histories = {}
        self.game_id, self.room, self.enemies = plan.game_id, plan.room, set(plan.sides[1])
        self.view = GAME

    # ─── 一次稽查 ───
    def request(self, query: list[RiotId], key: str, *, games: int, me_riot_id: RiotId | None,
                my_puuids: dict[str, str], **game) -> AuditRequest:
        """用 key 查 query（其他人沿用近期戰績）；game：查對面時的 puuids、champions、sides。"""
        self.key = key
        return AuditRequest(list(self.room), list(query), dict(self.histories), games, me_riot_id,
                            dict(my_puuids), key, game)

    def restarted(self, key: str):
        """換 Key 了：之前的結果都出自舊 Key，丟掉（ADR 0002）。"""
        self.key, self.histories = key, {}

    def finished(self, outcome: AuditOutcome):
        self.histories = outcome.histories
