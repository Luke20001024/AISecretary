# Memento

> 一份从主动记录延伸到可追溯理解的个人认知界面。

**当前版本：v0.10.1 Preview**

Memento 4.0 的公开预览以 20 天、261 条合成记录呈现“时间河 → 认知地景 → 她理解的我”。在线界面于 **2026-09-06** 更新，包含清晰的主页导航、默认按标签打开的记录轨迹，以及标签/时间切换和详情查阅。

## 两种体验方式

1. **在线阅读说明书与预览**：<https://luke20001024.github.io/Memento/>。GitHub Pages 根页是本次发行的说明书，内嵌可操作的完整 Demo；也可[单独打开同一个 Demo](https://luke20001024.github.io/Memento/demo/dashboard.html)。
2. **安装 Chrome 新建页 Demo**：从 [v0.10.1 Preview Release](https://github.com/Luke20001024/Memento/releases/tag/v0.10.1) 下载 `Memento-New-Tab-Demo-v0.10.1.zip`，在 `chrome://extensions` 开启开发者模式后，加载其中的 `Memento-Demo` 文件夹。详见 [Demo 安装说明](docs/MEMENTO_DEMO_INSTALL.md)。

## Preview 边界

- 固定的 20 天、261 条合成记录；不包含真实个人数据。
- 公开预览锁定示例数据，不连接本机后端、不读取个人目录、不写入 Markdown，也不调用模型。
- 不创建定时任务、后台 Worker、每日照片或 Daily Review。
- 地图主题可打开详情抽屉；固定数据不会因为新记录自动变化。

快捷键采集、Apple OCR、语音及真实 AI 整理需要本地安装版；公开预览的 21:00 自动整理控件会说明当前不可用。本次更新仅发布网页，已有的 v0.10.1 下载包保持原样。完整的产品状态与技术衔接见 [产品终态说明](docs/MEMENTO_PRODUCT_FINAL_STATE.md) 和 [认知秘书技术设计](docs/cognitive-secretary-mvp/TECHNICAL_DESIGN.md)。

## 历史入口

- [打开 Memento 3.0 产品故事与可操作演示](https://luke20001024.github.io/Memento/Memento-3.0.html?v=20260720)
- [产品命题：一种轻度理解](https://luke20001024.github.io/Memento/Memento-Product-Thesis.html)
- [产品演进规划](docs/PRODUCT_ROADMAP.md)

历史 `v0.8.9` macOS 安装包保留给既有用户回溯。其功能边界包含旧基础记录流程，以及每日第一帧照片和 Daily Review 等旧能力；当前 Preview 请使用 v0.10.1 的安装路径。历史下载地址：<https://github.com/Luke20001024/Memento/releases/tag/v0.8.9>。

## 仓库结构

```text
chrome-newtab/        完整 Demo 的唯一前端源
docs/demo/            由唯一源生成；独立预览、说明书 iframe 与安装 ZIP 共用
docs/                 GitHub Pages 说明书与产品文档
backend/              后端领域模型与离线测试基础
context-agent/        历史认知运行时与研究代码，当前 Preview 不执行
scripts/              预览构建与发布脚本
```

## 本地验证

```bash
python3 scripts/build_standalone_preview.py
python3 -m unittest tests/test_standalone_preview.py
bash tests/test_newtab_demo_package.sh
bash tests/test_release_contract.sh
```

发布时从已提交、已打 tag 的 HEAD 构建：

```bash
MEMENTO_REQUIRE_RELEASE_TAG=1 ./scripts/package_newtab_demo.sh
```

这条命令从已提交的 `docs/demo/` 逐文件构建安装包；发布测试会验证它与 `chrome-newtab/` 以及线上 GitHub Pages 的 SHA-256 完全一致。
