"""使用者設定（API Key、本人、查詢場數等）存在 %APPDATA%\\BPAuditor\\config.json，絕不打包進程式。"""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "BPAuditor" / "config.json"


@dataclass
class Config:
    api_key: str = ""
    my_riot_id: str = ""
    my_puuid: str = ""  # 本人以 PUUID 認定，改名後 PUUID 不變
    recent_games: int = 10
    autostart: bool = False
    key_saved_at: float = 0.0  # epoch 秒；開發 Key 產生後 24 小時過期，以存入時間估算
    personal_key: bool = False  # Personal API Key 不會過期，不顯示倒數；字串上和開發 Key 分不出來，由使用者勾選

    def set_key(self, key: str, now: float):
        key = key.strip()
        if key != self.api_key:
            self.api_key, self.key_saved_at = key, now

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
