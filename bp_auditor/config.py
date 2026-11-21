"""使用者設定（API Key、本人、查詢場數等）存在 %APPDATA%\\BPAuditor\\config.json，絕不打包進程式。"""
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .analysis import FEEDING_DEATHS_PER_MINUTE
from .keys import KeyPool
from .puuids import Blacklist, Version, Versions


def config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "BPAuditor" / "config.json"


@dataclass
class Config:
    api_keys: KeyPool = field(default_factory=KeyPool)  # 多組 Personal Key，依序 #1、#2…
    my_riot_id: str = ""
    # 本人以 PUUID 認定，改名後 PUUID 不變；PUUID 依 Key 加密，每組 Key 各存一份（Key → PUUID，ADR 0002）
    my_puuids: Versions = field(default_factory=dict)
    recent_games: int = 5
    autostart: bool = False
    # 送頭場門檻：死亡速率（單場死亡數 ÷ 整局分鐘）≥ 此值；舊版的 KDA／KD／死亡次數三個欄位載入時直接捨棄
    feeding_deaths_per_minute: float = FEEDING_DEATHS_PER_MINUTE
    # 黑名單以帳號認定（改名後仍認得）：每一筆存各組 Key 的 PUUID 版本
    blacklist: Blacklist = field(default_factory=Blacklist)

    def my_puuid(self, key: str) -> str:
        """本人在這組 Key 下的 PUUID；還沒查到時是空字串。"""
        return self.my_puuids.get(key, "") if key else ""

    def is_blacklisted(self, key: str, puuid: str) -> bool:
        """puuid 出自 key（這次稽查所用的 Key）。"""
        return self.blacklist.contains(key, puuid)

    def toggle_blacklist(self, key: str, puuid: str) -> bool:
        """切換黑名單並立即存檔；回傳切換後是否在黑名單。加入時只先存 key 這一版，其他 Key 的版本用 add_versions 補。"""
        on = self.blacklist.toggle(key, puuid)
        self.save()
        return on

    def add_versions(self, versions: list[Version]):
        """套用換算結果（新增 Key 或加入黑名單後，背景查回來的其他 Key 版本）並存檔。"""
        if any([self.blacklist.add_version(v) for v in versions]):
            self.save()

    def remove_key(self, key: str) -> bool:
        """從 Key 清單刪除，並一併刪掉本人與黑名單中它那個版本。"""
        if not self.api_keys.remove(key):
            return False
        self.my_puuids.pop(key.strip(), None)
        self.blacklist.drop_key(key.strip())
        return True

    @classmethod
    def load(cls) -> "Config":
        try:
            data = _migrate_puuids(_migrate_keys(json.loads(config_path().read_text(encoding="utf-8"))))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError, AttributeError):
            return cls()

    def save(self):
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {**asdict(self), "api_keys": self.api_keys.to_json(), "blacklist": self.blacklist.snapshot()}
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _migrate_keys(data: dict) -> dict:
    """舊設定檔只有一組 api_key（＋開發 Key 倒數用的 key_saved_at）：變成清單的 #1。"""
    if "api_keys" not in data and str(data.get("api_key", "")).strip():
        data["api_keys"] = [{"key": data["api_key"], "added_at": data.get("key_saved_at", 0.0)}]
    data["api_keys"] = KeyPool.from_json(data.get("api_keys"))
    return data


def _migrate_puuids(data: dict) -> dict:
    """舊設定檔的本人與黑名單 PUUID 只出自一組 Key（my_puuid、一串 PUUID）：都歸到清單的 #1。
    清單沒有 Key 時無從歸屬，捨棄（PUUID 不知道是哪組 Key 加密的，比對不到任何人）。"""
    first = next((k.key for k in data["api_keys"]), "")
    if "my_puuids" not in data:
        old = str(data.get("my_puuid") or "")
        data["my_puuids"] = {first: old} if old and first else {}
    data["my_puuids"] = {str(k): str(v) for k, v in dict(data["my_puuids"]).items() if k and v}
    data["blacklist"] = Blacklist.from_json(data.get("blacklist"), legacy_key=first)
    return data
