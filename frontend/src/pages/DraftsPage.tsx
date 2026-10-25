/** 章节创作入口（§8.1）：大纲卡片 → 创建/打开创作稿；右上角查看全部创作稿。 */
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Dropdown, Empty, Modal, Space, Spin, Tag, Typography, message } from "antd";
import { MoreOutlined, DownloadOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { del, download, get, post } from "../api/client";
import type { Draft, Outline } from "../api/types";
import { fmtDate } from "../lib/format";

export function DraftsPage() {
  const [outlines, setOutlines] = useState<Outline[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [os, ds] = await Promise.all([
        get<Outline[]>("/api/outlines"),
        get<Draft[]>("/api/drafts"),
      ]);
      setOutlines(os);
      setDrafts(ds);
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const openOrCreate = async (outlineId: number) => {
    const mine = drafts.filter((d) => d.outline_id === outlineId);
    if (mine.length) navigate(`/drafts/${mine[mine.length - 1].id}`);
    else {
      try {
        const d = await post<Draft>("/api/drafts", { outline_id: outlineId });
        navigate(`/drafts/${d.id}`);
      } catch (e) {
        message.error((e as { error?: { userMessage: string } }).error?.userMessage || "创建失败");
      }
    }
  };

  return (
    <div className="list-page">
      <div className="page-toolbar">
        <Typography.Title level={4} style={{ margin: 0 }}>章节创作</Typography.Title>
        <span style={{ flex: 1 }} />
        <Button onClick={() => navigate("/drafts/all")}>查看全部创作稿</Button>
      </div>
      <Spin spinning={loading}>
        {outlines.length === 0 ? (
          <Empty style={{ marginTop: 80 }} description={
            <Space direction="vertical">
              <span>还没有大纲。先完成「大纲设计」再开始创作。</span>
              <Button type="primary" onClick={() => navigate("/outlines")}>去大纲设计</Button>
            </Space>
          } />
        ) : (
          <div className="card-grid">
            {outlines.map((o) => {
              const mine = drafts.filter((d) => d.outline_id === o.id);
              return (
                <Card key={o.id} size="small" hoverable className="doc-card"
                  onClick={() => void openOrCreate(o.id)}
                  title={<span className="ellipsis">{o.name}</span>}
                  extra={
                    <Dropdown menu={{ items: [
                      { key: "new", label: "新建创作稿", onClick: async () => {
                          const d = await post<Draft>("/api/drafts", { outline_id: o.id });
                          navigate(`/drafts/${d.id}`);
                      } },
                      { key: "list", label: "查看已有创作稿", onClick: () => navigate("/drafts/all") },
                    ] }}>
                      <MoreOutlined onClick={(e) => e.stopPropagation()} />
                    </Dropdown>
                  }>
                  <Space direction="vertical" size={2} style={{ width: "100%" }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      源设计：《{o.design_title}》 ｜ {o.leaf_count ?? 0} 个叶子 ｜ 目标 {o.total_word_count} 字
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      已有创作稿 {mine.length} 个 ｜ 更新 {fmtDate(o.updated_at)}
                    </Typography.Text>
                    <Button size="small" type="link" style={{ padding: 0 }}>
                      {mine.length ? "打开最近创作稿 →" : "创建创作稿 →"}
                    </Button>
                  </Space>
                </Card>
              );
            })}
          </div>
        )}
      </Spin>
    </div>
  );
}

/** 查看全部创作稿（§8.1）。 */
export function AllDraftsPage() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try { setDrafts(await get<Draft[]>("/api/drafts")); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="list-page">
      <div className="page-toolbar">
        <Button type="text" onClick={() => navigate("/drafts")}>← 返回章节创作</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>全部创作稿</Typography.Title>
      </div>
      <Spin spinning={loading}>
        {drafts.length === 0 ? <Empty style={{ marginTop: 80 }} description="还没有创作稿" /> : (
          <div className="card-grid">
            {drafts.map((d) => (
              <Card key={d.id} size="small" hoverable className="doc-card"
                onClick={() => navigate(`/drafts/${d.id}`)}
                title={<span className="ellipsis">{d.title || "未命名论文"}</span>}
                extra={
                  <Dropdown menu={{ items: [
                    { key: "open", label: "打开", onClick: () => navigate(`/drafts/${d.id}`) },
                    { key: "export", label: "导出工作稿", onClick: () => void download(`/api/drafts/${d.id}/export`, { mode: "work" }) },
                    { key: "del", label: "删除", danger: true, onClick: () => {
                        Modal.confirm({ title: `删除创作稿「${d.title || "未命名"}」？`, okText: "删除",
                          okButtonProps: { danger: true },
                          onOk: async () => {
                            try { await del(`/api/drafts/${d.id}`); void load(); }
                            catch (e) { message.error((e as { error?: { userMessage: string } }).error?.userMessage || "删除失败"); }
                          } });
                    } },
                  ] }}>
                    <MoreOutlined onClick={(e) => e.stopPropagation()} />
                  </Dropdown>
                }>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {d.done_count ?? 0}/{d.leaf_count ?? 0} 章完成 ｜ 实际 {d.actual_words ?? 0} / 目标 {d.target_word_count ?? "-"} 字
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    大纲：{d.outline_name} ｜ 更新 {fmtDate(d.updated_at)}
                  </Typography.Text>
                </Space>
              </Card>
            ))}
          </div>
        )}
      </Spin>
    </div>
  );
}
