"""網站規則的網址範本只收 http(s)（或「{}」）：其他協定不能拿來開啟或讀取。"""
from app import refresh
from app.database import Resource


def test_source_template_must_be_http(client):
    assert client.put("/api/sources", json={"name": "怪網站", "template": "file:///etc/{}"}).status_code == 422
    assert client.put("/api/sources", json={"name": "怪網站", "template": "javascript:alert('{}')"}).status_code == 422
    assert client.put("/api/sources", json={"name": "正常網站", "template": "https://example.com/{}"}).status_code == 200
    assert client.put("/api/sources", json={"name": "直接網址", "template": "{}"}).status_code == 200


def test_restore_skips_bad_templates(client):
    backup = {"format": "oas-backup", "version": 1, "sources": [{"name": "壞範本", "template": "file:///x/{}"}, {"name": "好範本", "template": "https://ok.example/{}"}], "resources": []}
    client.post("/api/restore", json=backup)
    names = {row["name"]: row["template"] for row in client.get("/api/sources").json()}
    assert names.get("好範本") == "https://ok.example/{}" and not names.get("壞範本")


def test_page_url_only_http():
    assert refresh.page_url(Resource(code="1"), "file:///x/{}") == ""
    assert refresh.page_url(Resource(code="1"), "https://a.example/{}") == "https://a.example/1"
    assert refresh.page_url(Resource(code="https://b.example/x"), "{}") == "https://b.example/x"
