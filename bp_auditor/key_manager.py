"""Key 生命週期（ADR 0002）：Key 清單的驗證與失效、使用中的 Key、本人每組 Key 的 PUUID。

不碰 Qt、不開執行緒、不跑計時器。介面告訴這裡「發生了什麼」（新增／刪除 Key、驗證結果、稽查中回 401/403、
查到本人、補查本人的結果），這裡更新設定，回傳「該做什麼」（下面的動作），由介面在主執行緒執行。
只有 lookup_me 與 KeyManager.run 在背景執行緒跑，兩者都不改設定。

Riot 的 PUUID 依 Key 持有者加密：同一位玩家在不同 Personal Key 下是不同的 PUUID。
所以本人存成「Key → 該 Key 的 PUUID」（Config.my_puuids），比對時一律用這次稽查所用 Key 的版本。
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .audit import Me
from .config import Config, Versions
from .keys import OK, UNVERIFIED, WARMING
from .parsing import RiotId
from .riot import RiotClient, RiotError
from .rotation import KeyRotation, run_rotating

RECHECK_MS = 15_000  # 生效中的 Key 每這麼久重驗一次

T = TypeVar("T")
ClientFor = Callable[[str], object]  # Key → RiotClient（或測試用的假客戶端）


# ─── 動作：介面依序執行 ───
@dataclass(frozen=True)
class Save:
    """存設定檔。"""


@dataclass(frozen=True)
class RedrawKeys:
    """重畫 Key 狀態（側欄、設定頁；全部失效時顯示橫幅）。hide_banner：先收起 Key 橫幅。"""
    hide_banner: bool = False


@dataclass(frozen=True)
class Recheck:
    """delay_ms 毫秒後驗證這組 Key（到時候已經被刪掉就不驗）。"""
    key: str
    delay_ms: int = 0


@dataclass(frozen=True)
class LookUpMe:
    """在背景用本人的 Riot ID 查這些 Key 的 PUUID（lookup_me），結果交回 me_looked_up。"""
    riot_id: RiotId
    keys: tuple[str, ...]


@dataclass(frozen=True)
class RunPending:
    """有 Key 可用了：排隊中的稽查可以跑了（還在稽查或仍在等 Key 生效時照舊排隊）。"""


@dataclass(frozen=True)
class RequeueAudit:
    """這次稽查用的 Key 還沒生效、每個人都查失敗：Key 生效後整個重查。"""


@dataclass(frozen=True)
class Message:
    """狀態列訊息。"""
    text: str


@dataclass(frozen=True)
class Alert:
    """閃爍工作列提醒使用者。"""


@dataclass(frozen=True)
class ShowMe:
    """本人的 Riot ID（稽查中查到改名時會變）；note：稽查結束時顯示在狀態列，空字串代表沒改名。"""
    riot_id: str
    note: str = ""


@dataclass(frozen=True)
class MeLookedUp:
    """補查本人的結果，給設定頁：error 為空字串代表已確認。"""
    error: str


Action = Save | RedrawKeys | Recheck | LookUpMe | RunPending | RequeueAudit | Message | Alert | ShowMe | MeLookedUp


def lookup_me(rid: RiotId, keys: list[str], client_for: ClientFor) -> tuple[Versions, RiotError | None]:
    """（背景執行緒）用本人的 Riot ID 在每組 Key 下各查一次 PUUID。
    回傳 (查到的版本, 第一個錯誤)；某組查不到不影響其他組。不改設定，結果交給 KeyManager.me_looked_up。"""
    found, error = {}, None
    for k in keys:
        try:
            found[k] = client_for(k).account_puuid(rid.name, rid.tag)
        except RiotError as e:
            error = error or e
    return found, error


class KeyManager:
    """Key 清單與本人 PUUID 的唯一擁有者。除了 run 之外都只在介面執行緒呼叫；now 是 epoch 秒。"""

    def __init__(self, cfg: Config, rotation: KeyRotation):
        self.cfg, self.rotation = cfg, rotation

    # ─── 使用中的 Key ───
    def active_key(self) -> str:
        """使用中的 Key：一直用同一組，冷卻或失效了才依序換下一組；沒有可用的時是空字串。"""
        return self.rotation.current()

    def use(self, key: str = "") -> str:
        """這次稽查要用的 Key：指定了（查對面時是查到對局的那組）就改用它，否則用使用中的 Key。"""
        if key:
            self.rotation.set_current(key)
        return key or self.active_key()

    def warming(self) -> bool:
        """沒有可用的 Key，但有新 Key 在等 Riot 啟用：這段期間的稽查先排隊。"""
        return not self.active_key() and any(k.status == WARMING for k in self.cfg.api_keys)

    def run(self, job: Callable[[str, RiotClient], T], on_key_failed: Callable[[str], None]) -> T:
        """（背景執行緒）用使用中的 Key 跑 job(key, client)；冷卻或失效時輪替、整個 job 用新 Key 重跑（ADR 0002）。
        途中回 401/403 的 Key 只讓 rotation 先跳過，再用 on_key_failed 通知介面執行緒呼叫 key_failed。"""
        return run_rotating(self.rotation, job, on_key_error=on_key_failed)

    # ─── Key 清單 ───
    def add_key(self, key: str, now: float) -> list[Action] | None:
        """加到清單最後並驗證；空字串或已經在清單裡回 None。"""
        entry = self.cfg.api_keys.add(key, now)
        if entry is None:
            return None
        return [Save(), RedrawKeys(hide_banner=True), Recheck(entry.key)]

    def remove_key(self, key: str) -> list[Action]:
        """刪除 Key，本人與黑名單在它那個版本的 PUUID 一併刪掉（Config.remove_key）。"""
        if not self.cfg.remove_key(key):
            return []
        return [Save(), RedrawKeys(hide_banner=True)]

    def key_checked(self, key: str, kind: str, error: str, now: float) -> list[Action]:
        """驗證結果：kind 為空字串代表有效，'key' 是回 401/403，其他是連線／伺服器問題。"""
        pool = self.cfg.api_keys
        if not pool.get(key):
            return []  # 驗證期間被刪掉了
        if kind == "key":
            actions = self._auth_failed(key, now)
            if pool.get(key).status == WARMING:
                return actions  # 生效後重驗時才提醒
        else:
            # 驗證通過；或是連線／伺服器問題，分不出 Key 好壞，先當作可用（標成「未能驗證」，與驗證過的分開）
            pool.set_status(key, UNVERIFIED if error else OK)
            actions = [Message(f"驗證 Key 時發生錯誤：{error}")] if error else []
            actions += [Save(), RedrawKeys()]
            actions += self._backfill([key])
            if not error:
                actions.append(RunPending())
        return actions + [Alert()]

    def key_failed(self, key: str, now: float, *, switched: bool) -> list[Action]:
        """稽查中這組 Key 回 401/403。switched：稽查已換到別組 Key 重跑（run 的 on_key_failed）；
        否則是沒得換、落在每個人的結果上，Key 生效後要整個重查。"""
        entry = self.cfg.api_keys.get(key)
        actions = []
        if entry and entry.usable:  # 同一次稽查每個人都會回報一次，只處理第一次
            actions = self._auth_failed(key, now)
            if not switched and entry.status == WARMING:
                actions.insert(0, RequeueAudit())
        self.rotation.clear_bad(key)  # 清單狀態已更新（或 Key 已刪除），rotation 不必再另外跳過
        return actions

    def _auth_failed(self, key: str, now: float) -> list[Action]:
        """回 401/403：剛加入的當作生效中（15 秒後重驗），否則失效（不自動刪除，由使用者決定）。"""
        if self.cfg.api_keys.auth_failed(key, now) == WARMING:
            return [Save(), RedrawKeys(hide_banner=True), Recheck(key, RECHECK_MS)]
        return [Save(), RedrawKeys()]  # 全部失效時 RedrawKeys 會顯示錯誤橫幅；否則下次稽查改用下一組

    # ─── 本人 ───
    def set_me(self, text: str) -> list[Action]:
        """使用者改了本人的 Riot ID（text 已檢查過格式，空字串代表清掉）：每組 Key 都用新的 Riot ID 重查。"""
        self.cfg.my_riot_id, self.cfg.my_puuids = text, {}
        actions = [Save()]
        rid = RiotId.parse(text) if text else None
        if rid and self.active_key():
            actions.append(LookUpMe(rid, tuple(self._usable_keys())))
        return actions

    def me_found(self, me: Me, key: str) -> list[Action]:
        """稽查中查到本人（可能改名了）與他在 key 的 PUUID。改名不影響其他 Key 的 PUUID（帳號不變）；
        其他還沒有版本的 Key 用新的 Riot ID 補查。"""
        old = self.cfg.my_riot_id
        self.cfg.my_riot_id = str(me.riot_id)
        if self.cfg.api_keys.get(key):  # 稽查期間被刪掉的 Key 不存
            self.cfg.my_puuids[key] = me.puuid
        renamed = bool(old) and RiotId.parse(old) != me.riot_id
        actions = [Save(), ShowMe(self.cfg.my_riot_id, f"你的 Riot ID 已更新為 {me.riot_id}" if renamed else "")]
        missing = [k for k in self._usable_keys() if not self.cfg.my_puuid(k)]
        if missing:
            actions.append(LookUpMe(me.riot_id, tuple(missing)))
        return actions

    def me_looked_up(self, riot_id: str, found: Versions, error: RiotError | None) -> list[Action]:
        """lookup_me 的結果。查詢期間換了本人的 Riot ID 就不要；查詢期間被刪掉的 Key 不存。"""
        if riot_id != self.cfg.my_riot_id:
            return []
        message = "" if found or error is None else "找不到這個 Riot ID" if error.kind == "not_found" else str(error)
        found = {k: v for k, v in found.items() if self.cfg.api_keys.get(k)}
        self.cfg.my_puuids.update(found)
        return ([Save()] if found else []) + [MeLookedUp(message)]

    def _backfill(self, keys: list[str]) -> list[Action]:
        """Key 可用之後：本人用 Riot ID 查一次這些 Key 的 PUUID；已經有版本的不查。"""
        rid = RiotId.parse(self.cfg.my_riot_id) if self.cfg.my_riot_id else None
        missing = tuple(k for k in keys if not self.cfg.my_puuid(k))
        return [LookUpMe(rid, missing)] if rid and missing else []

    def _usable_keys(self) -> list[str]:
        return [k.key for k in self.cfg.api_keys if k.usable]
