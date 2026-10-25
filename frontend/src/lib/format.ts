/** 格式化工具。 */

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtSize(bytes: number): string {
  if (bytes > 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  if (bytes > 1024) return (bytes / 1024).toFixed(1) + " KB";
  return bytes + " B";
}

export function countWords(text: string): number {
  if (!text) return 0;
  const cjk = (text.match(/[一-鿿]/g) || []).length;
  const words = (text.match(/[A-Za-z0-9]+/g) || []).length;
  return cjk + words;
}

export const DOC_TYPE_NAMES: Record<string, string> = {
  学术论文: "学术论文",
  学位论文: "学位论文",
  专著: "专著",
  法律法规: "法律法规",
  司法案例: "司法案例",
  新闻报道: "新闻报道",
  网络资料: "网络资料",
  其他: "其他",
};

export const STATUS_NAMES: Record<string, { text: string; color: string }> = {
  uploading: { text: "上传中", color: "processing" },
  parsing: { text: "解析中", color: "processing" },
  extracting: { text: "抽取元数据", color: "processing" },
  indexing: { text: "建立索引", color: "processing" },
  ready: { text: "就绪", color: "success" },
  failed: { text: "失败", color: "error" },
  needs_ocr: { text: "扫描件待OCR", color: "warning" },
  parsed: { text: "已解析(未索引)", color: "warning" },
};

export const OUTLINE_STATUS: Record<string, { text: string; color: string }> = {
  pending: { text: "未生成", color: "default" },
  generating: { text: "生成中", color: "processing" },
  generated: { text: "已生成", color: "success" },
  modified: { text: "已修改", color: "warning" },
  invalid: { text: "已失效", color: "error" },
};

export const DRAFT_STATUS: Record<string, { text: string; color: string }> = {
  pending: { text: "未生成", color: "default" },
  generating: { text: "生成中", color: "processing" },
  generated: { text: "已生成", color: "success" },
  modified: { text: "已修改", color: "warning" },
  done: { text: "已完成", color: "cyan" },
};

export const NOTICE_TYPE_NAMES: Record<string, string> = {
  verify: "需核实",
  cite: "需补引用",
  case: "需补案例",
  data: "需补数据",
  image: "需配图表",
  transition: "需补衔接",
};
