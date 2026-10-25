/** 大纲编辑页（§7.2–§7.4）：左侧叶子树 + 右侧编辑区 + 流式生成 + 结构同步。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Badge, Button, Card, Col, Empty, Input, Progress, Row, Space, Spin, Tag,
  Tree, Typography, message,
} from "antd";
import {
  ArrowLeftOutlined, SaveOutlined, StopOutlined, SyncOutlined, ThunderboltOutlined, FileTextOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { get, patch, post, put } from "../api/client";
import type { Outline, OutlineTreeNode, StreamEvent } from "../api/types";
import { streamJob } from "../lib/stream";
import { applyEvent, initialState, StreamBox, type StreamState } from "../components/StreamBox";
import { ChunkList } from "../components/ChunkList";
import { MarkdownView } from "../components/MarkdownView";
import { countWords, OUTLINE_STATUS } from "../lib/format";

export function OutlineEditor() {
  const { id } = useParams();
  const outlineId = Number(id);
  const navigate = useNavigate();
  const [outline, setOutline] = useState<Outline | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null); // outline_chapter id
  const [content, setContent] = useState("");
  const [saveState, setSaveState] = useState<"saved" | "saving" | "unsaved">("saved");
  const [stream, setStream] = useState<StreamState>(initialState);
  const [streamJobId, setStreamJobId] = useState<number | null>(null);
  const [diff, setDiff] = useState<{ has_diff: boolean; added: { number: string }[]; removed: { number: string }[]; changed: { number: string; new_number: string }[] } | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [preview, setPreview] = useState(false);
  const saveTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const o = await get<Outline>(`/api/outlines/${outlineId}`);
      setOutline(o);
      // 默认选中第一个未生成的叶子，否则第一个叶子
      const leaves = (o.tree || []).filter((n) => n.outline_chapter);
      const cur = leaves.find((n) => n.outline_chapter?.id === selectedId);
      const pick = cur || leaves.find((n) => !(n.outline_chapter?.content || "").trim()) || leaves[0];
      if (pick?.outline_chapter) {
        setSelectedId(pick.outline_chapter.id);
        setContent(pick.outline_chapter.content || "");
      }
    } finally { setLoading(false); }
  }, [outlineId, selectedId]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    void get<typeof diff>(`/api/outlines/${outlineId}/diff`).then(setDiff).catch(() => {});
  }, [outlineId, outline]);

  const leaves = useMemo(
    () => (outline?.tree || []).filter((n) => n.outline_chapter) as OutlineTreeNode[],
    [outline],
  );
  const current = leaves.find((n) => n.outline_chapter?.id === selectedId);
  const targetWords = current?.outline_chapter?.word_budget || 0;
  const curWords = countWords(content);
  const generatedCount = leaves.filter((n) => (n.outline_chapter?.content || "").trim()).length;

  // ---- 自动保存 ----
  const autoSave = (text: string) => {
    setSaveState("unsaved");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      if (!selectedId) return;
      setSaveState("saving");
      try {
        await put(`/api/outlines/chapters/${selectedId}`, { content: text, autosave: true });
        setSaveState("saved");
      } catch { setSaveState("unsaved"); }
    }, 2000);
  };

  const saveNow = async (silent = false) => {
    if (!selectedId) return;
    try {
      await put(`/api/outlines/chapters/${selectedId}`, { content });
      setSaveState("saved");
      if (!silent) message.success("已保存");
      await load();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "保存失败");
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); void saveNow(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ---- 生成 ----
  const generate = async () => {
    if (!selectedId) return;
    setStream(initialState);
    try {
      const res = await post<{ job_id: number }>(`/api/outlines/chapters/${selectedId}/generate`);
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

  const syncStructure = async () => {
    setSyncing(true);
    try {
      const res = await post<{ changes: { added: number; removed: number; renamed: number } }>(
        `/api/outlines/${outlineId}/sync`);
      message.success(`已同步：新增 ${res.changes.added}、失效 ${res.changes.removed}、更新 ${res.changes.renamed}`);
      setDiff(null);
      await load();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "同步失败");
    } finally { setSyncing(false); }
  };

  if (loading && !outline) return <Spin style={{ display: "block", margin: "80px auto" }} />;
  if (!outline) return <Empty style={{ marginTop: 80 }} />;

  // 保持树结构：按 parent_id 组装
  const buildTreeData = (nodes: Outline["tree"]): object[] => {
    if (!nodes) return [];
    const map = new Map(nodes.map((n) => [n.id, { node: n, children: [] as object[] }]));
    const roots: object[] = [];
    for (const n of nodes) {
      const item = {
        key: n.outline_chapter ? `oc-${n.outline_chapter.id}` : `container-${n.id}`,
        title: (
          <span className={`outline-tree-node ${n.outline_chapter ? "leaf" : ""}`}
            onClick={() => { if (n.outline_chapter) { setSelectedId(n.outline_chapter.id); setContent(n.outline_chapter.content || ""); setSaveState("saved"); } }}>
            <span className="tree-node-number">{n.number}</span>
            <span className="ellipsis" style={{ flex: 1 }}>{n.title}</span>
            {n.outline_chapter && (
              <Badge status={
                n.outline_chapter.status === "generated" ? "success" :
                n.outline_chapter.status === "modified" ? "warning" :
                n.outline_chapter.status === "generating" ? "processing" :
                n.outline_chapter.status === "invalid" ? "error" : "default"
              } text={OUTLINE_STATUS[n.outline_chapter.status]?.text || "未生成"} />
            )}
          </span>
        ),
        children: [] as object[],
      };
      map.get(n.id)?.children.push(item);
      if (n.parent_id == null || !map.has(n.parent_id)) roots.push(item);
      else map.get(n.parent_id)!.children.push(item);
    }
    return roots;
  };

  return (
    <div className="editor-page">
      <div className="page-toolbar">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate("/outlines")}>返回</Button>
        <Typography.Title level={5} style={{ margin: 0 }}>{outline.name}</Typography.Title>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          源设计：{outline.design?.title || "-"} ｜ 进度 {generatedCount}/{leaves.length}
        </Typography.Text>
        <span style={{ flex: 1 }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {saveState === "saved" ? "已保存" : saveState === "saving" ? "保存中…" : "未保存"}
        </Typography.Text>
        <Button icon={<SaveOutlined />} onClick={() => void saveNow()}>保存</Button>
        <Button onClick={() => navigate("/outlines/all")}>查看全部大纲</Button>
        <Button type="primary" ghost onClick={() => navigate("/drafts")}>章节创作 →</Button>
      </div>

      {diff?.has_diff && (
        <Alert
          type="warning" showIcon
          message="源章节设计已更新"
          description={
            <Space wrap>
              {diff.added.length > 0 && <span>新增：{diff.added.map((a) => a.number).join("、")}</span>}
              {diff.removed.length > 0 && <span>删除：{diff.removed.map((a) => a.number).join("、")}</span>}
              {diff.changed.length > 0 && <span>改动：{diff.changed.map((a) => `${a.number}→${a.new_number}`).join("、")}</span>}
              <Button size="small" type="primary" icon={<SyncOutlined />} loading={syncing} onClick={() => void syncStructure()}>
                同步结构（已有内容不丢）
              </Button>
            </Space>
          }
          style={{ marginBottom: 12 }}
        />
      )}

      <Row gutter={16}>
        <Col span={7}>
          <Card size="small" title="章节树（点击叶子编辑，父节点仅展开）">
            <Tree treeData={buildTreeData(outline.tree)} defaultExpandAll blockNode
              selectedKeys={selectedId != null ? [`oc-${selectedId}`] : []} />
            <div style={{ marginTop: 12 }}>
              <Progress percent={leaves.length ? Math.round((generatedCount / leaves.length) * 100) : 0}
                size="small" format={() => `${generatedCount}/${leaves.length}`} />
            </div>
          </Card>
        </Col>
        <Col span={17}>
          {current ? (
            <Card size="small"
              title={<Space>
                {current.number} {current.title}
                <Tag color="blue">目标 {targetWords} 字</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>当前大纲 {curWords} 字</Typography.Text>
                {current.outline_chapter?.status === "invalid" && <Tag color="error">已失效（不参与导出）</Tag>}
              </Space>}
              extra={<Space>
                <Button size="small" onClick={() => setPreview(!preview)}>{preview ? "编辑" : "预览"}</Button>
                <Button size="small" type="primary" icon={<ThunderboltOutlined />}
                  onClick={() => void generate()}
                  disabled={!!streamJobId || current.outline_chapter?.status === "invalid"}>
                  生成大纲
                </Button>
                {streamJobId && <Button size="small" danger icon={<StopOutlined />} onClick={stop}>停止</Button>}
              </Space>}>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                本章思路：{current.outline_chapter?.chapter_idea_snapshot || "（无）"}
              </Typography.Paragraph>
              {preview ? (
                <MarkdownView text={content} />
              ) : (
                <Input.TextArea
                  value={content}
                  onChange={(e) => { setContent(e.target.value); autoSave(e.target.value); }}
                  rows={14}
                  placeholder={current.outline_chapter?.content
                    ? ""
                    : "点击「生成大纲」获得本节写作方案：写作目标、论证结构、材料安排、注意事项。也可直接手写。"}
                />
              )}
              <StreamBox state={stream} onStop={stop} rawLabel="模型原始输出" />
              <div style={{ marginTop: 12 }}>
                <Typography.Title level={5} style={{ fontSize: 13 }}>
                  本节使用的片段（{outline.chunks?.filter((c) => c.outline_chapter_id === selectedId).length || 0}）
                </Typography.Title>
                <ChunkList
                  chunks={(outline.chunks || []).filter((c) => c.outline_chapter_id === selectedId)}
                  emptyText="生成大纲时会自动检索并关联片段" />
              </div>
            </Card>
          ) : (
            <Empty description="从左侧选择一个叶子章节" style={{ marginTop: 80 }} />
          )}
        </Col>
      </Row>
    </div>
  );
}
