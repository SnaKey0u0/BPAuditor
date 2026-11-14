"""把使用者貼上的房間訊息解析成 Riot ID 清單。"""
import re
from dataclasses import dataclass, field

_LINE = re.compile(r"^\s*(?P<name>.+?)\s*#\s*(?P<tag>[^\s#]+)\s*(?:進入組隊房間.*)?$")


@dataclass(frozen=True)
class RiotId:
    name: str
    tag: str

    def __str__(self):
        return f"{self.name}#{self.tag}"

    @classmethod
    def parse(cls, text: str) -> "RiotId | None":
        m = _LINE.match(text or "")
        return cls(m["name"], m["tag"]) if m else None

    def same_as(self, other: "RiotId") -> bool:
        return self.name.casefold() == other.name.casefold() and self.tag.casefold() == other.tag.casefold()


@dataclass
class ParseResult:
    members: list[RiotId] = field(default_factory=list)
    bad_lines: list[str] = field(default_factory=list)


def parse_room_messages(text: str) -> ParseResult:
    """每行 `<遊戲名稱> #<標籤> 進入組隊房間`；也接受只有 `名稱#標籤`。重複者合併，壞行收集起來。"""
    result = ParseResult()
    for line in text.splitlines():
        if not line.strip():
            continue
        rid = RiotId.parse(line)
        if rid is None:
            result.bad_lines.append(line.strip())
        elif not any(rid.same_as(m) for m in result.members):
            result.members.append(rid)
    return result


ROOM_JOIN = "進入組隊房間"


def room_members_in_clipboard(text: str) -> list[RiotId]:
    """自動稽查用：只認含「進入組隊房間」的行，其餘聊天內容一律略過。"""
    lines = [l for l in (text or "").splitlines() if ROOM_JOIN in l]
    return parse_room_messages("\n".join(lines)).members


def _key(m: RiotId) -> tuple[str, str]:
    return m.name.casefold(), m.tag.casefold()


def room_change(old: list[RiotId], new: list[RiotId]) -> str:
    """比較前後兩份房間成員：'same'（不用查）、'grow'（只查新的人）、'shrink'（不用查，移除離開的人）、'new'（全部重查）。"""
    a, b = {_key(m) for m in old}, {_key(m) for m in new}
    if not a:
        return "new"
    if a == b:
        return "same"
    if a < b:
        return "grow"
    if b < a:
        return "shrink"
    return "new"


def members_to_query(old: list[RiotId], new: list[RiotId], change: str) -> list[RiotId]:
    """要發請求的成員：'grow' 只查新加入的人；'new'（含手動強制重查）全部重查；其餘不查。"""
    if change == "new":
        return list(new)
    if change == "grow":
        seen = {_key(m) for m in old}
        return [m for m in new if _key(m) not in seen]
    return []
