import type { SkillFsTreeNode, SkillFsTreeResponse, SkillSource } from '@/api/skills'

export interface SkillPackageOption {
  id: string
  source: SkillSource
}

/** n-tree 不接受 children: null，统一为 undefined */
export function normalizeSkillsTreeNodes(nodes: SkillFsTreeNode[]): SkillFsTreeNode[] {
  return nodes.map((node) => {
    const children = node.children?.length
      ? normalizeSkillsTreeNodes(node.children)
      : undefined
    return { ...node, children }
  })
}

/** 合并树优先；缺失时由 platform/user 段拼装（兼容旧响应） */
export function resolveSkillsDisplayTree(payload: SkillFsTreeResponse | null): SkillFsTreeNode[] {
  if (!payload) {
    return []
  }
  if (payload.tree?.length) {
    return normalizeSkillsTreeNodes(payload.tree)
  }
  const merged: SkillFsTreeNode[] = []
  if (payload.platform?.tree?.length || payload.platform?.root_exists) {
    merged.push({
      key: 'platform:',
      label: '平台预置',
      isLeaf: false,
      children: payload.platform.tree ?? [],
      source: 'platform',
    })
  }
  merged.push({
    key: 'user:',
    label: '个人技能',
    isLeaf: false,
    children: payload.user?.tree ?? [],
    source: 'user',
  })
  return normalizeSkillsTreeNodes(merged)
}

/** Composer / Mention 用的顶层技能包列表 */
export function collectSkillPackages(payload: SkillFsTreeResponse | null): SkillPackageOption[] {
  if (!payload) {
    return []
  }
  const out: SkillPackageOption[] = []
  const seen = new Set<string>()
  const add = (id: string, source: SkillSource) => {
    const key = `${source}:${id}`
    if (!id || seen.has(key)) {
      return
    }
    seen.add(key)
    out.push({ id, source })
  }

  for (const section of [payload.platform, payload.user]) {
    for (const node of section?.tree ?? []) {
      if (!node.isLeaf) {
        add(node.label, node.source)
      }
    }
  }

  if (!out.length) {
    for (const group of payload.tree ?? []) {
      const groupSource = group.source ?? 'platform'
      for (const node of group.children ?? []) {
        if (!node.isLeaf && node.label !== '平台预置' && node.label !== '个人技能') {
          add(node.label, node.source ?? groupSource)
        }
      }
    }
  }

  return out
}

export function hasSkillPackages(payload: SkillFsTreeResponse | null): boolean {
  if (!payload) {
    return false
  }
  const counted = (payload.platform?.skill_count ?? 0) + (payload.user?.skill_count ?? 0)
  if (counted > 0) {
    return true
  }
  return collectSkillPackages(payload).length > 0 || resolveSkillsDisplayTree(payload).length > 0
}
