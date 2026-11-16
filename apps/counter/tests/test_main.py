import pytest
from counter_app.main import CounterSettings, requirements


def test_required_state_store_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_STATESTORE_NAME", "visits")
    (requirement,) = requirements(CounterSettings())
    assert requirement.name == "visits"
    assert requirement.type_prefix == "state."
    assert "ETAG" in requirement.capabilities
