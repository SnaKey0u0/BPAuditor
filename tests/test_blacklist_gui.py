"""卡片的「!」黑名單按鈕（#25）：按了加入／移除並存檔，卡片標籤跟著變；本人與身分被隱藏的玩家沒有按鈕。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from bp_auditor.analysis import recent_stats  # noqa: E402
from bp_auditor.audit import PlayerResult  # noqa: E402
from bp_auditor.config import Config, config_path  # noqa: E402
from bp_auditor.parsing import RiotId  # noqa: E402

K1, K2 = "RGAPI-1", "RGAPI-2"


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_path().is_relative_to(tmp_path)
    from bp_auditor import gui
    QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    yield w
    w.close()


def card(win, name: str, puuid: str, key: str):
    from bp_auditor import gui
    rid = RiotId(name, "TW")
    c = gui.PlayerCard(rid, win)
    c.setParent(win)  # 與實際畫面一樣掛在視窗底下；孤兒卡片在之後的測試被回收時會讓 Qt 閃退
    c.puuid_key = key
    c.show_result(PlayerResult(rid, puuid, [], recent_stats(puuid, []), ranks={}))
    return c


def test_flag_adds_and_removes_and_saves(win):
    c = card(win, "壞人", "p1", K1)
    assert c.flag.isEnabled() and not c.blacklisted
    c.flag.click()
    assert c.blacklisted and c.black_tag.isVisibleTo(c)
    assert Config.load().blacklist.check(K1, "p1", "壞人#TW").listed
    c.flag.click()
    assert not c.blacklisted and not c.black_tag.isVisibleTo(c)
    assert not Config.load().blacklist.check(K1, "p1", "壞人#TW").listed


def test_blacklisted_player_recognised_under_new_key_by_riot_id(win):
    card(win, "壞人", "p1", K1).flag.click()
    c = card(win, "壞人", "q1", K2)  # 換了 Key：PUUID 不同，以 Riot ID 認出
    assert c.blacklisted and c.black_tag.isVisibleTo(c) and c.flag.isChecked()  # 補齊與存檔的規則見 test_blacklist.py


def test_flag_disabled_without_puuid_and_hidden_for_me(win):
    from bp_auditor import gui
    c = gui.PlayerCard(RiotId("查詢中", "TW"), win)
    c.setParent(win)
    assert not c.flag.isEnabled()
    me = card(win, "我", "p-me", K1)
    me.mark_me()
    assert me.flag.isHidden() and not me.blacklisted
