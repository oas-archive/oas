"""顯示用短標題：標題都是虛構的。"""
from app.titles import MAX_WIDTH, short_title


def test_strips_circle_event_and_translation_marks():
    assert short_title("(C999) [虛構社團 (某作者)] Kasou no Hon (Kasou Gensaku) [Chinese] [虛構漢化組] [Digital]") == "Kasou no Hon"
    assert short_title("[某作者] 架空の本 (COMIC 架空天 2099年1月号) [中国翻訳] [DL版] [無修正]") == "架空の本"
    assert short_title("[某某汉化] [虛構作者] 架空の本2 [中国翻訳]") == "架空の本2"
    assert short_title("架空アニメ THE ANIMATION 上巻 [中文字幕]") == "架空アニメ THE ANIMATION 上巻"


def test_strips_site_suffixes():
    assert short_title("ABCD-123 虛構的影片標題 - Jable.TV | 免費高清AV在線看 | J片 AV看到飽") == "ABCD-123 虛構的影片標題"
    assert short_title("[某作者] 架空の本 [中国翻訳] - 列表") == "架空の本"
    assert short_title("Kasou的色情片 | Pornhub") == "Kasou的色情片"
    assert short_title("虛構寫真標題 - 貼圖 - 清涼寫真 - JKF 捷克論壇") == "虛構寫真標題"
    assert short_title("Netflav - 虛構的影片") == "虛構的影片"


def test_keeps_one_translation_preferring_chinese():
    assert short_title("[Kasou] Kasou no Hon | 虛構的書 [Chinese]") == "虛構的書"
    assert short_title("Kasou no Hon | A Made-up Book") == "Kasou no Hon"
    # 簡繁兩段譯名取繁體那段
    assert short_title("虚构的恋爱/虛構的戀愛") == "虛構的戀愛"
    # 禁漫的「中文譯名 [社團] 日文原名」
    assert short_title("虛構的書 [某某汉化组] (C999) [虛構社團 (某作者)] 架空の本 [中国翻訳]") == "虛構的書"
    # 「/」兩邊都不是中文時是標題本身，不拆
    assert short_title("かそう101/かそうのほん") == "かそう101/かそうのほん"
    # 開頭是番號的不拆「/」
    assert short_title("ABCD-123 新宿 / 虛構的影片") == "ABCD-123 新宿 / 虛構的影片"


def test_keeps_codes_and_volumes():
    assert short_title("[ABCD-123]架空の作品 - AV01.tv") == "[ABCD-123]架空の作品"
    assert short_title("[某作者] Kasou no Hon (2) [Chinese]") == "Kasou no Hon (2)"


def test_truncates_long_titles_at_word_boundary():
    title = "Kasou no Hon wa Totemo Nagai Taitoru de Owaranai Monogatari no Tsuzuki Desu"
    short = short_title(title)
    assert short.endswith("…") and title.startswith(short[:-1]) and len(short) < len(title)
    assert not short[:-1].endswith(" ")
    # 只長一點點的不截
    slightly = "Kasou no Hon wa Totemo Nagai Taitoru de Owaranai Mo"
    assert MAX_WIDTH < len(slightly) and short_title(slightly) == slightly


def test_falls_back_when_nothing_meaningful_left():
    assert short_title("[Chinese] [Digital]") == "[Chinese] [Digital]"
    assert short_title("[某作者] 夜 [中国翻訳]") == "[某作者] 夜"  # 只剩一個字就不去掉社團
    assert short_title("") == ""
    assert short_title("虛構的短標題") == "虛構的短標題"


def test_api_returns_short_title_but_backup_does_not(client):
    created = client.post("/api/resources", json={"code": "999001", "source_name": "站A", "title": "[某作者] Kasou no Hon | 虛構的書 [Chinese]"}).json()
    assert created["title"] == "[某作者] Kasou no Hon | 虛構的書 [Chinese]"
    assert created["short_title"] == "虛構的書"
    assert client.get("/api/resources").json()["items"][0]["short_title"] == "虛構的書"
    backup = client.get("/api/backup").json()
    assert "short_title" not in backup["resources"][0]
