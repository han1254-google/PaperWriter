/** 统一请求封装（产品文档 §11.3 前端要求）。

- 每次用户操作生成 trace_id，通过 X-Trace-Id 传给后端。
- 错误归一化为 AppError；只读请求自动重试 1 次，写入/生成不自动重试。
- 后端不可达时抛 SYS_API_FAILED。
*/
import type { ApiResp, AppError } from "./types";

let traceCounter = 0;
export function newTraceId(): string {
  return Math.random().toString(16).slice(2, 10).padEnd(8, "0") + (traceCounter++ % 10);
}

const fallbackError: AppError = {
  code: "SYS_API_FAILED",
  message: "backend unreachable",
  userMessage: "后端没有响应",
  suggestion: "确认启动脚本窗口仍在运行，刷新页面或重新双击启动脚本",
  traceId: "",
  stage: "unknown",
  retryable: false,
};

export class ApiError extends Error {
  error: AppError;
  constructor(e: AppError) {
    super(e.userMessage);
    this.error = e;
  }
}

async function rawRequest(
  method: string,
  path: string,
  body?: unknown,
  opts: { formData?: FormData; params?: Record<string, string> } = {},
): Promise<Response> {
  const trace = newTraceId();
  let url = path;
  if (opts.params) {
    const qs = new URLSearchParams(opts.params).toString();
    url += (path.includes("?") ? "&" : "?") + qs;
  }
  const init: RequestInit = { method, headers: { "X-Trace-Id": trace } };
  if (opts.formData) {
    init.body = opts.formData;
  } else if (body !== undefined) {
    init.headers = { ...(init.headers as Record<string, string>), "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch {
    fallbackError.traceId = trace;
    throw new ApiError(fallbackError);
  }
  if (!resp.ok) {
    let payload: ApiResp<unknown> | null = null;
    try {
      payload = (await resp.json()) as ApiResp<unknown>;
    } catch {
      /* 非 JSON 错误体 */
    }
    // 注意：错误体可能缺 error 字段（如 405 的 FastAPI 默认 JSON），不能把 undefined 传给 ApiError
    const err: AppError =
      payload && !payload.ok && payload.error
        ? payload.error
        : {
            code: "SYS_API_FAILED",
            message: `http ${resp.status}`,
            userMessage: `后端返回异常（HTTP ${resp.status}）`,
            suggestion: "刷新页面重试",
            traceId: trace,
            stage: "unknown",
            retryable: false,
          };
    throw new ApiError(err);
  }
  return resp;
}

export async function api<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: { formData?: FormData; params?: Record<string, string>; retry?: boolean } = {},
): Promise<T> {
  const retry = opts.retry ?? (method === "GET");
  let lastErr: ApiError | null = null;
  for (let attempt = 0; attempt <= (retry ? 1 : 0); attempt++) {
    try {
      const resp = await rawRequest(method, path, body, opts);
      const payload = (await resp.json()) as ApiResp<T>;
      if (!payload.ok) {
        if (payload.error) throw new ApiError(payload.error);
        throw new ApiError({
          code: "SYS_API_FAILED",
          message: "unexpected response shape",
          userMessage: "后端返回异常",
          suggestion: "刷新页面重试",
          traceId: "",
          stage: "unknown",
          retryable: false,
        });
      }
      return payload.data;
    } catch (e) {
      if (e instanceof ApiError) {
        lastErr = e;
        if (!e.error.retryable && attempt > 0) break;
        if (attempt === (retry ? 1 : 0)) throw e;
      } else throw e;
    }
  }
  throw lastErr;
}

export const get = <T>(path: string, params?: Record<string, string>) =>
  api<T>("GET", path, undefined, { params });

export const post = <T>(path: string, body?: unknown) => api<T>("POST", path, body);

export const put = <T>(path: string, body?: unknown) => api<T>("PUT", path, body);

export const patch = <T>(path: string, body?: unknown) => api<T>("PATCH", path, body);

export const del = <T>(path: string) => api<T>("DELETE", path);

/** 下载文件（导出/诊断包/原文件）。 */
export async function download(path: string, params?: Record<string, string>): Promise<void> {
  let url = path;
  if (params) url += "?" + new URLSearchParams(params).toString();
  const resp = await fetch(url, { headers: { "X-Trace-Id": newTraceId() } });
  if (!resp.ok) {
    try {
      const payload = (await resp.json()) as ApiResp<unknown>;
      if (!payload.ok) throw new ApiError(payload.error);
    } catch (e) {
      if (e instanceof ApiError) throw e;
    }
    throw new ApiError(fallbackError);
  }
  const blob = await resp.blob();
  const disposition = resp.headers.get("content-disposition") || "";
  const m = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
  const filename = m ? decodeURIComponent(m[1]) : path.split("/").pop() || "download";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
