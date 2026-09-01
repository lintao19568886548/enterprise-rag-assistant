import subprocess

from scripts.scan_secrets import expand_paths, scan_history, scan_text


def test_secret_scanner_detects_likely_key_without_echoing_it():
    credential = "sk-" + "livecredentialvalue1234567890"
    findings = scan_text(f"OPENAI_API_KEY={credential}", path="config.py")
    assert {(finding.rule, finding.path, finding.line) for finding in findings} == {
        ("openai-compatible-key", "config.py", 1),
    }
    assert credential not in repr(findings)


def test_secret_scanner_detects_quoted_generic_credential():
    credential = "live-" + "credential-value-1234567890"
    findings = scan_text(f'client_secret = "{credential}"', path="config.py")
    assert [finding.rule for finding in findings] == ["credential-assignment"]


def test_secret_scanner_allows_explicit_examples_and_test_values():
    assert not scan_text("OPENAI_API_KEY=replace-with-a-new-key", path=".env.example")
    assert not scan_text("OPENAI_API_KEY=sk-test-secret-value-123456", path="test_settings.py")


def test_secret_scanner_detects_private_key_header():
    header = "-----BEGIN " + "PRIVATE KEY-----"
    findings = scan_text(header, path="accidental.pem")
    assert [finding.rule for finding in findings] == ["private-key-header"]


def test_secret_scanner_inspects_reachable_history_without_echoing_values(tmp_path):
    credential = "sk-" + "historicalcredentialvalue1234567890"
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "scanner@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Secret Scanner Test"], cwd=tmp_path, check=True)
    source = tmp_path / "config.py"
    source.write_text(f'OPENAI_API_KEY = "{credential}"\n', encoding="utf-8")
    subprocess.run(["git", "add", "config.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "add historical fixture"], cwd=tmp_path, check=True)
    source.write_text('OPENAI_API_KEY = "replace-with-a-new-key"\n', encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "redact fixture"], cwd=tmp_path, check=True)

    findings, blob_count = scan_history(tmp_path)

    assert blob_count >= 2
    assert any(finding.path.endswith(":config.py") for finding in findings)
    assert credential not in repr(findings)


def test_secret_scanner_expands_explicit_directories(tmp_path):
    nested = tmp_path / "logs" / "nested"
    nested.mkdir(parents=True)
    artifact = nested / "application.log"
    artifact.write_text("safe log", encoding="utf-8")

    assert expand_paths([tmp_path / "logs"]) == [artifact.resolve()]
