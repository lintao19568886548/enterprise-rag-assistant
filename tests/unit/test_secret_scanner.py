from scripts.scan_secrets import scan_text


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
