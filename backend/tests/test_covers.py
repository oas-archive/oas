import io
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from app import covers

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture()
def fake_download(monkeypatch):
    calls = []

    def download(url):
        calls.append(url)
        if "broken" in url:
            raise covers.CoverError("來源拒絕（HTTP 403）")
        return PNG, "image/png"

    monkeypatch.setattr(covers, "download", download)
    return calls


def make(client, code, image_url):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "image_url": image_url}).json()


def wait_for(predicate, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_cover_miss_is_fetched_by_the_backend_then_served_locally(client, fake_download, monkeypatch):
    # 個人版也不再 307 轉址到來源：外部網址可以再轉址回手機上的其他 App，瀏覽器會帶著通行碼過去
    monkeypatch.setattr(covers, "schedule", lambda url: None)  # 不讓新增時的背景下載先存好，這裡要看讀封面這一次
    item = make(client, "1", "https://img.example/1.png")
    first = client.get(f"/api/covers/{item['id']}", follow_redirects=False)
    assert first.status_code == 200 and "location" not in first.headers and first.content == PNG
    assert fake_download == ["https://img.example/1.png"] and client.get("/api/covers/status").json()["cached"] == 1
    second = client.get(f"/api/covers/{item['id']}", follow_redirects=False)
    assert second.status_code == 200 and second.headers["content-type"] == "image/png" and second.content == PNG
    # 直接開啟封面網址也不准瀏覽器猜類型、不執行程式
    assert second.headers["x-content-type-options"] == "nosniff" and "sandbox" in second.headers["content-security-policy"]


def test_batch_cache_and_failures(client, fake_download):
    make(client, "1", "https://img.example/1.png")
    make(client, "2", "https://img.example/broken.png")
    make(client, "3", "https://img.example/1.png")  # 同一網址只下載一次
    make(client, "4", "")
    assert client.post("/api/covers/cache").json() == {"queued": 2}
    status = client.get("/api/covers/status").json()
    assert status["with_image"] == 2 and status["cached"] == 1 and status["failed"] == 1 and status["pending"] == 0
    assert status["batch"]["done"] == 2 and status["batch"]["running"] is False
    # 失敗紀錄 24 小時內不重試，除非明確要求
    assert client.post("/api/covers/cache").json() == {"queued": 0}
    assert client.post("/api/covers/cache", params={"retry_failed": True}).json() == {"queued": 1}
    assert fake_download.count("https://img.example/1.png") == 1


def test_cover_without_image(client, fake_download):
    item = make(client, "1", "")
    assert client.get(f"/api/covers/{item['id']}").status_code == 404
    assert client.get("/api/covers/99999").status_code == 404


def test_internal_addresses_rejected():
    for url in ["http://127.0.0.1/a.png", "http://localhost/a.png", "file:///etc/passwd", "http://10.0.0.5/a.png"]:
        with pytest.raises(covers.CoverError):
            covers.check_url(url)


def test_thumbnail_downscales_and_falls_back(client, monkeypatch):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 1600), (200, 40, 60)).save(buffer, "PNG")
    big = buffer.getvalue()
    monkeypatch.setattr(covers, "download", lambda url: (big if "big" in url else PNG, "image/png"))
    large = make(client, "big", "https://img.example/big.png")
    broken = make(client, "bad", "https://img.example/not-really.png")
    assert client.post("/api/covers/cache").json() == {"queued": 2}

    thumb = client.get(f"/api/covers/{large['id']}", params={"w": 360})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert image.width == 360 and image.height == 480
    assert len(thumb.content) < len(big)
    assert client.get(f"/api/covers/{large['id']}").content == big  # 不帶 w 仍回傳原圖

    fallback = client.get(f"/api/covers/{broken['id']}", params={"w": 360})
    assert fallback.status_code == 200 and fallback.headers["content-type"] == "image/png" and fallback.content == PNG


class FakeResponse:
    def __init__(self, content_type, body=b"<svg/>"):
        self.headers, self.body = self, body
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type

    def read(self, size):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.mark.parametrize("content_type", ["image/svg+xml", "text/html"])  # ICO 是點陣圖，可以收（網站頁面用網站圖示當封面）
