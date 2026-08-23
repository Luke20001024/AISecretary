# R0 · Backend V2 基线审计

> 日期：2026-08-22
>
> 状态：完成

## 1. 本轮目标

在不触碰现有前端和真实 Vault 的条件下，建立独立 `backend/` package、测试入口和后续实现边界

## 2. 受保护的现有工作树

审计时工作树已经包含用户正在进行的前端、产品说明、V1 后端和安装脚本改动。这些内容全部视为用户资产

后端开发阶段保持不动：

```text
docs/index.html
docs/assets/product/**
docs/Memento-Cognitive-Home-Standalone.html
chrome-newtab/dashboard.html
chrome-newtab/dashboard.css
chrome-newtab/dashboard.js
chrome-newtab/cognitive-demo-fixture.js
context-agent/**
```

本轮没有创建 git commit，也没有移动、清理或覆盖既有文件

## 3. 运行环境

| 项目 | 审计结果 |
|---|---|
| 系统 Python | `/usr/bin/python3` · Python 3.9.6 |
| 系统 Python pytest | 未安装 |
| 可用 pytest | `/opt/anaconda3/bin/pytest` · pytest 7.4.4 |
| 可用 mypy | `/opt/anaconda3/bin/mypy` · mypy 1.10.0 |
| ruff | 当前 PATH 中未安装 |
| Node.js | v24.18.0 |
| npm | 11.16.0 |

R0 采用 `/opt/anaconda3/bin/python -m pytest` 和 `/opt/anaconda3/bin/mypy`

## 4. V1 可复用模块

| 模块 | V2 使用方式 | 可复用内容 |
|---|---|---|
| `context-agent/cognitive_v1.py` | adapter / 提取 | strict contract、ID、ObjectRef、SourceSpan、canonical hash |
| `context-agent/cognitive_store_v1.py` | adapter / 提取 | 原始记录解析、稳定 record identity、RecordStore |
| `context-agent/cognitive_bundle_store_v1.py` | 提取机制 | staging、原子 bundle、head index、revision、恢复 |
| `context-agent/cognitive_actions_v1.py` | 提取机制 | append-only action、CAS、用户 revision、tombstone |
| `context-agent/cognitive_runtime_v1.py` | 提取机制 | bounded action loop、budget、attempt 与 terminal state |
| `context-agent/deepseek_provider.py` | Provider adapter | provider 调用、错误与 usage 收口 |
| `context-agent/cognitive_projection_v1.py` | 行为参考 | 确定性 Projection 和稳定地形布局 |

V2 不直接复制整个 V1 模块。每次只提取已经被合同测试覆盖的稳定机制，并保持 V1 路径可继续运行

## 5. 当前前端合同

前端正式 validator 位于：

```text
chrome-newtab/cognitive-home-library.js
```

已确认的入口：

- `validateLandscapeSnapshot`
- `validateHomeProjection`
- `validateProjectionPair`
- `validateProjectionAuthority`

当前确定性演示数据位于：

```text
chrome-newtab/cognitive-demo-fixture.js
```

fixture 已覆盖：

- 20 天、261 条合成记录
- 6 个主题及子峰
- source、receipt、memory、relation、understanding 的引用链
- 第三层 `portrait` 临时投影
- revision、tension 与多种处理状态

V2 在 R3 中提供 V2 → V1 adapter，先让新正式对象通过现有 validator，再进入 V2 原生前端合同

## 6. R0 交付文件

```text
backend/pyproject.toml
backend/README.md
backend/src/memento_backend/__init__.py
backend/tests/conftest.py
backend/tests/test_bootstrap.py
backend/reports/R0_BASELINE.md
```

## 7. 完成状态

- 文件已创建：是
- 合同测试通过：是，`backend/tests/test_bootstrap.py` 1 passed
- 类型检查通过：是，mypy 0 issues
- 回归测试通过：是，现有 homepage contract 与 20 天 demo fixture 均通过
- 模型评测通过：未运行
- 影子运行通过：未运行
- 真实写入启用：否

## 8. 下一轮入口

R1 只建立基础 domain primitives 与输入合同：

```text
ID
ObjectRef
SourceSpan
Revision metadata
SourceRecordRevision
CaptureDecisionRevision
ResourceCardRevision
ReadLaterIntentRevision
```

R1 继续保持 0 模型、0 正式 Vault 写入和 0 前端修改

## 9. 测试证据

```text
/opt/anaconda3/bin/python -m pytest backend/tests -q
1 passed

cd backend && /opt/anaconda3/bin/mypy src tests
Success: no issues found

node tests/test_cognitive_home_library.js
cognitive-home-library contract tests passed

node tests/test_cognitive_demo_fixture.js
cognitive demo fixture tests passed
```
