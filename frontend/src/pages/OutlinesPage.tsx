/** 大纲设计入口（§7.1）：已确认设计卡片 → 创建/打开大纲；右上角查看全部大纲。 */
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Dropdown, Empty, Modal, Space, Spin, Tag, Typography, message } from "antd";
import { MoreOutlined, PlusOutlined, ArrowRightOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { del, get, patch, post } from "../api/client";
import type { Design, Outline } from "../api/types";
import { fmtDate } from "../lib/format";

export function OutlinesPage() {
  const [designs, setDesigns] = useState<Design[]>([]);
  const [outlines, setOutlines] = useState<Outline[]>([]);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ds, os] = await Promise.all([
        get<Design[]>("/api/designs"),
        get<Outline[]>("/api/outlines"),
      ]);
      setDesigns(ds.filter((d) => d.status === "confirmed"));
      setOutlines(os);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const openOrCreate = async (designId: number) => {
    const mine = outlines.filter((o) => o.design_id === designId);
    if (mine.length) navigate(`/outlines/${mine[mine.length - 1].id}`);
    else {
      try {
        const o = await post<Outline>("/api/outlines", { design_id: designId });
        navigate(`/outlines/${o.id}`);
      } catch (e) {
        message.error((e as { error?: { userMessage: string } }).error?.userMessage || "创建失败");
      }
    }
  };

  return (
    <div className="list-page">
      <div className="page-toolbar">
        <Typography.Title level={4} style={{ margin: 0 }}>大纲设计</Typography.Title>
        <span style={{ flex: 1 }} />
        <Link to="/outlines/all" />
        <Button onClick={() => navigate("/outlines/all")}>查看全部大纲</Button>
      </div>
      <Spin spinning={loading}>
        {designs.length === 0 ? (
          <Empty style={{ marginTop: 80 }} description={
            <Space direction="vertical">
              <span>还没有已确认的章节设计。先在「章节设计」中完成并确认。</span>
              <Button type="primary" onClick={() => navigate("/designs")}>去章节设计</Button>
            </Space>
          } />
        ) : (
          <div className="card-grid">
            {designs.map((d) => {
              const mine = outlines.filter((o) => o.design_id === d.id);
              return (
                <Card key={d.id} size="small" hoverable className="doc-card"
                  onClick={() => void openOrCreate(d.id)}
                  title={<span className="ellipsis" title={d.title || d.intent}>{d.title || d.intent}</span>}
                  extra={
                    <Dropdown menu={{ items: [
                      { key: "new", label: "新建大纲", onClick: async () => {
                          const o = await post<Outline>("/api/outlines", { design_id: d.id });
                          navigate(`/outlines/${o.id}`);
                      } },
                      { key: "list", label: "查看已有大纲", onClick: () => navigate("/outlines/all") },
                    ] }}>
                      <MoreOutlined onClick={(e) => e.stopPropagation()} />
                    </Dropdown>
                  }>
                  <Space direction="vertical" size={2} style={{ width: "100%" }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {d.chapter_count ?? 0} 个章节 ｜ 目标 {d.total_word_count} 字
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      已有大纲 {mine.length} 个 ｜ 更新 {fmtDate(d.updated_at)}
                    </Typography.Text>
                    <Button size="small" type="link" style={{ padding: 0 }}>
                      {mine.length ? "打开最近大纲 →" : "创建大纲 →"}
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

/** 查看全部大纲（右上角入口，§7.1）。 */
export function AllOutlinesPage() {
  const [outlines, setOutlines] = useState<Outline[]>([]);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try { setOutlines(await get<Outline[]>("/api/outlines")); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="list-page">
      <div className="page-toolbar">
        <Button type="text" onClick={() => navigate("/outlines")}>← 返回大纲设计</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>全部大纲</Typography.Title>
      </div>
      <Spin spinning={loading}>
        {outlines.length === 0 ? <Empty style={{ marginTop: 80 }} description="还没有大纲" /> : (
          <div className="card-grid">
            {outlines.map((o) => (
              <Card key={o.id} size="small" hoverable className="doc-card"
                onClick={() => navigate(`/outlines/${o.id}`)}
                title={<span className="ellipsis">{o.name}</span>}
                extra={
                  <Dropdown menu={{ items: [
                    { key: "open", label: "打开", onClick: () => navigate(`/outlines/${o.id}`) },
                    { key: "rename", label: "重命名", onClick: () => {
                        const name = window.prompt("新名称", o.name);
                        if (name) void patch(`/api/outlines/${o.id}`, { name }).then(() => void load());
                    } },
                    { key: "del", label: "删除", danger: true, onClick: () => {
                        Modal.confirm({ title: `删除大纲「${o.name}」？`, content: "存在创作稿时无法删除。",
                          okText: "删除", okButtonProps: { danger: true },
                          onOk: async () => {
                            try { await del(`/api/outlines/${o.id}`); void load(); }
                            catch (e) { message.error((e as { error?: { userMessage: string } }).error?.userMessage || "删除失败"); }
                          } });
                    } },
                  ] }}>
                    <MoreOutlined onClick={(e) => e.stopPropagation()} />
                  </Dropdown>
                }>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    源设计：《{o.design_title}》 ｜ {o.leaf_count ?? 0} 个叶子 ｜ {o.total_word_count} 字
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>更新 {fmtDate(o.updated_at)}</Typography.Text>
                </Space>
              </Card>
            ))}
          </div>
        )}
      </Spin>
    </div>
  );
}
