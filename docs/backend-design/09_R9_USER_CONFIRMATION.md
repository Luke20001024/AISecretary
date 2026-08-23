# 09 · R9 真实数据影子评测确认单

> 当前状态：等待用户确认
>
> 本页只定义一次只读影子评测的授权范围，不会读取真实 Vault，不会调用产品 Provider，也不会开启正式写入

## 1. 这次评测会做什么

```text
用户确认一份精确配置
→ 对授权目录创建密封只读快照
→ 已授权 worker 生成候选 ProjectionBundle 与逐例 observation
→ 本地评测误连、漏连、过度推断、停止质量、证据回溯、成本与延迟
→ 将 consent、plan、snapshot ref、observation、candidate 与 report 原子封存
→ 根据预注册阈值得到 passed 或 failed
```

整个过程的 `formal_write_count` 固定为 `0`。原始 Vault 只以只读方式打开，复制前后都要复核 hash。候选结果只能写入快照所在的隔离 shadow workspace

## 2. 需要确认的六组配置

### A · 数据范围

| 配置 | 建议值 | 含义 |
|---|---|---|
| source label | 由用户填写一个可识别名称 | 绑定这一次允许读取的 Vault 来源 |
| source root | 由用户填写规范绝对路径 | 精确绑定被授权的本地目录 |
| snapshot kind | `real_vault_snapshot` | 真实数据只进入只读快照 |
| allowed suffixes | `.json`、`.md`、`.txt` | 只复制这些文件类型 |
| read only | `true` | 禁止对原始目录写入 |

真实目录路径只保存在本地 consent 与密封影子证据中。快照 manifest 保存路径 hash；创建快照时会核对命令传入路径解析后的真实目录、consent 路径和 manifest hash

### B · 质量阈值

| 指标 | 建议阈值 | 判定含义 |
|---|---:|---|
| 误连率 | ≤ 10% | 不应关联的记忆被错误连接 |
| 漏连率 | ≤ 10% | 应该建立的关系没有建立 |
| 过度推断率 | ≤ 5% | 证据无法支持的判断被生成 |
| 停止 F1 | ≥ 90% | 该停下确认时能停下，不该停时能继续 |
| 停止样本数 | ≥ 3 | 停止质量至少有三个可评分案例 |
| 来源引用有效率 | 100% | 每条引用都能回到来源 |
| Self 全链路回溯率 | 100% | SelfInsight 能回到 Theme、MemoryAtom 与 Source |
| 资源误写为用户观点 | 0% | 网页内容不能被当作用户判断 |
| 旧对象错误复活 | 0 | 撤回或过期对象不能重新进入当前理解 |
| V1 adapter 通过率 | 100% | 候选投影继续满足现有前端合同 |
| 原文 hash 稳定率 | 100% | 运行前后来源字节保持一致 |
| 本次成本上限 | ≤ 0.50 USD | Provider 实际成本硬上限 |
| P95 延迟 | ≤ 12 秒 | 逐例端到端延迟门槛 |

这些数值已写入 `backend/eval/consent-template.json`，当前仍是建议草案

### C · Theme 与 SelfInsight 成形条件

当前实现只支持以下固定 Gate，确认后才能作为本轮评测标准：

- Theme：至少 2 条 active MemoryAtom，覆盖至少 2 个不同日期，并具有正式 Relation
- Theme：连续 30 天没有有效证据后进入 dormant 判断
- SelfInsight：至少 2 个不同的有效 Theme 共同支持
- SelfInsight：每个 Theme 至少提供 2 条可回溯证据

如果希望修改这些 Gate，需要先更新实现、Schema 与合成测试，再生成新的 consent

### D · 敏感信息策略

- 敏感推断：停下并等待用户确认
- 外部 Context：默认排除未确认的敏感理解
- 用户修订、限定和撤回：优先于 Agent 旧结论

### E · Agent 运行频率

| Agent | 建议频率 |
|---|---|
| Capture Understanding | 每次输入触发 |
| Record Interpreter | 每次需要理解的记录触发 |
| Daily Integrator | 每天 22:00 |
| Theme Synthesizer | 每周一 08:00 |
| Self Understanding | 每周日 09:00 |
| Context Router | 每次外部 Context 请求触发 |

频率只用于冻结本轮评测配置，不会在 R9 自动安装系统定时任务

### F · Provider、模型与预算

下面三项需要用户最终填写：

