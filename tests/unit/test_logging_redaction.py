import json
import logging

from backend.logging_setup import REDACTED, JsonFormatter, Redactor

SECRET = "a4b0c0ffee1234567890deadbeef0067"


def _format(msg: str, **extra: object) -> dict[str, object]:
    record = logging.makeLogRecord({"msg": msg, "levelname": "INFO", "name": "t", **extra})
    line = JsonFormatter(Redactor([SECRET])).format(record)
    parsed: dict[str, object] = json.loads(line)
    return parsed


def test_configured_secret_is_redacted_from_message() -> None:
    out = _format(f"calling library with key {SECRET}")
    assert SECRET not in json.dumps(out)
    assert REDACTED in str(out["msg"])


def test_configured_secret_is_redacted_from_extra_fields() -> None:
    out = _format("request", headers={"api-key": SECRET})
    assert SECRET not in json.dumps(out)


def test_anthropic_key_pattern_is_redacted_even_if_not_configured() -> None:
    out = _format("oops sk-ant-api03-AbCdEf123456_-xyz leaked")
    assert "sk-ant-api03" not in str(out["msg"])


def test_short_values_are_not_treated_as_secrets() -> None:
    redact = Redactor(["abc"])
    assert redact("abcdef") == "abcdef"


def test_output_is_single_json_line_with_extras() -> None:
    out = _format("study created", study="paloma-3")
    assert out["study"] == "paloma-3"
    assert out["level"] == "INFO"
