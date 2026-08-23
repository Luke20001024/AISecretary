# 10 · “她理解的我”到认知地图的投影桥

> 状态：前端交互合同已落地，后端 V2 接线基线
>
> 日期：2026-08-23
>
> 适用页面：`chrome-newtab/dashboard.html`

## 1. 目标

第三层 Self Insight 需要与第一层主题地图建立一条可追溯、低干扰的阅读路径：

1. 默认地图只显示地形、主题峰、记忆点和已确认的主题关系
2. 用户主动点击一条“她理解的我”后，前端临时显影支撑它的主题峰
3. 前端用一组无文字等高线把这些主题峰围成一条“山系”
4. 详情抽屉继续展示这条理解的陈述、边界和来源主题
5. 再点同一条理解、点击地图空白或按 `Esc`，地图恢复默认状态

这条山系属于只读 Projection 的视觉表达。它不创建 Relation，不修改 Theme 或 SelfInsight，也不写回正式对象

## 2. 唯一语义来源

前端只允许读取 `SelfInsightRevision.theme_refs`。下列方式全部禁止：

- 按地图距离推测一条理解关联哪些主题
- 对标题、陈述或标签做前端关键词匹配
- 用记录数量、峰高或近期变化补齐缺失关系
- 让浏览器调用模型重新判断关系

过渡期 fixture 使用 `portrait[].themeIds`。V2 adapter 负责把精确的 `theme_refs[].id` 映射为该字段；fixture 字段不进入正式持久化 Schema

## 3. SelfProjection 最小合同

`SelfProjection` 中每条可展示理解至少提供：

```json
{
  "insight_ref": {
    "id": "sin_0123456789abcdef01234567",
    "revision": 2,
    "sha256": "<sha256>"
  },
  "title": "可验证的结论更值得长期保留",
  "statement": "在重要判断中，会优先保留可追溯依据和允许结论被修订的空间",
  "maturity": "observed",
  "confirmation": "observed",
  "theme_refs": [
    { "id": "thm_a", "revision": 3, "sha256": "<sha256>" },
    { "id": "thm_b", "revision": 5, "sha256": "<sha256>" }
  ],
  "detail_ref": {
    "projection_id": "self_insight_detail_sin_0123456789abcdef01234567",
    "sha256": "<sha256>"
  }
}
```

要求：

- `insight_ref`、`theme_refs` 和 `detail_ref` 都必须是当前 sealed bundle 内的精确引用
- `theme_refs` 至少包含两个不同的 active / tension Theme
- 顺序由 Projector 确定，建议按稳定 ID 排序；前端不得依赖数组顺序表达强弱
- `restricted` 或不允许在当前 Home 中显示的理解不得进入可点击列表
- `SelfInsightDetailProjection` 必须能沿 `theme_refs` 回到每个 Theme 的形成依据

## 4. Projector 与 adapter 职责

后端 Projector：

1. 以 bundle 的 `as_of` 截取可见的 SelfInsight 和 Theme heads
2. 解析 SelfInsight 的精确 `theme_refs`
3. 过滤已 tombstone、不可见或不属于当前 bundle 的 Theme
4. 少于两个有效 Theme 时保留详情入口，但给该条理解标记 `map_bridge_status: "insufficient_themes"`
5. 生成可重放、确定排序的 `SelfProjection`
6. 将 Self、Landscape、DetailIndex 与详情文件放入同一个 sealed bundle

V2 → V1 adapter：

```text
SelfProjection.insights[].insight_ref.id       → portrait[].id
SelfProjection.insights[].theme_refs[].id      → portrait[].themeIds[]
SelfProjection.insights[].maturity             → portrait[].maturity
SelfInsightDetailProjection.statement/boundary → 当前详情抽屉字段
```

adapter 只做字段兼容与引用校验，不生成山系坐标

## 5. 前端投影流程

```text
用户点击 Self Insight
  ↓
读取该条显式 theme IDs
  ↓
将 theme IDs 映射到当前 LandscapeProjection.peaks
  ↓
有效峰少于 2 个 ──→ 只打开详情，不画山系
  ↓
围绕有效峰生成少量支撑点
  ↓
确定性凸包 + 平滑闭合路径
  ↓
绘制 1 个淡色填充 + 3 条无文字等高线
  ↓
突出来源主题峰，压低无关峰与无关主题关系
  ↓
打开 SelfInsight 详情抽屉
  ↓
按 insight_ref.id 定位同一条详情，滚入抽屉视野并播放一次定位提示
```

实现位置：

