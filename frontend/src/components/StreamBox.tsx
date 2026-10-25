/** 流式生成框（§3.4 生成中）：进度、检索结果、原始输出、停止按钮。 */
import { useRef, useState } from "react";
import { Button, Progress, Space, Tag, Typography } from "antd";
import { StopOutlined, LoadingOutlined } from "@ant-design/icons";
import type { StreamEvent } from "../api/types";

export type StreamState = {
  phase: "idle" | "retrieving" | "generating" | "parsing" | "done" | "error";
  message: string;
  raw: string;
  chunks: { chunk_id: number; title: string; page_start: number | null }[];
  error?: string;
  warnings: string[];
  result?: Record<string, unknown>;
};

export const initialState: StreamState = {
  phase: "idle", message: "", raw: "", chunks: [], warnings: [],
};

export function applyEvent(st: StreamState, ev: StreamEvent): StreamState {
  switch (ev.type) {
    case "progress":
      return { ...st, message: ev.message, phase: st.phase === "idle" ? "retrieving" : st.phase };
    case "warning":
      return { ...st, warnings: [...st.warnings, ev.message] };
    case "retrieval":
      return { ...st, chunks: ev.chunks, phase: "generating", message: "正在生成…" };
    case "retrieval_empty":
      return { ...st, phase: "generating", message: "未检索到相关文献，正在生成…",
               warnings: [...st.warnings, "当前检索范围没有找到相关文献"] };
    case "delta":
      return { ...st, raw: st.raw + ev.text, phase: "generating" };
    case "parsing":
      return { ...st, phase: "parsing", message: ev.message };
    case "parse_failed":
      return { ...st, phase: "error", error: "模型输出无法解析为章节结构" };
    case "result":
      return { ...st, result: { ...st.result, ...ev } };
    case "done":
      return { ...st, phase: "done" };
    case "error":
      return { ...st, phase: "error", error: ev.userMessage };
    default:
      return st;
  }
}

export function StreamBox({ state, onStop, rawLabel }: {
  state: StreamState;
  onStop?: () => void;
  rawLabel?: string;
}) {
  const rawRef = useRef<HTMLDivElement>(null);
  if (state.phase === "idle") return null;
  const pct = state.phase === "done" ? 100 : state.phase === "generating" ? 60 : 20;
  return (
    <div className="stream-box">
      <div className="stream-box-head">
        <Space>
          {state.phase === "done" ? <Tag color="success">完成</Tag>
            : state.phase === "error" ? <Tag color="error">失败</Tag>
            : <LoadingOutlined spin />}
          <Typography.Text>{state.message || "处理中…"}</Typography.Text>
        </Space>
        <span style={{ flex: 1 }} />
        {(state.phase !== "done" && state.phase !== "error") && (
          <Button size="small" danger icon={<StopOutlined />} onClick={onStop}>停止</Button>
        )}
      </div>
      <Progress percent={pct} status={state.phase === "error" ? "exception" : state.phase === "done" ? "success" : "active"} size="small" />
      {state.warnings.map((w, i) => (
        <Typography.Paragraph key={i} type="warning" style={{ margin: "4px 0", fontSize: 12 }}>⚠ {w}</Typography.Paragraph>
      ))}
      {state.error && <Typography.Text type="danger">{state.error}</Typography.Text>}
      {state.chunks.length > 0 && (
        <div className="stream-chunks">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            已检索到 {state.chunks.length} 条片段：{state.chunks.map((c) => c.title).join("、").slice(0, 120)}
          </Typography.Text>
        </div>
      )}
      {state.raw && (
        <div className="stream-raw" ref={rawRef}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {rawLabel || "模型原始输出"}（{state.raw.length} 字）
          </Typography.Text>
          <pre>{state.raw}</pre>
        </div>
      )}
    </div>
  );
}