def test_only_raster_covers_are_downloaded(monkeypatch, content_type):
    monkeypatch.setattr(covers, "check_url", lambda url: None)
    monkeypatch.setattr(covers, "safe_urlopen", lambda request, timeout: FakeResponse(content_type))
    with pytest.raises(covers.CoverError):
        covers.download("https://img.example/evil.svg")


def test_svg_cached_by_old_versions_is_not_served(client, monkeypatch):
    # 舊版會收下 SVG：以典藏館自己的網址提供時，裡面的程式會以典藏館的身分執行
    monkeypatch.setattr(covers, "download", lambda url: (b"<svg><script>1</script></svg>", "image/svg+xml"))
    item = make(client, "1", "https://img.example/evil.svg")
    with covers.SessionLocal() as db:
        assert covers.ensure_cover(db, "https://img.example/evil.svg").status == "ok"
        db.commit()
    monkeypatch.setattr(covers, "download", lambda url: (_ for _ in ()).throw(covers.CoverError("只接受點陣圖")))
    response = client.get(f"/api/covers/{item['id']}", follow_redirects=False)
    assert response.status_code == 404 and b"<script>" not in response.content and "location" not in response.headers
    assert client.get(f"/api/covers/{item['id']}", params={"w": 360}, follow_redirects=False).status_code == 404


def test_uncached_cover_source_failure_is_404_and_not_retried_every_time(client, fake_download, monkeypatch):
    monkeypatch.setattr(covers, "schedule", lambda url: None)
    item = make(client, "1", "https://img.example/broken.png")
    for _ in range(3):
        response = client.get(f"/api/covers/{item['id']}", follow_redirects=False)
        assert response.status_code == 404 and "location" not in response.headers
    assert fake_download == ["https://img.example/broken.png"]  # 24 小時內失敗過的不再連線


@pytest.mark.parametrize("extension,kind", [("tif", "TIFF"), ("bmp", "BMP"), ("ppm", "PPM")])
def test_only_web_image_formats_are_decoded(tmp_path, extension, kind):
    disguised = tmp_path / f"cover-{extension}.jpg"
    Image.new("RGB", (40, 60), (200, 40, 60)).save(disguised, kind)
    assert covers.image_ratio(disguised) is None and covers.image_colour(disguised) == "" and covers.image_fingerprint(disguised) == ""


@pytest.mark.parametrize("kind", ["JPEG", "PNG", "GIF", "ICO", "WEBP"])
def test_web_image_formats_still_work(tmp_path, kind):
    path = tmp_path / f"cover.{kind.lower()}"
    Image.new("RGB", (64, 64), (200, 40, 60)).save(path, kind)
    assert covers.image_ratio(path) == 1.0 and covers.image_colour(path) and covers.image_fingerprint(path)


def test_image_bomb_is_refused(tmp_path):
    path = tmp_path / "bomb.png"
    Image.MAX_IMAGE_PIXELS = None
    Image.new("1", (9000, 9000)).save(path, "PNG")
    assert covers.image_ratio(path) is None and covers.image_colour(path) == "" and covers.image_fingerprint(path) == ""


class SlowResponse(FakeResponse):
    """每次只送一點點的慢速來源：每段都推進假的時鐘，不真的等待、不連外。"""

    def __init__(self, clock, seconds_per_chunk):
        super().__init__("image/png", b"x" * 10)
        self.clock, self.seconds_per_chunk, self.reads = clock, seconds_per_chunk, 0

    def read1(self, size):
        self.reads += 1
        self.clock[0] += self.seconds_per_chunk
        return self.body


def test_slow_source_is_cut_off_within_total_time(monkeypatch):
    clock = [1000.0]
    slow = SlowResponse(clock, seconds_per_chunk=4)
    monkeypatch.setattr(covers, "_clock", lambda: clock[0])
    monkeypatch.setattr(covers, "check_url", lambda url: None)
    monkeypatch.setattr(covers, "safe_urlopen", lambda request, timeout: slow)
    with pytest.raises(covers.CoverError, match="超過"):
        covers.fetch_image("https://img.example/slow.png")
    assert clock[0] - 1000.0 <= covers.TOTAL_SECONDS + 4  # 最多多等一段
    assert slow.reads == 4


