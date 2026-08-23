"""Minimal DeepSeek chat-completions client using only Python stdlib."""

from __future__ import annotations

import getpass
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


DEFAULT_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"
KEYCHAIN_SERVICE = "com.memento.context-agent.deepseek-api-key"


def read_deepseek_api_key() -> str:
    """Read the key from the process environment or the user's macOS Keychain.

    The environment remains the portable/CI override.  Keychain lookup is a
    local macOS convenience and never places the credential in argv, files, or
    error messages.
    """

    environment_key = os.environ.get("DEEPSEEK_API_KEY")
    if environment_key:
        return environment_key
    if sys.platform != "darwin":
        raise ProviderError(
            "缺少 DEEPSEEK_API_KEY 环境变量；macOS 之外不支持钥匙串回退"
        )
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                getpass.getuser(),
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProviderError("无法读取 macOS 钥匙串中的 DeepSeek API Key") from exc
    keychain_key = result.stdout.rstrip("\r\n") if result.returncode == 0 else ""
    if not keychain_key:
        raise ProviderError(
            "缺少 DEEPSEEK_API_KEY，且 macOS 钥匙串中没有 Memento DeepSeek API Key"
        )
    return keychain_key


class ProviderError(RuntimeError):
    """A redacted provider failure safe to show in a CLI."""

    def __init__(
        self,
        message: str,
        *,
        usage: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        # Only structured billing metadata crosses the error boundary.  The
        # response content and request messages are intentionally discarded.
        self.usage = dict(usage) if isinstance(usage, Mapping) else None
        self.request_id = request_id if isinstance(request_id, str) else None
        self.model = model if isinstance(model, str) else None


@dataclass(frozen=True)
class CompletionResult:
    content: str
    usage: Mapping[str, Any]
    request_id: str | None
    model: str


class DeepSeekProvider:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 60.0,
        thinking: str = "disabled",
        reasoning_effort: str | None = None,
        max_tokens: int = 1200,
    ) -> None:
        if thinking not in {"disabled", "enabled"}:
            raise ValueError("thinking 必须是 disabled 或 enabled")
        if reasoning_effort not in {None, "high", "max"}:
            raise ValueError("reasoning_effort 必须是 high 或 max")
        if reasoning_effort and thinking != "enabled":
            raise ValueError("reasoning_effort 只可与 thinking=enabled 一起使用")
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("max_tokens 必须是大于 0 的整数")
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens

    def complete(self, messages: Sequence[Mapping[str, str]]) -> CompletionResult:
        api_key = read_deepseek_api_key()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "thinking": {"type": self.thinking},
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Memento-Context-Agent/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout,
                context=ssl.create_default_context(),
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # Do not include response bodies: upstream messages are not part of
            # the stable contract and may contain echoed request details.
            raise ProviderError(f"DeepSeek API 返回 HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            reason = type(exc.reason).__name__
            raise ProviderError(f"DeepSeek API 连接失败（{reason}）") from exc
        except TimeoutError as exc:
            raise ProviderError("DeepSeek API 请求超时") from exc

        usage_for_error: Mapping[str, Any] | None = None
        request_id_for_error: str | None = None
        model_for_error: str | None = self.model
        try:
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise TypeError("response body is not an object")
            raw_usage = body.get("usage")
            usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
            usage_for_error = usage
            raw_request_id = body.get("id")
            request_id = raw_request_id if isinstance(raw_request_id, str) else None
            request_id_for_error = request_id
            raw_model = body.get("model")
            response_model = raw_model if isinstance(raw_model, str) else self.model
            model_for_error = response_model
            choice = body["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason != "stop":
                safe_reason = (
                    finish_reason
                    if finish_reason
                    in {
                        "length",
                        "content_filter",
                        "insufficient_system_resource",
                        "tool_calls",
                    }
                    else "unknown"
                )
                raise ProviderError(
                    f"DeepSeek 响应未正常结束（{safe_reason}）",
                    usage=usage,
                    request_id=request_id,
                    model=response_model,
                )
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise KeyError("empty content")
        except ProviderError:
            raise
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
        ) as exc:
            raise ProviderError(
                "DeepSeek API 返回了无法识别的响应结构",
                usage=usage_for_error,
                request_id=request_id_for_error,
                model=model_for_error,
            ) from exc
        return CompletionResult(
            content=content,
            usage=usage,
            request_id=request_id,
            model=response_model,
        )
