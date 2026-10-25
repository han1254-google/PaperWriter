"""模型客户端（产品文档 §10）。

- DeepSeek：OpenAI 兼容 /chat/completions，支持 JSON 模式与流式。
- Qwen 嵌入：OpenAI 兼容 /embeddings（DashScope 或联调地址均可）。
- 错误分类 → 错误码；429/5xx/超时指数退避重试 2 次；401/403/402 不重试。
- 每次调用写 ai-*.log：trace、model、endpoint_host、prompt_version、attempt、
  duration、tokens、http_status、error_code。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

import httpx

from ..config import get_config
from ..errors import AppError
from ..logging_setup import log_ai, redact

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def classify_http(exc: httpx.HTTPStatusError) -> AppError:
    resp = exc.response
    status = resp.status_code
    detail = redact(resp.text[:2000])
    if status in (401, 403):
        return AppError("CFG_KEY_INVALID", detail=detail, stage="generate", retryable=False)
    if status == 402:
        return AppError("AI_BALANCE", detail=detail, stage="generate", retryable=False)
    if status == 429:
        return AppError("AI_RATE_LIMIT", detail=detail, stage="generate", retryable=True)
    if status in _RETRYABLE_STATUS:
        return AppError("AI_SERVER", detail=detail, stage="generate", retryable=True)
    body = detail.lower()
    if status == 400:
        if "context" in body or "length" in body or "maximum" in body:
            return AppError("AI_CONTEXT_LENGTH", detail=detail, stage="generate", retryable=False)
        if "content" in body and ("filter" in body or "policy" in body or "safety" in body):
            return AppError("AI_CONTENT_FILTER", detail=detail, stage="generate", retryable=False)
        if "model" in body:
            return AppError("CFG_MODEL_DENIED", detail=detail, stage="generate", retryable=False)
    return AppError("AI_SERVER", detail=detail, stage="generate", retryable=False)


def classify_transport(exc: httpx.TransportError) -> AppError:
    msg = str(exc).lower()
    if isinstance(exc, httpx.ReadTimeout):
        return AppError("AI_TIMEOUT", message=str(exc), stage="generate", retryable=True)
    if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)):
        return AppError("NET_TIMEOUT", message=str(exc), stage="generate", retryable=True)
    if "name or service not known" in msg or "getaddrinfo" in msg or "dns" in msg:
        return AppError("NET_DNS", message=str(exc), stage="generate", retryable=True)
    if "proxy" in msg:
        return AppError("NET_PROXY", message=str(exc), stage="generate", retryable=False)
    if isinstance(exc, httpx.ConnectError):
        return AppError("NET_OFFLINE", message=str(exc), stage="generate", retryable=True)
    return AppError("NET_TIMEOUT", message=str(exc), stage="generate", retryable=True)


async def _request_with_retry(request_fn, *, trace: str, module: str, prompt_version: str | None,
                              attempts: int = 3, base_delay: float = 1.5):
    """指数退避重试。最后一次失败原样抛出。"""
    last: AppError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await request_fn(attempt)
        except httpx.HTTPStatusError as e:
            last = classify_http(e)
            log_ai(trace=trace, module=module,
                   msg="request failed",
                   prompt_version=prompt_version or "-",
                   attempt=attempt, http_status=e.response.status_code,
                   error_code=last.code, retryable=last.retryable)
            if not last.retryable or attempt == attempts:
                raise last from e
        except httpx.TransportError as e:
            last = classify_transport(e)
            log_ai(trace=trace, module=module,
                   msg="request failed",
                   prompt_version=prompt_version or "-",
                   attempt=attempt, http_status=0, error_code=last.code,
                   retryable=last.retryable)
            if not last.retryable or attempt == attempts:
                raise last from e
        await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
    raise last  # type: ignore[misc]


async def chat(messages: list[dict[str, str]], *, trace: str, module: str,
               prompt_version: str | None = None, json_mode: bool = False,
               temperature: float | None = None, max_tokens: int | None = None) -> str:
    """非流式调用，返回完整文本。JSON 模式自动解析修复由调用方处理。"""
    cfg = get_config()
    ds = cfg.get("deepseek", {})
    timeout = cfg.get("generation.timeout_s", 180)
    url = ds.get("base_url", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": ds.get("model", "deepseek-v4-flash"),
        "messages": messages,
        "temperature": temperature if temperature is not None else ds.get("temperature", 0.4),
        "max_tokens": max_tokens or ds.get("max_output_tokens", 8192),
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async def _call(attempt: int):
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=payload, headers={
                "Authorization": f"Bearer {ds.get('api_key', '')}",
                "Content-Type": "application/json",
            })

    started = time.time()
    try:
        resp = await _request_with_retry(_call, trace=trace, module=module,
                                         prompt_version=prompt_version)
        data = resp.json()
        usage = data.get("usage", {})
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        log_ai(trace=trace, module=module, msg="request ok",
               prompt_version=prompt_version or "-", attempt=1,
               model=payload["model"],
               endpoint_host=url.split("/chat")[0],
               duration_ms=int((time.time() - started) * 1000),
               prompt_tokens=usage.get("prompt_tokens"),
               completion_tokens=usage.get("completion_tokens"),
               http_status=resp.status_code)
        if not content.strip():
            raise AppError("AI_EMPTY_OUTPUT", stage="generate", trace=trace, retryable=True)
        return content
    except AppError:
        raise
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise AppError("AI_BAD_JSON", message=str(e), stage="generate", trace=trace,
                       retryable=False) from e


async def chat_stream(messages: list[dict[str, str]], *, trace: str, module: str,
                      prompt_version: str | None = None,
                      temperature: float | None = None,
                      max_tokens: int | None = None,
                      json_mode: bool = False) -> AsyncIterator[dict[str, Any]]:
    """流式调用。yield {type: delta, text} …最后 {type: done, usage}。

    错误以 {type: error, code, userMessage, suggestion, traceId} 结束，不抛异常，
    便于直接透传给前端 SSE。
    """
    cfg = get_config()
    ds = cfg.get("deepseek", {})
    timeout = cfg.get("generation.timeout_s", 180)
    url = ds.get("base_url", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions"
    payload = {
        "model": ds.get("model", "deepseek-v4-flash"),
        "messages": messages,
        "temperature": temperature if temperature is not None else ds.get("temperature", 0.4),
        "max_tokens": max_tokens or ds.get("max_output_tokens", 8192),
        "stream": True,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers={
                "Authorization": f"Bearer {ds.get('api_key', '')}",
            }) as resp:
                if resp.status_code != 200:
                    e = httpx.HTTPStatusError("", request=resp.request, response=resp)
                    err = classify_http(e)
                    yield {"type": "error", **err.to_dict()}
                    return
                usage: dict[str, Any] = {}
                thinking_started = False
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    delta = ((obj.get("choices") or [{}])[0].get("delta") or {})
                    # 思维链模型：先流 reasoning_content，再流 content。
                    # 思考内容不进结果，仅提示前端「思考中」。
                    if delta.get("reasoning_content") and not thinking_started:
                        thinking_started = True
                        yield {"type": "progress", "message": "模型思考中…"}
                    text = delta.get("content")
                    if text:
                        yield {"type": "delta", "text": text}
                log_ai(trace=trace, module=module, msg="stream ok",
                       prompt_version=prompt_version or "-", model=payload["model"],
                       endpoint_host=url.split("/chat")[0],
                       duration_ms=int((time.time() - started) * 1000),
                       prompt_tokens=usage.get("prompt_tokens"),
                       completion_tokens=usage.get("completion_tokens"),
                       http_status=200)
                yield {"type": "done", "usage": usage}
    except (httpx.HTTPStatusError, httpx.TransportError) as e:
        err = classify_http(e) if isinstance(e, httpx.HTTPStatusError) else classify_transport(e)
        yield {"type": "error", **err.to_dict()}
    except AppError as e:
        yield {"type": "error", **e.to_dict()}


async def embed(texts: list[str], *, trace: str) -> list[list[float]]:
    """批量向量化（§5.4：批量 10–25 条）。

    支持两种协议，自动回退：
    1. OpenAI 兼容：POST {base}/embeddings，body {"model","input":[…]}
    2. DashScope 原生（联调网关 maas.qianwenaiapi.com）：
       POST {base}/api/v1/services/embeddings/text-embedding/text-embedding，
       body {"model","input":{"texts":[…]}}，响应 output.embeddings[].embedding
    """
    cfg = get_config()
    qw = cfg.get("qwen", {})
    timeout = cfg.get("generation.timeout_s", 180)
    base = qw.get("base_url", "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
    model = qw.get("embedding_model", "qwen3.7-text-embedding-flash")
    headers = {"Authorization": f"Bearer {qw.get('api_key', '')}"}
    started = time.time()

    async def _try(attempt: int):
        # 先 OpenAI 兼容
        url = base + "/embeddings"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json={"model": model, "input": texts}, headers=headers)
            if resp.status_code == 404 or (resp.status_code == 200 and not _is_json(resp.text)):
                # 回退 DashScope 原生
                native_base = base[:-len("/api/v1")] if base.endswith("/api/v1") else base
                url2 = native_base + "/api/v1/services/embeddings/text-embedding/text-embedding"
                resp2 = await client.post(url2, json={"model": model, "input": {"texts": texts}},
                                          headers=headers)
                if resp2.status_code != 200:
                    resp2.raise_for_status()
                data = resp2.json()
                out = (data.get("output") or {}).get("embeddings") or []
                items = sorted(out, key=lambda x: x.get("text_index", 0))
                vectors = [it["embedding"] for it in items]
                if len(vectors) != len(texts):
                    raise AppError("AI_BAD_JSON",
                                   message=f"embedding count {len(vectors)} != {len(texts)}",
                                   stage="embed", trace=trace)
                log_ai(trace=trace, module="embed", msg="embed ok (dashscope native)",
                       model=model, endpoint_host=base,
                       duration_ms=int((time.time() - started) * 1000),
                       batch=len(texts), dim=len(vectors[0]) if vectors else 0,
                       http_status=resp2.status_code)
                return vectors
            if resp.status_code != 200:
                resp.raise_for_status()
            data = resp.json()
            items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
            vectors = [it["embedding"] for it in items]
            if len(vectors) != len(texts):
                raise AppError("AI_BAD_JSON", message=f"embedding count {len(vectors)} != {len(texts)}",
                               stage="embed", trace=trace)
            log_ai(trace=trace, module="embed", msg="embed ok", model=model,
                   endpoint_host=base,
                   duration_ms=int((time.time() - started) * 1000),
                   batch=len(texts), dim=len(vectors[0]) if vectors else 0,
                   http_status=resp.status_code)
            return vectors

    return await _request_with_retry(_try, trace=trace, module="embed", prompt_version=None)


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, TypeError):
        return False
