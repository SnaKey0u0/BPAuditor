"""從 Data Dragon 取得英雄 id ↔ 台服繁中名稱與頭像（不需要 API Key）。"""
import json
import urllib.request
from pathlib import Path

from .config import config_path

DDRAGON = "https://ddragon.leagueoflegends.com"


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class Champions:
    def __init__(self, id_to_name: dict[str, str] | None = None, version: str = "",
                 key_to_id: dict[int, str] | None = None):
        self.id_to_name = id_to_name or {}
        self.version = version
        self.key_to_id = key_to_id or {}  # 觀戰／熟練度 API 用數字 key（例如 103 = Ahri）
        self._name_to_id = {}
        for cid, name in self.id_to_name.items():
            self._name_to_id[name] = cid
            self._name_to_id[cid.casefold()] = cid

    @classmethod
    def load(cls) -> "Champions":
        version = _get(f"{DDRAGON}/api/versions.json")[0]
        data = _get(f"{DDRAGON}/cdn/{version}/data/zh_TW/champion.json")["data"]
        return cls({c["id"]: c["name"] for c in data.values()}, version,
                   {int(c["key"]): c["id"] for c in data.values()})

    def canonical(self, champion_id: str) -> str:
        """match-v5 的 championName（大小寫可能不同）→ Data Dragon 的英雄 id。"""
        return self._name_to_id.get(champion_id.casefold(), champion_id)

    def name(self, champion_id: str) -> str:
        return self.id_to_name.get(self.canonical(champion_id), champion_id)

    def by_key(self, key: int) -> str | None:
        """數字 key → 英雄 id；英雄資料還沒載入時回 None。"""
        return self.key_to_id.get(key)


def icon_dir() -> Path:
    return config_path().parent / "icons"


def icon_path(champion_id: str) -> Path:
    return icon_dir() / f"{champion_id}.png"


def download_icon(champions: Champions, champion_id: str) -> Path:
    """下載英雄頭像存到本機；頭像不是對戰資料，可以跨次保留。"""
    cid = champions.canonical(champion_id)
    path = icon_path(cid)
    if not path.exists():
        with urllib.request.urlopen(f"{DDRAGON}/cdn/{champions.version}/img/champion/{cid}.png", timeout=10) as r:
            data = r.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path
