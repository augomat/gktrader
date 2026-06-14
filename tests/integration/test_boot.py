from fastapi.testclient import TestClient

from gktrader.api.app import create_app


def test_app_boots() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ui_login_page_renders() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/ui/login")

    assert response.status_code == 200
    assert "GKTrader" in response.text
    assert 'form method="post" action="/ui/login"' in response.text
