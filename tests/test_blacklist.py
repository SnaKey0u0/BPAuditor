"""黑名單（#25）：每筆記 Riot ID 與各組 Key 的 PUUID；比對時自動補齊，不額外打 Riot API。"""
import json

import pytest

from bp_auditor.blacklist import Blacklist
from bp_auditor.config import Config, config_path

K1, K2 = "RGAPI-1", "RGAPI-2"


def test_toggle_adds_then_removes():
    b = Blacklist()
    assert b.toggle(K1, "p1", "壞人#TW") is True
    assert b.check(K1, "p1", "壞人#TW") is True
    assert b.toggle(K1, "p1", "壞人#TW") is False
    assert b.check(K1, "p1", "壞人#TW") is False


def test_puuid_match_survives_rename_and_updates_riot_id():
    b = Blacklist(); b.toggle(K1, "p1", "舊名#TW")
    assert b.check(K1, "p1", "新名#TW") is True
    assert b.to_json()[0]["riot_id"] == "新名#TW"


def test_new_key_matches_by_riot_id_and_learns_its_puuid():
    b = Blacklist(); b.toggle(K1, "p1", "壞人#TW")
    assert b.check(K2, "q1", "壞人#tw") is True  # 不分大小寫
    assert b.to_json()[0]["puuids"] == {K1: "p1", K2: "q1"}
    assert b.check(K2, "q1", "改名了#TW") is True  # 學到之後改名也認得


def test_same_riot_id_but_different_puuid_under_same_key_is_someone_else():
    b = Blacklist(); b.toggle(K1, "p1", "壞人#TW")
    assert b.check(K1, "p-other", "壞人#TW") is False  # 名字被別的帳號拿走了


def test_stranger_not_blacklisted():
    b = Blacklist(); b.toggle(K1, "p1", "壞人#TW")
    assert b.check(K1, "p2", "路人#TW") is False
    assert b.check(K2, "q2", "路人#TW") is False


def test_toggle_under_new_key_removes_existing_entry_instead_of_duplicating():
    b = Blacklist(); b.toggle(K1, "p1", "壞人#TW")
    assert b.toggle(K2, "q1", "壞人#TW") is False  # 以 Riot ID 認出是同一筆：移除，不另外新增
    assert b.to_json() == []


def test_drop_key_keeps_entry_matchable_by_riot_id():
    b = Blacklist(); b.toggle(K1, "p1", "壞人#TW")
    b.drop_key(K1)
    assert b.to_json() == [{"riot_id": "壞人#TW", "puuids": {}}]
    assert b.check(K2, "q1", "壞人#TW") is True


def test_from_json_skips_malformed_and_old_format_entries():
    old = [{K1: "p1"}, {"key": K1}, "x", {"riot_id": "", "puuids": {}}]
    good = {"riot_id": "壞人#TW", "puuids": {K1: "p1"}}
    assert Blacklist.from_json(old + [good]).to_json() == [good]
    assert Blacklist.from_json(None).to_json() == []
    assert Blacklist.from_json({"a": 1}).to_json() == []


@pytest.fixture
def temp_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_path().is_relative_to(tmp_path)


def test_config_blacklist_survives_save_and_reload(temp_appdata):
    cfg = Config(); cfg.blacklist.toggle(K1, "p1", "壞人#TW"); cfg.save()
    assert Config.load().blacklist.check(K1, "p1", "壞人#TW") is True


def test_config_remove_key_drops_its_puuids_from_blacklist(temp_appdata):
    cfg = Config(); cfg.api_keys.add(K1, 0.0)
    cfg.blacklist.toggle(K1, "p1", "壞人#TW")
    assert cfg.remove_key(K1)
    assert cfg.blacklist.to_json() == [{"riot_id": "壞人#TW", "puuids": {}}]


def test_old_v1_4_2_blacklist_is_dropped(temp_appdata):
    p = config_path(); p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"api_keys": [{"key": K1}], "blacklist": [{K1: "p1"}]}), encoding="utf-8")
    assert Config.load().blacklist.to_json() == []