- 数据适配：`chrome-newtab/cognitive-demo-fixture.js`
- 山系几何：`chrome-newtab/dashboard.js` 的 `cognitiveInsightRangeMarkup`
- 选择状态：`cognitiveMapInteractionState.insightId`
- 视觉状态：`has-cognitive-insight`、`is-cognitive-insight-active`、`is-insight-related`
- 样式：`chrome-newtab/dashboard.css`

前端几何是可删除、可重建的显示结果，不进入 Projection 文件。对同一组峰坐标与同一条 Self Insight，浏览器必须生成相同路径

### 5.1 抽屉定位反馈

外侧卡片与抽屉条目必须共享同一个稳定身份：

```text
SelfProjection.insights[].insight_ref.id
  → 外侧 data-cognitive-portrait-id
  → 抽屉 data-cognitive-portrait-drawer-id
```

点击外侧条目后，抽屉可以渲染全部长期理解，但前端必须：

1. 用精确 ID 找到被点击条目
2. 将该条目标记为 `aria-current="true"`
3. 将目标滚动到抽屉可视区中央并程序化聚焦
4. 播放一次约 1.2 秒的克制定位提示，随后自动移除提示 class
5. 在 `prefers-reduced-motion: reduce` 下取消闪烁动画，只保留短暂静态底色

定位提示只用于建立“外侧点击项 → 抽屉详情项”的视觉对应，不改变任何业务状态，也不需要后端增加接口。若 ID 在详情列表中无法命中，抽屉从顶部打开并把焦点交给关闭按钮

## 6. 交互状态机

| 当前状态 | 用户动作 | 结果 |
|---|---|---|
| 默认 | 悬停 Self Insight | 卡片自身反馈，地图不变化 |
| 默认 | 点击 Self Insight A | A 的山系显影，A 进入选中态，打开详情 |
| 抽屉打开中 | 从 A 进入 | A 自动滚入视野并播放一次定位提示 |
| A 已选中 | 关闭详情 | 山系保留，便于对照地图 |
| A 已选中 | 再点 A | 清除山系与选中态 |
| A 已选中 | 点击 Self Insight B | 切换到 B 的山系 |
| A 已选中 | 点击来源主题 | 清除 A，进入该主题的第二层形成依据 |
| 任意固定态 | 点击地图空白或按 `Esc` | 回到默认地图 |

主题固定态与 Self Insight 固定态互斥。第三层阅读态不会展开 Theme → Memory 的证据支线；用户进入具体主题后才展示第二层证据

## 7. 失败与降级

| 情况 | 前端行为 | 后端检查 |
|---|---|---|
| `theme_refs` 缺失 | 详情可打开，地图不显影 | Projection 合同测试失败或输出 `insufficient_themes` |
| Theme 不在当前 Landscape | 忽略该 ref；有效峰不足时不画 | 检查 bundle 内跨文件引用 |
| Self 与 Landscape 版本不同 | 继续使用上一份完整合法 bundle | publication / current pointer 回退 |
| 详情文件损坏 | 不打开损坏详情，保留上一份合法页面 | DetailIndex hash 校验失败 |
| 前端几何失败 | 清除临时山系，基础地图仍可使用 | 记录本地可观测错误，不写正式对象 |

## 8. 性能边界

每条理解通常关联 2–4 个主题。当前算法为每个峰生成 10 个支撑点，再计算凸包，复杂度约为 `O(k log k)`，其中 `k <= 40`。选择态只修改现有 SVG class，不触发模型、文件写入或后端请求

后端新增成本限于 SelfProjection 的精确引用校验和序列化。山系路径、颜色、透明度和选中态均由浏览器负责

## 9. 验收门

- 默认截图看不到任何 Self Insight 山系或理解文案
- 悬停理解卡片不改变地图
- 点击每条理解只突出其 `theme_refs` 对应峰
- 从外侧点击第 N 条理解后，抽屉必须滚到相同 `insight_ref.id` 的条目并只提示一次
- 抽屉定位动画结束后不得残留动画 class；减少动态效果时不得播放闪烁
- 地图中不出现 Self Insight 标题、Statement 或标签
- 第三层阅读态继续隐藏 Theme → Memory 证据支线
- 关闭详情后山系保留；同条点击、空白点击和 `Esc` 均可清除
- 点击来源主题会进入第二层详情，并退出第三层山系
- 少于两个有效 `theme_refs` 时不绘制山系
- 同一 bundle 重放得到相同山系路径
- 前端过程不产生模型调用、正式对象或用户 action
