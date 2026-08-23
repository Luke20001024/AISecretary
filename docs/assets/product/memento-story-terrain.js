// Extracted presentation renderer for Memento 4.0
// Geometry and deterministic six-theme fixture follow chrome-newtab/dashboard.js
// and chrome-newtab/cognitive-demo-fixture.js
(function renderMementoStoryTerrain() {
  'use strict'

  const svg = document.getElementById('story-terrain')
  if (!svg) return

  const NS = 'http://www.w3.org/2000/svg'
  const layers = Object.fromEntries(
    Array.from(svg.querySelectorAll('[data-story-terrain]')).map((node) => [node.dataset.storyTerrain, node])
  )

  const themes = [
    {
      id: 'product', title: '产品决策', x: .15, y: .26, elevation: .94, evidence: 26, boundary: 1,
      spreadX: 76, spreadY: 38, angle: -12,
      subpeaks: [
        { x: .035, y: .095, elevation: .53, spreadX: 46, spreadY: 23 },
        { x: .285, y: .405, elevation: .64, spreadX: 58, spreadY: 28 }
      ]
    },
    {
      id: 'evidence', title: '证据优先', x: .43, y: .17, elevation: .91, evidence: 28, boundary: 2,
      spreadX: 82, spreadY: 35, angle: 8,
      subpeaks: [
        { x: .305, y: .335, elevation: .60, spreadX: 55, spreadY: 26 },
        { x: .605, y: .045, elevation: .42, spreadX: 38, spreadY: 21 }
      ]
    },
    {
      id: 'accumulation', title: '长期积累', x: .82, y: .25, elevation: .88, evidence: 30, boundary: 1,
      spreadX: 86, spreadY: 40, angle: 14,
      subpeaks: [
        { x: .675, y: .425, elevation: .62, spreadX: 56, spreadY: 30 },
        { x: .965, y: .105, elevation: .36, spreadX: 34, spreadY: 19 }
      ]
    },
    {
      id: 'collaboration', title: '协作边界', x: .87, y: .69, elevation: .82, evidence: 32, boundary: 2,
      spreadX: 80, spreadY: 40, angle: -18,
      subpeaks: [
        { x: .725, y: .595, elevation: .58, spreadX: 52, spreadY: 29 },
        { x: .965, y: .885, elevation: .40, spreadX: 36, spreadY: 21 }
      ]
    },
    {
      id: 'research', title: '研究方法', x: .53, y: .81, elevation: .86, evidence: 26, boundary: 1, recent: true,
      spreadX: 84, spreadY: 37, angle: 5,
      subpeaks: [
        { x: .395, y: .625, elevation: .63, spreadX: 56, spreadY: 27 },
        { x: .685, y: .935, elevation: .46, spreadX: 43, spreadY: 22, recent: true }
      ]
    },
    {
      id: 'iteration', title: '迭代节奏', x: .17, y: .74, elevation: .89, evidence: 28, boundary: 2, recent: true,
      spreadX: 78, spreadY: 39, angle: 18,
      subpeaks: [
        { x: .035, y: .915, elevation: .39, spreadX: 36, spreadY: 20 },
        { x: .305, y: .545, elevation: .59, spreadX: 51, spreadY: 28, recent: true }
      ]
    }
  ]

  const relationPairs = [
    ['product', 'evidence'],
    ['evidence', 'accumulation'],
    ['evidence', 'research'],
    ['research', 'collaboration'],
    ['iteration', 'research'],
    ['accumulation', 'collaboration'],
    ['iteration', 'product']
  ]

  // The drawer copy follows the same deterministic 20-day fixture used by
  // the current cognitive home. It demonstrates the real information depth
  // without reading personal data from the browser.
  const themeDetails = {
    product: {
      statement: '在方案尚未稳定时，会先把目标指标、护栏指标和验证周期写清，再进入功能讨论',
      boundaryText: '目前主要出现在需要定义目标与取舍的产品工作中，不外推到所有日常决定',
      subpeaks: [
        { title: '目标先行', evidence: 12, statement: '先明确必须成立的用户场景 再排列功能清单' },
        { title: '护栏意识', evidence: 15, statement: '在扩大范围前保留可撤回的决策点' }
      ],
      evidenceRows: [
        ['07.30', '评审前先写清主指标 护栏指标和验证周期'],
        ['08.09', '先把必须成立的用户场景列出来 功能清单随后再排'],
        ['08.18', '这次评审只决定是否继续投入']
      ]
    },
    evidence: {
      statement: '面对判断分歧时，倾向先追问证据来源、可复现路径与反例，再决定是否采用结论',
      boundaryText: '在高不确定或高失败成本的判断中更稳定 低风险探索仍可以先行动再补证据',
      subpeaks: [
        { title: '来源核对', evidence: 14, statement: '让每个判断都能返回原文位置与可复核路径' },
        { title: '保留反例', evidence: 9, statement: '证据不足时保留反例入口与尚未核实状态' }
      ],
      evidenceRows: [
        ['07.31', '搜索结果需要保留原文位置和可复核链接'],
        ['08.10', '同一指标出现两个版本 先核对时间窗与过滤条件'],
        ['08.17', '没有找到原文定位时只写尚未核实']
      ]
    },
    accumulation: {
      statement: '更重视能在未来复用的判断机制，而非一次任务中的临时完成度',
      boundaryText: '适用于反复出现且值得复用的问题 一次性小事不强制沉淀为长期机制',
      subpeaks: [
        { title: '复用机制', evidence: 15, statement: '同类问题反复出现后沉淀为检查表或模板' },
        { title: '版本轨迹', evidence: 7, statement: '保留结论如何变化以及当时为什么这样取舍' }
      ],
      evidenceRows: [
        ['08.01', '一次完成之后继续留下可复用的判断步骤'],
        ['08.11', '同类问题第三次出现 应该沉淀成规则或模板'],
        ['08.18', '新增材料先进入主题 再判断是否改变长期理解']
      ]
    },
    collaboration: {
      statement: '在协作中会主动定义产品判断与项目推进的边界，让决策责任可以被追溯',
      boundaryText: '强调决策责任清晰 同时保留主动推进和在关键节点补位的空间',
      subpeaks: [
        { title: '责任清晰', evidence: 13, statement: '区分建议 待验证假设与已经决定' },
        { title: '留出接口', evidence: 8, statement: '让协作者能够理解 接手并继续推进' }
      ],
      evidenceRows: [
        ['08.03', '会议结束前补上负责人和决策截止点'],
        ['08.12', '把跨团队争议收束为两个可选择方案'],
        ['08.18', '输入条件变化后 原决策回到待确认状态']
      ]
    },
    research: {
      statement: '做研究时会先固定问题、筛选门槛与证据定位，再扩大材料规模',
      boundaryText: '针对需要形成可复核论证的研究任务 探索初期仍允许暂存较宽的线索',
      subpeaks: [
        { title: '先定门槛', evidence: 15, statement: '先固定筛选标准 再扩大候选材料' },
        { title: '回到原文', evidence: 10, statement: '进入论证前回到可核查的原始材料' }
      ],
      evidenceRows: [
        ['08.04', '文献卡片必须带页码或图表位置'],
        ['08.13', '先做一轮小样本盲筛 检查标准能否稳定执行'],
        ['08.18', '模型只负责发现候选 论证仍需回到原始材料']
      ]
    },
    iteration: {
      statement: '会用短周期样品尽早暴露问题，并依据观测结果调整下一轮投入',
      boundaryText: '适用于能在短周期获得反馈的工作 基础约束尚不清楚时仍需先补底层验证',
      subpeaks: [
        { title: '小步验证', evidence: 8, statement: '每轮只验证少量核心假设' },
        { title: '失败可见', evidence: 13, statement: '把失败状态和前后差异一同保留' }
      ],
      evidenceRows: [
        ['08.05', '先用可点击样品检查信息层级'],
        ['08.14', '把失败状态也放进样品'],
        ['08.18', '每轮只改一个核心假设并保留前后截图']
      ]
    }
  }

  function element(name, attributes) {
    const node = document.createElementNS(NS, name)
    Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, String(value)))
    return node
  }

  function point(x, y) {
    return { x: 70 + x * 980, y: 38 + y * 480 }
  }

  const peaks = themes.map((theme) => ({ ...theme, point: point(theme.x, theme.y), kind: 'primary' }))
  const subpeaks = themes.flatMap((theme) => theme.subpeaks.map((subpeak, index) => ({
    ...subpeak,
    id: `${theme.id}-${index}`,
    parentId: theme.id,
    point: point(subpeak.x, subpeak.y),
    kind: 'subpeak'
  })))
  const allPeaks = [...peaks, ...subpeaks]
  const peakById = new Map(peaks.map((peak) => [peak.id, peak]))

  const ridges = [
    ...subpeaks.map((subpeak) => ({
      from: subpeak.point,
      to: peakById.get(subpeak.parentId).point,
      strength: .065,
      width: 25,
      parentId: subpeak.parentId
    })),
    ...relationPairs.map(([fromId, toId]) => ({
      from: peakById.get(fromId).point,
      to: peakById.get(toId).point,
      strength: .052,
      width: 28,
      parentId: fromId,
      crossTheme: true
    }))
  ]

  const growthStages = [
    {
      id: 1,
      counter: '01 / 04',
      title: '一座地形',
      description: '第一组反复出现的记忆形成一座完整地形',
      themeIds: ['evidence'],
      includeSubpeaks: true,
      relationPairs: []
    },
    {
      id: 2,
      counter: '02 / 04',
      title: '几组等高圈',
      description: '更多主题各自形成稳定的等高圈',
      themeIds: ['product', 'evidence', 'accumulation'],
      includeSubpeaks: false,
      relationPairs: []
    },
    {
      id: 3,
      counter: '03 / 04',
      title: '两块地形',
      description: '相近主题彼此靠近 形成两块连续地形',
      themeIds: themes.map((theme) => theme.id),
      includeSubpeaks: true,
      relationPairs: [
        ['product', 'evidence'], ['evidence', 'iteration'], ['iteration', 'product'],
        ['accumulation', 'collaboration'], ['collaboration', 'research'], ['research', 'accumulation']
      ],
      ridgeStrength: .105,
      ridgeWidth: 54
    },
    {
      id: 4,
      counter: '04 / 04',
      title: '完整认知地形',
      description: '跨主题关系继续生长 最终形成完整认知地形',
      themeIds: themes.map((theme) => theme.id),
      includeSubpeaks: true,
      relationPairs,
      ridgeStrength: .074,
      ridgeWidth: 42
    }
  ]

  function geometryForStage(stage) {
    const activeIds = new Set(stage.themeIds)
    const stagePeaks = peaks.filter((peak) => activeIds.has(peak.id))
    const stageSubpeaks = stage.includeSubpeaks
      ? subpeaks.filter((subpeak) => activeIds.has(subpeak.parentId))
      : []
    const stageRidges = [
      ...stageSubpeaks.map((subpeak) => ({
        from: subpeak.point,
        to: peakById.get(subpeak.parentId).point,
        strength: stage.id === 3 ? .082 : .065,
        width: stage.id === 3 ? 38 : 25,
        parentId: subpeak.parentId
      })),
      ...stage.relationPairs.map(([fromId, toId]) => ({
        from: peakById.get(fromId).point,
        to: peakById.get(toId).point,
        strength: stage.ridgeStrength || .052,
        width: stage.ridgeWidth || 28,
        parentId: fromId,
        crossTheme: true
      }))
    ]
    return {
      peaks: stagePeaks,
      subpeaks: stageSubpeaks,
      allPeaks: [...stagePeaks, ...stageSubpeaks],
      ridges: stageRidges
    }
  }

  function distanceToSegmentSquared(x, y, from, to) {
    const dx = to.x - from.x
    const dy = to.y - from.y
    const lengthSquared = dx * dx + dy * dy || 1
    const ratio = Math.max(0, Math.min(1, ((x - from.x) * dx + (y - from.y) * dy) / lengthSquared))
    const nearestX = from.x + ratio * dx
    const nearestY = from.y + ratio * dy
    return (x - nearestX) ** 2 + (y - nearestY) ** 2
  }

  function peakContribution(item, x, y) {
    const width = Number(item.spreadX || 58)
    const height = Number(item.spreadY || 30)
    const angle = Number(item.angle || 0) * Math.PI / 180
    const offsetX = x - item.point.x
    const offsetY = y - item.point.y
    const cosine = Math.cos(angle)
    const sine = Math.sin(angle)
    const dx = (offsetX * cosine + offsetY * sine) / width
    const dy = (-offsetX * sine + offsetY * cosine) / height
    const evidence = Number(item.evidence || 10)
    const amplitude = (item.kind === 'primary' ? .20 : .13)
      + Number(item.elevation || .5) * (item.kind === 'primary' ? .34 : .31)
      + Math.log1p(evidence) * (item.kind === 'primary' ? .018 : .012)
    return amplitude * Math.exp(-.5 * (dx * dx + dy * dy))
  }

  function terrainValue(x, y, geometry) {
    let value = 0
    geometry.allPeaks.forEach((item) => { value += peakContribution(item, x, y) })
    geometry.ridges.forEach((ridge) => {
      value += ridge.strength * Math.exp(-distanceToSegmentSquared(x, y, ridge.from, ridge.to) / (2 * ridge.width ** 2))
    })
    return value
  }

  function interpolate(x1, y1, value1, x2, y2, value2, threshold) {
    const denominator = value2 - value1
    const ratio = Math.abs(denominator) < .00001 ? .5 : (threshold - value1) / denominator
    return { x: x1 + (x2 - x1) * ratio, y: y1 + (y2 - y1) * ratio }
  }

  function terrainSegments(values, columns, rows, step, originX, originY, threshold) {
    const segments = []
    const edgePoint = (edge, column, row, corners) => {
      const x = originX + column * step
      const y = originY + row * step
      if (edge === 'top') return interpolate(x, y, corners.tl, x + step, y, corners.tr, threshold)
      if (edge === 'right') return interpolate(x + step, y, corners.tr, x + step, y + step, corners.br, threshold)
      if (edge === 'bottom') return interpolate(x, y + step, corners.bl, x + step, y + step, corners.br, threshold)
      return interpolate(x, y, corners.tl, x, y + step, corners.bl, threshold)
    }

    for (let row = 0; row < rows - 1; row += 1) {
      for (let column = 0; column < columns - 1; column += 1) {
        const index = row * columns + column
        const corners = {
          tl: values[index], tr: values[index + 1],
          bl: values[index + columns], br: values[index + columns + 1]
        }
        const states = {
          tl: corners.tl >= threshold, tr: corners.tr >= threshold,
          br: corners.br >= threshold, bl: corners.bl >= threshold
        }
        const crossings = []
        if (states.tl !== states.tr) crossings.push('top')
        if (states.tr !== states.br) crossings.push('right')
        if (states.bl !== states.br) crossings.push('bottom')
        if (states.tl !== states.bl) crossings.push('left')
        if (crossings.length === 2) {
          segments.push(crossings.map((edge) => edgePoint(edge, column, row, corners)))
        } else if (crossings.length === 4) {
          const centerHigh = (corners.tl + corners.tr + corners.br + corners.bl) / 4 >= threshold
          const pairs = states.tl === centerHigh
            ? [['top', 'right'], ['bottom', 'left']]
            : [['top', 'left'], ['right', 'bottom']]
          pairs.forEach((pair) => segments.push(pair.map((edge) => edgePoint(edge, column, row, corners))))
        }
      }
    }
    return segments
  }

  function key(value) {
    return `${Math.round(value.x * 2)},${Math.round(value.y * 2)}`
  }

  function stitch(segments) {
    const edges = segments.map(([from, to]) => ({ from, to }))
    const adjacency = new Map()
    edges.forEach((edge, edgeIndex) => {
      ;[edge.from, edge.to].forEach((entry) => {
        const entryKey = key(entry)
        if (!adjacency.has(entryKey)) adjacency.set(entryKey, [])
        adjacency.get(entryKey).push(edgeIndex)
      })
    })
    const visited = new Set()
    const lines = []
    const walk = (firstEdgeIndex, firstKey) => {
      const points = []
      let edgeIndex = firstEdgeIndex
      let currentKey = firstKey
      let guard = 0
      while (!visited.has(edgeIndex) && guard <= edges.length) {
        guard += 1
        visited.add(edgeIndex)
        const edge = edges[edgeIndex]
        const forward = key(edge.from) === currentKey
        const start = forward ? edge.from : edge.to
        const end = forward ? edge.to : edge.from
        if (!points.length) points.push(start)
        points.push(end)
        currentKey = key(end)
        const next = (adjacency.get(currentKey) || []).find((candidate) => !visited.has(candidate))
        if (next === undefined) break
        edgeIndex = next
      }
      return points
    }
    adjacency.forEach((edgeIndices, entryKey) => {
      if (edgeIndices.length !== 1 || visited.has(edgeIndices[0])) return
      const points = walk(edgeIndices[0], entryKey)
      if (points.length > 1) lines.push(points)
    })
    edges.forEach((edge, edgeIndex) => {
      if (visited.has(edgeIndex)) return
      const points = walk(edgeIndex, key(edge.from))
      if (points.length > 1) lines.push(points)
    })
    return lines
  }

  function linePath(points) {
    if (points.length < 2) return ''
    const first = points[0]
    const last = points[points.length - 1]
    const closed = key(first) === key(last) && points.length > 4
    const line = closed ? points.slice(0, -1) : points
    const midpoint = (left, right) => ({ x: (left.x + right.x) / 2, y: (left.y + right.y) / 2 })
    if (closed) {
      const start = midpoint(line[line.length - 1], line[0])
      const parts = [`M ${start.x.toFixed(1)} ${start.y.toFixed(1)}`]
      line.forEach((entry, index) => {
        const middle = midpoint(entry, line[(index + 1) % line.length])
        parts.push(`Q ${entry.x.toFixed(1)} ${entry.y.toFixed(1)} ${middle.x.toFixed(1)} ${middle.y.toFixed(1)}`)
      })
      return `${parts.join(' ')} Z`
    }
    const parts = [`M ${line[0].x.toFixed(1)} ${line[0].y.toFixed(1)}`]
    for (let index = 1; index < line.length - 1; index += 1) {
      const middle = midpoint(line[index], line[index + 1])
      parts.push(`Q ${line[index].x.toFixed(1)} ${line[index].y.toFixed(1)} ${middle.x.toFixed(1)} ${middle.y.toFixed(1)}`)
    }
    parts.push(`L ${line[line.length - 1].x.toFixed(1)} ${line[line.length - 1].y.toFixed(1)}`)
    return parts.join(' ')
  }

  function renderContours(stage, geometry, target) {
    const step = 10
    const originX = 10
    const originY = 10
    const columns = 111
    const rows = 55
    const values = []
    for (let row = 0; row < rows; row += 1) {
      for (let column = 0; column < columns; column += 1) {
        values.push(terrainValue(originX + column * step, originY + row * step, geometry))
      }
    }
    const maximum = Math.max(...values)
    const count = 12
    const floorRatio = stage.id === 2 ? .12 : stage.id === 3 ? .08 : stage.id === 4 ? .025 : .055
    for (let index = 0; index < count; index += 1) {
      const ratio = index / Math.max(1, count - 1)
      const floor = Math.max(.018, maximum * floorRatio)
      const threshold = floor + (maximum * .92 - floor) * Math.pow(ratio, .92)
      const lines = stitch(terrainSegments(values, columns, rows, step, originX, originY, threshold))
      let d = lines.map(linePath).filter(Boolean).join(' ')
      if (stage.id === 1 && index === 0) {
        d = 'M 329 118 C 360 61 431 39 507 51 C 575 32 655 58 681 113 C 710 169 680 236 622 264 C 574 304 499 301 449 266 C 385 253 337 211 326 163 C 322 147 323 132 329 118 Z'
      }
      if (stage.id === 4 && index === 0) {
        d = 'M 38 145 C 64 76 163 44 274 78 C 384 34 510 47 607 82 C 721 44 856 62 969 102 C 1066 136 1093 226 1061 313 C 1090 395 1038 481 930 502 C 812 526 716 493 612 509 C 486 532 358 492 270 506 C 156 522 61 458 66 365 C 28 295 10 218 38 145 Z'
      }
      if (!d) continue
      const path = element('path', {
        d,
        class: `story-terrain-contour${index % 3 === 0 ? ' is-major' : ''}`,
        style: `--terrain-opacity:${(.24 + index * .026).toFixed(3)};--terrain-delay:${(.32 + index * .035).toFixed(3)}s`
      })
      target.appendChild(path)
    }
  }

  function renderRidges(geometry, target) {
    geometry.ridges.forEach((ridge, index) => {
      const bend = ridge.crossTheme ? (index % 2 ? -24 : 24) : 0
      const midX = (ridge.from.x + ridge.to.x) / 2 + bend
      const midY = (ridge.from.y + ridge.to.y) / 2 - bend * .35
      const path = element('path', {
        d: `M ${ridge.from.x.toFixed(1)} ${ridge.from.y.toFixed(1)} Q ${midX.toFixed(1)} ${midY.toFixed(1)} ${ridge.to.x.toFixed(1)} ${ridge.to.y.toFixed(1)}`,
        class: 'story-terrain-ridge',
        style: `--terrain-delay:${(.16 + index * .025).toFixed(3)}s`
      })
      target.appendChild(path)
    })
  }

  function renderShadows(geometry, target) {
    geometry.allPeaks.forEach((item) => {
      target.appendChild(element('ellipse', {
        cx: item.point.x + 3,
        cy: item.point.y + 5,
        rx: Number(item.spreadX || 54) * .78,
        ry: Number(item.spreadY || 28) * .76,
        class: 'story-terrain-shadow',
        transform: `rotate(${Number(item.angle || 0)} ${item.point.x} ${item.point.y})`,
        opacity: item.kind === 'primary' ? .82 : .42
      }))
    })
  }

  function renderNodes(geometry, target) {
    geometry.subpeaks.forEach((item, index) => {
      const parent = peakById.get(item.parentId)
      const detail = themeDetails[item.parentId]?.subpeaks?.[Number(item.id.split('-').at(-1))]
      const group = element('g', {
        class: 'story-terrain-node-group',
        transform: `translate(${item.point.x.toFixed(1)} ${item.point.y.toFixed(1)})`,
        tabindex: '0',
        role: 'button',
        'data-terrain-node': item.id,
        'aria-label': `${detail?.title || '可用记忆'} ${detail?.evidence || 0} 条依据 属于 ${parent?.title || '认知主题'}`
      })
      const marker = element('g', {
        class: 'story-terrain-marker',
        style: `--terrain-delay:${(.04 + index * .03).toFixed(3)}s`
      })
      marker.append(
        element('circle', { cx: 0, cy: 0, r: 18, class: 'story-terrain-hit' }),
        element('circle', {
          cx: 0, cy: 0, r: 4.2,
          class: `story-terrain-node${item.recent ? ' is-recent' : ''}`
        })
      )
      group.appendChild(marker)
      target.appendChild(group)
    })
  }

  function renderPeaks(geometry, target) {
    geometry.peaks.forEach((peak, index) => {
      const group = element('g', {
        class: 'story-terrain-peak',
        transform: `translate(${peak.point.x.toFixed(1)} ${peak.point.y.toFixed(1)})`,
        tabindex: '0',
        role: 'button',
        'data-terrain-theme': peak.id,
        'aria-label': `${peak.title} 依据 ${peak.evidence} 边界 ${peak.boundary}${peak.recent ? ' 近期有变化' : ''} 点击查看形成详情`
      })
      const marker = element('g', {
        class: 'story-terrain-marker',
        style: `--terrain-delay:${(.82 + index * .07).toFixed(3)}s`
      })
      const hit = element('circle', { cx: 0, cy: 1, r: 42, class: 'story-terrain-hit' })
      const dot = element('circle', { cx: 0, cy: -28, r: 5.8, class: 'peak-dot' })
      const title = element('text', { x: 0, y: 3, class: 'peak-title' })
      title.textContent = peak.title
      const count = element('text', { x: 0, y: 22, class: 'peak-count' })
      count.textContent = `依据 ${peak.evidence} · 边界 ${peak.boundary}`
      marker.append(hit, dot, title, count)
      if (peak.recent) {
        const change = element('text', { x: 0, y: 40, class: 'peak-change' })
        change.textContent = '近期有变化'
        marker.appendChild(change)
      }
      group.appendChild(marker)
      target.appendChild(group)
    })
  }

  const terrainStageGroups = new Map()
  growthStages.forEach((stage) => {
    const geometry = geometryForStage(stage)
    const stageLayers = {}
    Object.entries(layers).forEach(([name, layer]) => {
      const group = element('g', {
        class: 'terrain-stage-group',
        'data-terrain-growth-stage': stage.id,
        'aria-hidden': 'true'
      })
      layer.appendChild(group)
      stageLayers[name] = group
    })
    renderShadows(geometry, stageLayers.shadows)
    renderContours(stage, geometry, stageLayers.contours)
    renderRidges(geometry, stageLayers.ridges)
    renderNodes(geometry, stageLayers.nodes)
    renderPeaks(geometry, stageLayers.peaks)
    terrainStageGroups.set(stage.id, Object.values(stageLayers))
  })

  const frame = document.getElementById('terrain-interactive')
  const zoomOutput = document.getElementById('terrain-zoom')
  const help = document.getElementById('terrain-help')
  const live = document.getElementById('terrain-live')
  const detailPanel = document.getElementById('terrain-detail')
  const detailEyebrow = document.getElementById('terrain-detail-eyebrow')
  const detailTitle = document.getElementById('terrain-detail-title')
  const detailBody = document.getElementById('terrain-detail-body')
  const detailClose = detailPanel?.querySelector('.terrain-detail-close')
  const stageCounter = document.getElementById('terrain-stage-counter')
  const stageTitle = document.getElementById('terrain-stage-title')
  const stageDescription = document.getElementById('terrain-stage-description')
  const stageButtons = Array.from(document.querySelectorAll('[data-terrain-stage]'))
  const stageReplay = document.querySelector('[data-terrain-replay]')
  const query = new URLSearchParams(window.location.search)
  const forcedStage = Number(query.get('terrain-stage'))
  const visualCheck = query.get('terrain-visual-check') === '1'
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  let activeStage = 1
  let stageTimers = []
  let stagePlaybackStarted = false
  const bounds = { x: 0, y: 0, width: 1120, height: 560 }
  const camera = {
    zoom: 1,
    minZoom: 1,
    maxZoom: 3.2,
    centerX: bounds.width / 2,
    centerY: bounds.height / 2,
    pointers: new Map(),
    dragPointerId: null,
    dragStart: null,
    pinchDistance: 0,
    moved: false,
    suppressClickUntil: 0,
    selectedElement: null,
    fallbackFullscreen: false
  }

  if (visualCheck && frame) {
    document.documentElement.classList.add('terrain-visual-check')
    document.body.appendChild(frame)
  }

  function clearStageTimers() {
    stageTimers.forEach((timer) => window.clearTimeout(timer))
    stageTimers = []
  }

  function setTerrainStage(stageId, { announce = true } = {}) {
    const stage = growthStages.find((item) => item.id === Number(stageId)) || growthStages.at(-1)
    activeStage = stage.id
    terrainStageGroups.forEach((groups, id) => {
      const active = id === stage.id
      groups.forEach((group) => {
        group.classList.toggle('is-active', active)
        group.setAttribute('aria-hidden', String(!active))
        group.querySelectorAll('[tabindex]').forEach((entry) => entry.setAttribute('tabindex', active ? '0' : '-1'))
      })
    })
    stageButtons.forEach((button) => {
      const selected = Number(button.dataset.terrainStage) === stage.id
      button.classList.toggle('is-active', selected)
      button.setAttribute('aria-pressed', String(selected))
    })
    if (stageCounter) stageCounter.textContent = stage.counter
    if (stageTitle) stageTitle.textContent = stage.title
    if (stageDescription) stageDescription.textContent = stage.description
    frame?.setAttribute('data-terrain-stage', String(stage.id))
    closePanel({ restoreFocus: false })
    resetCamera()
    if (announce && live) live.textContent = `${stage.counter} ${stage.title} ${stage.description}`
  }

  function playTerrainGrowth() {
    clearStageTimers()
    stagePlaybackStarted = true
    frame?.setAttribute('aria-busy', 'true')
    setTerrainStage(1)
    ;[2, 3, 4].forEach((stageId, index) => {
      stageTimers.push(window.setTimeout(() => {
        setTerrainStage(stageId)
        if (stageId === 4) frame?.removeAttribute('aria-busy')
      }, 1200 * (index + 1)))
    })
  }

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    })[character])
  }

  function cameraDepth() {
    if (camera.zoom >= 2.05) return '依据层'
    if (camera.zoom >= 1.38) return '主题层'
    return '全景'
  }

  function applyCamera() {
    const width = bounds.width / camera.zoom
    const height = bounds.height / camera.zoom
    camera.centerX = Math.max(width / 2, Math.min(bounds.width - width / 2, camera.centerX))
    camera.centerY = Math.max(height / 2, Math.min(bounds.height - height / 2, camera.centerY))
    svg.setAttribute('viewBox', [
      camera.centerX - width / 2,
      camera.centerY - height / 2,
      width,
      height
    ].map((value) => value.toFixed(2)).join(' '))

    const markerScale = (1 / camera.zoom).toFixed(4)
    svg.querySelectorAll('.story-terrain-marker').forEach((marker) => {
      marker.style.setProperty('--terrain-marker-scale', markerScale)
    })
    if (zoomOutput) zoomOutput.textContent = `${cameraDepth()} · ${Math.round(camera.zoom * 100)}%`
    frame?.querySelectorAll('[data-terrain-action="zoom-out"]').forEach((button) => {
      button.disabled = camera.zoom <= camera.minZoom + .001
    })
    frame?.querySelectorAll('[data-terrain-action="zoom-in"]').forEach((button) => {
      button.disabled = camera.zoom >= camera.maxZoom - .001
    })
  }

  function svgPoint(clientX, clientY) {
    if (typeof svg.createSVGPoint !== 'function') return null
    const matrix = svg.getScreenCTM?.()
    if (!matrix) return null
    const coordinate = svg.createSVGPoint()
    coordinate.x = clientX
    coordinate.y = clientY
    return coordinate.matrixTransform(matrix.inverse())
  }

  function zoomAt(nextZoom, clientX = null, clientY = null) {
    const previousZoom = camera.zoom
    const boundedZoom = Math.max(camera.minZoom, Math.min(camera.maxZoom, nextZoom))
    if (Math.abs(previousZoom - boundedZoom) < .001) return
    const anchor = Number.isFinite(clientX) && Number.isFinite(clientY)
      ? svgPoint(clientX, clientY) : null
    camera.zoom = boundedZoom
    if (anchor) {
      const ratio = previousZoom / boundedZoom
      camera.centerX = anchor.x + (camera.centerX - anchor.x) * ratio
      camera.centerY = anchor.y + (camera.centerY - anchor.y) * ratio
    }
    applyCamera()
  }

  function resetCamera() {
    camera.zoom = 1
    camera.centerX = bounds.width / 2
    camera.centerY = bounds.height / 2
    applyCamera()
  }

  function panCamera(deltaX, deltaY) {
    camera.centerX += deltaX
    camera.centerY += deltaY
    applyCamera()
  }

  function relatedThemeIds(themeId) {
    return [...new Set(relationPairs.flatMap(([left, right]) => {
      if (left === themeId) return [right]
      if (right === themeId) return [left]
      return []
    }))]
  }

  function relatedButtons(themeId) {
    const related = relatedThemeIds(themeId)
    if (!related.length) return '<p>当前没有跨主题关系</p>'
    return `<div class="terrain-related">${related.map((id) => {
      const theme = peakById.get(id)
      return `<button type="button" data-terrain-related="${escapeHTML(id)}">${escapeHTML(theme?.title || id)}</button>`
    }).join('')}</div>`
  }

  function evidenceList(rows) {
    return `<ul class="terrain-evidence-list">${(rows || []).map(([date, statement]) => (
      `<li><time>${escapeHTML(date)}</time><span>${escapeHTML(statement)}</span></li>`
    )).join('')}</ul>`
  }

  function setSelected(element) {
    camera.selectedElement?.classList.remove('is-selected')
    camera.selectedElement = element || null
    camera.selectedElement?.classList.add('is-selected')
  }

  function showPanel({ eyebrow, title, body, element: selected, focusPanel = false }) {
    if (!frame || !detailPanel || !detailEyebrow || !detailTitle || !detailBody) return
    setSelected(selected)
    detailEyebrow.textContent = eyebrow
    detailTitle.textContent = title
    detailBody.innerHTML = body
    detailPanel.inert = false
    detailPanel.setAttribute('aria-hidden', 'false')
    frame.classList.add('is-detail-open')
    if (live) live.textContent = `已打开 ${title} 的形成详情`
    if (focusPanel) detailClose?.focus({ preventScroll: true })
  }

  function openTheme(themeId, selected, focusPanel = false) {
    const theme = peakById.get(themeId)
    const details = themeDetails[themeId]
    if (!theme || !details) return
    showPanel({
      eyebrow: theme.recent ? 'LONG-TERM UNDERSTANDING · RECENT CHANGE' : 'LONG-TERM UNDERSTANDING · CURRENT',
      title: theme.title,
      element: selected,
      focusPanel,
      body: `
        <p class="terrain-detail-lead">${escapeHTML(details.statement)}</p>
        <div class="terrain-detail-metrics" aria-label="主题形成概览">
          <span>支持依据<strong>${theme.evidence}</strong></span>
          <span>边界记录<strong>${theme.boundary}</strong></span>
          <span>观察跨度<strong>20 天</strong></span>
        </div>
        <section class="terrain-detail-section"><h4>适用边界</h4><p>${escapeHTML(details.boundaryText)}</p></section>
        <section class="terrain-detail-section"><h4>代表依据</h4>${evidenceList(details.evidenceRows)}</section>
        <section class="terrain-detail-section"><h4>已形成的可用记忆</h4>${evidenceList(details.subpeaks.map((item) => [`${item.evidence} 条`, item.statement]))}</section>
        <section class="terrain-detail-section"><h4>正式关系</h4>${relatedButtons(themeId)}</section>`
    })
  }

  function openNode(nodeId, selected, focusPanel = false) {
    const node = subpeaks.find((item) => item.id === nodeId)
    if (!node) return
    const index = Number(nodeId.split('-').at(-1))
    const parent = peakById.get(node.parentId)
    const details = themeDetails[node.parentId]
    const nodeDetail = details?.subpeaks?.[index]
    if (!parent || !nodeDetail) return
    showPanel({
      eyebrow: node.recent ? 'REUSABLE MEMORY · RECENTLY MERGED' : 'REUSABLE MEMORY · COMMITTED',
      title: nodeDetail.title,
      element: selected,
      focusPanel,
      body: `
        <p class="terrain-detail-lead">${escapeHTML(nodeDetail.statement)}</p>
        <div class="terrain-detail-metrics" aria-label="可用记忆概览">
          <span>形成依据<strong>${nodeDetail.evidence}</strong></span>
          <span>当前状态<strong>${node.recent ? '近期归并' : '已提交'}</strong></span>
          <span>归属主题<strong>${escapeHTML(parent.title)}</strong></span>
        </div>
        <section class="terrain-detail-section"><h4>一条代表记录</h4>${evidenceList([details.evidenceRows[index] || details.evidenceRows[0]])}</section>
        <section class="terrain-detail-section"><h4>在形成链路中的位置</h4><p>原始记录经过逐条整理后成为可用记忆点 随后通过稳定关系进入 ${escapeHTML(parent.title)} 主题峰</p></section>
        <section class="terrain-detail-section"><h4>继续查看</h4><div class="terrain-related"><button type="button" data-terrain-related="${escapeHTML(node.parentId)}">打开 ${escapeHTML(parent.title)}</button></div></section>`
    })
  }

  function closePanel({ restoreFocus = true } = {}) {
    if (!frame || !detailPanel || !frame.classList.contains('is-detail-open')) return
    frame.classList.remove('is-detail-open')
    const returnTarget = camera.selectedElement
    setSelected(null)
    if (live) live.textContent = '详情已关闭'
    if (restoreFocus) returnTarget?.focus({ preventScroll: true })
    detailPanel.inert = true
    detailPanel.setAttribute('aria-hidden', 'true')
  }

  function toggleFullscreen() {
    if (!frame) return
    camera.fallbackFullscreen = !camera.fallbackFullscreen
    frame.classList.toggle('is-fullscreen-fallback', camera.fallbackFullscreen)
    document.body.classList.toggle('has-terrain-fullscreen', camera.fallbackFullscreen)
    updateFullscreenControl(camera.fallbackFullscreen)
    if (camera.fallbackFullscreen) frame.focus({ preventScroll: true })
  }

  function updateFullscreenControl(active = camera.fallbackFullscreen) {
    const button = frame?.querySelector('[data-terrain-action="fullscreen"]')
    if (!button) return
    button.textContent = active ? '退出全屏' : '全屏查看'
    button.setAttribute('aria-pressed', String(active))
    if (help) help.textContent = active
      ? '滚轮缩放 · 拖拽移动 · Esc 退出全屏'
      : '滚轮缩放 · 拖拽移动 · 点击主题查看详情'
    requestAnimationFrame(applyCamera)
  }

  function pointerDistance(left, right) {
    return Math.hypot(right.clientX - left.clientX, right.clientY - left.clientY)
  }

  function pointerMidpoint(left, right) {
    return { clientX: (left.clientX + right.clientX) / 2, clientY: (left.clientY + right.clientY) / 2 }
  }

  if (frame) {
    frame.addEventListener('click', (event) => {
      const stageButton = event.target.closest?.('.terrain-evolution-steps [data-terrain-stage]')
      if (stageButton) {
        event.preventDefault()
        event.stopPropagation()
        clearStageTimers()
        frame.removeAttribute('aria-busy')
        setTerrainStage(Number(stageButton.dataset.terrainStage))
        return
      }
      if (event.target.closest?.('[data-terrain-replay]')) {
        event.preventDefault()
        event.stopPropagation()
        playTerrainGrowth()
        return
      }
      const action = event.target.closest?.('[data-terrain-action]')?.dataset.terrainAction
      if (action) {
        event.preventDefault()
        event.stopPropagation()
        if (action === 'zoom-in') zoomAt(camera.zoom * 1.32)
        if (action === 'zoom-out') zoomAt(camera.zoom / 1.32)
        if (action === 'reset') resetCamera()
        if (action === 'fullscreen') toggleFullscreen()
        return
      }
      if (event.target.closest?.('.terrain-detail-close')) {
        closePanel()
        return
      }
      const related = event.target.closest?.('[data-terrain-related]')
      if (related) {
        const themeId = related.dataset.terrainRelated
        const element = svg.querySelector(`[data-terrain-theme="${themeId}"]`)
        openTheme(themeId, element)
        return
      }
      if (performance.now() < camera.suppressClickUntil) return
      const themeElement = event.target.closest?.('[data-terrain-theme]')
      if (themeElement) {
        openTheme(themeElement.dataset.terrainTheme, themeElement)
        return
      }
      const nodeElement = event.target.closest?.('[data-terrain-node]')
      if (nodeElement) openNode(nodeElement.dataset.terrainNode, nodeElement)
    })

    frame.addEventListener('wheel', (event) => {
      if (event.target.closest?.('.terrain-controls, .terrain-evolution, .terrain-detail')) return
      event.preventDefault()
      zoomAt(camera.zoom * Math.exp(-event.deltaY * .00145), event.clientX, event.clientY)
    }, { passive: false })

    frame.addEventListener('dblclick', (event) => {
      if (event.target.closest?.('.terrain-controls, .terrain-evolution, .terrain-detail')) return
      event.preventDefault()
      zoomAt(camera.zoom * 1.55, event.clientX, event.clientY)
    })

    frame.addEventListener('pointerdown', (event) => {
      if (event.target.closest?.('.terrain-controls, .terrain-evolution, .terrain-detail')) return
      if (event.pointerType === 'mouse' && event.button !== 0) return
      camera.pointers.set(event.pointerId, { clientX: event.clientX, clientY: event.clientY })
      camera.moved = false
      if (camera.pointers.size === 1) {
        camera.dragPointerId = event.pointerId
        camera.dragStart = { clientX: event.clientX, clientY: event.clientY }
      } else if (camera.pointers.size === 2) {
        const pointers = [...camera.pointers.values()]
        camera.dragPointerId = null
        camera.pinchDistance = Math.max(1, pointerDistance(pointers[0], pointers[1]))
      }
    })

    frame.addEventListener('pointermove', (event) => {
      const previous = camera.pointers.get(event.pointerId)
      if (!previous) return
      camera.pointers.set(event.pointerId, { clientX: event.clientX, clientY: event.clientY })
      if (camera.pointers.size >= 2) {
        const pointers = [...camera.pointers.values()].slice(0, 2)
        const distance = Math.max(1, pointerDistance(pointers[0], pointers[1]))
        const midpoint = pointerMidpoint(pointers[0], pointers[1])
        if (!camera.moved) {
          camera.pointers.forEach((_, pointerId) => frame.setPointerCapture?.(pointerId))
          frame.classList.add('is-dragging')
        }
        camera.moved = true
        zoomAt(camera.zoom * distance / Math.max(1, camera.pinchDistance), midpoint.clientX, midpoint.clientY)
        camera.pinchDistance = distance
        event.preventDefault()
        return
      }
      if (camera.dragPointerId !== event.pointerId) return
      if (camera.dragStart && Math.hypot(event.clientX - camera.dragStart.clientX, event.clientY - camera.dragStart.clientY) > 4) {
        if (!camera.moved) {
          frame.setPointerCapture?.(event.pointerId)
          frame.classList.add('is-dragging')
        }
        camera.moved = true
      }
      if (camera.moved) {
        const from = svgPoint(previous.clientX, previous.clientY)
        const to = svgPoint(event.clientX, event.clientY)
        if (from && to) panCamera(from.x - to.x, from.y - to.y)
        event.preventDefault()
      }
    })

    const finishPointer = (event) => {
      if (!camera.pointers.has(event.pointerId)) return
      if (camera.moved) camera.suppressClickUntil = performance.now() + 320
      camera.pointers.delete(event.pointerId)
      camera.pinchDistance = 0
      const remaining = [...camera.pointers.entries()]
      if (remaining.length === 1) {
        camera.dragPointerId = remaining[0][0]
        camera.dragStart = { ...remaining[0][1] }
      } else {
        camera.dragPointerId = null
        camera.dragStart = null
        frame.classList.remove('is-dragging')
      }
    }
    frame.addEventListener('pointerup', finishPointer)
    frame.addEventListener('pointercancel', finishPointer)
    frame.addEventListener('lostpointercapture', finishPointer)

    frame.addEventListener('keydown', (event) => {
      const interactiveTheme = event.target.closest?.('[data-terrain-theme]')
      const interactiveNode = event.target.closest?.('[data-terrain-node]')
      if ((event.key === 'Enter' || event.key === ' ') && (interactiveTheme || interactiveNode)) {
        event.preventDefault()
        if (interactiveTheme) openTheme(interactiveTheme.dataset.terrainTheme, interactiveTheme, true)
        else openNode(interactiveNode.dataset.terrainNode, interactiveNode, true)
        return
      }
      if (event.key === 'Escape') {
        if (frame.classList.contains('is-detail-open')) {
          event.preventDefault()
          closePanel()
          return
        }
        if (camera.fallbackFullscreen) {
          event.preventDefault()
          toggleFullscreen()
        }
        return
      }
      if (event.target.closest?.('.terrain-controls, .terrain-evolution, .terrain-detail')) return
      if (!['+', '=', '-', '_', '0', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return
      event.preventDefault()
      const step = 46 / camera.zoom
      if (event.key === '+' || event.key === '=') zoomAt(camera.zoom * 1.28)
      if (event.key === '-' || event.key === '_') zoomAt(camera.zoom / 1.28)
      if (event.key === '0') resetCamera()
      if (event.key === 'ArrowLeft') panCamera(-step, 0)
      if (event.key === 'ArrowRight') panCamera(step, 0)
      if (event.key === 'ArrowUp') panCamera(0, -step)
      if (event.key === 'ArrowDown') panCamera(0, step)
    })

    if (typeof ResizeObserver === 'function') new ResizeObserver(applyCamera).observe(frame)
  }

  applyCamera()

  if (Number.isInteger(forcedStage) && forcedStage >= 1 && forcedStage <= 4) {
    setTerrainStage(forcedStage, { announce: false })
  } else if (reduceMotion) {
    setTerrainStage(4, { announce: false })
  } else if ('IntersectionObserver' in window) {
    setTerrainStage(1, { announce: false })
    const playbackObserver = new IntersectionObserver((entries, observer) => {
      if (!entries.some((entry) => entry.isIntersecting) || stagePlaybackStarted) return
      observer.disconnect()
      playTerrainGrowth()
    }, { threshold: .35 })
    playbackObserver.observe(frame)
  } else {
    playTerrainGrowth()
  }

  requestAnimationFrame(() => {
    svg.querySelectorAll('.story-terrain-contour, .story-terrain-ridge').forEach((path) => {
      try {
        path.style.setProperty('--terrain-length', `${Math.ceil(path.getTotalLength())}`)
      } catch (_) {
        path.style.setProperty('--terrain-length', '900')
      }
    })
  })

})()
