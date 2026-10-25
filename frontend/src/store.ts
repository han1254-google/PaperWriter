/** 全局状态：设置、任务状态、前端日志上报（§11.2 / §11.3）。 */
import { create } from "zustand";
import { get, post } from "./api/client";
import type { Job, Settings, StreamEvent } from "./api/types";
import { newTraceId } from "./api/client";

type AppState = {
  settings: Settings | null;
  loadingSettings: boolean;
  runningJobs: Record<string, Job>; // key: entityType:entityId 或 job_id
  refreshSettings: () => Promise<void>;
  setRunningJob: (key: string, job: Job | null) => void;
};

export const useApp = create<AppState>((set) => ({
  settings: null,
  loadingSettings: true,
  runningJobs: {},
  refreshSettings: async () => {
    try {
      const s = await get<Settings>("/api/settings");
      set({ settings: s, loadingSettings: false });
    } catch {
      set({ loadingSettings: false });
    }
  },
  setRunningJob: (key, job) =>
    set((st) => {
      const runningJobs = { ...st.runningJobs };
      if (job) runningJobs[key] = job;
      else delete runningJobs[key];
      return { runningJobs };
    }),
}));

// ---------------------------------------------------------------------------
// 前端日志：优先 localStorage 队列，补发失败再入队（§11.2）
// ---------------------------------------------------------------------------
const LOG_KEY = "lp_client_logs";
const _tracer = () => Math.random().toString(16).slice(2, 10);

export function clientLog(level: "info" | "warn" | "error", message: string,
                          extra: Record<string, unknown> = {}) {
  const entry = { level, message, trace_id: _tracer(), ts: Date.now(), ...extra };
  try {
    const q = JSON.parse(localStorage.getItem(LOG_KEY) || "[]");
    q.push(entry);
    localStorage.setItem(LOG_KEY, JSON.stringify(q.slice(-200)));
  } catch { /* 忽略 */ }
  if (navigator.onLine) void flushClientLogs();
}

export async function flushClientLogs(): Promise<void> {
  let q: unknown[] = [];
  try {
    q = JSON.parse(localStorage.getItem(LOG_KEY) || "[]");
  } catch { return; }
  if (!q.length) return;
  try {
    await post("/api/client-logs", { entries: q });
    localStorage.removeItem(LOG_KEY);
  } catch { /* 保留队列，下次补发 */ }
}

export function setupGlobalErrorHandlers() {
  window.onerror = (msg, source, lineno) => {
    clientLog("error", `window.onerror: ${msg} @${source}:${lineno}`, { route: location.pathname });
  };
  window.addEventListener("unhandledrejection", (e) => {
    clientLog("error", `unhandledrejection: ${String(e.reason)}`, { route: location.pathname });
  });
  setInterval(flushClientLogs, 30000);
}

// ---------------------------------------------------------------------------
// 生成任务跟踪：轮询任务状态直到终态（离开页面不中断任务）
// ---------------------------------------------------------------------------
export function trackJob(key: string, jobId: number, onFinish?: (job: Job) => void) {
  const { setRunningJob } = useApp.getState();
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try {
      const job = await get<Job>(`/api/jobs/${jobId}`);
      setRunningJob(key, job);
      if (["succeeded", "failed", "cancelled"].includes(job.status)) {
        onFinish?.(job);
        setTimeout(() => setRunningJob(key, null), 5000);
        return;
      }
    } catch {
      /* 后端暂不可达，继续轮询 */
    }
    setTimeout(tick, 1500);
  };
  void tick();
  return () => { stopped = true; };
}

export { newTraceId };
export type { StreamEvent };
