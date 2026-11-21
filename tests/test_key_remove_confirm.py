"""設定頁按 ✕ 刪除 API Key 前先確認（#22）：取消就不動，確認才刪。

刪 Key 時連帶刪掉本人與黑名單在它那個版本的 PUUID，是 key_manager 的規則，見 test_key_manager（不需要 Qt）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from bp_auditor.config import config_path  # noqa: E402
from bp_auditor.keys import KeyPool  # noqa: E402

K1 = "RGAPI-11111111-1111-1111-1111-111111111111"
K2 = "RGAPI-22222222-2222-2222-2222-222222222222"


@pytest.fixture
def keys(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_path().is_relative_to(tmp_path)
    from bp_auditor import gui
    QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    w.cfg.api_keys = KeyPool.from_json([{"key": K1}, {"key": K2}])
    w.settings.keys.refresh()
    yield w, w.settings.keys
    w.close()


def click_remove(key_list, row: int):
    buttons = [b for b in key_list.findChildren(QPushButton) if b.text() == "✕"]
    buttons[row].click()


def remaining(w):
    return [k.key for k in w.cfg.api_keys]


def test_cancel_keeps_the_key(keys, monkeypatch):
    w, kl = keys
    asked = []
    monkeypatch.setattr(kl, "ask_remove", lambda key, n: asked.append((key, n)) or False)
    click_remove(kl, 1)
    assert asked == [(K2, 2)]
    assert remaining(w) == [K1, K2]


def test_confirm_removes_the_key(keys, monkeypatch):
    w, kl = keys
    monkeypatch.setattr(kl, "ask_remove", lambda key, n: True)
    click_remove(kl, 0)
    assert remaining(w) == [K2]
