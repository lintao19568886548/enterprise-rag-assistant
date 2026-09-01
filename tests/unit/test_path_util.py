from app.utils.path_util import get_project_root


def test_project_root_does_not_require_dotenv(monkeypatch) -> None:
    monkeypatch.delenv("PROJECT_ROOT", raising=False)

    project_root = get_project_root()

    assert (project_root / "pyproject.toml").is_file()
