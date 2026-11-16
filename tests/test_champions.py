from bp_auditor.champions import DDRAGON, Champions

# Data Dragon 形狀的小樣本（champion.json、runesReforged.json、summoner.json 的 data）
CHAMPS = {"Ahri": {"id": "Ahri", "key": "103", "name": "阿璃"}}
RUNES = [{"id": 8100, "key": "Domination", "icon": "perk-images/Styles/7200_Domination.png", "name": "主宰",
          "slots": [{"runes": [{"id": 8112, "key": "Electrocute", "name": "電刑",
                                "icon": "perk-images/Styles/Domination/Electrocute/Electrocute.png"}]},
                    {"runes": [{"id": 8126, "key": "CheapShot", "name": "撿便宜",
                                "icon": "perk-images/Styles/Domination/CheapShot/CheapShot.png"}]}]}]
SPELLS = {"SummonerFlash": {"id": "SummonerFlash", "key": "4", "name": "閃現",
                            "image": {"full": "SummonerFlash.png"}}}


def champs(version="16.22.1"):
    return Champions.from_data(version, CHAMPS, RUNES, SPELLS)


def test_champion_tables_still_built():
    c = champs()
    assert (c.name("ahri"), c.by_key(103)) == ("阿璃", "Ahri")


def test_rune_name_and_icon_from_runes_reforged():
    r = champs().rune(8112)
    assert r.name == "電刑"
    assert r.url == f"{DDRAGON}/cdn/img/perk-images/Styles/Domination/Electrocute/Electrocute.png"


def test_summoner_spell_by_numeric_key():
    s = champs().spell(4)
    assert s.name == "閃現"
    assert s.url == f"{DDRAGON}/cdn/16.22.1/img/spell/SummonerFlash.png"


def test_item_icon_url_from_item_id():
    assert champs().item(3089).url == f"{DDRAGON}/cdn/16.22.1/img/item/3089.png"


def test_unknown_ids_fall_back_to_text_without_download():
    c = champs()
    assert (c.rune(9999).name, c.rune(9999).url) == ("符文 9999", "")
    assert (c.spell(77).name, c.spell(77).url) == ("召喚師技能 77", "")


def test_static_data_not_loaded_still_gives_text_and_no_download():
    c = Champions()
    assert [(i.name, i.url) for i in (c.item(3089), c.rune(8112), c.spell(4))] == [
        ("裝備 3089", ""), ("符文 8112", ""), ("召喚師技能 4", "")]


def test_icon_files_keyed_by_id_and_distinct_per_kind():
    # 以 id 命名：靜態資料沒載入時，之前下載過的圖示仍找得到
    files = {champs().item(4).file, champs().rune(4).file, champs().spell(4).file, Champions().spell(4).file}
    assert len(files) == 3
