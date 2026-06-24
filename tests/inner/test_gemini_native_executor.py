"""Unit tests for the Gemini CLI ACP executor."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from omnigent.inner.gemini_native_executor import (
    GeminiNativeExecutor,
    _approve_permission,
    _read_text_file,
)


class _FakeStdout:
    def __init__(self, lines: list[dict[str, Any]]) -> None:
        self._lines = [json.dumps(line).encode("utf-8") + b"\n" for line in lines]

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None


class _FakeProc:
    def __init__(self, *, stdout: _FakeStdout | None = None, returncode: int | None = 7) -> None:
        self.stdout = stdout
        self.stdin = _FakeStdin()
        self.stderr = None
        self.returncode = returncode
        self.pid = 999999
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_read_loop_eof_fails_pending_requests_and_resets_session_state() -> None:
    """If Gemini exits, pending requests fail and the next turn creates a new session."""

    async def _run() -> None:
        executor = GeminiNativeExecutor()
        proc = _FakeProc(stdout=_FakeStdout([]), returncode=42)
        executor._proc = proc  # type: ignore[attr-defined]
        executor._session_id = "stale-session"  # type: ignore[attr-defined]
        executor._sent_system_prompt = True  # type: ignore[attr-defined]
        executor._stderr_tail = ["fatal acp error"]  # type: ignore[attr-defined]
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        executor._pending[1] = future  # type: ignore[attr-defined]

        await executor._read_loop()  # type: ignore[attr-defined]

        assert proc.waited is True
        assert future.done()
        exc = future.exception()
        assert isinstance(exc, RuntimeError)
        assert "gemini --acp exited with exit code 42" in str(exc)
        assert "fatal acp error" in str(exc)
        assert executor._pending == {}  # type: ignore[attr-defined]
        assert executor._proc is None  # type: ignore[attr-defined]
        assert executor._session_id is None  # type: ignore[attr-defined]
        assert executor._sent_system_prompt is False  # type: ignore[attr-defined]

        calls: list[tuple[str, dict[str, Any]]] = []

        async def _fake_start() -> None:
            executor._proc = _FakeProc()  # type: ignore[attr-defined]

        async def _fake_request(
            method: str,
            params: dict[str, Any],
            *,
            timeout: float = 30.0,
        ) -> dict[str, Any]:
            del timeout
            calls.append((method, params))
            if method == "session/new":
                return {"sessionId": "fresh-session"}
            return {}

        executor._start = _fake_start  # type: ignore[method-assign]
        executor._request = _fake_request  # type: ignore[method-assign]

        await executor._ensure_session(None)  # type: ignore[attr-defined]

        assert [method for method, _params in calls] == ["initialize", "session/new"]
        assert executor._session_id == "fresh-session"  # type: ignore[attr-defined]

    asyncio.run(_run())


def test_interrupt_session_sends_cancel_notification() -> None:
    """Active turns are cancelled via ACP ``session/cancel`` notification."""

    async def _run() -> None:
        executor = GeminiNativeExecutor()
        proc = _FakeProc()
        executor._proc = proc  # type: ignore[attr-defined]
        executor._session_id = "session-1"  # type: ignore[attr-defined]

        assert await executor.interrupt_session("ignored") is True

        sent = json.loads(proc.stdin.writes[0])
        assert sent == {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": "session-1"},
        }

    asyncio.run(_run())


def test_send_frames_compact_json_rpc_lines() -> None:
    """JSON-RPC writes are newline-delimited compact JSON objects."""

    async def _run() -> None:
        executor = GeminiNativeExecutor()
        proc = _FakeProc()
        executor._proc = proc  # type: ignore[attr-defined]

        await executor._send(  # type: ignore[attr-defined]
            {"jsonrpc": "2.0", "id": 3, "method": "probe", "params": {"a": 1}}
        )

        assert proc.stdin.writes == [
            b'{"jsonrpc":"2.0","id":3,"method":"probe","params":{"a":1}}\n'
        ]

    asyncio.run(_run())


def test_read_loop_demuxes_response_update_and_client_request() -> None:
    """The ACP reader separates responses, notifications, and client requests."""

    async def _run() -> None:
        executor = GeminiNativeExecutor()
        proc = _FakeProc(
            stdout=_FakeStdout(
                [
                    {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "s",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "hello"},
                            },
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "fs/read_text_file",
                        "params": {"sessionId": "s", "path": "/tmp/x"},
                    },
                ]
            ),
            returncode=0,
        )
        executor._proc = proc  # type: ignore[attr-defined]
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        executor._pending[1] = future  # type: ignore[attr-defined]
        captured_requests: list[dict[str, Any]] = []

        async def _capture_request(msg: dict[str, Any]) -> None:
            captured_requests.append(msg)

        executor._handle_client_request = _capture_request  # type: ignore[method-assign]

        await executor._read_loop()  # type: ignore[attr-defined]
        await asyncio.sleep(0)

        assert future.result() == {"ok": True}
        assert (await executor._updates.get())["sessionUpdate"] == "agent_message_chunk"  # type: ignore[attr-defined]
        assert captured_requests == [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "fs/read_text_file",
                "params": {"sessionId": "s", "path": "/tmp/x"},
            }
        ]

    asyncio.run(_run())


def test_permission_response_falls_back_to_real_offered_option() -> None:
    """When no allow-once option exists, permission selection uses a real option id."""
    result = _approve_permission(
        {
            "options": [
                {"optionId": "allow-session", "kind": "allow_always"},
                {"optionId": "reject-once", "kind": "reject_once"},
            ]
        }
    )

    assert result == {"outcome": {"outcome": "selected", "optionId": "allow-session"}}


def test_read_text_file_uses_one_based_line_offsets(tmp_path) -> None:
    """ACP ``line`` is 1-based, so line=2 starts at the second line."""

    async def _run() -> None:
        path = tmp_path / "sample.txt"
        path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        result = await _read_text_file({"path": str(path), "line": 2, "limit": 1})

        assert result == {"content": "two\n"}

    asyncio.run(_run())
