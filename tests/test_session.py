"""稽查場次（#15）：房間 → 對局「要查誰、沿用誰」，以及換 Key 時整次重跑（ADR 0002）。

用假的傳輸層（tests.test_request_count.FakeRiot）走真的 RiotClient，不碰 Qt。
"""
from bp_auditor.audit import ME
from bp_auditor.keys import BAD, WARMUP, WARMING, KeyPool
from bp_auditor.parsing import RiotId
from bp_auditor.riot import RateLimiter, RiotClient
from bp_auditor.session import GAME, AuditSession, Listener, card_key
from tests.test_request_count import ENEMIES, GAMES, ROOM as ROOM_NAMES, FakeRiot

K1, K2 = "RGAPI-1", "RGAPI-2"
ME_RID = RiotId("我", "TW")


def rid(n):
    return RiotId(n, "TW")


class Recorder:
    def __init__(self):
        self.players, self.restarts, self.outcome, self.mastery = [], [], None, {}

    def listener(self):
        return Listener(lambda r, k: self.players.append((r, k)), lambda m, k: None,
                        lambda o: setattr(self, "outcome", o), self.mastery.__setitem__,
                        lambda k, n: self.restarts.append((k, n)))


def client(riot):
    return RiotClient("RGAPI-test", RateLimiter(limits=((10 ** 6, 1.0),)), fetch=riot.fetch)


def run(session, riot, query, key, **game):
    """像 GUI 一樣：request → job（用 key 跑）→ finished。"""
    rec = Recorder()
    req = session.request(query, key, games=GAMES, me_riot_id=ME_RID, my_puuids={K1: "p-我", K2: "p-我"}, **game)
    req.job(rec.listener())(key, client(riot))
    session.finished(rec.outcome)
    return rec


def room_audit(session, riot, names, key=K1, force=False):
    plan = session.plan_room([rid(n) for n in names], key, force)
    session.enter_room(plan)
    return plan, run(session, riot, plan.query, key)


def spectator(allies, enemies, hidden_enemy=False):
    ps = [{"puuid": "p-" + n, "riotId": f"{n}#TW", "teamId": 100, "championId": 103} for n in allies]
    ps += [{"puuid": "p-" + n, "riotId": f"{n}#TW", "teamId": 200, "championId": 1} for n in enemies]
    if hidden_enemy:
        ps.append({"puuid": None, "riotId": "Ahri", "teamId": 200, "championId": 103})
    return {"gameId": 42, "participants": ps}


# ─── 房間 ───

def test_房間第一次全部查_名單沒變不重查():
    s = AuditSession()
    plan, _ = room_audit(s, FakeRiot(), ["我", "A", "B"])

    assert plan.change == "new" and plan.query == [ME_RID, rid("A"), rid("B")]
    assert s.plan_room([rid("我"), rid("a"), rid("B")], K1) is None  # 不分大小寫


def test_房間多了新人只查新人_近期戰績沿用():
    s, riot = AuditSession(), FakeRiot()
    room_audit(s, riot, ["我", "A"])

    plan, rec = room_audit(s, riot, ["我", "A", "B"])

    assert plan.change == "grow" and plan.query == [rid("B")]
    assert [str(r.riot_id) for r, _ in rec.players] == ["B#TW"]
    assert set(s.histories) == {ME, "A#TW", "B#TW"}


def test_有人離開_拿掉他的近期戰績_不用查():
    s = AuditSession()
    room_audit(s, FakeRiot(), ["我", "A", "B"])

    plan = s.plan_room([rid("我"), rid("A")], K1)
    s.enter_room(plan)

    assert plan.change == "shrink" and plan.query == []
    assert set(s.histories) == {ME, "A#TW"}


def test_使用中的Key換了_整房重查_舊結果不沿用():
    s = AuditSession()
    room_audit(s, FakeRiot(), ["我", "A"])

    plan = s.plan_room([rid("我"), rid("A"), rid("B")], K2)
    s.enter_room(plan)

    assert plan.change == "new" and len(plan.query) == 3
    assert s.histories == {}


def test_身分被隱藏的人有卡片但不查():
    s = AuditSession()
    plan = s.plan_room([rid("A"), RiotId("河蟹", "")], K1)

    assert plan.added == [rid("A"), RiotId("河蟹", "")]
    assert plan.query == [rid("A")]


# ─── 對局 ───

