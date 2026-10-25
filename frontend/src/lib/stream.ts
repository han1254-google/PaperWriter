/** NDJSON 流式读取（生成任务事件流，§3.4 生成中）。 */
import type { StreamEvent } from "../api/types";

export async function streamJob(
  jobId: number,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`/api/stream/${jobId}`, {
    headers: { "X-Trace-Id": Math.random().toString(16).slice(2, 10) },
    signal,
  });
  if (!resp.ok || !resp.body) throw new Error(`stream failed: ${resp.status}`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line) continue;
      try {
        onEvent(JSON.parse(line) as StreamEvent);
      } catch {
        /* 忽略坏行 */
      }
    }
  }
}

/** 流式 JSON 生成：把 delta 拼起来，返回全文。 */
export async function streamText(
  jobId: number,
  onDelta: (text: string, full: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  let full = "";
  await streamJob(jobId, (ev) => {
    if (ev.type === "delta") {
      full += ev.text;
      onDelta(ev.text, full);
    } else if (ev.type === "error") {
      throw Object.assign(new Error(ev.userMessage), ev);
    }
  }, signal);
  return full;
}
