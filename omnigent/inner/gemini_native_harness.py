"""``harness: gemini-native`` wrap for the Gemini CLI ACP mode."""

from __future__ import annotations

from fastapi import FastAPI

from omnigent.inner.executor import Executor
from omnigent.inner.gemini_native_executor import GeminiNativeExecutor
from omnigent.runtime.harnesses._executor_adapter import ExecutorAdapter


def _build_gemini_native_executor() -> Executor:
    """Construct the Gemini CLI ACP executor."""
    return GeminiNativeExecutor()


def create_app() -> FastAPI:
    """Build the ``gemini-native`` harness FastAPI app."""
    adapter = ExecutorAdapter(executor_factory=_build_gemini_native_executor)
    return adapter.build()
