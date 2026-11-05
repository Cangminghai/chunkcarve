# 贡献指南

感谢参与 ChunkCarve（取方）。仓库标识名为 `chunkcarve`。开始改动前，请先查看 README、依赖许可和本指南；项目只从用户本机读取 Minecraft 资源，不要把游戏文件或个人存档提交到仓库。

## 开发环境

```bash
git clone <repository-url> chunkcarve
cd chunkcarve
python -m venv .venv
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m mcmap.server --world <存档目录> --jar <版本jar> --mods <模组目录>
```

> 不需要真实存档也能跑单元测试（用合成数据）。手动验证渲染时建议自备一个小存档。

## 提交前检查

- `python -m pytest -q` 全绿
- `node --check web/hub.js && node --check web/ui.js && node --check web/app.js && node --check web/worker3d.js`
- 更新 README、导出指南或相关测试，保持功能说明与实现一致
- 不要提交 `data/`、`out/`、`config.json`、`projects.json`、`desktop_error.log` 或 Minecraft 存档

## 代码约定

- Python：3.11–3.14；函数短小；路径一律走 `mcmap/config.py`，**不得新增机器硬编码路径**
- 前端：原生 JS（无框架）；deepslate 渲染只允许在 `worker3d.js`（主线程只做相机/输入/overlay）
- 批处理文件（.bat）：**ASCII 注释 + CRLF 换行**（UTF-8+LF 会被 Windows 解释器吞字符）

## Issue / PR

- Bug 报告请附：复现步骤、系统与 Python 版本、相关日志；提交前移除存档名、用户名和本机绝对路径等个人信息
- PR 请保持单一目的；涉及行为变更时同步更新 README、相关文档与测试
- 安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告，不要创建公开 Issue

## 合规提醒

请勿提交任何 Minecraft 官方资源（贴图/模型/音效）或他人存档。所有游戏资源应在运行时从用户本机读取。
