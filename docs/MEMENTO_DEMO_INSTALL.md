# Memento 4.0 Preview · Chrome 新建页 Demo 安装说明

当前包用于体验 Memento 的前端产品形态。它内置固定的 20 天、261 条合成记录汇总与 6 条代表记录，不读取真实目录，不调用模型，也不创建自动化任务。

## 安装

1. 从 [v0.10.0 Preview Release](https://github.com/Luke20001024/Memento/releases/tag/v0.10.0) 下载 `Memento-New-Tab-Demo-v0.10.0.zip` 与同名 `.sha256` 文件。
2. 在下载目录校验：

   ```bash
   shasum -a 256 -c Memento-New-Tab-Demo-v0.10.0.zip.sha256
   ```

   只有输出 `OK` 才继续。
3. 解压 ZIP。Chrome 打开 `chrome://extensions`，在右上角开启“开发者模式”。
4. 选择“加载已解压的扩展程序”，选中解压目录内的 `chrome-newtab-demo` 文件夹。
5. 新开一个标签页。你会看到固定的“时间河、认知地景、她理解的我”演示数据。

## 体验边界

- 这份 Demo 可安装为新建页扩展，全部数据固定在扩展包中，不会写入 `~/AISecretary`。
- 它不展示真实个人记录，关闭、移除扩展或重新安装不会影响任何本地 Markdown。
- 它不联网，不请求目录、相机、麦克风或其他系统权限。
- Chrome 的开发者模式和“加载已解压的扩展程序”需要用户亲自操作；这是 Chrome 对本地扩展的要求。
- 历史 `v0.8.9` macOS 安装包保留旧照片、Daily Review 与基础记录能力，只用于历史回溯。

## 卸载

打开 `chrome://extensions`，找到 **Memento 4.0 Preview**，选择“移除”。Chrome 会恢复默认的新建页。
