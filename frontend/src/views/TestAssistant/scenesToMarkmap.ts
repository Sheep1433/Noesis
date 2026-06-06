/** 测试场景/测试点 → Markmap 用 Markdown（与 PRD §7.3 一致） */

export interface TcTestPoint {
  point_name: string
  point_level?: string
}

export interface TcScene {
  scene_name: string
  scene_description?: string
  test_points?: TcTestPoint[]
}

export interface TcTestCase {
  case_id?: string
  point_name: string
  point_level?: string
  point_type?: string
  preconditions?: string[]
  test_steps?: string[]
  expected_results?: string[]
}

export type CaseGenStatus = 'pending' | 'generating' | 'done' | 'error'

export const MARKMAP_WELCOME = `# 测试场景与测试点\n\n上传需求文档并完成解析后，此处将展示模型生成的**测试场景与测试点**树（非需求原文）。`

export function countTestPoints(scenes: TcScene[]): number {
  let n = 0
  for (const sc of scenes) {
    for (const tp of sc.test_points || []) {
      if (tp.point_name?.trim()) {
        n += 1
      }
    }
  }
  return n
}

export interface SceneCaseProgress {
  sceneName: string
  total: number
  done: number
  generating: number
  failed: number
  pending: number
}

export function computeSceneCaseProgress(
  scenes: TcScene[],
  selectedPointNames: string[],
  caseGenStatus: Record<string, CaseGenStatus>,
): SceneCaseProgress[] {
  const selected = new Set(selectedPointNames.filter((n) => n.trim()))
  const result: SceneCaseProgress[] = []

  for (const sc of scenes) {
    const name = (sc.scene_name || '').trim()
    if (!name) {
      continue
    }
    const points = (sc.test_points || []).filter((tp) => {
      const pn = tp.point_name?.trim()
      return pn && selected.has(pn)
    })
    if (!points.length) {
      continue
    }

    const item: SceneCaseProgress = {
      sceneName: name,
      total: points.length,
      done: 0,
      generating: 0,
      failed: 0,
      pending: 0,
    }
    for (const tp of points) {
      const pn = tp.point_name.trim()
      const status = caseGenStatus[pn] || 'pending'
      if (status === 'done') {
        item.done += 1
      } else if (status === 'generating') {
        item.generating += 1
      } else if (status === 'error') {
        item.failed += 1
      } else {
        item.pending += 1
      }
    }
    result.push(item)
  }
  return result
}

export function sceneProgressStatus(progress: SceneCaseProgress): CaseGenStatus {
  if (progress.generating > 0) {
    return 'generating'
  }
  if (progress.done + progress.failed >= progress.total) {
    return progress.failed > 0 && progress.done === 0 ? 'error' : 'done'
  }
  if (progress.done > 0 || progress.failed > 0) {
    return 'generating'
  }
  return 'pending'
}

export function sceneProgressLabel(progress: SceneCaseProgress): string {
  const finished = progress.done + progress.failed
  if (progress.generating > 0) {
    return `生成中 ${finished}/${progress.total}`
  }
  if (finished >= progress.total) {
    return progress.failed
      ? `已完成 ${progress.done}/${progress.total}（${progress.failed} 失败）`
      : `已完成 ${progress.done}/${progress.total}`
  }
  if (progress.pending === progress.total) {
    return '等待生成'
  }
  return `${finished}/${progress.total}`
}

function appendSceneProgressLine(lines: string[], progress: SceneCaseProgress) {
  const status = sceneProgressStatus(progress)
  const label = sceneProgressLabel(progress)
  if (status === 'generating') {
    lines.push(`- ⏳ 用例${label}`)
  } else if (status === 'pending') {
    lines.push('- ⏸ 等待生成')
  } else if (status === 'error') {
    lines.push(`- ❌ ${label}`)
  } else {
    lines.push(`- ✅ ${label}`)
  }
}

function appendCaseLines(lines: string[], tc: TcTestCase) {
  const title = tc.case_id?.trim() || '测试用例'
  lines.push(`#### ${title}`)
  const pre = (tc.preconditions || []).filter(Boolean)
  if (pre.length) {
    lines.push(`- 前置：${pre.join('；')}`)
  }
  for (const [i, step] of (tc.test_steps || []).entries()) {
    if (step?.trim()) {
      lines.push(`- 步骤${i + 1}：${step.trim()}`)
    }
  }
  const exp = (tc.expected_results || []).filter(Boolean)
  if (exp.length) {
    lines.push(`- 预期：${exp.join('；')}`)
  }
}

export function scenesToMarkmap(
  scenes: TcScene[],
  selectedPointNames?: string[] | null,
  casesByPoint?: Record<string, TcTestCase>,
  caseGenStatus?: Record<string, CaseGenStatus>,
  caseGenErrors?: Record<string, string>,
): string {
  const selected = selectedPointNames?.length
    ? new Set(selectedPointNames.filter((n) => n.trim()))
    : null

  const lines: string[] = ['# 测试场景与测试点']
  let hasPoint = false

  for (const sc of scenes) {
    const name = (sc.scene_name || '').trim()
    if (!name) {
      continue
    }
    const points = (sc.test_points || []).filter((tp) => {
      const pn = tp.point_name?.trim()
      if (!pn) {
        return false
      }
      if (selected) {
        return selected.has(pn)
      }
      return true
    })
    if (selected && !points.length) {
      continue
    }
    lines.push(`## ${name}`)
    const desc = (sc.scene_description || '').trim()
    if (desc) {
      lines.push(desc)
    }

    const sceneProgress = selected && caseGenStatus
      ? computeSceneCaseProgress([sc], [...selected], caseGenStatus)[0]
      : undefined
    if (sceneProgress && sceneProgress.total > 0) {
      appendSceneProgressLine(lines, sceneProgress)
    }

    for (const tp of points) {
      hasPoint = true
      const pn = tp.point_name.trim()
      const level = (tp.point_level || '').trim()
      lines.push(level ? `### ${pn} [${level}]` : `### ${pn}`)

      const tc = casesByPoint?.[pn]
      if (tc) {
        appendCaseLines(lines, tc)
      } else if (caseGenStatus?.[pn] === 'error') {
        const errMsg = (caseGenErrors?.[pn] || '生成失败').trim()
        lines.push(`- ❌ ${errMsg}`)
      }
    }
  }

  if (!hasPoint) {
    return selected
      ? '# 测试场景与测试点\n\n（暂无已采纳的测试点）'
      : '# 测试场景与测试点\n\n（暂无可展示的测试点）'
  }
  return lines.join('\n')
}
