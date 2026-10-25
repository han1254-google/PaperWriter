/** 创作编辑页（§8.2–§8.4 / §9）：逐章正文、引用列表、提示块、导出。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Badge, Button, Card, Checkbox, Col, Collapse, Descriptions, Drawer, Empty, Form,
  Input, InputNumber, List, Modal, Popconfirm, Progress, Radio, Row, Select, Space, Spin, Tag,
  Tree, Typography, message,
} from "antd";
import {
  ArrowLeftOutlined, CheckCircleOutlined, DownloadOutlined, EditOutlined, SaveOutlined,
  StopOutlined, ThunderboltOutlined, WarningOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { download, get, patch, post, put } from "../api/client";
import type { Chunk, Citation, Draft, DraftTreeNode, ExportChecks, StreamEvent } from "../api/types";
import { streamJob } from "../lib/stream";
import { applyEvent, initialState, StreamBox, type StreamState } from "../components/StreamBox";
import { ChunkList } from "../components/ChunkList";
import { DraftView, MarkdownView, NoticeTypeTag } from "../components/MarkdownView";
import { countWords, DRAFT_STATUS } from "../lib/format";

export function DraftEditor() {
  const { id } = useParams();
  const draftId = Number(id);
  const navigate = useNavigate();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null); // draft_chapter id
  const [content, setContent] = useState("");
  const [saveState, setSaveState] = useState<"saved" | "saving" | "unsaved">("saved");
  const [stream, setStream] = useState<StreamState>(initialState);
  const [streamJobId, setStreamJobId] = useState<number | null>(null);
  const [targetWords, setTargetWords] = useState<number | null>(null);
  const [citeDensity, setCiteDensity] = useState("中");
  const [tone, setTone] = useState("学术严谨");
  const [preview, setPreview] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportMode, setExportMode] = useState<"final" | "work">("final");
  const [exportFormat, setExportFormat] = useState("gb7714");
  const [exportTodo, setExportTodo] = useState(false);
  const [checks, setChecks] = useState<ExportChecks | null>(null);
  const [fixCitation, setFixCitation] = useState<Citation | null>(null);
  const [fixChunkId, setFixChunkId] = useState<number | null>(null);
  const saveTimer = useRef<number | null>(null);
  const [infoForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await get<Draft>(`/api/drafts/${draftId}`);
      setDraft(d);
      infoForm.setFieldsValue({
        title: d.title, abstract: d.abstract, keywords: d.keywords,
        author: d.author, school: d.school, publish_date: d.publish_date,
      });
      const leaves = (d.tree || []).filter((n) => n.draft_chapter);
      const cur = leaves.find((n) => n.draft_chapter?.id === selectedId);
      const pick = cur || leaves.find((n) => !(n.draft_chapter?.content || "").trim()) || leaves[0];
      if (pick?.draft_chapter) {
        setSelectedId(pick.draft_chapter.id);
        setContent(pick.draft_chapter.content || "");
        setTargetWords(pick.draft_chapter.target_word_count || null);
      }
    } finally { setLoading(false); }
  }, [draftId, selectedId, infoForm]);

  useEffect(() => { void load(); }, [load]);

  const leaves = useMemo(
    () => (draft?.tree || []).filter((n) => n.draft_chapter) as DraftTreeNode[],
    [draft],
  );
  const current = leaves.find((n) => n.draft_chapter?.id === selectedId);
  const curWords = countWords(content);
  const stats = draft?.stats;

  // ---- 保存 ----
  const autoSave = (text: string) => {
    setSaveState("unsaved");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      if (!selectedId) return;
      setSaveState("saving");
      try {
        await put(`/api/drafts/chapters/${selectedId}`, { content: text, autosave: true });
        setSaveState("saved");
      } catch { setSaveState("unsaved"); }
    }, 2000);
  };

  const saveNow = async (silent = false) => {
    if (!selectedId) return;
    try {
      await put(`/api/drafts/chapters/${selectedId}`, { content });
      setSaveState("saved");
      if (!silent) message.success("已保存本章");
      await load();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "保存失败");
    }
  };

  const saveAll = async () => {
    message.info("正在保存全部脏章节…");
    // 逐章保存由 autosave 覆盖；此处对当前章显式保存并刷新
    await saveNow(true);
    message.success("已保存");
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
    if (!current?.draft_chapter?.outline_content?.trim()) {
      message.warning("请先在「大纲设计」中生成这一节的大纲");
      return;
    }
    setStream(initialState);
    try {
      const res = await post<{ job_id: number }>(`/api/drafts/chapters/${selectedId}/generate`, {
        target_words: targetWords ?? undefined, cite_density: citeDensity, tone,
      });
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

  const markDone = async () => {
    if (!selectedId) return;
    try {
      await post(`/api/drafts/chapters/${selectedId}/complete`, {});
      message.success("已标记完成");
      await load();
    } catch (e) {
      const err = (e as { error?: { userMessage: string; code: string } }).error;
      if (err?.code === "VALIDATION_ERROR") {
        Modal.confirm({
          title: "存在无效引用",
          content: err.userMessage + "。确认完成将保留无效引用，导出定稿前需处理。",
          okText: "仍然完成",
          onOk: async () => {
            await post(`/api/drafts/chapters/${selectedId}/complete`, { force: true });
            await load();
          },
        });
      } else {
        message.error(err?.userMessage || "操作失败");
      }
    }
  };

  // ---- 导出 ----
  const openExport = async () => {
    setExportOpen(true);
    try { setChecks(await get<ExportChecks>(`/api/drafts/${draftId}/export-checks`)); }
    catch { setChecks(null); }
  };

  const doExport = async () => {
    try {
      await download(`/api/drafts/${draftId}/export`, {
        mode: exportMode, citation_format: exportFormat,
        include_todo: exportTodo ? "true" : "false",
      });
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "导出失败");
    }
  };

  // ---- 论文信息 ----
  const saveInfo = async () => {
    try {
      await patch(`/api/drafts/${draftId}`, infoForm.getFieldsValue());
      message.success("论文信息已保存");
      setInfoOpen(false);
      await load();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "保存失败");
    }
  };

  // ---- 引用修复 ----
  const doFixCitation = async () => {
    if (!fixCitation) return;
    try {
      await post(`/api/citations/${fixCitation.id}/fix`, { chunk_id: fixChunkId });
      message.success("引用已修复");
      setFixCitation(null);
      await load();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "修复失败");
    }
  };

  if (loading && !draft) return <Spin style={{ display: "block", margin: "80px auto" }} />;
  if (!draft) return <Empty style={{ marginTop: 80 }} />;

  const myCitations = (draft.citations || []).filter((c) => c.draft_chapter_id === selectedId);
  const myNotices = (draft.notices || []).filter((n) => n.draft_chapter_id === selectedId);
  const myChunks = (draft.chunks || []).filter((c) => c.outline_chapter_id === current?.draft_chapter?.outline_chapter_id);
  const invalidCount = myCitations.filter((c) => !c.valid).length;

  const buildTreeData = (nodes: Draft["tree"]): object[] => {
    if (!nodes) return [];
    const map = new Map<number, { node: DraftTreeNode; children: object[] }>();
    nodes.forEach((n) => map.set(n.id, { node: n, children: [] }));
    const roots: object[] = [];
    for (const n of nodes) {
      const st = n.draft_chapter?.status || "pending";
      const item = {
        key: n.draft_chapter ? `dc-${n.draft_chapter.id}` : `container-${n.id}`,
        title: (
          <span className={`outline-tree-node ${n.draft_chapter ? "leaf" : ""}`}
            onClick={() => {
              if (n.draft_chapter) {
                setSelectedId(n.draft_chapter.id);
                setContent(n.draft_chapter.content || "");
                setTargetWords(n.draft_chapter.target_word_count || null);
                setSaveState("saved");
              }
            }}>
            <span className="tree-node-number">{n.number}</span>
            <span className="ellipsis" style={{ flex: 1 }}>{n.title}</span>
            {n.draft_chapter && (
              <Badge status={
                st === "done" ? "success" : st === "generated" ? "processing" :
                st === "modified" ? "warning" : st === "generating" ? "processing" : "default"
              } text={DRAFT_STATUS[st]?.text || "未生成"} />
            )}
          </span>
        ),
        children: [] as object[],
      };
      map.get(n.id)!.children.push(item);
      if (n.parent_id == null || !map.has(n.parent_id)) roots.push(item);
      else map.get(n.parent_id)!.children.push(item);
    }
    return roots;
  };

  return (
    <div className="editor-page">
      <div className="page-toolbar">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate("/drafts")}>返回</Button>
        <Typography.Title level={5} style={{ margin: 0 }} className="ellipsis">
          {draft.title || "未命名论文"}
        </Typography.Title>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          全文 {stats?.actual_words ?? 0} / {draft.target_word_count ?? "-"} 字 ｜ 完成 {stats?.done ?? 0}/{stats?.total ?? 0}
        </Typography.Text>
        <span style={{ flex: 1 }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {saveState === "saved" ? "已保存" : saveState === "saving" ? "保存中…" : "未保存"}
        </Typography.Text>
        <Button icon={<EditOutlined />} onClick={() => setInfoOpen(true)}>论文信息</Button>
        <Button icon={<SaveOutlined />} onClick={() => void saveNow()}>保存本章</Button>
        <Button type="primary" icon={<DownloadOutlined />} onClick={() => void openExport()}>导出</Button>
        <Button onClick={() => navigate("/drafts/all")}>查看全部创作稿</Button>
      </div>

      <Row gutter={16}>
        <Col span={6}>
          <Card size="small" title="章节树">
            <Tree treeData={buildTreeData(draft.tree)} defaultExpandAll blockNode
              selectedKeys={selectedId != null ? [`dc-${selectedId}`] : []} />
          </Card>
        </Col>
        <Col span={18}>
          {current ? (
            <Card size="small"
              title={<Space>
                {current.number} {current.title}
                <Tag color="blue">目标 {targetWords ?? current.draft_chapter?.target_word_count ?? "-"} 字</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>已写 {curWords} 字</Typography.Text>
                {invalidCount > 0 && <Tag color="error">无效引用 {invalidCount}</Tag>}
              </Space>}
              extra={<Space>
                <Button size="small" onClick={() => setPreview(!preview)}>{preview ? "编辑" : "预览"}</Button>
                <Button size="small" type="primary" icon={<ThunderboltOutlined />}
                  disabled={!!streamJobId} onClick={() => void generate()}>生成</Button>
                {streamJobId && <Button size="small" danger icon={<StopOutlined />} onClick={stop}>停止</Button>}
                <Button size="small" icon={<CheckCircleOutlined />} onClick={() => void markDone()}>标记完成</Button>
              </Space>}>
              <Collapse size="small" ghost items={[
                {
                  key: "outline", label: "章节大纲（点击展开）",
                  children: <MarkdownView text={current.draft_chapter?.outline_content || "（本节大纲未生成，请先到大纲设计生成）"} />,
                },
                {
                  key: "idea", label: "章节设计思路",
                  children: <Typography.Paragraph>{current.idea || "（无）"}</Typography.Paragraph>,
                },
              ]} />
              <Space wrap style={{ margin: "8px 0" }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>生成参数：</Typography.Text>
                <InputNumber size="small" min={300} max={30000} step={100}
                  value={targetWords} onChange={(v) => setTargetWords(v)} placeholder="目标字数" />
                <Select size="small" style={{ width: 110 }} value={citeDensity} onChange={setCiteDensity}
                  options={[{ value: "低" }, { value: "中" }, { value: "高" }]} />
                <Select size="small" style={{ width: 120 }} value={tone} onChange={setTone}
                  options={[{ value: "学术严谨" }, { value: "平实通顺" }]} />
              </Space>
              {preview ? (
                <DraftView text={content} />
              ) : (
                <Input.TextArea
                  value={content}
                  onChange={(e) => { setContent(e.target.value); autoSave(e.target.value); }}
                  rows={16}
                  placeholder="点击「生成」基于大纲撰写正文；引用自动标记 [章节号-序号]，证据不足处用 <NOTICE> 提示补充。"
                />
              )}
              <StreamBox state={stream} onStop={stop} rawLabel="模型原始输出" />
            </Card>
          ) : (
            <Empty description="从左侧选择一个章节" style={{ marginTop: 80 }} />
          )}

          {current && (
            <Row gutter={12} style={{ marginTop: 12 }}>
              <Col span={10}>
                <Card size="small" title={`引用列表（${myCitations.length}）`}>
                  {myCitations.length === 0 ? (
                    <Typography.Text type="secondary">生成正文后自动解析引用标记</Typography.Text>
                  ) : (
                    <List size="small" dataSource={myCitations}
                      renderItem={(c) => (
                        <List.Item actions={[
                          !c.valid ? (
                            <Button size="small" type="link" onClick={() => { setFixCitation(c); setFixChunkId(null); }}>
                              指定来源片段
                            </Button>
                          ) : null,
                        ]}>
                          <Space direction="vertical" size={0}>
                            <Space size={4}>
                              <span className={c.valid ? "cite-marker" : "cite-marker invalid"}>
                                {c.marker}
                              </span>
                              {c.page_start != null && (
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>第{c.page_start}页</Typography.Text>
                              )}
                              {!c.valid && <Tag color="error">无法映射/待核实</Tag>}
                              {!c.chunk_id && c.valid && <Tag>源文件已删除</Tag>}
                            </Space>
                            <Typography.Text style={{ fontSize: 12 }}>
                              {c.doc_title || "（未关联文献）"}{c.doc_authors ? `，${c.doc_authors}` : ""}
                            </Typography.Text>
                          </Space>
                        </List.Item>
                      )} />
                  )}
                </Card>
              </Col>
              <Col span={6}>
                <Card size="small" title={`提示块（${myNotices.filter((n) => n.status === "open").length}）`}>
                  {myNotices.length === 0 ? (
                    <Typography.Text type="secondary">无提示块</Typography.Text>
                  ) : (
                    <List size="small" dataSource={myNotices}
                      renderItem={(n) => (
                        <List.Item actions={[
                          n.status === "open" ? (
                            <Button size="small" type="link"
                              onClick={() => void patch(`/api/notices/${n.id}`, { status: "resolved" }).then(() => void load())}>
                              解决
                            </Button>
                          ) : null,
                        ]}>
                          <Space direction="vertical" size={0}>
                            <NoticeTypeTag type={n.notice_type} />
                            <Typography.Text style={{ fontSize: 12 }}>{n.content}</Typography.Text>
                          </Space>
                        </List.Item>
                      )} />
                  )}
                </Card>
              </Col>
              <Col span={8}>
                <Card size="small" title={`参考内容（${myChunks.length}）`}>
                  <ChunkList chunks={myChunks} emptyText="本节暂无关联片段" />
                </Card>
              </Col>
            </Row>
          )}
        </Col>
      </Row>

      <div className="save-all-bar">
        <Button type="primary" icon={<SaveOutlined />} onClick={() => void saveAll()}>保存全部</Button>
      </div>

      {/* 论文信息抽屉 */}
      <Drawer open={infoOpen} onClose={() => setInfoOpen(false)} title="论文信息" width={480}>
        <Form form={infoForm} layout="vertical">
          <Form.Item label="论文标题" name="title"><Input /></Form.Item>
          <Form.Item label="作者" name="author"><Input /></Form.Item>
          <Form.Item label="学校" name="school"><Input /></Form.Item>
          <Form.Item label="日期" name="publish_date"><Input placeholder="2026-10-25" /></Form.Item>
          <Form.Item label="摘要（P0 手动填写）" name="abstract"><Input.TextArea rows={4} /></Form.Item>
          <Form.Item label="关键词（用；分隔）" name="keywords"><Input /></Form.Item>
        </Form>
        <Button type="primary" block onClick={() => void saveInfo()}>保存论文信息</Button>
      </Drawer>

      {/* 导出弹层 */}
      <Modal open={exportOpen} title="导出" onCancel={() => setExportOpen(false)} width={560}
        footer={<Space>
          <Button onClick={() => setExportOpen(false)}>取消</Button>
          <Button type="primary" icon={<DownloadOutlined />} onClick={() => void doExport()}>导出</Button>
        </Space>}>
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          {checks && (
            <>
              {checks.unfinished.length > 0 && (
                <Alert type="warning" showIcon message={`有 ${checks.unfinished.length} 个章节未生成（${checks.unfinished.map((u) => u.number).join("、")}），导出将标注「未完成」`} />
              )}
              {checks.info_missing.length > 0 && (
                <Alert type="info" showIcon message={`论文信息缺少：${checks.info_missing.join("、")}`} />
              )}
              {checks.deviation_exceeded && (
                <Alert type="warning" showIcon message={`实际字数 ${checks.actual_words} 与目标 ${checks.target_words} 偏差超过 20%`} />
              )}
              {checks.invalid_citations > 0 && (
                <Alert type="error" showIcon message={`存在 ${checks.invalid_citations} 条无效引用，定稿导出将被阻止；请先处理或改用工作稿`} />
              )}
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="实际字数">{checks.actual_words}</Descriptions.Item>
                <Descriptions.Item label="目标字数">{checks.target_words}</Descriptions.Item>
              </Descriptions>
            </>
          )}
          <div>
            <Typography.Text strong>导出模式</Typography.Text>
            <Radio.Group value={exportMode} onChange={(e) => setExportMode(e.target.value)} style={{ marginLeft: 12 }}>
              <Radio value="final" disabled={!!checks && checks.invalid_citations > 0}>定稿（正文 + 参考文献）</Radio>
              <Radio value="work">工作稿（含思路、大纲、片段清单）</Radio>
            </Radio.Group>
          </div>
          <div>
            <Typography.Text strong>参考文献格式</Typography.Text>
            <Radio.Group value={exportFormat} onChange={(e) => setExportFormat(e.target.value)} style={{ marginLeft: 12 }}>
              <Radio value="gb7714">GB/T 7714-2015（重编号 [1][2]…）</Radio>
              <Radio value="chapter">章节编码制（保留 [4.1.1-1]）</Radio>
            </Radio.Group>
          </div>
          <Checkbox checked={exportTodo} onChange={(e) => setExportTodo(e.target.checked)}>
            文末附「待办清单」（保留未解决的提示块）
          </Checkbox>
        </Space>
      </Modal>

      {/* 修复引用弹层 */}
      <Modal open={!!fixCitation} title={`为引用 ${fixCitation?.marker} 指定来源片段`}
        onCancel={() => setFixCitation(null)}
        footer={<Space>
          <Button onClick={() => setFixCitation(null)}>取消</Button>
          <Button type="primary" disabled={!fixChunkId} onClick={() => void doFixCitation()}>确认</Button>
        </Space>}>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          从本节的参考片段中选择真实来源（引文：{(fixCitation?.quote || "").slice(0, 60)}…）
        </Typography.Paragraph>
        <List size="small" dataSource={myChunks}
          renderItem={(c) => (
            <List.Item
              className={fixChunkId === c.id ? "chunk-selected" : ""}
              onClick={() => setFixChunkId(c.id)}
              style={{ cursor: "pointer" }}>
              <Space direction="vertical" size={0}>
                <Typography.Text>{c.title}{c.page_start != null ? `（第${c.page_start}页）` : ""}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{(c.text || "").slice(0, 80)}…</Typography.Text>
              </Space>
            </List.Item>
          )} />
      </Modal>
    </div>
  );
}
