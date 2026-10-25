/** ErrorBoundary + 错误展示组件（§11.3：兜底界面 + 三层呈现）。 */
import { Component, type ReactNode } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { ReloadOutlined, CopyOutlined, FolderOpenOutlined } from "@ant-design/icons";
import type { AppError } from "../api/types";
import { clientLog } from "../store";

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error) {
    clientLog("error", `render error: ${error.message}`, { route: location.pathname });
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 48, maxWidth: 640, margin: "0 auto" }}>
          <Alert
            type="error"
            showIcon
            message="页面渲染出错"
            description={String(this.state.error)}
            action={
              <Space>
                <Button icon={<ReloadOutlined />} onClick={() => location.reload()}>刷新页面</Button>
                <Button icon={<CopyOutlined />} onClick={() => navigator.clipboard.writeText(String(this.state.error))}>复制诊断信息</Button>
                <Button icon={<FolderOpenOutlined />} onClick={() => post("/api/diagnostics/open-logs")}>打开日志目录</Button>
              </Space>
            }
          />
        </div>
      );
    }
    return this.props.children;
  }
}

import { post } from "../api/client";

export function ErrorPanel({ error, onRetry, compact }: {
  error: AppError; onRetry?: () => void; compact?: boolean;
}) {
  return (
    <Alert
      type="error"
      showIcon
      message={error.userMessage}
      description={
        <Space direction="vertical" size={4} style={{ width: "100%" }}>
          {error.suggestion && <Typography.Text type="secondary">{error.suggestion}</Typography.Text>}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            错误码：{error.code} ｜ 诊断号：{error.traceId}
            <Button
              type="link" size="small" icon={<CopyOutlined />}
              onClick={() => navigator.clipboard.writeText(`${error.code} ${error.traceId}`)}
            >
              复制诊断信息
            </Button>
            <Button type="link" size="small" onClick={() => void post("/api/diagnostics/open-logs")}>
              打开日志目录
            </Button>
          </Typography.Text>
        </Space>
      }
      action={onRetry ? <Button size="small" onClick={onRetry}>重试</Button> : undefined}
      style={compact ? undefined : { marginBottom: 12 }}
    />
  );
}
