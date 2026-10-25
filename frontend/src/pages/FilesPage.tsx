/** 文件入库页（§5.5）：分类树 + 列表 + 上传 + 详情抽屉。 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Button, Card, Drawer, Dropdown, Empty, Form, Input, InputNumber, List, Modal,
  Progress, Select, Space, Spin, Tag, Tree, Typography, Upload, message,
} from "antd";
import {
  DeleteOutlined, DownloadOutlined, EditOutlined, FolderAddOutlined, MoreOutlined,
  ReloadOutlined, SearchOutlined, UploadOutlined, FileTextOutlined,
} from "@ant-design/icons";
import { del, download, get, patch, post, put } from "../api/client";
import type { Category, Chunk, DocumentItem, Job } from "../api/types";
import { ChunkList } from "../components/ChunkList";
import { ErrorPanel } from "../components/ErrorBoundary";
import { trackJob, useApp } from "../store";
import { DOC_TYPE_NAMES, STATUS_NAMES, fmtDate, fmtSize } from "../lib/format";

// ---------------------------------------------------------------------------
// 上传
// ---------------------------------------------------------------------------
async function fileHash(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function UploadModal({ open, onClose, onDone, categoryId }: {
  open: boolean; onClose: () => void; onDone: () => void; categoryId: number | null;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [errors, setErrors] = useState<{ file: string; msg: string }[]>([]);
  const [dupModal, setDupModal] = useState<{ file: File; existingTitle: string } | null>(null);

  const doUpload = async (file: File, onDuplicate: string) => {
    const fd = new FormData();
    fd.append("files", file);
    if (categoryId) fd.append("category_id", String(categoryId));
    const resp = await fetch(`/api/documents/upload?on_duplicate=${onDuplicate}`, {
      method: "POST", headers: { "X-Trace-Id": Math.random().toString(16).slice(2, 10) }, body: fd,
    });
    const payload = await resp.json();
    const data = payload?.data || [];
    for (const r of data) {
      if (r.error) setErrors((e) => [...e, { file: r.file, msg: r.error.userMessage }]);
      if (r.job_id) trackJob(`job:${r.job_id}`, r.job_id, () => onDone());
    }
    return payload?.data || [];
  };

  const submit = async () => {
    setUploading(true);
    setErrors([]);
    for (const f of files) {
      const res = await doUpload(f, "skip");
      const dup = res.find((r: { duplicate?: boolean }) => r.duplicate);
      if (dup) {
        setDupModal({ file: f, existingTitle: dup.existing_title || "已有文献" });
        break;
      }
    }
    setUploading(false);
    setFiles([]);
    if (!dupModal) { onDone(); onClose(); }
  };

  const resolveDup = async (action: string) => {
    if (!dupModal) return;
    setUploading(true);
    const res = await doUpload(dupModal.file, action);
    for (const r of res) {
      if (r.job_id) trackJob(`job:${r.job_id}`, r.job_id, () => onDone());
      if (r.error) setErrors((e) => [...e, { file: r.file, msg: r.error.userMessage }]);
    }
    setUploading(false);
    setDupModal(null);
    onDone(); onClose();
  };

  return (
    <Modal open={open} title="上传文献" onCancel={onClose} footer={null} width={560}>
      <Upload.Dragger
        multiple
        beforeUpload={(f) => { setFiles((s) => [...s, f]); return false; }}
        onRemove={(f) => setFiles((s) => s.filter((x) => x.name !== f.name))}
        fileList={files.map((f) => ({ uid: f.name, name: f.name, size: f.size }))}
        accept=".docx,.doc,.pdf"
        disabled={uploading}
      >
        <p className="ant-upload-drag-icon"><UploadOutlined /></p>
        <p>点击或拖拽文件到此处上传（.docx / .doc / .pdf，单文件 ≤ 100MB，单次 ≤ 20 个）</p>
      </Upload.Dragger>
      {errors.map((e, i) => (
        <Alert key={i} type="error" showIcon style={{ marginTop: 8 }} message={`${e.file}：${e.msg}`} />
      ))}
      <div style={{ marginTop: 16, textAlign: "right" }}>
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" icon={<UploadOutlined />} loading={uploading}
            disabled={!files.length} onClick={submit}>
            开始上传
          </Button>
        </Space>
      </div>
      <Modal
        open={!!dupModal}
        title="检测到重复文件"
        onCancel={() => setDupModal(null)}
        footer={null}
      >
        <p>文件「{dupModal?.file.name}」与「{dupModal?.existingTitle}」内容相同。</p>
        <Space>
          <Button onClick={() => { setDupModal(null); onDone(); onClose(); }}>跳过</Button>
          <Button danger onClick={() => void resolveDup("overwrite")}>覆盖（将重新解析，可能影响已有引用）</Button>
          <Button type="primary" onClick={() => void resolveDup("copy")}>另存为副本</Button>
        </Space>
      </Modal>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// 详情抽屉
// ---------------------------------------------------------------------------
function DocDetail({ docId, onClose, onChanged }: { docId: number | null; onClose: () => void; onChanged: () => void }) {
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [catSuggest, setCatSuggest] = useState<string[] | null>(null);

  const load = useCallback(async () => {
    if (!docId) return;
    const d = await get<DocumentItem>(`/api/documents/${docId}`);
    setDoc(d);
    form.setFieldsValue({
      title: d.title, doc_type: d.doc_type, authors: d.authors, publisher: d.publisher,
      published_at: d.published_at, language: d.language, summary: d.summary,
    });
    const cs = await get<Chunk[]>(`/api/documents/${docId}/chunks`).catch(() => []);
    setChunks(cs.map((c) => ({ ...c, title: d.title })));
  }, [docId, form]);

  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      await patch(`/api/documents/${docId}`, form.getFieldsValue());
      message.success("已保存");
      onChanged(); void load();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "保存失败");
    } finally { setSaving(false); }
  };

  const suggestCat = async () => {
    try {
      const res = await post<{ categories: string[] }>(`/api/documents/${docId}/category-suggest`);
      setCatSuggest(res.categories);
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "分类建议失败");
    }
  };

  return (
    <Drawer open={!!docId} onClose={onClose} width={680}
      title={doc?.title || "文献详情"}
      extra={doc && (
        <Button icon={<DownloadOutlined />} onClick={() => void download(`/api/documents/${docId}/download`)}>
          下载原文件
        </Button>
      )}>
      {doc && (
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Space wrap>
            {STATUS_NAMES[doc.status] && <Tag color={STATUS_NAMES[doc.status].color}>{STATUS_NAMES[doc.status].text}</Tag>}
            <Tag>{doc.doc_type || "类型未定"}</Tag>
            <Typography.Text type="secondary">页数：{doc.page_count ?? "—"} ｜ 大小：{fmtSize(doc.file_size)} ｜ 片段：{doc.chunk_count}</Typography.Text>
          </Space>
          {doc.parse_error && <Alert type="error" showIcon message={doc.parse_error} />}
          {doc.status === "needs_ocr" && (
            <Alert type="warning" showIcon message="扫描版 PDF 无文本层，暂不参与检索；OCR 支持后续版本提供" />
          )}
          <Form form={form} layout="vertical" size="small">
            <Form.Item label="标题" name="title"><Input /></Form.Item>
            <Space wrap style={{ display: "flex" }} size={12}>
              <Form.Item label="文献类型" name="doc_type" style={{ width: 160 }}>
                <Select options={Object.entries(DOC_TYPE_NAMES).map(([v, l]) => ({ value: v, label: l }))} allowClear />
              </Form.Item>
              <Form.Item label="作者 / 机构" name="authors" style={{ width: 200 }}><Input /></Form.Item>
              <Form.Item label="出版者 / 平台" name="publisher" style={{ width: 200 }}><Input /></Form.Item>
              <Form.Item label="日期" name="published_at" style={{ width: 140 }}><Input placeholder="2024-05 或 2024-05-01" /></Form.Item>
              <Form.Item label="语言" name="language" style={{ width: 110 }}>
                <Select options={[{ value: "中文" }, { value: "英文" }, { value: "其他" }]} allowClear />
              </Form.Item>
            </Space>
            <Form.Item label="摘要" name="summary"><Input.TextArea rows={3} /></Form.Item>
          </Form>
          <Space>
            <Button type="primary" loading={saving} onClick={save}>保存元数据</Button>
            <Button onClick={suggestCat}>AI 分类建议</Button>
            {catSuggest && <Typography.Text>{catSuggest.join(" → ") || "无建议"}</Typography.Text>}
          </Space>
          {doc.referenced_by && doc.referenced_by.length > 0 && (
            <Alert type="info" showIcon message="被引用情况"
              description={doc.referenced_by.map((r) => `${r.kind === "design" ? "章节设计" : r.kind === "outline" ? "大纲" : "创作稿"}《${r.name}》`).join("；")} />
          )}
          <Typography.Title level={5}>片段列表（{chunks.length}）</Typography.Title>
          <ChunkList chunks={chunks} emptyText="暂无片段" />
        </Space>
      )}
    </Drawer>
  );
}

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------
export function FilesPage() {
  const [cats, setCats] = useState<Category[]>([]);
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [q, setQ] = useState("");
  const [catFilter, setCatFilter] = useState<number | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [newCatParent, setNewCatParent] = useState<number | null>(null);
  const { setRunningJob } = useApp();

  const loadDocs = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { sort: "updated_at_desc" };
      if (q) params.q = q;
      if (catFilter) params.category_id = String(catFilter);
      if (typeFilter) params.doc_type = typeFilter;
      if (statusFilter) params.status = statusFilter;
      setDocs(await get<DocumentItem[]>("/api/documents", params));
    } finally { setLoading(false); }
  }, [q, catFilter, typeFilter, statusFilter]);

  const loadCats = useCallback(async () => {
    setCats(await get<Category[]>("/api/categories"));
  }, []);

  useEffect(() => { void loadDocs(); }, [loadDocs]);
  useEffect(() => { void loadCats(); }, [loadCats]);

  const treeData = cats.map((c) => ({ key: c.id, title: c.name }));

  const removeDoc = (d: DocumentItem) => {
    Modal.confirm({
      title: `删除文献「${d.title}」？`,
      content: "片段与索引将一并删除；已存在的引用会保留快照并标注「源文件已删除」。",
      okText: "删除", okButtonProps: { danger: true },
      onOk: async () => { await del(`/api/documents/${d.id}`); void loadDocs(); },
    });
  };

  const reparse = async (d: DocumentItem) => {
    try {
      const res = await post<{ job_id: number }>(`/api/documents/${d.id}/reparse`);
      trackJob(`doc:${d.id}`, res.job_id, () => void loadDocs());
      message.success("已开始重新解析");
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "操作失败");
    }
  };

  return (
    <div className="files-page">
      <div className="files-side">
        <Card size="small" title="分类" extra={
          <Button size="small" type="text" icon={<FolderAddOutlined />} onClick={() => {
            let name = window.prompt("新分类名称");
            if (name) void post("/api/categories", { name: name.trim() }).then(() => void loadCats());
          }} />
        }>
          <Tree
            treeData={treeData}
            selectedKeys={catFilter ? [catFilter] : []}
            onSelect={(keys) => setCatFilter((keys[0] as number) || null)}
            blockNode
          />
          {catFilter != null && (
            <Button size="small" type="link" onClick={() => setCatFilter(null)}>清除分类筛选</Button>
          )}
        </Card>
      </div>
      <div className="files-main">
        <div className="page-toolbar">
          <Input.Search
            placeholder="搜索标题、作者、摘要或片段内容…"
            allowClear style={{ width: 360 }} prefix={<SearchOutlined />}
            onSearch={(v) => setQ(v)}
          />
          <Select placeholder="类型" allowClear style={{ width: 130 }}
            options={Object.entries(DOC_TYPE_NAMES).map(([v, l]) => ({ value: v, label: l }))}
            onChange={(v) => setTypeFilter(v || "")} />
          <Select placeholder="状态" allowClear style={{ width: 140 }}
            options={Object.entries(STATUS_NAMES).map(([v, l]) => ({ value: v, label: l.text }))}
            onChange={(v) => setStatusFilter(v || "")} />
          <span style={{ flex: 1 }} />
          <Button type="primary" icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>
            上传文献
          </Button>
        </div>
        <Spin spinning={loading}>
          {docs.length === 0 ? (
            <Empty style={{ marginTop: 80 }} description={
              <Space direction="vertical">
                <span>还没有文献，先把论文、法条、案例上传进来</span>
                <Button type="primary" icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>上传文献</Button>
              </Space>
            } />
          ) : (
            <div className="card-grid">
              {docs.map((d) => (
                <Card key={d.id} size="small" className="doc-card"
                  title={<span className="ellipsis" title={d.title}>{d.title}</span>}
                  extra={
                    <Dropdown menu={{ items: [
                      { key: "detail", label: "查看详情", onClick: () => setDetailId(d.id) },
                      { key: "reparse", label: "重新解析", onClick: () => void reparse(d) },
                      { key: "del", label: "删除", danger: true, onClick: () => removeDoc(d) },
                    ] }}>
                      <MoreOutlined />
                    </Dropdown>
                  }>
                  <Space direction="vertical" size={2} style={{ width: "100%" }}>
                    <Space size={4} wrap>
                      {STATUS_NAMES[d.status] && (
                        <Tag color={STATUS_NAMES[d.status].color} style={{ margin: 0 }}>{STATUS_NAMES[d.status].text}</Tag>
                      )}
                      {d.doc_type && <Tag style={{ margin: 0 }}>{d.doc_type}</Tag>}
                    </Space>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {d.authors || "作者未标注"}
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {d.published_at || ""} ｜ {d.page_count != null ? `${d.page_count} 页` : "页数未知"} ｜ 更新 {fmtDate(d.updated_at)}
                    </Typography.Text>
                  </Space>
                </Card>
              ))}
            </div>
          )}
        </Spin>
      </div>
      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)}
        onDone={() => void loadDocs()} categoryId={catFilter} />
      <DocDetail docId={detailId} onClose={() => setDetailId(null)} onChanged={() => void loadDocs()} />
    </div>
  );
}
