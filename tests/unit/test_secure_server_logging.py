from app.core.server import run_api


def test_uvicorn_raw_access_log_is_disabled(monkeypatch):
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("app.core.server.uvicorn.run", fake_run)
    application = object()

    run_api(application, host="127.0.0.1", port=8001)

    assert captured == {
        "app": application,
        "host": "127.0.0.1",
        "port": 8001,
        "access_log": False,
    }
