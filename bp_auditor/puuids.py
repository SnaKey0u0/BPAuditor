"""本人的 PUUID 依 Key 分開存（ADR 0002）。

Riot 的 PUUID 依 Key 持有者加密：同一位玩家在不同 Personal Key 下是不同的 PUUID。
所以本人存成「Key → 該 Key 的 PUUID」（Versions），比對時一律用這次稽查所用 Key 的版本。

不碰 Qt；查詢（打 Riot）的函式收 `client_for(key)`，回傳查到的結果，由呼叫端在主執行緒存進設定。
"""
from collections.abc import Callable

from .parsing import RiotId
from .riot import RiotError

Versions = dict[str, str]  # Key → 該 Key 加密的 PUUID；查不到的 Key 不會出現
ClientFor = Callable[[str], object]  # Key → RiotClient（或測試用的假客戶端）


def lookup_me(rid: RiotId, keys: list[str], client_for: ClientFor) -> tuple[Versions, RiotError | None]:
    """用本人的 Riot ID 在每組 Key 下各查一次 PUUID。回傳 (查到的版本, 第一個錯誤)；某組查不到不影響其他組。"""
    found, error = {}, None
    for k in keys:
        try:
            found[k] = client_for(k).account_puuid(rid.name, rid.tag)
        except RiotError as e:
            error = error or e
    return found, error
