/** 与后端约定的类型（产品文档 §11.3 AppError）。 */

export type AppError = {
  code: string;
  message: string;
  userMessage: string;
  suggestion?: string;
  detail?: string;
  traceId: string;
  stage: string;
  retryable: boolean;
};

export type ApiOk<T> = { ok: true; data: T };
export type ApiErr = { ok: false; error: AppError };
export type ApiResp<T> = ApiOk<T> | ApiErr;

export type DocStatus = "uploading" | "parsing" | "extracting" | "indexing" | "ready" | "failed" | "needs_ocr";

export type DocumentItem = {
  id: number;
  category_id: number | null;
  title: string;
  doc_type: string | null;
  authors: string | null;
  publisher: string | null;
  published_at: string | null;
  language: string | null;
  file_name: string;
  file_path: string;
  file_hash: string;
  file_size: number;
  page_count: number | null;
  summary: string | null;
  metadata: Record<string, unknown> | null;
  status: DocStatus;
  parse_error: string | null;
  created_at: string;
  updated_at: string;
  chunk_count?: number;
  referenced_by?: { kind: string; name: string; entity_id: number }[];
};

export type Category = { id: number; parent_id: number | null; name: string; sort: number };

export type Chunk = {
  id: number;
  document_id?: number;
  chunk_index?: number;
  chunk_type: string;
  text: string;
  token_count?: number;
  page_start: number | null;
  page_end: number | null;
  para_start?: number | null;
  para_end?: number | null;
  heading_path: string;
  title: string;
  authors?: string | null;
  publisher?: string | null;
  published_at?: string | null;
  doc_type?: string | null;
  score?: number;
  matched_by?: string;
  outline_chapter_id?: number;
};

export type ChapterNode = {
  id: number;
  parent_id: number | null;
  number: string;
  title: string;
  idea: string | null;
  word_budget: number;
  sort: number;
  depth?: number;
};

export type Design = {
  id: number;
  title: string | null;
  intent: string;
  description: string | null;
  overall_idea: string | null;
  total_word_count: number;
  discipline?: string;
  status: "draft" | "confirmed";
  model: string | null;
  prompt_version: string | null;
  conflict_report: {
    has_conflict?: boolean;
    conflicts?: ConflictItem[];
    check_failed?: boolean;
    checked_at?: string;
  } | null;
  confirm_notes?: string | null;
  chapter_count?: number;
  outline_count?: number;
  created_at: string;
  updated_at: string;
  chapters?: ChapterNode[];
  chunks?: DesignChunkLink[];
  budget?: { total: number; allocated: number; diff: number; within_tolerance: boolean; leaf_count: number };
};

export type DesignChunkLink = Chunk & {
  link_id: number;
  design_chapter_id: number | null;
  selected: number;
};

export type ConflictItem = {
  type: string;
  severity: "high" | "medium" | "low";
  chapters: string[];
  description: string;
  suggestion: string;
};

export type OutlineChapter = {
  id: number;
  outline_id: number;
  design_chapter_id: number | null;
  chapter_number: string;
  chapter_title: string;
  chapter_idea_snapshot: string | null;
  content: string | null;
  word_budget: number | null;
  status: "pending" | "generating" | "generated" | "modified" | "invalid";
  sort: number;
};

export type OutlineTreeNode = ChapterNode & { outline_chapter: OutlineChapter | null };

export type Outline = {
  id: number;
  design_id: number;
  name: string;
  status: string;
  model: string | null;
  created_at: string;
  updated_at: string;
  design?: Design;
  design_title?: string;
  design_status?: string;
  total_word_count?: number;
  leaf_count?: number;
  tree?: OutlineTreeNode[];
  chunks?: Chunk[];
  progress?: { generated: number; total: number };
};

export type DraftChapter = {
  id: number;
  draft_id: number;
  outline_chapter_id: number;
  content: string | null;
  word_count: number;
  target_word_count: number | null;
  status: "pending" | "generating" | "generated" | "modified" | "done";
  sort: number;
  chapter_number: string;
  chapter_title: string;
  outline_content: string | null;
};

