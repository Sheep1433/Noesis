/**
 * Mention catalog：预取 skills tree + session context，TTL 缓存，本地过滤。
 */
import type { SessionContextResponse, SessionFsTreeNode } from '@/api/chat'
import type { SkillFsTreeResponse } from '@/api/skills'
import type { SubagentOption } from '@/config/subagents'
import { getSessionContext } from '@/api/chat'
import { getSkillsFsTree } from '@/api/skills'
import { getSubagentsForQaType } from '@/config/subagents'
import { collectSkillPackages } from '@/utils/skillsTree'

export type MentionKind = 'skill' | 'file' | 'folder' | 'subagent'

export interface MentionCandidate {
  kind: MentionKind
  id?: string
  path?: string
  source?: 'platform' | 'user'
  virtualPath?: string
  label: string
  description?: string
}

export interface ComposerMention {
  type: MentionKind
  id?: string
  path?: string
  source?: 'platform' | 'user'
  virtual_path?: string
  label: string
}

const SKILLS_TTL_MS = 60_000
const CONTEXT_TTL_MS = 30_000

let skillsCache: { at: number, data: SkillFsTreeResponse } | null = null
const contextCache = new Map<string, { at: number, data: SessionContextResponse }>()

export function invalidateMentionSkillsCache() {
  skillsCache = null
}

export function invalidateMentionContextCache(sessionId?: string) {
  if (sessionId) {
    contextCache.delete(sessionId)
    return
  }
  contextCache.clear()
}

async function loadSkills(force = false): Promise<SkillFsTreeResponse | null> {
  if (!force && skillsCache && Date.now() - skillsCache.at < SKILLS_TTL_MS) {
    return skillsCache.data
  }
  try {
    const data = await getSkillsFsTree()
    skillsCache = { at: Date.now(), data }
    return data
  } catch (e) {
    console.warn('mention catalog: skills 加载失败', e)
    return skillsCache?.data ?? null
  }
}

async function loadContext(sessionId: string, force = false): Promise<SessionContextResponse | null> {
  if (!sessionId) {
    return null
  }
  const hit = contextCache.get(sessionId)
  if (!force && hit && Date.now() - hit.at < CONTEXT_TTL_MS) {
    return hit.data
  }
  try {
    const data = await getSessionContext(sessionId)
    if (data) {
      contextCache.set(sessionId, { at: Date.now(), data })
    }
    return data
  } catch (e) {
    console.warn('mention catalog: context 加载失败', e)
    return hit?.data ?? null
  }
}

function flattenSkillPackages(tree: SkillFsTreeResponse): MentionCandidate[] {
  return collectSkillPackages(tree).map((pkg) => ({
    kind: 'skill' as const,
    id: pkg.id,
    source: pkg.source,
    label: pkg.id,
    description: `${pkg.source} skill`,
  }))
}

function flattenFsNodes(
  nodes: SessionFsTreeNode[] | undefined,
  sessionId: string,
): MentionCandidate[] {
  const out: MentionCandidate[] = []
  if (!nodes?.length) {
    return out
  }
  const wsPrefix = `sessions/${sessionId}/workspace/`
  const upPrefix = `sessions/${sessionId}/uploads/`
  const walk = (list: SessionFsTreeNode[]) => {
    for (const node of list) {
      const key = node.key
      if (node.isLeaf) {
        const ok = key === 'AGENTS.md'
          || key === 'USER.md'
          || key.startsWith('skills/')
          || key.startsWith(wsPrefix)
          || key.startsWith(upPrefix)
        if (ok) {
          out.push({
            kind: 'file',
            path: key,
            label: node.label,
            description: key,
          })
        }
      } else {
        const isFolder = key.startsWith(wsPrefix)
          || key.startsWith(upPrefix)
          || (key.startsWith('skills/') && key !== 'skills')
        if (isFolder) {
          out.push({
            kind: 'folder',
            path: key,
            label: `${node.label}/`,
            description: key,
          })
        }
      }
      if (node.children?.length) {
        walk(node.children)
      }
    }
  }
  walk(nodes)
  return out
}

export async function ensureMentionCatalog(opts: {
  qaType: string
  sessionId: string
  mode: 'slash' | 'at'
  force?: boolean
}): Promise<MentionCandidate[]> {
  const { qaType, sessionId, mode, force } = opts
  if (mode === 'slash') {
    const tree = await loadSkills(force)
    return tree ? flattenSkillPackages(tree) : []
  }
  const candidates: MentionCandidate[] = []
  const ctx = await loadContext(sessionId, force)
  if (ctx?.tree) {
    candidates.push(...flattenFsNodes(ctx.tree, sessionId))
  }
  const subs: SubagentOption[] = getSubagentsForQaType(qaType)
  for (const s of subs) {
    candidates.push({
      kind: 'subagent',
      id: s.id,
      label: s.label,
      description: s.description,
    })
  }
  return candidates
}

export function candidateToMention(c: MentionCandidate): ComposerMention {
  return {
    type: c.kind,
    id: c.id,
    path: c.path,
    source: c.source,
    virtual_path: c.virtualPath,
    label: formatMentionTokenFromCandidate(c),
  }
}

/** 写入输入框的纯文本 token（无 chip） */
export function formatMentionTokenFromCandidate(c: MentionCandidate): string {
  if (c.kind === 'skill') {
    return `/${c.id}`
  }
  if (c.kind === 'subagent') {
    return `@${c.id}`
  }
  // file / folder：优先路径，便于识别
  const path = (c.path || c.label || '').replace(/\/$/, '')
  return `@${path}`
}

export function formatMentionToken(m: ComposerMention): string {
  if (m.type === 'skill') {
    return `/${m.id}`
  }
  if (m.type === 'subagent') {
    return `@${m.id}`
  }
  const path = (m.path || m.label || '').replace(/^@/, '').replace(/\/$/, '')
  return `@${path}`
}

export function mentionToPayload(m: ComposerMention) {
  return {
    type: m.type,
    ...(m.id ? { id: m.id } : {}),
    ...(m.path ? { path: m.path } : {}),
    ...(m.source ? { source: m.source } : {}),
    ...(m.virtual_path ? { virtual_path: m.virtual_path } : {}),
  }
}
