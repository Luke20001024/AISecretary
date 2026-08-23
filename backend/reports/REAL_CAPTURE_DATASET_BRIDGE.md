# 真实快捷键测试集旁路

## 结论

Memento 当前的 macOS Services 与 `⌃1–⌃5` 继续承担全部采集操作。后端新增的旁路只读取已经完整落盘的记录，并将本轮新增记录与附件复制到隔离测试集

```text
现有快捷键
  → ~/AISecretary/YYYY-MM-DD.md + assets/
  → 只读增量收集器
  → backend/.capture-dataset/
  → 待用户标注的数据集草案
```

没有新增产品页面，也没有修改安装脚本、Service 或快捷键

## 使用流程

### 1. 建立本轮基线

```bash
cd backend
PYTHONPATH=src python eval/collect_existing_captures.py \
  --workspace .capture-dataset \
  start \
  --source "$HOME/AISecretary" \
  --source-label "example AISecretary"
```

如需把开始会话前已经记录的当天样本一并纳入，可添加：

```text
--include-date 2026-08-23
```

默认只检查会话启动当天及后续日期的日记文件，不扫描历史附件。确需从更早日期取样时，可以显式传入 `--from-date YYYY-MM-DD`

### 2. 继续使用现有能力

- `⌃1` 直接记录
- `⌃2` 加备注
- `⌃3` 选标签
- `⌃4` 截图与本地 OCR
- `⌃5` 语音与本地转写

同时可以打开只读监看页，查看已收到的真实记录、结构覆盖和待补清单：

```bash
PYTHONPATH=src python eval/run_capture_monitor.py \
  --workspace .capture-dataset --port 4317
```

监看页每 5 秒调用一次增量收取，只展示隔离测试集中的摘要。页面没有截图、录音或文本输入控件

### 3. 收取新增记录

```bash
PYTHONPATH=src python eval/collect_existing_captures.py \
  --workspace .capture-dataset collect
```

### 4. 导出待标注草案

```bash
PYTHONPATH=src python eval/collect_existing_captures.py \
  --workspace .capture-dataset export
```

## 保留内容

每条样本保存：

- 完整原始 Markdown 记录块
- 日期、时间、来源应用、标签与备注
- 原文 hash 与日文件快照 hash
- 截图或语音附件的 MIME、大小与 hash
- 内容寻址的附件副本

`expected` 字段保持空白，后续由用户确认内容角色、处理路由、长期记忆价值、允许形成的对象和禁止输出

## 安全边界

- 来源 Vault 只读打开，读取前后复核文件身份与时间戳
- 只接受 Vault 根目录的日期文件和 `assets/` 下单层附件路径
- symlink、外部 owner、共享权限、硬链接、超限文件和不完整记录全部停止导入
- 不调用 Provider
- 不写正式 Vault
- 测试集目录为 owner-only，并由 Git 忽略
