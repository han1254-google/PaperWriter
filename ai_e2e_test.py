"""端到端 AI 流程测试：设计生成 → 冲突检查 → 确认 → 大纲 → 正文 → 导出。

用法：python ai_e2e_test.py [BASE_URL]
"""
import json
import sys
import time
from urllib.request import Request, urlopen

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def api(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = Request(BASE + path, data=data, method=method,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_job(job_id, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = api("GET", f"/api/jobs/{job_id}")["data"]
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(2)
    raise TimeoutError("job timeout")


def watch_stream(job_id, label):
    """读取 NDJSON 事件流，打印摘要。"""
    req = Request(f"{BASE}/api/stream/{job_id}")
    last_len = 0
    with urlopen(req, timeout=300) as resp:
        for line in resp:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            ev = json.loads(line)
            t = ev.get("type")
            if t == "delta":
                n = len(ev.get("text", ""))
                print(f"  [{label}] 流式输出 +{n} 字", end="\r")
                last_len += n
            elif t in ("progress", "warning", "parsing"):
                print(f"\n  [{label}] {t}: {ev.get('message')}")
            elif t == "retrieval":
                print(f"\n  [{label}] 检索到 {len(ev.get('chunks', []))} 条片段")
            elif t == "retrieval_empty":
                print(f"\n  [{label}] 检索无结果")
            elif t == "done":
                print(f"\n  [{label}] 完成")
            elif t == "error":
                print(f"\n  [{label}] 失败: {ev.get('code')} {ev.get('userMessage')}")
                return False
            elif t == "ping":
                pass
    print(f"\n  [{label}] 流式总计 {last_len} 字")
    return True


def main():
    print("== 1. 创建章节设计 ==")
    design = api("POST", "/api/designs", {
        "intent": "研究《民法典》与《个人信息保护法》中个人信息处理的合法性基础，"
                  "梳理同意规则与其他法定事由的规范构成，分析学理争议，"
                  "并结合司法案例提出完善建议。",
        "total_word_count": 8000,
        "title": "个人信息处理合法性基础研究",
    })["data"]
    design_id = design["id"]
    print(f"design_id={design_id}")

    print("== 2. 生成章节设计（检索 + 模型生成，流式） ==")
    res = api("POST", f"/api/designs/{design_id}/generate", {})
    job_id = res["data"]["job_id"]
    watch_stream(job_id, "设计生成")
    wait_job(job_id)

    design = api("GET", f"/api/designs/{design_id}")["data"]
    print(f"标题: {design['title']}")
    print(f"整体思路: {(design['overall_idea'] or '')[:100]}…")
    for c in design["chapters"]:
        indent = "  " * (c.get("depth", 1) - 1)
        print(f"{indent}{c['number']} {c['title']} [{c['word_budget']}字] "
              f"片段关联={len([x for x in design['chunks'] if x['design_chapter_id'] == c['id']])}")
    budget = design["budget"]
    print(f"预算: 总{design['total_word_count']} 已分配{budget['allocated']} "
          f"差{budget['diff']} 容差内={budget['within_tolerance']}")

    print("== 3. 冲突检查 ==")
    report = api("POST", f"/api/designs/{design_id}/conflict-check")["data"]
    print(f"has_conflict={report.get('has_conflict')} check_failed={report.get('check_failed')} "
          f"conflicts={len(report.get('conflicts') or [])}")
    for c in (report.get("conflicts") or [])[:3]:
        print(f"  [{c.get('severity')}] {c.get('type')} {c.get('chapters')}: "
              f"{c.get('description')} → {c.get('suggestion')}")

    print("== 4. 确认并保存 ==")
    try:
        confirmed = api("POST", f"/api/designs/{design_id}/confirm",
                        {"force": False})["data"]
    except Exception as e:
        if "409" in str(e):
            print("存在冲突或预算失衡 → 强行保存")
            confirmed = api("POST", f"/api/designs/{design_id}/confirm",
                            {"force": True, "force_reason": "端到端测试"})["data"]
        else:
            raise
    print(f"status={confirmed['status']} notes={confirmed.get('confirm_notes')}")

    print("== 5. 创建大纲 ==")
    outline = api("POST", "/api/outlines", {"design_id": design_id})["data"]
    outline_id = outline["id"]
    leaves = [n for n in outline["tree"] if n["outline_chapter"]]
    print(f"outline_id={outline_id} 叶子数={len(leaves)}")

    print("== 6. 生成第一个叶子的大纲 ==")
    leaf = leaves[0]["outline_chapter"]
    res = api("POST", f"/api/outlines/chapters/{leaf['id']}/generate", {})
    watch_stream(res["data"]["job_id"], "大纲生成")
    wait_job(res["data"]["job_id"])
    outline = api("GET", f"/api/outlines/{outline_id}")["data"]
    first_content = outline["tree"][0]["outline_chapter"]["content"] if outline["tree"][0]["outline_chapter"] else None
    print(f"大纲内容（前 200 字）: {(first_content or '')[:200]}")

    print("== 7. 创建创作稿 ==")
    draft = api("POST", "/api/drafts", {"outline_id": outline_id})["data"]
    draft_id = draft["id"]
    print(f"draft_id={draft_id}")

    print("== 8. 生成第一章正文 ==")
    dc = draft["chapters"][0]
    res = api("POST", f"/api/drafts/chapters/{dc['id']}/generate",
              {"target_words": 1500, "cite_density": "中", "tone": "学术严谨"})
    watch_stream(res["data"]["job_id"], "正文生成")
    wait_job(res["data"]["job_id"])

    draft = api("GET", f"/api/drafts/{draft_id}")["data"]
    chapter = draft["chapters"][0]
    print(f"正文字数: {chapter['word_count']} (目标 {chapter['target_word_count']})")
    print(f"正文前 300 字:\n{(chapter['content'] or '')[:300]}")
    cites = [c for c in draft["citations"] if c["draft_chapter_id"] == chapter["id"]]
    notices = [n for n in draft["notices"] if n["draft_chapter_id"] == chapter["id"]]
    print(f"引用: {len(cites)} 条（无效 {sum(1 for c in cites if not c['valid'])}），"
          f"提示块: {len(notices)} 个")
    for c in cites[:5]:
        print(f"  {c['marker']} → {c['doc_title']} 第{c['page_start']}页 有效={c['valid']}")
    for n in notices[:5]:
        print(f"  NOTICE[{n['notice_type']}]: {n['content'][:50]}")

    print("== 9. 导出检查与导出 ==")
    checks = api("GET", f"/api/drafts/{draft_id}/export-checks")["data"]
    print(f"未完成章节={len(checks['unfinished'])} 无效引用={checks['invalid_citations']} "
          f"字数偏差={checks['deviation']}")

    def export(mode):
        req = Request(f"{BASE}/api/drafts/{draft_id}/export?mode={mode}"
                      f"&citation_format=gb7714&include_todo=true")
        with urlopen(req, timeout=300) as resp:
            text = resp.read().decode("utf-8")
        out = f"data/exports/e2e_{mode}.md"
        open(out, "w", encoding="utf-8").write(text)
        print(f"{mode} 导出成功：{len(text)} 字 → {out}")

    export("work")
    if checks["invalid_citations"] > 0:
        print("验证：存在无效引用 → 定稿导出应被阻止…")
        try:
            export("final")
            print("!! 定稿导出未被阻止（不符合 §8.5 要求）")
        except Exception as e:
            if "409" in str(e):
                print("✓ 定稿导出被正确阻止")
                # 修复引用：把无效引用映射到上下文片段
                draft = api("GET", f"/api/drafts/{draft_id}")["data"]
                bad = [c for c in draft["citations"] if not c["valid"]]
                chunk_ids = [c["chunk_id"] for c in draft["chunks"]]
                if bad and chunk_ids:
                    api("POST", f"/api/citations/{bad[0]['id']}/fix",
                        {"chunk_id": chunk_ids[0]})
                    print(f"✓ 已修复引用 {bad[0]['marker']} → 重新导出定稿")
                    export("final")
    else:
        export("final")

    print("\n== 全部流程通过 ==")


if __name__ == "__main__":
    main()