def test_查對面_我方房間成員沿用_只查對面與路人():
    s, riot = AuditSession(), FakeRiot()
    room_audit(s, riot, ROOM_NAMES[:4])  # D 不在房間：是路人
    cards = {card_key(rid(n)) for n in ROOM_NAMES[:4]}

    plan = s.plan_game(spectator(ROOM_NAMES, ENEMIES, hidden_enemy=True), K1, "p-我", cards)

    assert [x.rid for x in plan.seats if x.reused] == [ME_RID, rid("A"), rid("B"), rid("C")]
    assert plan.query == [rid("D")] + [rid(n) for n in ENEMIES]
    assert plan.seats[0].is_me and plan.seats[-1].hidden and plan.seats[-1].side == 1
    assert plan.sides == [[ME, "A#TW", "B#TW", "C#TW", "D#TW"], [f"{n}#TW" for n in ENEMIES]]

    s.enter_game(plan, K1)
    rec = run(s, riot, plan.query, K1, puuids=plan.puuids, champions=plan.champions, sides=plan.sides)

    assert s.view == GAME and s.enemies == set(plan.sides[1])
    assert len(rec.players) == 6 and set(rec.mastery) == {"我#TW", "A#TW", "B#TW", "C#TW"}  # 沿用的人只補熟練度
    assert s.plan_room([rid("我")], K1).change == "new"  # 從對局回到房間：整房重查


def test_查到對局的Key與房間階段不同_我方不沿用():
    s, riot = AuditSession(), FakeRiot()
    room_audit(s, riot, ROOM_NAMES)
    cards = {card_key(rid(n)) for n in ROOM_NAMES}

    plan = s.plan_game(spectator(ROOM_NAMES, ENEMIES), K2, "p-我", cards)
    s.enter_game(plan, K2)

    assert not any(x.reused for x in plan.seats)
    assert len(plan.query) == 10 and s.histories == {}


def test_畫面上已沒有卡片的人不沿用():
    s, riot = AuditSession(), FakeRiot()
    room_audit(s, riot, ROOM_NAMES)

    plan = s.plan_game(spectator(ROOM_NAMES, ENEMIES), K1, "p-我", cards={card_key(ME_RID)})

    assert [x.rid for x in plan.seats if x.reused] == [ME_RID]


def test_本人改名_對局中用房間裡那張本人卡片():
    """房間裡以新名字出現、查完才知道是本人（me_card）；觀戰資料寫的是另一種大小寫也對得上。"""
    s, riot = AuditSession(), FakeRiot()
    room_audit(s, riot, ROOM_NAMES)
    s.me_card = card_key(ME_RID)
    game = spectator(ROOM_NAMES, ENEMIES)
    game["participants"][0]["riotId"] = "別的寫法#tw"

    plan = s.plan_game(game, K1, "p-我", {card_key(rid(n)) for n in ROOM_NAMES})

    assert plan.seats[0].rid == ME_RID and plan.seats[0].reused


def test_對局資料裡找不到本人():
    assert AuditSession().plan_game(spectator(["A"], ["E1"]), K1, "p-我") is None


# ─── 換 Key 重跑 ───

def test_換Key重跑_每個人都用新Key重查_不沿用舊結果():
    s, riot = AuditSession(), FakeRiot()
    room_audit(s, riot, ["我", "A"])
    plan = s.plan_room([rid("我"), rid("A"), rid("B")], K1)
    s.enter_room(plan)
    rec = Recorder()

    job = s.request(plan.query, K1, games=GAMES, me_riot_id=ME_RID, my_puuids={K2: "p-我"}).job(rec.listener())
    job(K2, client(riot))  # run_rotating 換到 K2 重跑

    assert rec.restarts == [(K2, 3)]
    assert sorted(str(r.riot_id) for r, k in rec.players) == ["A#TW", "B#TW", "我#TW"]
    assert {k for _, k in rec.players} == {K2}


def test_查對面換Key重跑_用新Key重查一次對局取得PUUID():
    s, riot = AuditSession(), FakeRiot()
    riot.game = spectator(ROOM_NAMES, ENEMIES)
    plan = s.plan_game(riot.game, K1, "p-我")
    s.enter_game(plan, K1)
    rec = Recorder()

    job = s.request(plan.query, K1, games=GAMES, me_riot_id=ME_RID, my_puuids={K2: "p-我"},
                    puuids=plan.puuids, champions=plan.champions, sides=plan.sides).job(rec.listener())
    riot.urls.clear()
    job(K2, client(riot))

    assert sum(n for u, n in riot.urls.items() if "/spectator/" in u) == 1
    assert not [u for u in riot.urls if "/by-riot-id/" in u]  # PUUID 都從重查的對局拿到
    assert len(rec.players) == 10


def test_換Key了_舊Key的近期戰績丟掉():
    s = AuditSession()
    room_audit(s, FakeRiot(), ["我", "A"])

    s.restarted(K2)

    assert s.key == K2 and s.histories == {}


# ─── Key 回 401/403 ───

def test_回401_剛加入的Key當作生效中_舊的Key標成失效():
    pool = KeyPool()
    pool.add(K1, now=0); pool.add(K2, now=1000)

    assert pool.auth_failed(K2, now=1000 + WARMUP - 1) == WARMING
    assert pool.auth_failed(K1, now=1000 + WARMUP - 1) == BAD
    assert pool.auth_failed("RGAPI-不在清單", now=0) is None
