/** Markdown 渲染：NOTICE 提示块高亮、引用标记高亮（§9.3）。 */
import { useMemo } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { Tag } from "antd";
import { NOTICE_TYPE_NAMES } from "../lib/format";

marked.setOptions({ breaks: true, gfm: true });

const NOTICE_RE = /<NOTICE\s+type="(\w+)">([\s\S]*?)<\/NOTICE>/gi;
const MARKER_RE = /\[(\d+(?:\.\d+)*-\d+)\]/g;

export function highlightContent(text: string): string {
  let html = text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // 引用标记
  html = html.replace(MARKER_RE, (m, k) => `<span class="cite-marker">[${k}]</span>`);
  // NOTICE 块
  html = html.replace(/&lt;NOTICE\s+type="(\w+)"&gt;([\s\S]*?)&lt;\/NOTICE&gt;/gi,
    (_, t: string, body: string) =>
      `<span class="notice-block notice-${t.toLowerCase()}"><span class="notice-tag">` +
      `${NOTICE_TYPE_NAMES[t.toLowerCase()] || "提示"}</span>${body}</span>`);
  return html;
}

export function MarkdownView({ text, className }: { text: string; className?: string }) {
  const html = useMemo(() => {
    const raw = marked.parse(text || "", { async: false }) as string;
    return DOMPurify.sanitize(raw, { ADD_ATTR: ["target"] });
  }, [text]);
  return <div className={`markdown-view ${className || ""}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** 正文视图：先高亮 NOTICE/引用，再渲染 Markdown。 */
export function DraftView({ text }: { text: string }) {
  const html = useMemo(() => {
    const highlighted = highlightContent(text);
    // 把高亮后的 HTML 再走 markdown：NOTICE 与标记不包含 markdown 语法，可安全混排
    const raw = marked.parse(highlighted, { async: false }) as string;
    return DOMPurify.sanitize(raw, { ADD_ATTR: ["target"] });
  }, [text]);
  return <div className="markdown-view draft-view" dangerouslySetInnerHTML={{ __html: html }} />;
}

/** 提取提示块列表（与后端 NOTICE_RE 保持一致）。 */
export function extractNotices(text: string): { type: string; content: string }[] {
  const out: { type: string; content: string }[] = [];
  const re = /<NOTICE\s+type="(\w+)">([\s\S]*?)<\/NOTICE>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    out.push({ type: m[1].toLowerCase(), content: m[2].trim() });
  }
  return out;
}

export function NoticeTypeTag({ type }: { type: string }) {
  return <Tag color="orange">{NOTICE_TYPE_NAMES[type] || type}</Tag>;
}
