from bp_auditor.parsing import RiotId, parse_room_messages
from bp_auditor.riot import find_api_key


def test_find_api_key_in_copied_text():
    k = "RGAPI-12345678-abcd-ef01-2345-6789abcdef01"
    assert find_api_key(f"  Development API Key\n{k}\n") == k
    assert find_api_key("某某 #TW2 進入組隊房間") is None


def test_parses_room_messages_with_space_before_hash():
    r = parse_room_messages("測試玩家甲 #0001 進入組隊房間\nテスト玩家乙 #標籤 進入組隊房間")
    assert r.members == [RiotId("測試玩家甲", "0001"), RiotId("テスト玩家乙", "標籤")]
    assert r.bad_lines == []


def test_accepts_bare_riot_id_and_names_with_spaces():
    r = parse_room_messages("Faker Fan#TW2\n  a b c # xyz  ")
    assert r.members == [RiotId("Faker Fan", "TW2"), RiotId("a b c", "xyz")]


def test_merges_duplicates_case_insensitively_and_collects_bad_lines():
    r = parse_room_messages("Abc#tw2 進入組隊房間\nabc#TW2 進入組隊房間\n\n亂貼的一行\n")
    assert r.members == [RiotId("Abc", "tw2")]
    assert r.bad_lines == ["亂貼的一行"]


def test_clipboard_only_counts_room_join_lines():
    text = "小明: 安安\n路人 #TW2 進入組隊房間\nFaker Fan#TW2\n小雞 #0420 進入組隊房間"
    from bp_auditor.parsing import room_members_in_clipboard
    assert room_members_in_clipboard(text) == [RiotId("路人", "TW2"), RiotId("小雞", "0420")]
    assert room_members_in_clipboard("RGAPI-xxx") == []


def test_room_change():
    from bp_auditor.parsing import room_change
    a, b, c = RiotId("A", "1"), RiotId("B", "1"), RiotId("C", "1")
    assert room_change([], [a]) == "new"
    assert room_change([a, b], [RiotId("a", "1"), b]) == "same"
    assert room_change([a], [a, b]) == "grow"
    assert room_change([a, b], [a]) == "shrink"
    assert room_change([a, b], [a, c]) == "new"


def test_members_to_query():
    from bp_auditor.parsing import members_to_query
    a, b = RiotId("A", "1"), RiotId("B", "1")
    assert members_to_query([a], [a, b], "grow") == [b]
    # 手動按「稽查」強制重查：名單沒變也要全部重查
    assert members_to_query([a, b], [a, b], "new") == [a, b]
    assert members_to_query([a, b], [a, b], "same") == []


def test_strips_invisible_bidi_marks_copied_from_game_chat():
    """遊戲聊天室複製出來的名字與標籤前後包著 U+2066（LRI）／U+2069（PDI），不去掉會查無此人。"""
    text = ("⁦⁦測試玩家 甲⁩ #⁦0001⁩⁩ 進入組隊房間\r\n"
            "⁦⁦テスト玩家乙⁩ #⁦標籤⁩⁩ 進入組隊房間")
    from bp_auditor.parsing import room_members_in_clipboard
    assert room_members_in_clipboard(text) == [RiotId("測試玩家 甲", "0001"), RiotId("テスト玩家乙", "標籤")]
    assert RiotId.parse("⁦測試玩家 甲⁩#⁦0001⁩") == RiotId("測試玩家 甲", "0001")
