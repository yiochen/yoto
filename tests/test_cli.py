from __future__ import annotations

import json

from yoto_skill.cli import execute, main, parser
from yoto_skill.errors import YotoError


class FakeAuth:
    def complete(self, result):
        return {"received": json.loads(result)}


class FakeService:
    def __init__(self, failure: bool = False) -> None:
        self.failure = failure

    def list_cards(self):
        if self.failure:
            raise YotoError("mock_failure", "Mocked failure")
        return [{"card_id": "abc12", "title": "Bedtime", "songs": []}]


def test_cli_success_envelope(monkeypatch, capsys) -> None:
    monkeypatch.setattr("yoto_skill.cli.dependencies", lambda: (FakeAuth(), FakeService()))
    assert main(["--json", "cards", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "data": [{"card_id": "abc12", "title": "Bedtime", "songs": []}],
        "warnings": [],
    }


def test_cli_error_envelope(monkeypatch, capsys) -> None:
    monkeypatch.setattr("yoto_skill.cli.dependencies", lambda: (FakeAuth(), FakeService(True)))
    assert main(["--json", "cards", "list"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == {"code": "mock_failure", "message": "Mocked failure"}


def test_auth_complete_reads_result_from_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr("yoto_skill.cli.dependencies", lambda: (FakeAuth(), FakeService()))
    monkeypatch.setattr("sys.stdin", type("Input", (), {"read": lambda _self: '{"code":"c","state":"s"}'})())

    assert main(["--json", "auth", "complete"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"] == {"received": {"code": "c", "state": "s"}}


def test_numbered_icon_cli_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path))
    args = parser().parse_args(["icons", "number", "--number", "12"])
    result = execute(args, FakeAuth(), FakeService())
    assert result["width"] == result["height"] == 16
    assert result["icon"].endswith("icon-16.png")
