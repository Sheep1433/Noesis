/**
 * Mention catalog：预取 skills 包列表 + session context，TTL 缓存，本地过滤。
 */
import type { SessionContextResponse, SessionFsTreeNode, SlashCommand } from '@/api/chat'
import type { SkillPackageItem } from '@/api/skills'
import type { SubagentOption } from '@/config/subagents'
import { getSessionContext, getSlashCommands } from '@/api/chat'
import { getSkillsPackages } from '@/api/skills'
import { getSubagentsForQaType } from '@/config/subagents'

export type MentionKind = 'command' | 'skill' | 'file' | 'folder' | 'subagent'

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
const COMMANDS_TTL_MS = 60_000

let skillsCache: { at: number, data: SkillPackageItem[] } | null = null
let commandsCache: { at: number, data: SlashCommand[] } | null = null
const contextCache = new Map<string, { at: number, data: SessionContextResponse }>()

export function invalidateMentionSkillsCache() {
  skillsCache = null
  commandsCache = null
}

export function invalidateMentionContextCache(sessionId?: string) {
  if (sessionId) {
    contextCache.delete(sessionId)
    return
  }
  contextCache.clear()
}

async function loadSkills(force = false): Promise<SkillPackageItem[]> {
  if (!force && skillsCache && Date.now() - skillsCache.at < SKILLS_TTL_MS) {
    return skillsCache.data
  }
  try {
    const data = await getSkillsPackages()
    skillsCache = { at: Date.now(), data }
    return data
  } catch (e) {
    console.warn('mention catalog: skills 加载失败', e)
    return skillsCache?.data ?? []
  }
}

async function loadCommands(force = false): Promise<SlashCommand[]> {
  if (!force && commandsCache && Date.now() - commandsCache.at < COMMANDS_TTL_MS) {
    return commandsCache.data
  }
  try {
    const data = await getSlashCommands()
    commandsCache = { at: Date.now(), data }
    return data
  } catch (e) {
    console.warn('mention catalog: commands 加载失败', e)
    return commandsCache?.data ?? []
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

function flattenSkillPackages(packages: SkillPackageItem[]): MentionCandidate[] {
  return packages.map((pkg) => ({
    kind: 'skill' as const,
    id: pkg.name,
    source: pkg.source,
    label: pkg.name,
    description: pkg.description,
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
    const [commands, packages] = await Promise.all([loadCommands(force), loadSkills(force)])
    const candidates: MentionCandidate[] = []
    for (const cmd of commands) {
      candidates.push({
        kind: 'command',
        id: cmd.name,
        label: `/${cmd.name}`,
        description: cmd.description,
      })
    }
    candidates.push(...flattenSkillPackages(packages))
    return candidates
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
  if (c.kind === 'command' || c.kind === 'skill') {
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
  if (m.type === 'command' || m.type === 'skill') {
    return `/${m.id}`
  }
  if (m.type === 'subagent') {
    return `@${m.id}`
  }
  const path = (m.path || m.label || '').replace(/^@/, '').replace(/\/$/, '')
  return `@${path}`
}

export function mentionToPayload(m: ComposerMention) {
  // 控制命令是 ephemeral，不进 mention payload（dispatch 拦截，不经 Agent）。
  if (m.type === 'command') {
    return { type: m.type, ...(m.id ? { id: m.id } : {}) }
  }
  return {
    type: m.type,
    ...(m.id ? { id: m.id } : {}),
    ...(m.path ? { path: m.path } : {}),
    ...(m.source ? { source: m.source } : {}),
    ...(m.virtual_path ? { virtual_path: m.virtual_path } : {}),
  }
}
