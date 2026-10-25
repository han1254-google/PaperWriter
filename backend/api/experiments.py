"""实验方案设计 API（理工版）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .. import db
from ..errors import AppError, ok
from ..ids import trace_id
from ..logging_setup import trace_var
from ..paths import get_data_dir
from ..services import experiments

router = APIRouter(prefix="/api", tags=["experiments"])


@router.get("/plans")
def list_plans():
    return ok(experiments.list_plans())


@router.post("/plans")
def create_plan(body: dict[str, Any]):
    return ok(experiments.create_plan(body.get("research_idea", ""),
                                      title=body.get("title")))


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int):
    return ok(experiments.get_plan(plan_id))


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, body: dict[str, Any]):
    return ok(experiments.update_plan(plan_id, body))


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int):
    experiments.delete_plan(plan_id)
    return ok()


@router.post("/plans/{plan_id}/generate")
async def generate_plan(plan_id: int):
    from ..api.stream import on_event_factory
    from ..jobs import get_runner
    if get_runner().has_running("plan", plan_id):
        raise AppError("JOB_ALREADY_RUNNING", stage="generate")
    row = db.fetch_one("SELECT * FROM experiment_plan WHERE id=?", (plan_id,))
    if not row:
        raise AppError("NOT_FOUND")
    trace = trace_var.get() or trace_id()

    async def run(job_id: int):
        on_event = await on_event_factory(job_id)
        try:
            result = await experiments.generate_plan(
                plan_id, trace=trace, job_id=job_id, on_event=on_event)
            await on_event({"type": "result", "plan_id": plan_id,
                            "validation": result.get("validation")})
            await on_event({"type": "done", "message": "实验方案已生成"})
            return result
        except AppError as e:
            await on_event({"type": "error", **e.to_dict()})
            raise
        except Exception as e:  # noqa: BLE001 — 非预期异常也告知前端
            await on_event({"type": "error", "code": "SYS_UNCAUGHT",
                            "userMessage": "后端遇到问题，已记录日志",
                            "traceId": trace})
            raise

    job_id, started = get_runner().start("plan_generate", "plan", plan_id, trace, run)
    return ok({"job_id": job_id, "started": started})


@router.post("/plans/{plan_id}/reverify")
def reverify_plan(plan_id: int):
    """重新执行溯源绑定与数字校验（不重新生成）。"""
    result = experiments.reverify_plan(plan_id)
    return ok(result)


@router.get("/plans/{plan_id}/export")
def export_plan(plan_id: int):
    """导出方案：正文 + 溯源表（含数据待核实标注）。"""
    import re
    from ..logging_setup import log_backend
    plan = experiments.get_plan(plan_id)
    if not (plan["content"] or "").strip():
        raise AppError("EXPORT_NO_CONTENT", stage="export")
    date_str = plan["updated_at"][:10].replace("-", "")
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (plan["title"] or "实验方案").strip()[:60])
    filename = f"{safe_title}_{date_str}_实验方案.md"
    lines = [f"# {plan['title'] or '实验方案'}", "",
             f"> 研究想法：{plan['research_idea']}", ""]
    if plan.get("intent"):
        intent = plan["intent"]
        bits = "；".join(f"{k}: {v}" for k, v in intent.items()
                         if k != "queries" and v)
        if bits:
            lines.append(f"> 意图解析：{bits}")
        lines.append("")
    lines += [plan["content"], "", "## 溯源表", ""]
    if plan["citations"]:
        lines.append("| 来源 | 文献 | 定位 | 数据校验 |")
        lines.append("|---|---|---|---|")
        for c in plan["citations"]:
            status = "✓ 已核对" if c["verified"] else f"⚠ {c['data_note'] or '待核实'}"
            loc = f"第{c['page_start']}页" if c.get("page_start") else (c.get("heading_path") or "—")
            title = c.get("doc_title") or "（未关联文献）"
            lines.append(f"| [来源{c['source_no']}] | {title} | {loc} | {status} |")
        lines.append("")
        # 证据原文附录：完整片段内容，便于离线核对（有据可查）
        lines += ["## 证据原文", ""]
        bound = [c for c in plan["citations"] if c.get("chunk_id")]
        if bound:
            for c in bound:
                meta_bits = []
                if c.get("doc_authors"):
                    meta_bits.append(c["doc_authors"])
                if c.get("doc_published_at"):
                    meta_bits.append(c["doc_published_at"])
                if c.get("heading_path"):
                    meta_bits.append(c["heading_path"])
                if c.get("page_start"):
                    meta_bits.append(f"第{c['page_start']}页")
                head = f"### [来源{c['source_no']}] {c.get('doc_title') or '未命名文献'}"
                if meta_bits:
                    head += f"（{'；'.join(meta_bits)}）"
                lines += [head, ""]
                lines.append(f"> {(c.get('chunk_text') or '').strip()}")
                lines.append("")
        unbound = [c for c in plan["citations"] if not c.get("chunk_id")]
        if unbound:
            lines += ["### 未关联的来源", ""]
            for c in unbound:
                lines.append(f"- [来源{c['source_no']}]：{c.get('data_note') or '待核实'}"
                             f"（{c.get('quote') or ''}）")
            lines.append("")
    else:
        lines += ["（无溯源引用）", ""]
    out_dir = get_data_dir() / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    trace = trace_var.get() or trace_id()
    log_backend(trace=trace, module="export", msg="plan export ok", path=filename)
    return FileResponse(path, filename=filename, media_type="text/markdown; charset=utf-8")
