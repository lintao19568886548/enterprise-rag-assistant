from pathlib import Path

import yaml


def test_staging_compose_has_private_dependencies_and_independent_workers():
    path = Path("compose.staging.yaml")
    raw = path.read_text(encoding="utf-8")
    compose = yaml.safe_load(raw)
    services = compose["services"]

    assert {
        "nginx",
        "import-api",
        "query-api",
        "import-worker",
        "cleanup-worker",
        "evaluation-worker",
        "prometheus",
        "postgres",
        "redis",
        "milvus",
        "etcd",
        "minio",
        "migrate",
    }.issubset(services)
    for name in ("postgres", "redis", "milvus", "etcd", "minio"):
        assert "ports" not in services[name]
        assert "healthcheck" in services[name]
    for name in ("import-api", "query-api"):
        assert services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert "--queues=import" in services["import-worker"]["command"]
    assert "--queues=cleanup" in services["cleanup-worker"]["command"]
    assert "--queues=evaluation" in services["evaluation-worker"]["command"]
    assert compose["networks"]["backend"]["internal"] is True
    assert services["minio"]["environment"]["MINIO_ROOT_PASSWORD"].startswith("${MINIO_SECRET_KEY:?")
    assert services["redis"]["environment"]["REDIS_PASSWORD"].startswith("${REDIS_PASSWORD:?")
    assert "--requirepass" in " ".join(services["redis"]["command"])
    assert "REDISCLI_AUTH" in services["redis"]["healthcheck"]["test"][1]
    assert "minioadmin" not in raw
    assert "change-this" not in raw
    assert services["prometheus"]["networks"] == ["backend"]
    nginx = Path("deploy/nginx/nginx.staging.conf").read_text(encoding="utf-8")
    assert nginx.count("location = /metrics") == 2
    assert "return 404" in nginx
