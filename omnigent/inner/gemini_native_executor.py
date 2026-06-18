"""Executor that drives the Gemini CLI over Agent Client Protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, TypeAlias, cast

from omnigent.inner.executor import (
    Executor,
    ExecutorConfig,
    ExecutorError,
    ExecutorEvent,
    Message,
    ReasoningChunk,
    TextChunk,
    ToolCallRequest,
    ToolSpec,
    TurnCancelled,
    TurnComplete,
)
from omnigent.inner.native_attachments import materialize_attachment

_logger = logging.getLogger(__name__)

_GEMINI_BIN_ENV = "HARNESS_GEMINI_NATIVE_BIN"
_DEFAULT_GEMINI_BIN = "gemini"
JsonDict: TypeAlias = dict[str, Any]  # type: ignore[explicit-any]


class GeminiNativeExecutor(Executor):
    """ACP executor backed by ``gemini --acp`` and CLI subscription login."""

    def __init__(self, *, gemini_bin: str | None = None, cwd: Path | None = None) -> None:
        self._gemini_bin = gemini_bin or os.environ.get(_GEMINI_BIN_ENV) or _DEFAULT_GEMINI_BIN
        self._cwd = cwd or Path.cwd()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._client_request_tasks: set[asyncio.Task[None]] = set()
        self._next_id = 1
        self._pending: dict[int | str, asyncio.Future[JsonDict]] = {}
        self._updates: asyncio.Queue[JsonDict] = asyncio.Queue()
        self._session_id: str | None = None
        self._active_prompt_id: int | str | None = None
        self._sent_system_prompt = False
        self._terminals: dict[str, asyncio.subprocess.Process] = {}
        self._start_lock = asyncio.Lock()

    def supports_streaming(self) -> bool:
        return True

    def handles_tools_internally(self) -> bool:
        return True

    async def interrupt_session(self, session_key: str) -> bool:
        del session_key
        if self._session_id is None or self._proc is None or self._proc.stdin is None:
            return False
        await self._notify("session/cancel", {"sessionId": self._session_id})
        return True

    async def close_session(self, session_key: str) -> None:
        del session_key
        await self.aclose()

    async def aclose(self) -> None:
        """Terminate the Gemini ACP subprocess and any client terminals."""
        for proc in list(self._terminals.values()):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        self._terminals.clear()

        if self._proc is not None:
            proc = self._proc
            if proc.returncode is None:
                _signal_process_group(proc, signal.SIGTERM)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                if proc.returncode is None:
                    _signal_process_group(proc, signal.SIGKILL)
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(proc.wait(), timeout=3.0)
            self._proc = None

        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
            self._stderr_task = None
        for task in list(self._client_request_tasks):
            task.cancel()
        for task in list(self._client_request_tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._client_request_tasks.clear()

        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        self._session_id = None
        self._active_prompt_id = None

    async def run_turn(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        system_prompt: str,
        config: ExecutorConfig | None = None,
    ) -> AsyncIterator[ExecutorEvent]:
        del tools
        prompt_text = _latest_user_text(messages, self._cwd)
        if not prompt_text:
            yield ExecutorError(message="Gemini native turn had no user text to send")
            return

        try:
            await self._ensure_session(config)
        except Exception as exc:  # noqa: BLE001 - converted to executor error.
            yield ExecutorError(message=f"Gemini native failed to start: {exc}")
            return

        assert self._session_id is not None
        prompt = _build_prompt(prompt_text, system_prompt, first_turn=not self._sent_system_prompt)
        self._sent_system_prompt = True
        request_id = self._allocate_id()
        prompt_task = asyncio.create_task(
            self._request_with_id(
                request_id,
                "session/prompt",
                {"sessionId": self._session_id, "prompt": [{"type": "text", "text": prompt}]},
            )
        )
        self._active_prompt_id = request_id
        try:
            while True:
                update_task = asyncio.create_task(self._updates.get())
                done, pending = await asyncio.wait(
                    {prompt_task, update_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if update_task in done:
                    update = update_task.result()
                    event = _update_to_event(update)
                    if event is not None:
                        yield event
                    continue
                for task in pending:
                    task.cancel()
                result = prompt_task.result()
                stop_reason = str(result.get("stopReason") or "")
                usage = _usage_from_result(result)
                if stop_reason == "cancelled":
                    yield TurnCancelled()
                else:
                    yield TurnComplete(response=None, usage=usage)
                return
        except asyncio.CancelledError:
            await self.interrupt_session("default")
            raise
        except Exception as exc:  # noqa: BLE001 - surface provider/protocol failures.
            yield ExecutorError(message=f"Gemini native turn failed: {exc}")
        finally:
            self._active_prompt_id = None

    async def _ensure_session(self, config: ExecutorConfig | None) -> None:
        async with self._start_lock:
            if self._proc is None:
                await self._start()
                await self._request(
                    "initialize",
                    {
                        "protocolVersion": 1,
                        "clientCapabilities": {
                            "fs": {"readTextFile": True, "writeTextFile": True},
                            "terminal": True,
                        },
                        "clientInfo": {"name": "omnigent-gemini-native", "version": "0.1.0"},
                    },
                )
            if self._session_id is None:
                result = await self._request(
                    "session/new",
                    {"cwd": str(self._cwd), "mcpServers": []},
                )
                session_id = result.get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    raise RuntimeError(f"session/new returned no sessionId: {result!r}")
                self._session_id = session_id
            if config is not None and config.model:
                with contextlib.suppress(Exception):
                    await self._request(
                        "session/set_model",
                        {"sessionId": self._session_id, "modelId": config.model},
                        timeout=10.0,
                    )

    async def _start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._gemini_bin,
                "--acp",
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"{self._gemini_bin!r} CLI was not found on PATH") from exc
        self._reader_task = asyncio.create_task(self._read_loop(), name="gemini-acp-read-loop")
        self._stderr_task = asyncio.create_task(
            self._stderr_log_loop(),
            name="gemini-acp-stderr-log-loop",
        )

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                break
            try:
                msg = cast(JsonDict, json.loads(raw.decode("utf-8")))
            except json.JSONDecodeError:
                _logger.warning("Ignoring non-JSON Gemini ACP line: %r", raw)
                continue
            if "id" in msg and "method" in msg:
                task = asyncio.create_task(self._handle_client_request(msg))
                self._client_request_tasks.add(task)
                task.add_done_callback(self._client_request_tasks.discard)
            elif "id" in msg:
                request_id = msg["id"]
                fut = self._pending.pop(request_id, None)
                if fut is not None and not fut.done():
                    if "error" in msg:
                        error = msg["error"]
                        if isinstance(error, dict):
                            message = error.get("message", error)
                        else:
                            message = error
                        fut.set_exception(RuntimeError(str(message)))
                    else:
                        fut.set_result(msg.get("result") or {})
            elif msg.get("method") == "session/update":
                params = msg.get("params") or {}
                update = params.get("update") if isinstance(params, dict) else None
                if isinstance(update, dict):
                    self._updates.put_nowait(cast(JsonDict, update))

    async def _stderr_log_loop(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            raw = await self._proc.stderr.readline()
            if not raw:
                return
            _logger.debug("gemini --acp stderr: %s", raw.decode("utf-8", "replace").rstrip())

    async def _request(
        self,
        method: str,
        params: JsonDict,
        *,
        timeout: float = 30.0,
    ) -> JsonDict:
        return await self._request_with_id(self._allocate_id(), method, params, timeout=timeout)

    async def _request_with_id(
        self,
        request_id: int | str,
        method: str,
        params: JsonDict,
        *,
        timeout: float = 300.0,
    ) -> JsonDict:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[JsonDict] = loop.create_future()
        self._pending[request_id] = fut
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return await asyncio.wait_for(fut, timeout=timeout)

    async def _notify(self, method: str, params: JsonDict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _send(self, msg: JsonDict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Gemini ACP process is not running")
        self._proc.stdin.write(json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n")
        await self._proc.stdin.drain()

    def _allocate_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    async def _handle_client_request(self, msg: JsonDict) -> None:
        request_id = msg.get("id")
        method = str(msg.get("method") or "")
        params = cast(JsonDict, msg.get("params") if isinstance(msg.get("params"), dict) else {})
        try:
            if method == "session/request_permission":
                result = _approve_permission(params)
            elif method == "fs/read_text_file":
                result = _read_text_file(params)
            elif method == "fs/write_text_file":
                result = _write_text_file(params)
            elif method == "terminal/create":
                result = await self._terminal_create(params)
            elif method == "terminal/output":
                result = self._terminal_output(params)
            elif method == "terminal/wait_for_exit":
                result = await self._terminal_wait_for_exit(params)
            elif method == "terminal/kill":
                result = self._terminal_kill(params)
            elif method == "terminal/release":
                result = self._terminal_release(params)
            else:
                raise RuntimeError(f"unsupported Gemini ACP client request: {method}")
            await self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:  # noqa: BLE001 - JSON-RPC error response.
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
            )

    async def _terminal_create(self, params: JsonDict) -> JsonDict:
        command = str(params.get("command") or "")
        if not command:
            raise RuntimeError("terminal/create missing command")
        args = params.get("args")
        argv = [command, *(args if isinstance(args, list) else [])]
        env = os.environ.copy()
        for item in params.get("env") or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                env[item["name"]] = str(item.get("value") or "")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(params.get("cwd") or self._cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        terminal_id = f"term_{uuid.uuid4().hex}"
        self._terminals[terminal_id] = proc
        return {"terminalId": terminal_id}

    def _terminal_output(self, params: JsonDict) -> JsonDict:
        terminal_id = str(params.get("terminalId") or "")
        if terminal_id not in self._terminals:
            raise RuntimeError(f"unknown terminalId: {terminal_id}")
        # The ACP terminal API is pull-based; Phase 1 returns an empty slice.
        return {"output": "", "truncated": False}

    async def _terminal_wait_for_exit(self, params: JsonDict) -> JsonDict:
        proc = self._terminal_proc(params)
        code = await proc.wait()
        return {
            "exitCode": code if code >= 0 else None,
            "signal": None if code >= 0 else str(-code),
        }

    def _terminal_kill(self, params: JsonDict) -> JsonDict:
        proc = self._terminal_proc(params)
        if proc.returncode is None:
            proc.kill()
        return {}

    def _terminal_release(self, params: JsonDict) -> JsonDict:
        terminal_id = str(params.get("terminalId") or "")
        proc = self._terminals.pop(terminal_id, None)
        if proc is not None and proc.returncode is None:
            proc.kill()
        return {}

    def _terminal_proc(self, params: JsonDict) -> asyncio.subprocess.Process:
        terminal_id = str(params.get("terminalId") or "")
        proc = self._terminals.get(terminal_id)
        if proc is None:
            raise RuntimeError(f"unknown terminalId: {terminal_id}")
        return proc


def _build_prompt(prompt_text: str, system_prompt: str, *, first_turn: bool) -> str:
    if first_turn and system_prompt.strip():
        return f"{system_prompt.strip()}\n\n{prompt_text}"
    return prompt_text


def _signal_process_group(proc: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, sig)


def _latest_user_text(messages: list[Message], cwd: Path) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _content_to_text(message.get("content"), cwd)
    return ""


def _content_to_text(content: object, cwd: Path) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type")
                if item_type in {"input_text", "text"}:
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif item_type in {"input_image", "image", "input_file", "file"}:
                    materialized = materialize_attachment(cast(JsonDict, item), cwd)
                    if materialized is not None:
                        parts.append(str(materialized))
        return "\n".join(part for part in parts if part)
    return ""


def _update_to_event(update: JsonDict) -> ExecutorEvent | None:
    kind = update.get("sessionUpdate")
    if kind == "agent_message_chunk":
        text = _content_text(update.get("content"))
        return TextChunk(text=text) if text else None
    if kind == "agent_thought_chunk":
        text = _content_text(update.get("content"))
        return ReasoningChunk(delta=text, event_type="reasoning_text") if text else None
    if kind == "tool_call":
        name = _tool_name(update)
        return ToolCallRequest(
            name=name,
            args={"title": update.get("title"), "kind": update.get("kind")},
            metadata={"call_id": update.get("toolCallId")},
        )
    return None


def _content_text(content: object) -> str:
    if isinstance(content, dict) and content.get("type") == "text":
        text = content.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _tool_name(update: JsonDict) -> str:
    tool_call_id = update.get("toolCallId")
    if isinstance(tool_call_id, str) and "__" in tool_call_id:
        return tool_call_id.split("__", 1)[0]
    title = update.get("title")
    return str(title or update.get("kind") or "gemini_tool")


def _approve_permission(params: JsonDict) -> JsonDict:
    options = params.get("options")
    option_id = "proceed_once"
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and option.get("kind") == "allow_once":
                raw = option.get("optionId")
                if isinstance(raw, str):
                    option_id = raw
                    break
    return {"outcome": {"outcome": "selected", "optionId": option_id}}


def _read_text_file(params: JsonDict) -> JsonDict:
    path = Path(str(params["path"]))
    text = path.read_text(encoding="utf-8")
    line = params.get("line")
    limit = params.get("limit")
    if isinstance(line, int) or isinstance(limit, int):
        lines = text.splitlines(keepends=True)
        start = line if isinstance(line, int) else 0
        end = start + limit if isinstance(limit, int) else None
        text = "".join(lines[start:end])
    return {"content": text}


def _write_text_file(params: JsonDict) -> JsonDict:
    path = Path(str(params["path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(params.get("content") or ""), encoding="utf-8")
    return {}


def _usage_from_result(result: JsonDict) -> JsonDict | None:
    quota = (result.get("_meta") or {}).get("quota")
    if not isinstance(quota, dict):
        return None
    tokens = quota.get("token_count")
    if not isinstance(tokens, dict):
        return None
    usage: JsonDict = {}
    if isinstance(tokens.get("input_tokens"), int):
        usage["input_tokens"] = tokens["input_tokens"]
    if isinstance(tokens.get("output_tokens"), int):
        usage["output_tokens"] = tokens["output_tokens"]
    if usage:
        usage["total_tokens"] = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    return usage or None
