from fastapi.testclient import TestClient

from app.import_process.api.file_import_service import app as import_app
from app.query_process.api.query_service import app as query_app


def test_import_liveness():
    with TestClient(import_app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["service"] == "import"


def test_query_liveness_and_request_id():
    with TestClient(query_app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "test-request-id"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-id"


def test_upload_rejects_disallowed_extension_before_writing():
    with TestClient(import_app) as client:
        response = client.post(
            "/upload",
            files={"files": ("malware.exe", b"MZ", "application/octet-stream")},
        )
    assert response.status_code == 415
    assert response.json()["code"] == "FILE_TYPE_NOT_ALLOWED"
