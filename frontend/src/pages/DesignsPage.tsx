/** 章节设计列表（§6.1）+ 新建对话框（§6.2）。 */
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Dropdown, Empty, Form, Input, InputNumber, Modal, Radio, Select, Space, Spin, Tag, Typography, message } from "antd";
import { CopyOutlined, DeleteOutlined, MoreOutlined, PlusOutlined, FileTextOutlined, CheckCircleOutlined, EditOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { del, get, post } from "../api/client";
import type { Design } from "../api/types";
import { fmtDate } from "../lib/format";

const WORD_PRESETS = [3000, 5000, 8000, 12000, 20000];

export function CreateDesignModal({ open, onClose, onCreated }: {
  open: boolean; onClose: () => void; onCreated: (id: number) => void;
}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const intent = Form.useWatch("intent", form);

  const submit = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      const d = await post<Design>("/api/designs", {
        intent: v.intent, total_word_count: v.total_word_count,
        title: v.title || undefined, description: v.description || undefined,
      });
      onCreated(d.id);
      onClose();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "创建失败");
    } finally { setSaving(false); }
  };

  return (
    <Modal open={open} title="新建章节设计" onCancel={onClose} width={640}
      footer={<Space>
        <Button onClick={onClose}>取消</Button>
        <Button type="primary" loading={saving} disabled={!intent || intent.trim().length < 20}
          onClick={submit}>创建</Button>
      </Space>}>
      <Form form={form} layout="vertical" initialValues={{ total_word_count: 8000 }}>
        <Form.Item label="写作意图（必填，20–2000 字）" name="intent"
          rules={[{ required: true, message: "请填写写作意图" }]}
          extra={<span>研究问题、核心观点、写作目的、范围边界。已输入 {intent?.length || 0} 字</span>}>
          <Input.TextArea rows={5} placeholder="例如：研究《民法典》中个人信息处理的合法性基础，梳理学理争议并结合典型案例提出完善建议……" />
        </Form.Item>
        <Form.Item label="整体目标字数（必填，仅正文）" required
          extra="课程论文 3000–5000 字；学年论文 6000–10000 字；毕业论文 10000–20000 字（以学校要求为准）">
          <Space wrap>
            <Form.Item name="total_word_count" noStyle
              rules={[{ required: true, message: "请选择或输入目标字数" }]}>
              <Radio.Group>
                {WORD_PRESETS.map((w) => (
                  <Radio.Button key={w} value={w}>{w}字</Radio.Button>
                ))}
              </Radio.Group>
            </Form.Item>
            <Form.Item name="total_word_count" noStyle>
              <InputNumber min={3000} max={50000} step={500}
                placeholder="或自定义（如 15000）" style={{ width: 170 }} />
            </Form.Item>
          </Space>
        </Form.Item>
        <Form.Item label="文章标题（可选，留空由 AI 补全）" name="title">
          <Input maxLength={100} placeholder="不超过 100 字" />
        </Form.Item>
        <Form.Item label="详细描述（可选）" name="description">
          <Input.TextArea rows={3} placeholder="材料、视角、方法、导师要求、已有提纲等，不超过 5000 字" maxLength={5000} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export function DesignsPage() {
  const [designs, setDesigns] = useState<Design[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try { setDesigns(await get<Design[]>("/api/designs")); } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const remove = (d: Design) => {
    Modal.confirm({
      title: `删除章节设计「${d.title || d.intent.slice(0, 20)}…」？`,
      content: d.outline_count ? `该设计已有 ${d.outline_count} 个大纲，需先删除大纲。` : "删除后不可恢复。",
      okText: "删除", okButtonProps: { danger: true },
      onOk: async () => {
        try { await del(`/api/designs/${d.id}`); void load(); }
        catch (e) { message.error((e as { error?: { userMessage: string } }).error?.userMessage || "删除失败"); }
      },
    });
  };

  return (
    <div className="list-page">
      <div className="page-toolbar">
        <Typography.Title level={4} style={{ margin: 0 }}>章节设计</Typography.Title>
        <span style={{ flex: 1 }} />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建</Button>
      </div>
      <Spin spinning={loading}>
        {designs.length === 0 ? (
          <Empty style={{ marginTop: 80 }} description={
            <Space direction="vertical">
              <span>还没有章节设计。先定整体思路与章节，再写大纲，最后写正文。</span>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建章节设计</Button>
            </Space>
          } />
        ) : (
          <div className="card-grid">
            {designs.map((d) => (
              <Card key={d.id} size="small" hoverable className="doc-card"
                onClick={() => navigate(`/designs/${d.id}`)}
                title={<span className="ellipsis" title={d.title || d.intent}>{d.title || d.intent}</span>}
                extra={
                  <Dropdown menu={{ items: [
                    { key: "open", label: "打开", onClick: () => navigate(`/designs/${d.id}`) },
                    { key: "dup", label: "复制", onClick: async () => {
                        const nd = await post<Design>(`/api/designs/${d.id}/duplicate`);
                        navigate(`/designs/${nd.id}`);
                    } },
                    { key: "del", label: "删除", danger: true, onClick: () => remove(d) },
                  ] }} trigger={["click"]}>
                    <MoreOutlined onClick={(e) => e.stopPropagation()} />
                  </Dropdown>
                }>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  <Space size={4} wrap>
                    {d.status === "confirmed"
                      ? <Tag color="success" icon={<CheckCircleOutlined />}>已确认</Tag>
                      : <Tag>草稿</Tag>}
                    {d.confirm_notes && <Tag color="orange">未完成检查</Tag>}
                  </Space>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {d.chapter_count ?? 0} 个章节 ｜ 目标 {d.total_word_count} 字 ｜ {d.outline_count ?? 0} 个大纲
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>更新 {fmtDate(d.updated_at)}</Typography.Text>
                  {d.status === "confirmed" && (
                    <Button size="small" type="link" style={{ padding: 0 }}
                      onClick={(e) => { e.stopPropagation(); navigate(`/outlines`); }}>
                      去设计大纲 →
                    </Button>
                  )}
                </Space>
              </Card>
            ))}
          </div>
        )}
      </Spin>
      <CreateDesignModal open={createOpen} onClose={() => setCreateOpen(false)}
        onCreated={(id) => navigate(`/designs/${id}`)} />
    </div>
  );
}
