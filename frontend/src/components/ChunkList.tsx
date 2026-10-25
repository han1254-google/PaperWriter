/** 片段列表（§5.5 / §6.5 / §7.2）：文档标题、作者、页码、相关度，可展开全文。 */
import { useState } from "react";
import { Button, Empty, List, Space, Tag, Typography } from "antd";
import { DownOutlined, RightOutlined, FileSearchOutlined } from "@ant-design/icons";
import type { Chunk } from "../api/types";

export function ChunkCard({ chunk, extra, compact }: {
  chunk: Chunk;
  extra?: React.ReactNode;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="chunk-card">
      <div className="chunk-card-head" onClick={() => setOpen(!open)} style={{ cursor: "pointer" }}>
        {open ? <DownOutlined /> : <RightOutlined />}
        <span className="chunk-card-title">{chunk.title}</span>
        <Space size={4} wrap>
          {chunk.authors && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{chunk.authors}</Typography.Text>}
          {(chunk.page_start != null || chunk.heading_path) && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {chunk.page_start != null ? `第${chunk.page_start}页` : chunk.heading_path}
            </Typography.Text>
          )}
          {chunk.chunk_type && chunk.chunk_type !== "body" && (
            <Tag style={{ fontSize: 11 }}>{chunk.chunk_type}</Tag>
          )}
          {chunk.score != null && (
            <Tag color="blue" style={{ fontSize: 11 }}>相关度 {chunk.score.toFixed(2)}</Tag>
          )}
        </Space>
        <span style={{ flex: 1 }} />
        {extra}
      </div>
      {open && (
        <div className="chunk-card-body">
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 4 }}>
            {chunk.text}
          </Typography.Paragraph>
        </div>
      )}
    </div>
  );
}

export function ChunkList({ chunks, emptyText, onViewDoc, extra, compact }: {
  chunks: Chunk[];
  emptyText?: string;
  onViewDoc?: (chunk: Chunk) => void;
  extra?: (chunk: Chunk) => React.ReactNode;
  compact?: boolean;
}) {
  if (!chunks.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText || "暂无片段"} />;
  }
  return (
    <List
      size="small"
      dataSource={chunks}
      renderItem={(c) => (
        <ChunkCard
          chunk={c}
          compact={compact}
          extra={
            <Space size={4}>
              {onViewDoc && c.document_id != null && (
                <Button size="small" type="text" icon={<FileSearchOutlined />}
                  onClick={(e) => { e.stopPropagation(); onViewDoc(c); }}>
                  原文
                </Button>
              )}
              {extra?.(c)}
            </Space>
          }
        />
      )}
    />
  );
}
