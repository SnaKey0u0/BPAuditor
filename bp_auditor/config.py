"""使用者設定（API Key、本人、查詢場數等）存在 %APPDATA%\\BPAuditor\\config.json，絕不打包進程式。"""
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .analysis import FEEDING_DEATHS_PER_MINUTE


def config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "BPAuditor" / "config.json"


@dataclass
class Config:
    api_key: str = ""
    my_riot_id: str = ""
    my_puuid: str = ""  # 本人以 PUUID 認定，改名後 PUUID 不變
    recent_games: int = 5
    autostart: bool = False
    key_saved_at: float = 0.0  # epoch 秒；開發 Key 產生後 24 小時過期，以存入時間估算
    personal_key: bool = False  # Personal API Key 不會過期，不顯示倒數；字串上和開發 Key 分不出來，由使用者勾選
    # 送頭場門檻：死亡速率（單場死亡數 ÷ 整局分鐘）≥ 此值；舊版的 KDA／KD／死亡次數三個欄位載入時直接捨棄
    feeding_deaths_per_minute: float = FEEDING_DEATHS_PER_MINUTE
    # 黑名單只存 PUUID（改名後仍認得）；PUUID 依 Key 類型加密，換 Key 類型會失效（使用者接受）
    blacklist: list[str] = field(default_factory=list)

    def is_blacklisted(self, puuid: str) -> bool:
        return bool(puuid) and puuid in self.blacklist

    def toggle_blacklist(self, puuid: str) -> bool:
        """切換黑名單並立即存檔；回傳切換後是否在黑名單。"""
        if puuid in self.blacklist:
            self.blacklist.remove(puuid)
        else:
            self.blacklist.append(puuid)
        self.save()
        return puuid in self.blacklist

    def set_key(self, key: str, now: float):
        key = key.strip()
        if key != self.api_key:
            self.api_key, self.key_saved_at = key, now
            self.my_puuid = ""  # PUUID 依 Key 所屬產品加密，換 Key 後要重查

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(config_path().read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self):
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
