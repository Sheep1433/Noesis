from noesis.runtime.logging import resolve_log_level


def test_prod_defaults_to_info(monkeypatch) -> None:
    monkeypatch.delenv("NOESIS_LOG_LEVEL", raising=False)
    assert resolve_log_level("prod") == "INFO"


def test_log_level_override_is_explicit_and_valid(monkeypatch) -> None:
    monkeypatch.setenv("NOESIS_LOG_LEVEL", "debug")
    assert resolve_log_level("prod") == "DEBUG"

    monkeypatch.setenv("NOESIS_LOG_LEVEL", "not-a-level")
    assert resolve_log_level("prod") == "INFO"
