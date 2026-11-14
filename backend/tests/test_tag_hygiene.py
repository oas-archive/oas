"""讀作品頁得到的標籤：框框、版本標記、翻譯組、超長敘述不收；名稱去掉括號別名（名稱與內容都是虛構的）。"""
from app import metadata, tag_hygiene


def test_general_tags_drop_junk():
    tags, _ = tag_hygiene.clean(["巨乳", "DL版", "Digital", "某某汉化组", "測試作品【全 12 集】", "某一部很長很長的作品標題後面還有一大段宣傳文字", "AV DEBUT(某某別名)", "中出"])
    assert tags == ["巨乳", "AV DEBUT", "中出"]


def test_named_tags_keep_long_names_and_strip_aliases():
    tags, categories = tag_hygiene.clean(
        ["甲乙丙子 (別名一、別名二) (甲乙丙子（別名三）)", "測試社團（某公司）", "a very long romanized parody title of some show"],
        {"甲乙丙子 (別名一、別名二) (甲乙丙子（別名三）)": "artist", "測試社團（某公司）": "group", "a very long romanized parody title of some show": "parody"})
    assert tags == ["甲乙丙子", "測試社團", "a very long romanized parody title of some show"]
    assert categories == {"甲乙丙子": "artist", "測試社團": "group", "a very long romanized parody title of some show": "parody"}


def test_character_and_parody_keep_their_parentheses():
    tags, categories = tag_hygiene.clean(["警衛（測試作品）", "某作品 (第二季)"], {"警衛（測試作品）": "character", "某作品 (第二季)": "parody"})
    assert tags == ["警衛（測試作品）", "某作品 (第二季)"] and categories == {"警衛（測試作品）": "character", "某作品 (第二季)": "parody"}


def test_event_code_in_parentheses_is_kept():
    assert tag_hygiene.clean(["(C999)"])[0] == ["(C999)"]


def test_missav_series_not_a_tag_and_generic_keywords_cleaned():
    page = '''<meta property="og:title" content="OASTEST-001 標題" />
<div class="text-secondary"> <span>女優:</span> <a href="https://missav.ai/actresses/x" class="a">女優乙 (舊名乙)</a> </div>
<div class="text-secondary"> <span>系列:</span> <a href="https://missav.ai/series/y" class="a">某系列名稱</a> </div>
<div class="text-secondary"> <span>類型:</span> <a href="https://missav.ai/genres/z" class="a">巨乳</a> </div>'''
    data = metadata.parse("https://missav.ai/oastest-001", page)
    assert data["tags"] == ["女優乙", "巨乳"] and data["categories"] == {"女優乙": "artist"}
    generic = metadata.parse("https://site.example/p", '<title>某頁</title><meta name="keywords" content="短標籤, 測試作品【全 99 話】, DL版">')
    assert generic["tags"] == ["短標籤"]


def test_long_english_tags_kept_but_long_cjk_and_sentences_dropped():
    # 網站明確給的多詞英文標籤要留下；含中日文的長句與整句英文宣傳仍不收（2026-11-11）
    tags, categories = tag_hygiene.clean(["double vaginal penetration", "full-packaged futanari"], {"double vaginal penetration": "tag"})
    assert tags == ["double vaginal penetration", "full-packaged futanari"] and categories == {"double vaginal penetration": "tag"}
    assert tag_hygiene.junk("某一部很長很長的作品標題後面還有一大段宣傳文字")
    assert tag_hygiene.junk("夏のサンプル作品シリーズ総集編スペシャル版")
    assert tag_hygiene.junk("the best sample collection you will ever watch this summer")
    assert not tag_hygiene.junk("big breasts")


def test_frames_and_version_marks_unchanged_for_english():
    assert tag_hygiene.junk("[Sample Group] title") and tag_hygiene.junk("Digital") and tag_hygiene.junk("Decensored")


def test_event_codes_dropped_only_for_general_tags():
    # 同人展場次不是標籤（2026-11-11）；正常標籤與名稱類的同名字串不受影響
    events = ["C99", "c102", "Ｃ１０２", "C102夏", "C102新刊", "COMIC1☆20", "例大祭20", "サンクリ65", "コミティア140", "紅楼夢19", "こみトレ40"]
    normal = ["CG", "3D", "C1", "C1234", "巨乳"]
    tags, _ = tag_hygiene.clean(events + normal)
    assert tags == normal
    tags, categories = tag_hygiene.clean(["C99", "例大祭20"], {"C99": "parody", "例大祭20": "character"})
    assert tags == ["C99", "例大祭20"] and categories == {"C99": "parody", "例大祭20": "character"}


def test_cup_sizes_dropped_only_for_general_tags():
    # 罩杯尺寸對作品沒有意義（2026-11-11）；只擋整個名稱就是罩杯的一般標籤
    cups = ["C cup", "G-cup", "d CUP", "Ｈcup", "Fカップ", "E罩杯"]
    normal = ["CG", "3D", "cupless", "cup holder", "AB cup", "巨乳"]
    tags, _ = tag_hygiene.clean(cups + normal)
    assert tags == normal
    tags, categories = tag_hygiene.clean(["C cup", "Fカップ"], {"C cup": "parody", "Fカップ": "character"})
    assert tags == ["C cup", "Fカップ"] and categories == {"C cup": "parody", "Fカップ": "character"}
