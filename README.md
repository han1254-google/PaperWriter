# 文科论文写作助手（v0.5.2）

面向文科（以法学为主）学生的论文辅助写作工具：把「资料入库 → 章节设计 → 大纲设计 → 逐章初稿 → 全文导出」串成一条可回溯、可修改的工作流。

**本地网页应用**：本机启动一个 Python 后端，浏览器访问 `http://127.0.0.1:8000`。文档、数据库、向量索引、日志全部保存在 `data/` 目录，复制整个文件夹即完成备份或迁移。

> 本产品是写作辅助工具，不提供代写服务，也不承诺规避查重或 AIGC 检测。最终文本、观点与引注由作者负责。

---

## 一、给学生：怎么用

1. 双击 `启动.bat`（Windows）或 `启动.command`（macOS）。
2. 首次会自动打开浏览器进入配置向导：填写 DeepSeek 与阿里云百炼（Qwen）的 API Key、确认数据目录、运行环境自检。
3. 按工作流使用：
   - **文件入库**：上传 .docx / .doc / .pdf 文献，自动解析、分块、建立检索索引；
   - **章节设计**：填写作意图与目标字数 → AI 检索文献并生成章节树与字数预算 → 冲突检查 → 确认；
   - **大纲设计**：对每个叶子章节生成简化写作方案（写作目标、论证结构、材料安排）；
   - **章节创作**：逐章生成正文，引用自动标记 `[章节号-序号]`，证据不足处用提示块标注；
   - **导出**：工作稿（含全部追溯信息）或定稿（正文 + GB/T 7714-2015 参考文献）。
4. 关闭启动脚本窗口即停止程序；数据已写入 `data/`，不会丢。

## 二、给配置者：怎么部署到一台新机器

### 打分发包（推荐）

```bash
python 打包.py
```

自动生成 `../publish/论文写作助手_v0.5.zip`：含后端、预构建前端、启动脚本、
安装说明与全新配置（**密钥已清空**，学生首次打开自己填，费用自负），
不含你的本机数据与密钥。

### 学生机器上（每台一次）

1. 安装 Python 3.11+：官网下载安装，勾选 *Add python.exe to PATH*；
   （或由配置者远程装好，推荐 conda：`conda create -n law_paper python=3.12 -y`）
2. 解压 zip 到固定位置 → 双击 `启动.bat`；
3. 首次启动自动安装依赖（需联网 2–5 分钟）→ 自动打开浏览器 → 填自己的两个密钥；
4. 之后双击即用；数据在 `data\`，复制整个文件夹即可备份。

前端为预构建静态文件（`frontend/dist/`），学生机器**不需要 Node**。

### 注意

- **不要**把 `data/` 里的原项目文件夹直接发给学生——里面有你的密钥；
  分发一律走 `python 打包.py` 的产物。
- 学生端若需要「完全零环境」（连 Python 都不用装），需要做便携 Python
  运行包（文档 §13 P1 项），当前版本未实现。

## 三、给开发者：怎么改

```
law_paper/
├── 启动.bat / 启动.command   启动脚本（找 Python → 装依赖 → 起后端 → 开浏览器）
├── backend/                  Python 后端（FastAPI + SQLite + sqlite-vec）
│   ├── main.py               应用入口与静态托管
│   ├── api/                  路由（files/designs/outlines/drafts/settings/system）
│   ├── services/             业务逻辑（章节设计/大纲/创作/导出）
│   ├── ingest/               解析（PDF/DOCX/DOC）与分块
│   ├── search/               向量索引 + FTS5 + RRF 混合检索
│   ├── ai/                   模型客户端与提示词（提示词按版本管理）
│   ├── db.py                 SQLite 数据层（§4 数据模型）
│   ├── config.py             data/config.json 配置
│   ├── errors.py             统一错误码与文案（shared/error-messages.zh-CN.json）
│   └── logging_setup.py      前后端日志（data/logs/）
├── frontend/                 React + Vite + antd 前端（源码）
│   └── dist/                 预构建产物（由后端静态托管）
├── shared/error-messages.zh-CN.json   错误文案单一来源
├── data/                     运行时数据（配置/数据库/原文件/日志/向量索引/导出）
├── pyproject.toml / requirements.txt
└── 产品文档.md               需求规格（v0.5）
```

**启动后端**（开发模式，热重载前端需另跑 `npm run dev`）：

```bash
python -X utf8 -m backend.main        # 端口默认 8000，可用 PORT 环境变量覆盖
```

**开发前端**：

```bash
cd frontend
npm install
npm run dev      # Vite 开发服务器，/api 代理到 127.0.0.1:8000
npm run build    # 构建产物到 frontend/dist/，由后端直接托管
```

**测试**：

- `python ai_e2e_test.py`：端到端 AI 流程测试（设计生成 → 冲突检查 → 确认 → 大纲 → 正文 → 引用校验 → 导出），需先配置 API Key 并启动后端；
- `cd frontend && node smoke-test.mjs`：浏览器冒烟测试（Playwright，覆盖向导、上传、章节树编辑、大纲、创作、导出、设置），截图输出到 `frontend/smoke-shots/`。

**数据目录**：默认 `data/`，可用环境变量 `LAW_PAPER_DATA_DIR` 覆盖（测试用）。

**模型配置**：

- DeepSeek：`data/config.json` 中 `deepseek.*`（API Key / Base URL / 模型 ID，默认 DeepSeek V4 Flash）；
- Qwen 嵌入：`qwen.*`（默认 `qwen3.7-text-embedding-flash`，DashScope 或服务商兼容地址）；
- 开发/测试可用环境变量 `DEEPSEEK_API_KEY`、`QWEN_API_KEY`、`QWEN_BASE_URL` 等覆盖。

**已知环境注意**：

- conda 版 Python 在 Windows 上禁用 SQLite 扩展加载，后端已改为直接加载 sqlite-vec 的 `vec0` 动态库；若某平台仍加载失败，自动降级 NumPy 暴力检索（自检页可见当前后端）。
- SQLite 3.53.x 的 FTS5 external-content `delete` 命令存在损坏 bug，代码统一用 `rebuild` + 脏位惰性重建替代。
- `.doc` 转换依赖本机 LibreOffice / Word / WPS；失败时提示另存为 `.docx`。
- 扫描版 PDF 无文本层：允许保存、标记 `needs_ocr`，不参与检索（OCR 为 P1）。

## 四、当前状态

P0 已实现：文件上传/解析/元数据/分块/向量化/混合检索；章节设计（生成、树编辑、预算、冲突检查、确认）；大纲设计（叶子生成、结构同步）；章节创作（正文生成、引用校验、提示块）；工作稿/定稿 Markdown 导出（GB/T 7714 / 章节编码制）；首次配置向导；API Key 管理；前后端日志、统一错误码、诊断包导出；Windows/macOS 启动脚本。

P1 候选（未实现）：OCR、Word 导出、重排模型、摘要/关键词自动生成、版本历史 UI、应用内日志查看器等（详见产品文档 §13）。

> 说明：模型生成能力需在设置中配置 DeepSeek / Qwen API Key 后可用；未配置时上传解析、关键词检索、手动编辑等本地功能不受影响。
