"""一键打包发布（产品文档 §1.5 分发方式）。

生成可直接发给学生的压缩包：
- 包含后端、预构建前端、启动脚本、依赖清单、说明文档；
- 不包含：node_modules、测试文件、本机数据、你的 API Key（学生首启自填）；
- 输出到项目外的 publish/ 目录，避免递归打包。

用法：python 打包.py
"""
import copy
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / "publish"
NAME = "论文写作助手_v0.5.3"
STAGE = OUT / NAME

# 与 backend/config.py 的默认配置保持一致；打包时密钥置空、向导状态重置
DEFAULT_CONFIG = {
    "app_version": "0.5.3",
    "deepseek": {
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "temperature": 0.4,
        "max_output_tokens": 16384,
    },
    "qwen": {
        "api_key": "",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "embedding_model": "qwen3.7-text-embedding-flash",
    },
    "generation": {"timeout_s": 180, "max_retries": 2, "prompt_version": "v1"},
    "setup_done": False,
    "privacy_consent": False,
}

INSTALL_GUIDE = """文科论文写作助手 · 安装与使用说明
======================================

一、第一次安装（每台电脑只需一次，约 5 分钟）

【Windows】

1. 安装 Python 3.11 或更高版本（如果电脑里已有可跳过）：
   打开 https://www.python.org/downloads/ 下载安装，
   安装时务必勾选 "Add python.exe to PATH"。

2. 把本文件夹解压到一个固定位置（例如 D:\\论文助手）。
   注意：不要在压缩包里直接双击运行，必须先解压！

3. 双击「启动.bat」。首次启动会自动安装依赖
   （需要联网，等待 2–5 分钟），然后自动打开浏览器。

【macOS】

1. 安装 Python 3.11 或更高版本（已有可跳过）：
   打开 https://www.python.org/downloads/ 下载安装，
   或终端执行 brew install python。

2. 把本文件夹完整解压到一个固定位置（例如 文稿/论文助手）。
   ！！务必先解压！！不要在访达的压缩包预览里直接双击运行，
   否则程序跑在只读挂载里，配置无法保存。

3. 双击「启动.command」。如果提示没有权限，打开终端执行：
   chmod +x 启动.command   （在文件夹路径下执行一次即可）

4. 若提示"无法打开，因为它来自身份不明的开发者"：
   系统设置 → 隐私与安全性 → 点「仍要打开」。

5. 首次启动会自动安装依赖（需要联网），然后自动打开浏览器。

二、填写密钥（首次打开浏览器时）

按向导填写你自己的两个密钥：
- DeepSeek：在 platform.deepseek.com/api_keys 获取（负责写作生成）
- 阿里云百炼：在 bailian.console.aliyun.com 获取（负责文献检索）
费用由你自己的账户承担，产品不经手；密钥只存在本机。

三、日常使用

- Windows 双击「启动.bat」；macOS 双击「启动.command」。
  关闭启动窗口即退出程序。
- 论文数据保存在本文件夹的 data 目录，复制整个文件夹即可备份。
- 常用顺序：文件入库（传文献）→ 章节设计（定结构）→
  大纲设计（逐节方案）→ 章节创作（写正文）→ 导出。

四、常见问题

- 提示找不到 Python / 版本过低：回到第一步安装 Python 3.11+。
- 依赖安装失败：检查网络后重新双击启动脚本（会继续安装）。
- 密钥保存失败（配置文件无法写入）：检查是否在压缩包预览里
  直接运行了程序，或文件夹被放在了只读位置（如系统目录、
  网盘锁定目录）。把整个文件夹移到普通位置重新解压再运行。
- 忘记密钥在哪里填：右上角「设置 → 模型与 API Key」。
- 出错时：记下界面上的错误码与诊断号，在「设置 → 日志与诊断」
  导出诊断包发给管理员。
"""


def copytree(src: Path, dst: Path, skip=("__pycache__", "node_modules", ".history", "smoke-shots")) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in skip or item.name.startswith(".git"):
            continue
        if item.is_dir():
            copytree(item, dst / item.name, skip)
        else:
            shutil.copy2(item, dst / item.name)


def main() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "data").mkdir(parents=True)

    # 1. 代码与文档
    copytree(ROOT / "backend", STAGE / "backend")
    copytree(ROOT / "shared", STAGE / "shared")
    copytree(ROOT / "frontend" / "dist", STAGE / "frontend" / "dist")
    for f in ("启动.bat", "启动.command", "requirements.txt", "pyproject.toml",
              "README.md", "产品文档.md"):
        src = ROOT / f
        if src.exists():
            shutil.copy2(src, STAGE / f)
    # 2. 全新配置（密钥置空，学生首次打开进入向导）
    import json
    (STAGE / "data" / "config.json").write_text(
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    # 3. 安装说明
    (STAGE / "安装说明.txt").write_text(INSTALL_GUIDE, encoding="utf-8")

    # 4. 压缩（保留 启动.command 的 Unix 可执行位，否则 macOS 解压后双击无权限）
    OUT.mkdir(parents=True, exist_ok=True)
    zip_path = OUT / f"{NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(STAGE.rglob("*")):
            if not p.is_file():
                continue
            arc = p.relative_to(OUT)
            if p.name in ("启动.command",) or p.name.endswith(".sh"):
                info = zipfile.ZipInfo.from_file(p, str(arc))
                info.external_attr = (0o100755 & 0xFFFF) << 16  # -rwxr-xr-x
                with open(p, "rb") as f:
                    zf.writestr(info, f.read())
            else:
                zf.write(p, arc)
    size_mb = zip_path.stat().st_size / 1048576
    print(f"打包完成：{zip_path}")
    print(f"大小：{size_mb:.1f} MB")
    print("内容：后端 + 预构建前端 + 启动脚本 + 全新配置（不含密钥）+ 安装说明")


if __name__ == "__main__":
    main()
