"""稽查排隊與自動查對面（#30，GLOSSARY「對局」）。純狀態機，時間由測試傳入（假時鐘），不需要 QApplication。"""
from bp_auditor.parsing import RiotId
from bp_auditor.scheduler import (EVERY, FIRST_WAIT, POLL_FOR, AuditScheduler, CheckGame, Message, PollBar,
                                  StartGame, StartRoom, StopPolling)

T0 = 1000.0
ROOM = [RiotId("A", "TW"), RiotId("B", "TW")]
OTHER = [RiotId("C", "TW")]
GAME = {"gameId": 7}


def checks(s: AuditScheduler, start: float, end: float, step: float = 0.2) -> list[float]:
    """從 start 到 end 每 step 秒 tick 一次（像讀條計時器），回傳查對面的時間點（相對 T0）。"""
    out, n = [], 0
    while (now := start + n * step) <= end:
        if CheckGame() in s.tick(now):
            out.append(round(now - T0, 1))
        n += 1
    return out


def polling(now: float = T0) -> AuditScheduler:
    """剛查完一個房間、本人有 PUUID：開始自動查對面。"""
    s = AuditScheduler()
    assert s.room(ROOM, auto=True, warming=False) == [StartRoom(ROOM, True, False)]
    s.started(room=True)
    assert s.finished(now, warming=False, me_ready=True) == [PollBar(FIRST_WAIT, 0.0, True)]
    return s


def test_房間查完先等60秒才第一次查_之後每10秒():
    s = polling()
    assert s.tick(T0 + 30) == [PollBar(30, 0.5, True)]
    assert checks(s, T0 + 30, T0 + 59.8) == []
    assert s.tick(T0 + FIRST_WAIT) == [CheckGame(), PollBar(EVERY, 0.0, False)]
    assert s.tick(T0 + 65) == [PollBar(5, 0.5, False)]
    assert checks(s, T0 + 65, T0 + 100) == [70, 80, 90, 100]


def test_開始查之後5分鐘就停():
    s = polling()
    times = checks(s, T0, T0 + FIRST_WAIT + POLL_FOR - 0.2)
    assert times == list(range(60, 360, 10))  # 60、70…350，共 30 次
    assert s.tick(T0 + FIRST_WAIT + POLL_FOR) == [StopPolling()]
    assert not s.polling and s.tick(T0 + 400) == []


def test_電腦睡過頭醒來只補查一次():
    s = polling()
    assert CheckGame() in s.tick(T0 + 95)  # 60 之後的 70、80、90 都錯過
    assert s.tick(T0 + 96) == [PollBar(4, 0.6, False)]  # 下一次是 100，不是 70


def test_手動查過就不再自動查():
    s = polling()
    s.tick(T0 + 60)
    assert s.checked_manually() == [StopPolling()]
    assert s.tick(T0 + 70) == []
    assert s.checked_manually() == []  # 沒在自動查時什麼都不做


def test_查到開局就停止自動查並稽查這場():
    s = polling()
    s.tick(T0 + 60)
    assert s.spectated(GAME, "", "K1", manual=False, current_game_id=None) == [StopPolling(), StartGame(GAME, "K1")]
    assert s.tick(T0 + 70) == []


def test_沒開局或查詢失敗繼續查_訊息只在手動查時顯示():
    s = polling()
    assert s.spectated(None, "", "K1", manual=False, current_game_id=None) == []
    assert s.spectated(None, "連線失敗", "K1", manual=False, current_game_id=None) == []
    assert s.polling
    assert s.spectated(None, "", "K1", manual=True, current_game_id=None) == [
        Message("你還沒進遊戲（載入畫面開始才查得到）")]
    assert s.spectated(None, "連線失敗", "K1", manual=True, current_game_id=None) == [Message("連線失敗")]


def test_查到的是畫面上同一場就不理():
    s = polling()
    assert s.spectated(GAME, "", "K1", manual=False, current_game_id=7) == []
    assert s.polling
    assert s.spectated(GAME, "", "K1", manual=True, current_game_id=7) == [Message("已經是這場了")]


def test_本人沒有PUUID就不自動查():
    s = AuditScheduler()
    s.started(room=True)
    assert s.finished(T0, warming=False, me_ready=False) == []
    assert not s.polling and s.tick(T0 + 60) == []


def test_對局稽查查完不自動查():
    s = AuditScheduler()
    assert s.game(GAME, "K1") == [StartGame(GAME, "K1")]
    s.started(room=False)
    assert s.finished(T0, warming=False, me_ready=True) == []


def test_新的房間稽查開始時停止自動查_查完重新計時():
    s = polling()
    s.tick(T0 + 200)
    assert s.room(OTHER, auto=True, warming=False) == [StartRoom(OTHER, True, False)]
    assert s.started(room=True) == [StopPolling()]
    assert s.tick(T0 + 210) == []
    assert s.finished(T0 + 300, warming=False, me_ready=True) == [PollBar(FIRST_WAIT, 0.0, True)]
    assert checks(s, T0 + 300, T0 + 360) == [360]  # 又是從頭等 60 秒


def test_稽查中輪到自動查就跳過():
    s = polling()
    s.running = True  # 介面上不會發生（開始稽查就停止自動查），保留原本的防護
    assert s.tick(T0 + 60) == [PollBar(EVERY, 0.0, False)]


def test_稽查中來的房間與對局排隊_查完時房間優先():
    s = AuditScheduler()
    s.room(ROOM, auto=True, warming=False)
    s.started(room=True)
    assert s.room(OTHER, auto=False, force=True, warming=False) == []
    assert s.spectated(GAME, "", "K2", manual=False, current_game_id=None) == []
    assert s.finished(T0, warming=False, me_ready=True) == [StartRoom(OTHER, False, True)]
    s.started(room=True)
    assert s.finished(T0 + 10, warming=False, me_ready=True) == [StartGame(GAME, "K2")]
    s.started(room=False)
    assert s.finished(T0 + 20, warming=False, me_ready=True) == []  # 排隊都跑完了；對局稽查後不自動查


def test_排隊只留最新的一筆():
    s = AuditScheduler()
    s.started(room=True)
    s.room(OTHER, auto=True, warming=False)
    s.room(ROOM, auto=False, warming=False)
    s.game({"gameId": 1}, "K1")
    s.game(GAME, "K2")
    assert s.finished(T0, warming=False, me_ready=True) == [StartRoom(ROOM, False, False)]
    s.started(room=True)
    assert s.finished(T0, warming=False, me_ready=True) == [StartGame(GAME, "K2")]


def test_等新Key生效時來的房間_Key生效後才跑():
    s = AuditScheduler()
    assert s.room(ROOM, auto=True, warming=True) == [Message("新 Key 生效後自動稽查")]
    assert s.key_ready(warming=True) == []
    assert s.key_ready(warming=False) == [StartRoom(ROOM, True, False)]
    assert s.key_ready(warming=False) == []  # 只跑一次


def test_Key生效時還在稽查就等查完():
    s = AuditScheduler()
    s.started(room=True)
    s.room(OTHER, auto=True, warming=False)
    assert s.key_ready(warming=False) == []
    assert s.finished(T0, warming=False, me_ready=True) == [StartRoom(OTHER, True, False)]


def test_查完時Key還在生效中_排隊的房間留到Key生效():
    s = AuditScheduler()
    s.started(room=True)
    s.requeue(ROOM)  # 這次稽查的 Key 還沒生效、每個人都查失敗
    assert s.finished(T0, warming=True, me_ready=False) == []
    assert s.key_ready(warming=False) == [StartRoom(ROOM, False, True)]  # 整房強制重查
