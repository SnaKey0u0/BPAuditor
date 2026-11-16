"""從 Data Dragon 取得英雄 id ↔ 台服繁中名稱與頭像，以及符文、召喚師技能、裝備的名稱與圖示（不需要 API Key）。"""
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import config_path

DDRAGON = "https://ddragon.leagueoflegends.com"


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


@dataclass(frozen=True)
class Icon:
    """單場提示框裡的一個圖示（裝備、主符文、召喚師技能）。"""
    name: str  # 顯示名稱；圖示缺少時以文字替代
    file: str  # icons 目錄下的相對路徑，以 id 命名
    url: str   # Data Dragon 網址；"" = 無法下載（靜態資料沒載入或 id 查不到）


class Champions:
    def __init__(self, id_to_name: dict[str, str] | None = None, version: str = "",
                 key_to_id: dict[int, str] | None = None, runes: dict[int, tuple[str, str]] | None = None,
                 spells: dict[int, tuple[str, str]] | None = None):
        self.id_to_name = id_to_name or {}
        self.version = version
        self.key_to_id = key_to_id or {}  # 觀戰／熟練度 API 用數字 key（例如 103 = Ahri）
        self.runes = runes or {}    # 符文 id → (名稱, 圖示路徑)
        self.spells = spells or {}  # 召喚師技能數字 key → (名稱, 圖示檔名)
        self._name_to_id = {}
        for cid, name in self.id_to_name.items():
            self._name_to_id[name] = cid
            self._name_to_id[cid.casefold()] = cid

    @classmethod
    def from_data(cls, version: str, champions: dict, runes: list, spells: dict) -> "Champions":
        """由 Data Dragon 的 champion.json、runesReforged.json、summoner.json 的 data 建出對照。"""
        return cls({c["id"]: c["name"] for c in champions.values()}, version,
                   {int(c["key"]): c["id"] for c in champions.values()},
                   {r["id"]: (r["name"], r["icon"]) for style in runes for slot in style["slots"] for r in slot["runes"]},
                   {int(s["key"]): (s["name"], s["image"]["full"]) for s in spells.values()})

    @classmethod
    def load(cls) -> "Champions":
        version = _get(f"{DDRAGON}/api/versions.json")[0]
        data = _get(f"{DDRAGON}/cdn/{version}/data/zh_TW/champion.json")["data"]
        try:  # 符文與召喚師技能只用在提示框，載入失敗時仍保留英雄資料
            runes = _get(f"{DDRAGON}/cdn/{version}/data/zh_TW/runesReforged.json")
            spells = _get(f"{DDRAGON}/cdn/{version}/data/zh_TW/summoner.json")["data"]
        except Exception:
            runes, spells = [], {}
        return cls.from_data(version, data, runes, spells)

    def item(self, item_id: int) -> Icon:
        url = f"{DDRAGON}/cdn/{self.version}/img/item/{item_id}.png" if self.version else ""
        return Icon(f"裝備 {item_id}", f"item/{item_id}.png", url)

    def rune(self, perk_id: int) -> Icon:
        name, icon = self.runes.get(perk_id, (f"符文 {perk_id}", ""))
        return Icon(name, f"rune/{perk_id}.png", f"{DDRAGON}/cdn/img/{icon}" if icon else "")

    def spell(self, key: int) -> Icon:
        name, image = self.spells.get(key, (f"召喚師技能 {key}", ""))
        url = f"{DDRAGON}/cdn/{self.version}/img/spell/{image}" if image and self.version else ""
        return Icon(name, f"spell/{key}.png", url)

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


def download_asset(icon: Icon) -> Path:
    """下載裝備／符文／召喚師技能圖示存到本機，和英雄頭像一樣跨次保留；不經過 Riot API 限速器。"""
    path = icon_dir() / icon.file
    if not path.exists():
        with urllib.request.urlopen(icon.url, timeout=10) as r:
            data = r.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")  # 寫完才改名，介面不會讀到一半的檔案
        part.write_bytes(data)
        part.replace(path)
    return path
