/** 实验方案编辑页：研究想法 → 流式生成 → 溯源表（[来源N] 点击查看原文）→ 导出。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert, Button, Card, Col, Empty, Input, List, Popover, Row, Space, Spin, Tag,
  Typography, message,
} from "antd";
import {
  ArrowLeftOutlined, DownloadOutlined, StopOutlined, ThunderboltOutlined, SaveOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { download, get, patch, post } from "../api/client";
import type { ExperimentPlan, StreamEvent } from "../api/types";
import { streamJob } from "../lib/stream";
import { applyEvent, initialState, StreamBox, type StreamState } from "../components/StreamBox";
import { DraftView, MarkdownView } from "../components/MarkdownView";
import { fmtDate } from "../lib/format";

export function ExperimentPlanEditor() {
  const { id } = useParams();
  const planId = Number(id);
  const navigate = useNavigate();
  const [plan, setPlan] = useState<ExperimentPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [idea, setIdea] = useState("");
  const [stream, setStream] = useState<StreamState>(initialState);
  const [streamJobId, setStreamJobId] = useState<number | null>(null);
  const [preview, setPreview] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const p = await get<ExperimentPlan>(`/api/plans/${planId}`);
      setPlan(p);
      setIdea(p.research_idea);
    } finally { setLoading(false); }
  }, [planId]);

  useEffect(() => { void load(); }, [load]);

  const generate = async () => {
    if (!idea || idea.trim().length < 10) { message.warning("请先填写研究想法（至少 10 字）"); return; }
    if (idea.trim() !== plan?.research_idea) {
      await patch(`/api/plans/${planId}`, { research_idea: idea.trim() });
    }
    setStream(initialState);
    try {
      const res = await post<{ job_id: number }>(`/api/plans/${planId}/generate`);
      setStreamJobId(res.job_id);
      void streamJob(res.job_id, (ev) => {
        setStream((st) => applyEvent(st, ev));
        if (ev.type === "done" || ev.type === "error") {
          void load();
          setStreamJobId(null);
        }
      });
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "生成失败");
    }
  };

  const stop = () => {
    if (streamJobId) void post(`/api/jobs/${streamJobId}/cancel`).then(() => setStreamJobId(null));
  };

  const saveContent = async () => {
    try {
      await patch(`/api/plans/${planId}`, { content: plan?.content, title: plan?.title });
      message.success("已保存");
      await load();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "保存失败");
    }
  };

  // 注意：hook 必须在所有条件 return 之前调用，否则触发 React #310
  const citations = plan?.citations || [];
  const unverified = citations.filter((c) => !c.verified);
  // 预览模式的证据原文附录（与导出文件同构）
  const evidenceMd = useMemo(() => {
    const bound = citations.filter((c) => c.chunk_id);
    if (!bound.length) return "";
    const blocks = bound.map((c) => {
      const meta = [c.doc_authors, c.doc_published_at, c.heading_path,
        c.page_start ? `第${c.page_start}页` : null].filter(Boolean).join("；");
      return `### [来源${c.source_no}] ${c.doc_title || "未命名文献"}${meta ? `（${meta}）` : ""}\n\n> ${(c.chunk_text || "").trim()}`;
    });
    return "\n\n## 证据原文\n\n" + blocks.join("\n\n");
  }, [citations]);

  if (loading && !plan) return <Spin style={{ display: "block", margin: "80px auto" }} />;
  if (!plan) return <Empty style={{ marginTop: 80 }} />;

  return (
    <div className="editor-page">
      <div className="page-toolbar">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate("/plans")}>返回</Button>
        <Typography.Title level={5} style={{ margin: 0 }} className="ellipsis">
          {plan.title || "未命名实验方案"}
        </Typography.Title>
        <span style={{ flex: 1 }} />
        <Button icon={<SaveOutlined />} onClick={() => void saveContent()}>保存</Button>
        <Button icon={<DownloadOutlined />} onClick={() => void download(`/api/plans/${planId}/export`)}>导出</Button>
      </div>

      <Row gutter={16}>
        <Col span={8}>
          <Card size="small" title="研究想法" extra={
            <Button size="small" type="primary" icon={<ThunderboltOutlined />}
              disabled={!!streamJobId} onClick={() => void generate()}>
              生成方案
            </Button>
          }>
            <Input.TextArea value={idea} rows={8} onChange={(e) => setIdea(e.target.value)}
              placeholder="例如：我想研究姜黄素对结直肠癌细胞自噬的影响，希望设计体外实验方案……" />
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
              生成流程：解析意图 → 构造查询 → 混合检索文献证据（方法学片段加权）→
              生成方案（[来源N] 绑定证据）→ 数字原文核对。
            </Typography.Paragraph>
            <StreamBox state={stream} onStop={stop} rawLabel="模型原始输出" />
          </Card>
          <Card size="small" title={`溯源表（${citations.length} 条）`} style={{ marginTop: 12 }}
            extra={<Button size="small" onClick={async () => {
              await post(`/api/plans/${planId}/reverify`);
              message.success("已重新校验");
              await load();
            }}>重新校验</Button>}>
            {citations.length === 0 ? (
              <Typography.Text type="secondary">生成方案后自动绑定文献证据</Typography.Text>
            ) : (
              <List size="small" dataSource={citations}
                renderItem={(c) => (
                  <List.Item>
                    <Space direction="vertical" size={0} style={{ width: "100%" }}>
                      <Space size={4}>
                        <span className="cite-marker">[来源{c.source_no}]</span>
                        {c.verified ? <Tag color="green" style={{ margin: 0 }}>已核对</Tag>
                          : <Tag color="red" style={{ margin: 0 }}>{c.data_note || "数据待核实"}</Tag>}
                      </Space>
                      {c.chunk_id ? (
                        <Popover
                          title={<Space direction="vertical" size={0}>
                            <span>{c.doc_title}</span>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {c.heading_path || ""}{c.page_start ? ` 第${c.page_start}页` : ""}
                            </Typography.Text>
                          </Space>}
                          content={<div style={{ maxWidth: 480, maxHeight: 300, overflow: "auto" }}>
                            {(c.chunk_text || "").slice(0, 800)}
                          </div>}
                          trigger="click">
                          <Button size="small" type="link" style={{ padding: 0 }}>
                            {c.doc_title ? (c.doc_title || "").slice(0, 24) : "查看证据原文"}
                          </Button>
                        </Popover>
                      ) : (
                        <Typography.Text type="danger" style={{ fontSize: 12 }}>
                          （模型引用了证据之外的内容）
                        </Typography.Text>
                      )}
                      {c.quote && (
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                          {(c.quote || "").slice(0, 60)}…
                        </Typography.Text>
                      )}
                    </Space>
                  </List.Item>
                )} />
            )}
            {unverified.length > 0 && (
              <Alert type="warning" showIcon style={{ marginTop: 8 }}
                message={`${unverified.length} 条溯源需人工核对（数字未在原文找到或引用越界）`} />
            )}
          </Card>
        </Col>
        <Col span={16}>
          <Card size="small" title="方案正文"
            extra={
              <Space>
                <Button size="small" onClick={() => setPreview(!preview)}>{preview ? "编辑" : "预览"}</Button>
                {plan.intent && Object.keys(plan.intent).length > 0 && (
                  <Tag color="blue">
                    {Object.entries(plan.intent)
                      .filter(([k, v]) => k !== "queries" && v)
                      .map(([k, v]) => `${k}: ${String(v).slice(0, 12)}`)
                      .join(" ｜ ").slice(0, 60)}
                  </Tag>
                )}
              </Space>
            }>
            {plan.content ? (
              preview ? (
                <>
                  <DraftView text={plan.content} />
                  {evidenceMd && <MarkdownView text={evidenceMd} />}
                </>
              ) : (
                <Input.TextArea
                  value={plan.content || ""}
                  onChange={(e) => setPlan({ ...plan, content: e.target.value })}
                  rows={26}
                  style={{ fontFamily: "inherit" }}
                />
              )
            ) : (
              <Empty description="点击左侧「生成方案」开始" style={{ marginTop: 60 }} />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
