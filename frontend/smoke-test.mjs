/** 浏览器冒烟测试：wizard → 文件上传 → 章节设计 → 大纲 → 创作 → 导出（无密钥路径）。 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const BASE = process.env.SMOKE_URL || "http://127.0.0.1:8000";
const shots = fileURLToPath(new URL("./smoke-shots/", import.meta.url));
mkdirSync(shots, { recursive: true });

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));

async function step(name, fn) {
  try {
    await fn();
    console.log("✓ " + name);
  } catch (e) {
    console.log("✗ " + name + " — " + e.message);
    await page.screenshot({ path: shots + "fail-" + name.replace(/[^\w]/g, "_") + ".png" });
    errors.push(name + ": " + e.message);
  }
}

await step("打开首页与配置向导", async () => {
  await page.goto(BASE + "/");
  await page.waitForTimeout(1500);
  const wizard = page.getByText("欢迎使用文科论文写作助手");
  if (await wizard.isVisible()) {
    await page.screenshot({ path: shots + "01-wizard.png" });
    // 跳过配置（测试无密钥路径）
    await page.getByText("暂不配置，直接使用").click();
    await page.waitForTimeout(800);
  } else {
    console.log("  （向导已完成过，跳过）");
  }
});

await step("导航到文件入库", async () => {
  await page.getByText("文件入库").first().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: shots + "02-files.png" });
});

await step("上传测试 docx", async () => {
  // 生成测试文件
  const { execSync } = await import("node:child_process");
  const docxScript = `
import docx
doc = docx.Document()
doc.add_heading('论个人信息保护的法律构造', level=0)
doc.add_heading('一、问题的提出', level=1)
doc.add_paragraph('随着数字经济发展，个人信息保护成为法学研究的重要议题。' * 30)
doc.add_heading('二、规范基础', level=1)
doc.add_paragraph('个人信息保护法于2021年施行，构建了较为完整的制度框架。' * 40)
doc.add_heading('参考文献', level=1)
doc.add_paragraph('[1] 王利明. 论个人信息权的法律保护[J]. 中国法学, 2023(3).')
doc.save('data/files/smoke_test.docx')
`;
  execSync(`/d/Environment/conda/envs/law_paper/python.exe -X utf8 -c "${docxScript.replaceAll('"', '\\"')}"`, { shell: "bash" });
  await page.getByRole("button", { name: /上传文献/ }).click();
  await page.waitForTimeout(500);
  const input = page.locator('input[type="file"]');
  await input.setInputFiles("data/files/smoke_test.docx");
  await page.getByRole("button", { name: "开始上传" }).click();
  await page.waitForTimeout(5000); // 等待解析任务
  await page.getByText("论个人信息保护的法律构造").first().waitFor({ timeout: 15000 });
  await page.screenshot({ path: shots + "03-uploaded.png" });
});

await step("查看文献详情", async () => {
  await page.locator(".doc-card").first().click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: shots + "04-doc-detail.png" });
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
});

await step("搜索片段", async () => {
  await page.locator('input[placeholder*="搜索标题"]').fill("个人信息保护");
  await page.locator('input[placeholder*="搜索标题"]').press("Enter");
  await page.waitForTimeout(1200);
});

await step("创建章节设计", async () => {
  await page.getByText("章节设计", { exact: true }).first().click();
  await page.waitForTimeout(600);
  await page.locator(".list-page").getByRole("button", { name: /新建/ }).first().click();
  await page.waitForTimeout(400);
  await page.locator(".ant-modal textarea").first().fill(
    "研究《民法典》中个人信息处理的合法性基础，梳理学理争议并结合典型案例提出完善建议，考察比较法经验并评析我国制度实践。");
  await page.locator(".ant-modal").getByRole("button", { name: /^创\s*建$/ }).click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: shots + "05-design-editor.png" });
});

await step("手动添加章节并调整预算", async () => {
  await page.getByRole("button", { name: "添加顶层章节" }).click();
  await page.waitForTimeout(400);
  await page.locator(".ant-modal input").first().fill("引言");
  await page.locator(".ant-modal textarea").first().fill("提出问题背景、研究意义与论证主线。");
  await page.getByRole("button", { name: /^确\s*定$/ }).click();
  await page.waitForTimeout(600);
  await page.getByRole("button", { name: "平均分配" }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: shots + "06-tree.png" });
});

await step("保存章节设计", async () => {
  await page.locator(".design-editor").getByRole("button", { name: /^save\s+保\s*存$/ }).click();
  await page.waitForTimeout(1200);
});

await step("冲突检查", async () => {
  await page.getByRole("button", { name: /开始检查|重新检查/ }).click();
  await page.waitForTimeout(5000); // 模型未配置 → 应给出「未完成检查」提示
});

await step("确认并保存（强行保存路径）", async () => {
  await page.getByRole("button", { name: "确认并保存" }).click();
  await page.waitForTimeout(600);
  // 未配置密钥 → 冲突检查失败 → 出现强行保存入口
  await page.screenshot({ path: shots + "07-confirm.png" });
  const forceBtn = page.getByRole("button", { name: "仍然保存" });
  if (await forceBtn.isVisible()) {
    await page.locator("#force-reason").fill("测试环境，未配置模型");
    await forceBtn.click();
    await page.waitForTimeout(1500);
  }
});

await step("大纲设计入口", async () => {
  // 确认后自动跳转 /outlines
  if (!page.url().includes("outlines")) await page.goto(BASE + "/#/outlines");
  await page.waitForTimeout(1200);
  await page.screenshot({ path: shots + "08-outlines.png" });
});

await step("创建大纲并编辑", async () => {
  const card = page.locator(".doc-card").first();
  if (await card.count()) {
    await card.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: shots + "09-outline-editor.png" });
    // 手写大纲内容（无密钥无法生成）
    const ta = page.locator("textarea").first();
    if (await ta.count()) {
      await ta.fill("### 写作目标\n本节梳理个人信息处理合法性基础的规范构成。\n\n### 论证结构\n1. 梳理现行规范\n2. 分析学理争议\n\n### 材料安排\n- 待补充文献\n\n### 注意事项\n- 需核实《民法典》最新条文表述\n\n### 预计字数\n约 1500 字");
      await page.waitForTimeout(2600); // 自动保存
    }
  }
});

await step("章节创作入口", async () => {
  await page.goto(BASE + "/#/drafts");
  await page.waitForTimeout(1200);
  await page.screenshot({ path: shots + "10-drafts.png" });
});

await step("创建创作稿并编辑", async () => {
  const card = page.locator(".doc-card").first();
  if (await card.count()) {
    await card.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: shots + "11-draft-editor.png" });
    const ta = page.locator("textarea").first();
    if (await ta.count()) {
      await ta.fill("正文示例内容，论证个人信息处理的合法性基础。需要补充引用[1.1-1]。\n\n<NOTICE type=\"verify\">请核实《民法典》第一千零三十五条的条文表述。</NOTICE>");
      await page.waitForTimeout(2600);
    }
  }
});

await step("论文信息与导出弹层", async () => {
  await page.getByRole("button", { name: "论文信息" }).click();
  await page.waitForTimeout(500);
  await page.locator(".ant-drawer input").first().fill("个人信息处理的合法性基础研究");
  await page.getByRole("button", { name: "保存论文信息" }).click();
  await page.waitForTimeout(800);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /导\s*出$/ }).click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: shots + "12-export.png" });
  await page.keyboard.press("Escape");
});

await step("设置页", async () => {
  await page.getByRole("button", { name: "设置" }).click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: shots + "13-settings.png" });
  await page.getByText("日志与诊断").click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: shots + "14-diagnostics.png" });
});

console.log("\n========== 控制台错误汇总 ==========");
if (errors.length === 0) console.log("无错误");
else errors.slice(0, 20).forEach((e) => console.log("· " + e));
console.log("\n截图目录：frontend/smoke-shots/");
await browser.close();
process.exit(errors.length ? 1 : 0);
