/** 章节生成编辑页（§6.2–§6.6）：输入区、流式生成、章节树编辑、预算、召回片段、冲突检查与确认。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Button, Card, Col, Collapse, Dropdown, Empty, Form, Input, InputNumber, Modal,
  Popconfirm, Progress, Radio, Row, Select, Space, Spin, Tag, Tree, Typography, message,
} from "antd";
import {
  ArrowLeftOutlined, CheckOutlined, DeleteOutlined, EditOutlined, PlusOutlined,
  RedoOutlined, SaveOutlined, StopOutlined, ThunderboltOutlined, UndoOutlined,
  VerticalAlignBottomOutlined, VerticalAlignTopOutlined, FileSearchOutlined,
} from "@ant-design/icons";
import { Link, useNavigate, useParams } from "react-router-dom";
import { get, post, put } from "../api/client";
import type { Chunk, ConflictItem, Design, DesignChunkLink, StreamEvent } from "../api/types";
import { streamJob } from "../lib/stream";
import { applyEvent, initialState, StreamBox, type StreamState } from "../components/StreamBox";
import { ChunkList } from "../components/ChunkList";
import { ErrorPanel } from "../components/ErrorBoundary";
import { fmtDate } from "../lib/format";

const WORD_PRESETS = [3000, 5000, 8000, 12000, 20000];

type TreeItem = {
  key: number;
  title: string;
  idea: string;
  parent_id: number | null;
  word_budget: number;
  number: string;
  children?: TreeItem[];
};

function toTreeItems(nodes: Design["chapters"] | undefined): TreeItem[] {
  const items: TreeItem[] = (nodes || []).map((n) => ({
    key: n.id, title: n.title, idea: n.idea || "", parent_id: n.parent_id,
    word_budget: n.word_budget, number: n.number,
  }));
  const byId = new Map(items.map((i) => [i.key, i]));
  const roots: TreeItem[] = [];
  for (const it of items) {
    const parent = it.parent_id != null ? byId.get(it.parent_id) : undefined;
    if (parent) (parent.children ||= []).push(it);
    else roots.push(it);
  }
  const sortRec = (list: TreeItem[]) => list.sort((a, b) => a.number.localeCompare(b.number, "zh"));
  sortRec(roots);
  return roots;
}

function flattenTree(roots: TreeItem[]): TreeItem[] {
  const out: TreeItem[] = [];
  const walk = (items: TreeItem[]) => items.forEach((i) => { out.push(i); walk(i.children || []); });
  walk(roots);
  return out;
}

export function DesignEditor() {
  const { id } = useParams();
  const designId = Number(id);
  const navigate = useNavigate();
  const [design, setDesign] = useState<Design | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<"saved" | "saving" | "unsaved">("saved");
  const [form] = Form.useForm();
  const [treeRoots, setTreeRoots] = useState<TreeItem[]>([]);
  const [selectedKey, setSelectedKey] = useState<number | null>(null);
  const [editNode, setEditNode] = useState<{ key: number; mode: "edit" | "add-child" | "add-sibling" } | null>(null);
  const [editForm] = Form.useForm();
  const [stream, setStream] = useState<StreamState>(initialState);
  const [streamJobId, setStreamJobId] = useState<number | null>(null);
  const [conflicts, setConflicts] = useState<ConflictItem[] | null>(null);
  const [conflictChecked, setConflictChecked] = useState<boolean>(false);
  const [conflictChecking, setConflictChecking] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<null | { userMessage: string; code: string; traceId: string; suggestion?: string }>(null);
  const undoStack = useRef<string[]>([]);
  const redoStack = useRef<string[]>([]);
  const saveTimer = useRef<number | null>(null);

  // ---- 载入 ----
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await get<Design>(`/api/designs/${designId}`);
      setDesign(d);
      setTreeRoots(toTreeItems(d.chapters));
      setConflicts(d.conflict_report?.conflicts || null);
      setConflictChecked(!!d.conflict_report && !d.conflict_report.check_failed);
      form.setFieldsValue({
        intent: d.intent, title: d.title, description: d.description,
        total_word_count: d.total_word_count,
      });
      setSaveState("saved");
    } finally { setLoading(false); }
  }, [designId, form]);

  useEffect(() => { void load(); }, [load]);

  // ---- 预算计算 ----
  const flat = useMemo(() => flattenTree(treeRoots), [treeRoots]);
  const leaves = useMemo(() => {
    const parentIds = new Set(flat.map((n) => n.parent_id).filter(Boolean));
    return flat.filter((n) => !parentIds.has(n.key));
  }, [flat]);
  const parentSum = useMemo(() => {
    const m = new Map<number, number>();
    for (const n of flat) if (n.parent_id != null) m.set(n.parent_id, (m.get(n.parent_id) || 0) + n.word_budget);
    return m;
  }, [flat]);
  const allocated = leaves.reduce((s, n) => s + n.word_budget, 0);
  const total = design?.total_word_count || 0;
  const diff = total - allocated;

  // ---- 树操作（带撤销） ----
  const pushUndo = () => {
    undoStack.current.push(JSON.stringify(treeRoots));
    if (undoStack.current.length > 20) undoStack.current.shift();
    redoStack.current = [];
    setSaveState("unsaved");
  };

  const applyTree = (roots: TreeItem[]) => {
    pushUndo();
    setTreeRoots(roots);
    void autoSave(roots);
  };

  const autoSave = async (roots: TreeItem[]) => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    setSaveState("unsaved");
    saveTimer.current = window.setTimeout(async () => {
      setSaveState("saving");
      try {
        await put(`/api/designs/${designId}`, {
          tree: flattenTree(roots).map((n, i) => ({
            id: n.key, parent_id: n.parent_id, title: n.title, idea: n.idea,
            word_budget: n.word_budget, sort: i,
          })),
          autosave: true,
        });
        setSaveState("saved");
      } catch {
        setSaveState("unsaved");
      }
    }, 2000);
  };

  const saveNow = async (silent = false) => {
    setSaving(true);
    try {
      const v = form.getFieldsValue();
      await put(`/api/designs/${designId}`, {
        intent: v.intent, title: v.title, description: v.description,
        overall_idea: design?.overall_idea, total_word_count: v.total_word_count,
        tree: flattenTree(treeRoots).map((n, i) => ({
          id: n.key, parent_id: n.parent_id, title: n.title, idea: n.idea,
          word_budget: n.word_budget, sort: i,
        })),
      });
      setSaveState("saved");
      if (!silent) message.success("已保存");
      await load();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "保存失败");
    } finally { setSaving(false); }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); void saveNow(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const undo = () => {
    const prev = undoStack.current.pop();
    if (prev) {
      redoStack.current.push(JSON.stringify(treeRoots));
      setTreeRoots(JSON.parse(prev));
      setSaveState("unsaved");
    }
  };
  const redo = () => {
    const next = redoStack.current.pop();
    if (next) {
      undoStack.current.push(JSON.stringify(treeRoots));
      setTreeRoots(JSON.parse(next));
      setSaveState("unsaved");
    }
  };

  // ---- 节点操作 ----
  const addNode = (parentKey: number | null, mode: "add-child" | "add-sibling", siblingOf?: number) => {
    setEditNode({ key: siblingOf ?? parentKey ?? 0, mode });
    editForm.setFieldsValue({ title: "", idea: "", word_budget: 0 });
  };

  const commitNode = () => {
    if (!editNode) return;
    const v = editForm.getFieldsValue();
    if (!v.title?.trim()) { message.warning("请输入章节标题"); return; }
    const all = flattenTree(treeRoots);
    if (all.length >= 200) { message.warning("章节节点已达上限 200"); return; }
    let parentId: number | null = null;
    if (editNode.mode === "add-child") parentId = editNode.key;
    else if (editNode.mode === "add-sibling") {
      parentId = all.find((n) => n.key === editNode.key)?.parent_id ?? null;
    }
    const newNode: TreeItem = {
      key: Date.now(), title: v.title.trim(), idea: v.idea || "",
      parent_id: parentId, word_budget: v.word_budget || 0, number: "",
    };
    const roots = JSON.parse(JSON.stringify(treeRoots)) as TreeItem[];
    const walk = (items: TreeItem[]): boolean => {
      for (let i = 0; i < items.length; i++) {
        if (editNode.mode === "add-sibling" && items[i].key === editNode.key) {
          items.splice(i + 1, 0, newNode);
          return true;
        }
        if (editNode.mode === "add-child" && items[i].key === editNode.key) {
          (items[i].children ||= []).push(newNode);
          return true;
        }
        if (walk(items[i].children || [])) return true;
      }
      return false;
    };
    if (!walk(roots)) roots.push(newNode);
    applyTree(roots);
    setSelectedKey(newNode.key);
    setEditNode(null);
  };

  const removeNode = (key: number) => {
    const all = flattenTree(treeRoots);
    const countSubtree = (k: number): number => {
      let n = 1;
      const child = all.filter((x) => x.parent_id === k);
      for (const c of child) n += countSubtree(c.key);
      return n;
    };
    const count = countSubtree(key);
    Modal.confirm({
      title: count > 1 ? `删除该章节及其 ${count - 1} 个子章节？` : "删除该章节？",
      content: "相关引用将进入孤立引用列表，不静默丢失。",
      okText: "删除", okButtonProps: { danger: true },
      onOk: () => {
        const roots = JSON.parse(JSON.stringify(treeRoots)) as TreeItem[];
        const del = (items: TreeItem[]): TreeItem[] =>
          items.filter((i) => i.key !== key).map((i) => ({ ...i, children: del(i.children || []) }));
        applyTree(del(roots));
        if (selectedKey === key) setSelectedKey(null);
      },
    });
  };

  const moveLevel = (key: number, dir: "up" | "down") => {
    const roots = JSON.parse(JSON.stringify(treeRoots)) as TreeItem[];
    const all = flattenTree(roots);
    const node = all.find((n) => n.key === key);
    if (!node) return;
    if (dir === "up" && node.parent_id == null) { message.info("顶层章节无法再升级"); return; }
    if (dir === "down") {
      const siblings = all.filter((n) => n.parent_id === node.parent_id);
      const idx = siblings.findIndex((n) => n.key === key);
      const prev = idx > 0 ? siblings[idx - 1] : null;
      if (!prev) { message.info("没有可降级到的同级章节"); return; }
      node.parent_id = prev.key;
    } else {
      const parent = all.find((n) => n.key === node.parent_id);
      node.parent_id = parent?.parent_id ?? null;
    }
    applyTree(roots);
  };

  const onDrop = (info: { node: { key: number }; dragNode: { key: number }; dropToGap: boolean }) => {
    const dragKey = info.dragNode.key;
    const targetKey = info.node.key;
    const roots = JSON.parse(JSON.stringify(treeRoots)) as TreeItem[];
    const all = flattenTree(roots);
    const drag = all.find((n) => n.key === dragKey);
    if (!drag) return;
    // 禁止拖入自己的后代节点
    const isDescendant = (k: number, anc: number): boolean => {
      let cur = all.find((n) => n.key === k);
      while (cur && cur.parent_id != null) {
        if (cur.parent_id === anc) return true;
        cur = all.find((n) => n.key === cur!.parent_id);
      }
      return false;
    };
    if (isDescendant(targetKey, dragKey)) { message.warning("不能移动到自己的子章节下"); return; }
    const newParent = info.dropToGap ? all.find((n) => n.key === targetKey)?.parent_id ?? null : targetKey;
    drag.parent_id = newParent;
    applyTree(roots);
  };

  // ---- 预算操作 ----
  const normalizeBudgets = (mode: "ai" | "average") => {
    const roots = JSON.parse(JSON.stringify(treeRoots)) as TreeItem[];
    const ls = flattenTree(roots).filter((n) => {
      const parentIds = new Set(flattenTree(roots).map((x) => x.parent_id).filter(Boolean));
      return !parentIds.has(n.key);
    });
    if (!ls.length || !total) return;
    if (mode === "average" || ls.every((n) => n.word_budget === 0)) {
      const per = Math.floor(total / ls.length);
      ls.forEach((n, i) => (n.word_budget = per + (i === 0 ? total - per * ls.length : 0)));
    } else {
      const cur = ls.reduce((s, n) => s + n.word_budget, 0);
      const ratio = total / cur;
      let sum = 0;
      ls.forEach((n, i) => {
        n.word_budget = i === ls.length - 1 ? total - sum : Math.round(n.word_budget * ratio);
        sum += n.word_budget;
      });
    }
    applyTree(roots);
    message.success("预算已调整，记得保存");
  };

  // ---- 生成 ----
  const generate = async () => {
    const v = form.getFieldsValue();
    if (!v.intent || v.intent.trim().length < 20) { message.warning("请先填写写作意图（至少 20 字）"); return; }
    if (!v.total_word_count) { message.warning("请填写整体目标字数"); return; }
    setError(null);
    setStream(initialState);
    try {
      const res = await post<{ job_id: number }>(`/api/designs/${designId}/generate`, {});
      setStreamJobId(res.job_id);
      void streamJob(res.job_id, (ev) => {
        setStream((st) => applyEvent(st, ev));
        if (ev.type === "done" || ev.type === "error") {
          void load();
          setStreamJobId(null);
        }
      });
    } catch (e) {
      const err = (e as { error?: { userMessage: string; code: string; traceId: string; suggestion?: string } }).error;
      setError(err || { userMessage: "生成请求失败", code: "", traceId: "" });
    }
  };

  const stopGenerate = () => {
    if (streamJobId) void post(`/api/jobs/${streamJobId}/cancel`).then(() => {
      setStreamJobId(null);
      message.info("已请求停止");
    });
  };

  // ---- 冲突检查与确认 ----
  const runConflictCheck = async () => {
    setConflictChecking(true);
    try {
      const report = await post<{ conflicts?: ConflictItem[]; has_conflict?: boolean; check_failed?: boolean }>(
        `/api/designs/${designId}/conflict-check`);
      setConflicts(report.conflicts || []);
      setConflictChecked(!report.check_failed);
      if (report.check_failed) message.warning("本次未完成冲突检查（模型调用失败）");
      else if (report.has_conflict) message.warning(`发现 ${report.conflicts?.length || 0} 个冲突`);
      else message.success("未发现明显冲突");
    } catch (e) {
      setConflictChecked(false);
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "检查失败");
    } finally { setConflictChecking(false); }
  };

  const confirmDesign = async (force: boolean, reason: string) => {
    try {
      await post(`/api/designs/${designId}/confirm`, { force, force_reason: reason });
      message.success("已确认，可以创建大纲");
      setConfirmOpen(false);
      await load();
      navigate("/outlines");
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "确认失败");
    }
  };

  // ---- 片段操作 ----
  const [moveChunk, setMoveChunk] = useState<{ chunkId: number } | null>(null);
  const [moveTarget, setMoveTarget] = useState<number | "unassigned">("unassigned");
  const updateChunkLinks = async (links: { chunk_id: number; chapter_id: number | null; selected: boolean; score?: number }[]) => {
    try {
      await put(`/api/designs/${designId}`, { chunk_links: links, autosave: true });
      await load();
      message.success("片段关联已更新");
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "更新失败");
    }
  };

  const moveChunkTo = async () => {
    if (!moveChunk) return;
    const links = (design?.chunks || []).map((c) => ({
      chunk_id: c.id, chapter_id: c.design_chapter_id, selected: !!c.selected, score: c.score,
    }));
    const target = links.find((l) => l.chunk_id === moveChunk.chunkId);
    if (target) target.chapter_id = moveTarget === "unassigned" ? null : moveTarget;
    await updateChunkLinks(links);
    setMoveChunk(null);
  };

  const selectedChunks = useMemo(() => {
    if (!design?.chunks) return { mine: [] as DesignChunkLink[], unassigned: [] as DesignChunkLink[] };
    const mine = design.chunks.filter((c) => c.design_chapter_id === selectedKey);
    const unassigned = design.chunks.filter((c) => c.design_chapter_id == null);
    return { mine, unassigned };
  }, [design, selectedKey]);

  const selectedNode = selectedKey != null ? flat.find((n) => n.key === selectedKey) : null;

  if (loading && !design) return <Spin style={{ display: "block", margin: "80px auto" }} />;
  if (!design) return <Empty description="设计不存在" style={{ marginTop: 80 }} />;

  const treeData = treeRoots;
  const budgetPct = total ? Math.min(100, Math.round((allocated / total) * 100)) : 0;

  const titleRender = (node: TreeItem) => (
    <div className="tree-node">
      <span className="tree-node-number">{node.number || "?"}</span>
      <span className="tree-node-title ellipsis" title={node.title}>{node.title}</span>
      <span className="tree-node-budget">
        {leaves.some((l) => l.key === node.key) ? (
          <InputNumber
            size="small" min={0} style={{ width: 76 }} value={node.word_budget}
            onClick={(e) => e.stopPropagation()}
            onChange={(v) => {
              const roots = JSON.parse(JSON.stringify(treeRoots)) as TreeItem[];
              const n = flattenTree(roots).find((x) => x.key === node.key);
              if (n) n.word_budget = Number(v) || 0;
              setTreeRoots(roots);
              setSaveState("unsaved");
              void autoSave(roots);
            }}
          />
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {parentSum.get(node.key) || 0} 字
          </Typography.Text>
        )}
      </span>
      <span className="tree-node-ops" onClick={(e) => e.stopPropagation()}>
        <Button size="small" type="text" icon={<PlusOutlined />} title="添加子章节"
          onClick={() => addNode(node.key, "add-child")} />
        <Button size="small" type="text" icon={<EditOutlined />} title="编辑标题与思路"
          onClick={() => { setEditNode({ key: node.key, mode: "edit" }); editForm.setFieldsValue({ title: node.title, idea: node.idea, word_budget: node.word_budget }); }} />
        <Button size="small" type="text" icon={<VerticalAlignTopOutlined />} title="升级"
          onClick={() => moveLevel(node.key, "up")} />
        <Button size="small" type="text" icon={<VerticalAlignBottomOutlined />} title="降级"
          onClick={() => moveLevel(node.key, "down")} />
        <Button size="small" type="text" danger icon={<DeleteOutlined />} title="删除"
          onClick={() => removeNode(node.key)} />
      </span>
    </div>
  );

  return (
    <div className="design-editor">
      <div className="page-toolbar">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate("/designs")}>返回</Button>
        <Typography.Title level={5} style={{ margin: 0 }} className="ellipsis">
          {design.title || design.intent.slice(0, 30)}
        </Typography.Title>
        <Tag color={design.status === "confirmed" ? "success" : "default"}>
          {design.status === "confirmed" ? "已确认" : "草稿"}
        </Tag>
        <span style={{ flex: 1 }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {saveState === "saved" ? "已保存" : saveState === "saving" ? "保存中…" : "未保存"}
        </Typography.Text>
        <Button icon={<UndoOutlined />} disabled={!undoStack.current.length} onClick={undo} />
        <Button icon={<RedoOutlined />} disabled={!redoStack.current.length} onClick={redo} />
        <Button icon={<SaveOutlined />} loading={saving} onClick={() => void saveNow()}>保存</Button>
        <Button type="primary" icon={<CheckOutlined />} onClick={() => setConfirmOpen(true)}>
          确认并保存
        </Button>
      </div>

      {error && <ErrorPanel error={{ ...error, stage: "generate", retryable: true, message: "", suggestion: undefined, detail: undefined } as never} onRetry={() => void generate()} />}

      <Collapse
        size="small"
        defaultActiveKey={["input"]}
        items={[{
          key: "input", label: "写作输入（意图 / 标题 / 描述 / 目标字数）",
          children: (
            <Form form={form} layout="vertical" size="small">
              <Row gutter={16}>
                <Col span={16}>
                  <Form.Item label="写作意图（必填，20–2000 字）" name="intent">
                    <Input.TextArea rows={3} placeholder="研究问题、核心观点、写作目的、范围边界" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label="整体目标字数（必填）" required>
                    <Space wrap>
                      <Form.Item name="total_word_count" noStyle
                        rules={[{ required: true, message: "请选择或输入目标字数" }]}>
                        <Radio.Group size="small">
                          {WORD_PRESETS.map((w) => <Radio.Button key={w} value={w}>{w}字</Radio.Button>)}
                        </Radio.Group>
                      </Form.Item>
                      <Form.Item name="total_word_count" noStyle>
                        <InputNumber min={3000} max={50000} step={500}
                          placeholder="或自定义" style={{ width: 130 }} />
                      </Form.Item>
                    </Space>
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="文章标题（可选，留空由 AI 补全）" name="title">
                    <Input maxLength={100} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="详细描述（可选）" name="description">
                    <Input.TextArea rows={2} maxLength={5000} placeholder="材料、视角、方法、导师要求等" />
                  </Form.Item>
                </Col>
              </Row>
              <Space>
                <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => void generate()}
                  disabled={stream.phase !== "idle" && stream.phase !== "done" && stream.phase !== "error"}>
                  生成章节设计
                </Button>
                {streamJobId && <Button danger icon={<StopOutlined />} onClick={stopGenerate}>停止</Button>}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  默认检索全部文件，可在高级设置中调整；目标字数只统计正文
                </Typography.Text>
              </Space>
            </Form>
          ),
        }]}
      />

      <StreamBox state={stream} onStop={stopGenerate} rawLabel="模型原始输出" />

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={14}>
          <Card size="small" title="章节设计" extra={
            <Space>
              <Button size="small" icon={<PlusOutlined />} onClick={() => addNode(null, "add-sibling")}>添加顶层章节</Button>
            </Space>
          }>
            {treeRoots.length === 0 ? (
              <Empty description="还没有章节，点击「生成章节设计」或手动添加" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <Tree
                treeData={treeData}
                draggable
                blockNode
                defaultExpandAll
                titleRender={(n) => titleRender(n as unknown as TreeItem)}
                onSelect={(keys) => setSelectedKey((keys[0] as number) || null)}
                selectedKeys={selectedKey != null ? [selectedKey] : []}
                onDrop={onDrop}
              />
            )}
            <div className="budget-bar">
              <Space direction="vertical" style={{ width: "100%" }} size={4}>
                <Space>
                  <Typography.Text>总目标 {total} ｜ 已分配 {allocated} ｜ 差额 {diff}</Typography.Text>
                  <Button size="small" onClick={() => normalizeBudgets("ai")}>按 AI 建议分配</Button>
                  <Button size="small" onClick={() => normalizeBudgets("average")}>平均分配</Button>
                  <Button size="small" type="link" onClick={() => normalizeBudgets("ai")}>一键归一化</Button>
                  {Math.abs(diff) > total * 0.05 && total > 0 && (
                    <Tag color="orange">差额超过 ±5%</Tag>
                  )}
                </Space>
                <Progress percent={budgetPct} size="small" showInfo={false} />
              </Space>
            </div>
            <Form.Item label="文章整体思路（AI 生成，可修改；300–800 字）" style={{ marginTop: 12 }}>
              <Input.TextArea rows={4} value={design.overall_idea || ""}
                onChange={(e) => { setDesign({ ...design, overall_idea: e.target.value }); setSaveState("unsaved"); }}
                onBlur={() => void saveNow(true)} />
            </Form.Item>
          </Card>
        </Col>
        <Col span={10}>
          <Card size="small" title={
            <Space>
              召回片段
              {selectedNode && <Tag color="blue">{selectedNode.number} {selectedNode.title}</Tag>}
              {!selectedNode && <Tag>未分配</Tag>}
            </Space>
          } extra={
            <Space>
              <Button size="small" type="link" onClick={() => setSelectedKey(null)}>查看未分配</Button>
            </Space>
          }>
            {!selectedNode && (
              <Alert type="info" showIcon style={{ marginBottom: 8 }}
                message="未分配片段可移动到任意章节；生成时 AI 会按片段支撑章节" />
            )}
            <ChunkList
              chunks={selectedNode ? selectedChunks.mine : selectedChunks.unassigned}
              emptyText={selectedNode ? "本章暂无关联片段" : "暂无未分配片段"}
              extra={(c) => (
                <Button size="small" type="text" icon={<EditOutlined />}
                  onClick={() => { setMoveChunk({ chunkId: c.id }); setMoveTarget(selectedNode ? "unassigned" : "unassigned"); }}>
                  移动
                </Button>
              )}
            />
            {selectedNode && selectedChunks.unassigned.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <Typography.Text type="secondary">未分配片段（{selectedChunks.unassigned.length}）：</Typography.Text>
                <ChunkList chunks={selectedChunks.unassigned} compact
                  extra={(c) => (
                    <Button size="small" type="text" icon={<PlusOutlined />}
                      onClick={() => void updateChunkLinks((design?.chunks || []).map((x) => ({
                        chunk_id: x.id, chapter_id: x.design_chapter_id, selected: !!x.selected, score: x.score,
                      })).map((l) => l.chunk_id === c.id ? { ...l, chapter_id: selectedKey } : l))}>
                      加入本章
                    </Button>
                  )} />
              </div>
            )}
          </Card>
          <Card size="small" title="冲突检查" style={{ marginTop: 12 }} extra={
            <Button size="small" loading={conflictChecking} onClick={() => void runConflictCheck()}>
              {conflictChecked ? "重新检查" : "开始检查"}
            </Button>
          }>
            {conflicts === null ? (
              <Typography.Text type="secondary">保存前建议做一次冲突检查（目标偏离、章节重叠、逻辑断层、字数分配失衡等）。</Typography.Text>
            ) : conflicts.length === 0 ? (
              <Alert type="success" showIcon message="未发现明显冲突" />
            ) : (
              <Space direction="vertical" style={{ width: "100%" }} size={8}>
                {conflicts.map((c, i) => (
                  <Alert key={i} type={c.severity === "high" ? "error" : c.severity === "medium" ? "warning" : "info"}
                    showIcon
                    message={`${c.chapters.join("、")}：${c.description}`}
                    description={c.suggestion} />
                ))}
              </Space>
            )}
          </Card>
        </Col>
      </Row>

      {/* 节点编辑弹层 */}
      <Modal open={!!editNode} title={
        editNode?.mode === "edit" ? "编辑章节" :
        editNode?.mode === "add-child" ? "添加子章节" : "添加章节"
      } onCancel={() => setEditNode(null)}
        footer={<Space>
          <Button onClick={() => setEditNode(null)}>取消</Button>
          <Button type="primary" onClick={commitNode}>确定</Button>
        </Space>}>
        <Form form={editForm} layout="vertical">
          <Form.Item label="章节标题（≤100 字）" name="title" rules={[{ required: true, message: "请输入标题" }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item label="本章思路（写什么、为什么写、用什么材料，80–300 字）" name="idea">
            <Input.TextArea rows={3} maxLength={1000} />
          </Form.Item>
          <Form.Item label="字数预算（仅叶子章节可编辑）" name="word_budget">
            <InputNumber min={0} style={{ width: 160 }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 移动片段弹层 */}
      <Modal open={!!moveChunk} title="移动片段到…" onCancel={() => setMoveChunk(null)}
        footer={<Space>
          <Button onClick={() => setMoveChunk(null)}>取消</Button>
          <Button type="primary" onClick={() => void moveChunkTo()}>确定</Button>
        </Space>}>
        <Select style={{ width: "100%" }} value={moveTarget} onChange={setMoveTarget}
          options={[
            { value: "unassigned", label: "未分配" },
            ...flattenTree(treeRoots).map((n) => ({ value: n.key, label: `${n.number} ${n.title}` })),
          ]} />
      </Modal>

      {/* 确认弹层 */}
      <Modal open={confirmOpen} title="确认并保存章节设计" onCancel={() => setConfirmOpen(false)}
        footer={null}>
        {(!conflictChecked || (conflicts?.length || 0) > 0 || Math.abs(diff) > total * 0.05) ? (
          <div>
            <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={
              !conflictChecked ? "本次未完成冲突检查"
              : (conflicts?.length || 0) > 0 ? `存在 ${conflicts?.length} 个未解决冲突`
              : "字数预算差额超过 ±5%"
            } />
            <Typography.Paragraph type="secondary">
              可以「返回修改」，或填写原因后强行保存（会记录在版本快照中，仍可创建大纲）。
            </Typography.Paragraph>
            <Input.TextArea rows={2} id="force-reason" placeholder="强行保存的原因（简短）" />
            <Space style={{ marginTop: 12 }}>
              <Button onClick={() => setConfirmOpen(false)}>返回修改</Button>
              <Button danger onClick={() => {
                const reason = (document.getElementById("force-reason") as HTMLTextAreaElement).value;
                void confirmDesign(true, reason);
              }}>仍然保存</Button>
            </Space>
          </div>
        ) : (
          <div>
            <Alert type="success" showIcon message="检查通过，可以确认保存" style={{ marginBottom: 12 }} />
            <Button type="primary" block onClick={() => void confirmDesign(false, "")}>确认并保存</Button>
          </div>
        )}
      </Modal>
    </div>
  );
}
