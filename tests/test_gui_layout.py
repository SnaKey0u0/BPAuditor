"""對局的兩隊畫面在各種視窗寬度下都排得穩（#20）。

標題列曾依卡片寬度切換「同一列／上下排」，切換會改變卡片的尺寸需求，兩欄畫面在某些寬度
無限重排，Qt 內部堆疊爆掉、程式閃退（例如遊戲結束切回視窗時）。這裡用真的 MainWindow、
長 Riot ID 與假的 Riot API 走到對局畫面，再把視窗寬度一路掃過去：會閃退的話整個測試行程會掛掉。
"""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")  # 用真的字型，寬度才接近實際

from PySide6.QtWidgets import QApplication  # noqa: E402

from bp_auditor.config import config_path  # noqa: E402
from bp_auditor.keys import KeyPool  # noqa: E402
from bp_auditor.parsing import RiotId  # noqa: E402
from tests.test_request_count import FakeRiot  # noqa: E402
from tests.test_session import spectator  # noqa: E402

K = "RGAPI-11111111-1111-1111-1111-111111111111"
LONG = ["長" * n + "x" for n in (6, 9, 12, 14, 16, 18, 20, 22, 24)]  # 各種長度，總有幾張卡片剛好卡在邊界


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_path().is_relative_to(tmp_path)
    from bp_auditor import gui
    app = QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    riot = FakeRiot()
    for n in LONG:
        riot.ids[n] = [f"{n}-{j}" for j in range(len(riot.ids["A"]))]
    w.rotation._fetch = riot.fetch
    w.cfg.api_keys = KeyPool.from_json([{"key": K}])
    w.cfg.my_riot_id, w.cfg.my_puuids = "我#TW", {K: "p-我"}
    w.show()
    yield app, w
    w.poll_timer.stop()
    w.hide()


def wait_done(app, w, timeout=10):
    t = time.monotonic()
    while w.running:
        app.processEvents()
        assert time.monotonic() - t < timeout, "稽查沒有結束"
        time.sleep(0.01)


def test_game_view_survives_every_window_width(window):
    app, w = window
    room = ["我"] + LONG[:4]
    w.start_room([RiotId(n, "TW") for n in room], auto=False, force=True)
    wait_done(app, w)
    w.start_game(spectator(room, LONG[4:], hidden_enemy=True), K)
    wait_done(app, w)
    assert w.session.view == "game"
    for width in range(560, 1500, 2):
        w.resize(width, 700)
        app.processEvents(); app.processEvents()
    assert all(c.isVisible() for c in w.cards.values())