export type DraftTreeNode = ChapterNode & { draft_chapter: DraftChapter | null; is_leaf: boolean };

export type Citation = {
  id: number;
  draft_chapter_id: number;
  chunk_id: number | null;
  marker: string;
  order_no: number;
  quote: string | null;
  page_start: number | null;
  page_end: number | null;
  document_snapshot_json: string | null;
  valid: number;
  doc_title?: string | null;
  doc_authors?: string | null;
  doc_publisher?: string | null;
  doc_published_at?: string | null;
};

export type Notice = {
  id: number;
  draft_chapter_id: number;
  notice_type: string;
  content: string;
  status: "open" | "resolved";
  chapter_number?: string;
  chapter_title?: string;
};

export type Draft = {
  id: number;
  outline_id: number;
  title: string | null;
  abstract: string | null;
  keywords: string | null;
  author: string | null;
  school: string | null;
  publish_date: string | null;
  target_word_count: number | null;
  status: string;
  citation_format: string;
  created_at: string;
  updated_at: string;
  outline?: Outline;
  design?: Design;
  tree?: DraftTreeNode[];
  chapters?: DraftChapter[];
  citations?: Citation[];
  notices?: Notice[];
  chunks?: Chunk[];
  stats?: { actual_words: number; done: number; generated: number; total: number; invalid_citations: number };
  leaf_count?: number;
  done_count?: number;
  actual_words?: number;
  outline_name?: string;
  design_title?: string;
};

export type Job = {
  id: number;
  job_type: string;
  entity_type: string | null;
  entity_id: number | null;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  error_code: string | null;
  error_detail: string | null;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
};

export type SelfcheckItem = { name: string; ok: boolean; detail: string };
export type Selfcheck = { passed: boolean; items: SelfcheckItem[] };

export type Settings = {
  config: {
    deepseek: { api_key: string; base_url: string; model: string; temperature: number; max_output_tokens: number };
    qwen: { api_key: string; base_url: string; embedding_model: string };
    limits: Record<string, number>;
    setup_done?: boolean;
    privacy_consent?: boolean;
  };
  key_status: { deepseek: "configured" | "missing"; qwen: "configured" | "missing" };
  setup_done: boolean;
  privacy_consent: boolean;
};

export type ExportChecks = {
  unfinished: { number: string; title: string }[];
  info_missing: string[];
  actual_words: number;
  target_words: number;
  deviation: number;
  deviation_exceeded: boolean;
  invalid_citations: number;
  can_final_export: boolean;
};

export type PlanCitation = {
  id: number;
  plan_id: number;
  source_no: number;
  chunk_id: number | null;
  quote: string | null;
  verified: number;
  data_note: string | null;
  chunk_text?: string | null;
  heading_path?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  doc_title?: string | null;
  doc_authors?: string | null;
  doc_published_at?: string | null;
  doc_doc_type?: string | null;
  doc_id?: number | null;
};

export type ExperimentPlan = {
  id: number;
  title: string | null;
  research_idea: string;
  intent?: Record<string, unknown> | null;
  content: string | null;
  model: string | null;
  prompt_version: string | null;
  status: string;
  citation_count?: number;
  citations?: PlanCitation[];
  verified_count?: number;
  invalid_count?: number;
  created_at: string;
  updated_at: string;
};

export type StreamEvent =
  | { type: "ping" | "heartbeat_end" }
  | { type: "progress"; message: string }
  | { type: "warning"; message: string }
  | { type: "retrieval"; chunks: { chunk_id: number; title: string; page_start: number | null; heading_path: string; text_preview: string }[] }
  | { type: "retrieval_empty" }
  | { type: "delta"; text: string }
  | { type: "parsing"; message: string }
  | { type: "parse_failed"; raw: string }
  | { type: "result"; [k: string]: unknown }
  | { type: "done"; message: string }
  | { type: "error"; code: string; userMessage: string; suggestion?: string; traceId: string };
