/** 实验方案设计（理工版）：列表 + 新建。 */
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Dropdown, Empty, Form, Input, Modal, Space, Spin, Tag, Typography, message } from "antd";
import { MoreOutlined, PlusOutlined, DownloadOutlined, ExperimentOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { del, download, get, post } from "../api/client";
import type { ExperimentPlan } from "../api/types";
import { fmtDate } from "../lib/format";

export function CreatePlanModal({ open, onClose, onCreated }: {
  open: boolean; onClose: () => void; onCreated: (id: number) => void;
}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      const p = await post<ExperimentPlan>("/api/plans", {
        research_idea: v.research_idea, title: v.title || undefined,
      });
      onCreated(p.id);
      onClose();
    } catch (e) {
      message.error((e as { error?: { userMessage: string } }).error?.userMessage || "创建失败");
    } finally { setSaving(false); }
  };

  return (
    <Modal open={open} title="新建实验方案" onCancel={onClose} width={640}
      footer={<Space>
        <Button onClick={onClose}>取消</Button>
        <Button type="primary" loading={saving} onClick={submit}>创建</Button>
      </Space>}>
      <Form form={form} layout="vertical">
        <Form.Item label="研究想法（必填，10–2000 字）" name="research_idea"
          rules={[{ required: true, message: "请填写研究想法" }]}>
          <Input.TextArea rows={5}
            placeholder="例如：我想研究姜黄素对结直肠癌细胞自噬的影响，希望设计体外实验方案，包括分组设置、检测指标与统计方法……" />
        </Form.Item>
        <Form.Item label="方案标题（可选，留空由 AI 补全）" name="title">
          <Input maxLength={100} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export function ExperimentPlansPage() {
  const [plans, setPlans] = useState<ExperimentPlan[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try { setPlans(await get<ExperimentPlan[]>("/api/plans")); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="list-page">
      <div className="page-toolbar">
        <Typography.Title level={4} style={{ margin: 0 }}>
          <ExperimentOutlined /> 实验方案设计（理工版）
        </Typography.Title>
        <span style={{ flex: 1 }} />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建方案</Button>
      </div>
      <Typography.Paragraph type="secondary">
        输入研究想法 → 自动解析意图并检索文献证据 → 生成带溯源的实验方案：
        每条建议标注 [来源N]，点击可查看原始文献片段；引用数据经原文核对，对不上的标「数据待核实」。
      </Typography.Paragraph>
      <Spin spinning={loading}>
        {plans.length === 0 ? (
          <Empty style={{ marginTop: 60 }} description={
            <Space direction="vertical">
              <span>还没有实验方案。描述你的研究想法，AI 会检索文献并生成方案。</span>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建方案</Button>
            </Space>
          } />
        ) : (
          <div className="card-grid">
            {plans.map((p) => (
              <Card key={p.id} size="small" hoverable className="doc-card"
                onClick={() => navigate(`/plans/${p.id}`)}
                title={<span className="ellipsis">{p.title || "未命名方案"}</span>}
                extra={
                  <Dropdown menu={{ items: [
                    { key: "open", label: "打开", onClick: () => navigate(`/plans/${p.id}`) },
                    { key: "export", label: "导出", onClick: () => void download(`/api/plans/${p.id}/export`) },
                    { key: "del", label: "删除", danger: true, onClick: () => {
                        Modal.confirm({ title: `删除方案「${p.title || "未命名"}」？`, okText: "删除",
                          okButtonProps: { danger: true },
                          onOk: async () => { await del(`/api/plans/${p.id}`); void load(); } });
                    } },
                  ] }}>
                    <MoreOutlined onClick={(e) => e.stopPropagation()} />
                  </Dropdown>
                }>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {(p.research_idea || "").slice(0, 60)}…
                  </Typography.Text>
                  <Space size={4}>
                    {p.content ? <Tag color="success">已生成</Tag> : <Tag>未生成</Tag>}
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      溯源 {p.citation_count ?? 0} 条 ｜ 更新 {fmtDate(p.updated_at)}
                    </Typography.Text>
                  </Space>
                </Space>
              </Card>
            ))}
          </div>
        )}
      </Spin>
      <CreatePlanModal open={createOpen} onClose={() => setCreateOpen(false)}
        onCreated={(id) => navigate(`/plans/${id}`)} />
    </div>
  );
}