1. Provider 名称
2. 产品运行模型的精确名称
3. 是否接受建议预算：最多 100,000 prompt tokens、30,000 completion tokens、0.50 USD、单例最长 12 秒

Prompt 与 Policy 版本已在模板中逐项固定。R9 CLI 不发起 Provider 请求；获得同一 consent 授权的 `ShadowProducer` worker 只读取预登记 case set 声明的快照文件，生成无 gold 的 work product 和候选 Bundle。评估器随后独立合并 case set 的标准答案

## 3. 确认后生成什么

### 3.1 确认前先运行无副作用预检

填写 reviewed draft 和 12—15 条 case draft 后，可以先一次检查所有启动阻塞项：

```bash
cd backend
PYTHONPATH=src /opt/anaconda3/bin/python3.12 eval/run_shadow.py preflight \
  --reviewed-draft /path/to/private-r9/reviewed-consent.json \
  --source /absolute/path/to/authorized-vault \
  --cases /path/to/private-r9/cases.json \
  --validation-at 2026-08-23T12:00:00+08:00
```

预检检查以下内容：

- 数据范围、阈值、Gate、敏感策略、Agent 频率、Provider、模型和预算是否满足冻结合同
- 命令路径与草案中的规范绝对路径是否完全一致
- 来源目录是否为当前用户持有的真实目录，是否含 symlink 或特殊文件
- 允许后缀下的文件数量、总字节数和 case 引用是否存在
- 场景数是否为 12—15，ID 与输入是否唯一，停止正例是否达到阈值，全部质量检查是否有分母

输出中的 `configuration_ready: true` 只表示配置可进入明确确认。`authorization_issued` 固定为 `false`。预检不读取来源文件正文、不创建快照、不调用 Provider、不写任何文件；返回码 `0` 表示配置就绪，`1` 表示仍有阻塞项

### 3.2 用户确认后签发正式 consent

用户确认后，使用 reviewed draft 生成正式 `shadow-consent-v1`：

```bash
cd backend
mkdir -m 700 /path/to/private-r9
cp eval/consent-template.json /path/to/private-r9/reviewed-consent.json
# 填写 source label、Provider、模型与确认时间前，保持 pending 状态

PYTHONPATH=src /opt/anaconda3/bin/python3.12 eval/run_shadow.py consent \
  --reviewed-draft /path/to/private-r9/reviewed-consent.json \
  --confirmed-at 2026-08-23T12:00:00+08:00 \
  --output-file /path/to/private-r9/consent.json
```

正式 consent 会产生确定性的 `consent_id` 与 SHA-256。后续整条引用链必须完全一致：

```text
consent.json
  ├─ snapshot-manifest.authorization_ref + source_root_sha256
  ├─ case-set.snapshot_ref + exact input hashes
  ├─ plan.user_confirmation.confirmation_ref
  ├─ plan.consent_ref + case_set_ref
  ├─ work-product.plan_ref + case_set_ref + snapshot_ref + candidate hash
  └─ sealed report.consent_ref + case_set_ref + work_product_ref
```

任何配置变化都必须生成新的 consent 和新的 plan，旧授权不会自动扩大

## 4. 当前仍未发生的动作

- 真实 Vault 读取：0
- 真实 Vault 写入：0
- 产品 Provider 调用：0
- 真实影子质量结论：无
- 正式 revision 写入：0
- R10 前后端合码：未开始

R9 只有在用户明确确认本页六组配置，并完成真实只读影子 run 后才能关闭

## 5. 可以直接回复的最小确认模板

复制下方内容并填写三个空项即可继续。“接受建议值”会精确采用本文 B–E 中的阈值、Gate、敏感策略和频率，不会扩大数据范围或开启正式写入

```text
我确认运行 Memento Backend V2 的 B6 只读影子评测

数据来源名称：<填写>
只读源目录绝对路径：<填写>
允许后缀：.md .json .txt

质量阈值、Theme / SelfInsight Gate、敏感策略、Agent 频率：接受 09_R9_USER_CONFIRMATION.md 建议值

Provider：<填写>
精确模型名称：<填写>
预算：最多 100,000 prompt tokens、30,000 completion tokens、0.50 USD、单例 12 秒

真实 Vault 写入：不允许
候选产物只写入隔离 shadow workspace：允许
评测失败后自动进入 B7：不允许
```

12–15 条匿名化真实案例可以与这段确认同时提供，也可以在快照建立前单独整理。每例仍使用本文已冻结的六字段标注格式
