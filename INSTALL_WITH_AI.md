# Memento 4.0 Preview · AI 安装指南

本指南只对应 `v0.10.1 Preview` 的 Chrome 新建页 Demo。它使用 20 天、261 条固定合成记录汇总与 6 条代表记录，供用户体验时间河、认知地景与“她理解的我”。

## 发布边界

- Demo 默认直接进入固定数据模式，不自动读取 `~/AISecretary` 或其他真实目录。
- Demo 不调用模型、不联网、不创建 LaunchAgent、定时任务或后台 Worker。
- Demo 不采集照片，不产生 Daily Review，也不写入任何 Markdown。
- Chrome 需要用户手动开启“开发者模式”并加载扩展；AI 不代替用户完成该授权。

## 安装步骤

1. 下载 [v0.10.1 Preview](https://github.com/Luke20001024/Memento/releases/tag/v0.10.1) 中的 `Memento-New-Tab-Demo-v0.10.1.zip` 与同名 `.sha256`。
2. 用户在下载目录执行：

   ```bash
   shasum -a 256 -c Memento-New-Tab-Demo-v0.10.1.zip.sha256
   ```

   输出 `OK` 后继续。
3. 解压 ZIP。Chrome 打开 `chrome://extensions`，开启右上角“开发者模式”。
4. 选择“加载已解压的扩展程序”，选中解压目录中的 `Memento-Demo` 文件夹。
5. 新开标签页，确认显示固定数据预览、“261 条记录”汇总和“6 条代表记录”。

## 验收口径

- **文件已下载**：ZIP 和 SHA-256 文件存在，校验通过。
- **入口已启用**：扩展显示在 `chrome://extensions`，新标签页打开 Memento。
- **Demo 已验证**：主题可打开详情抽屉，Esc 可关闭抽屉。
- **仍需用户操作**：Chrome 的开发者模式、加载目录和移除扩展。

## 卸载

用户在 `chrome://extensions` 中移除 **Memento**。该操作不会影响任何本地 Markdown 或 `~/AISecretary`。

## 历史版本提醒

`v0.8.9` 是历史 macOS 安装包，包含旧的基础记录能力，也可能带有每日第一帧照片与 Daily Review 等旧行为。它保留给历史回溯；当前 Preview 的安装路径统一使用 v0.10.1。