def test_image_finishing_in_time_is_read_in_chunks(monkeypatch):
    clock = [0.0]

    class Quick(FakeResponse):
        def __init__(self):
            super().__init__("image/png")
            self.parts = [b"\x89PNG", b"-part2", b""]

        def read1(self, size):
            clock[0] += 1
            return self.parts.pop(0)

    monkeypatch.setattr(covers, "_clock", lambda: clock[0])
    monkeypatch.setattr(covers, "check_url", lambda url: None)
    monkeypatch.setattr(covers, "safe_urlopen", lambda request, timeout: Quick())
    assert covers.fetch_image("https://img.example/ok.png") == (b"\x89PNG-part2", "image/png")


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class OneByteRaw(io.RawIOBase):
    """每次只給一個位元組、並推進假的時鐘：模擬一點一點送回應標頭的來源。"""

    def __init__(self, clock, data, seconds_per_byte):
        super().__init__()
        self.clock, self.data, self.seconds_per_byte = clock, data, seconds_per_byte

    def readable(self):
        return True

    def readinto(self, buffer):
        self.clock[0] += self.seconds_per_byte
        if not self.data:
            return 0
        buffer[0], self.data = self.data[0], self.data[1:]
        return 1


def test_every_wait_is_limited_by_the_remaining_total_time(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(covers, "_clock", lambda: clock[0])
    sock = FakeSocket()
    reader = io.BufferedReader(covers._DeadlineRaw(OneByteRaw(clock, b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 100, 3), sock, 115.0))
    with pytest.raises(TimeoutError, match="總時限"):
        reader.readline(); reader.readline()  # 標頭是一行一行讀的：單一行就能拖很久
    assert sock.timeouts and all(0 < value <= covers.TIMEOUT_SECONDS for value in sock.timeouts)
    assert sock.timeouts[-1] <= 3  # 越接近截止，等待上限越短
    assert clock[0] <= 115.0 + 3


@pytest.fixture()
def slow_site(monkeypatch):
    """本機的假來源（不連外）：/headers 一個位元組一個位元組地送標頭，/r0、/r1… 每次等一下才轉址到下一個。"""
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            try:
                if self.path == "/headers":
                    self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                    for byte in b"X-Slow: " + b"a" * 400:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.05)
                else:
                    time.sleep(0.3)
                    self.send_response(302)
                    self.send_header("Location", f"/r{int(self.path[2:]) + 1}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
            except OSError:
                pass  # 客戶端時間到就斷線

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(covers, "TOTAL_SECONDS", 1.0)
    monkeypatch.setattr(covers, "public_address", lambda address: True)  # 讓本機的假來源通過位址檢查
    monkeypatch.setattr(covers, "_opener", urllib.request.build_opener(  # 不經過系統代理設定
        urllib.request.ProxyHandler({}), covers._CheckedRedirect, covers._CheckedHTTPHandler, covers._CheckedHTTPSHandler))
    yield f"http://127.0.0.1:{server.server_address[1]}", hits
    server.shutdown()
    server.server_close()  # 關掉監聽的 socket，否則 pytest 會報 ResourceWarning


def test_slow_response_headers_are_cut_off_within_total_time(slow_site):
    base, _ = slow_site
    start = time.monotonic()
    with pytest.raises(covers.CoverError, match="總時限"):
        covers.fetch_image(f"{base}/headers")
    assert time.monotonic() - start < 2.5  # 沒有總時限時每個位元組都在 12 秒內送到，會一直等下去


def test_redirect_chain_is_cut_off_within_total_time(slow_site):
    base, hits = slow_site
    start = time.monotonic()
    with pytest.raises(covers.CoverError, match="總時限"):
        covers.fetch_image(f"{base}/r0")
    assert time.monotonic() - start < 2.5
    assert len(hits) < 10  # 不會把 10 次轉址都走完
