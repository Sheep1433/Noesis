import { describe, expect, it } from 'vitest'
import { buildDisplayParts } from '@/utils/groupAssistantParts'
import type { ToolUiPart, UiPart } from '@/views/chat/messageParts'

function makeTool(id: string, stepId?: string): ToolUiPart {
  return {
    id,
    type: 'tool',
    tool_call_id: id,
    name: 'read',
    input: {},
    output: '',
    status: 'running',
    state: 'running',
    ...(stepId ? { step_id: stepId } : {}),
  }
}

function makeText(id: string, content = 'txt'): UiPart {
  return { id, type: 'text', content, status: 'completed' }
}

function kinds(entries: ReturnType<typeof buildDisplayParts>) {
  return entries.map((e) => e.kind)
}

describe('buildDisplayParts parallel tool grouping', () => {
  it('相邻同 step_id 的工具合并为 parallel_tools', () => {
    const parts: UiPart[] = [makeTool('a', 'root:1'), makeTool('b', 'root:1'), makeTool('c', 'root:2')]
    const out = buildDisplayParts(parts)
    expect(kinds(out)).toEqual(['parallel_tools', 'part'])
    expect(out[0].kind).toBe('parallel_tools')
    if (out[0].kind === 'parallel_tools') {
      expect(out[0].parts.map((p) => p.tool_call_id)).toEqual(['a', 'b'])
    }
    expect(out[1].kind).toBe('part')
  })

  it('单工具不分组，保持 part', () => {
    const parts: UiPart[] = [makeTool('a', 'root:1')]
    const out = buildDisplayParts(parts)
    expect(kinds(out)).toEqual(['part'])
  })

  it('无 step_id 的工具不参与分组', () => {
    const parts: UiPart[] = [makeTool('a'), makeTool('b')]
    const out = buildDisplayParts(parts)
    expect(kinds(out)).toEqual(['part', 'part'])
  })

  it('邻接被打断时分别保持 part', () => {
    const parts: UiPart[] = [makeTool('a', 'root:1'), makeText('t'), makeTool('b', 'root:1')]
    const out = buildDisplayParts(parts)
    expect(kinds(out)).toEqual(['part', 'part', 'part'])
  })

  it('三连相同 step_id 合并为一组', () => {
    const parts: UiPart[] = [makeTool('a', 's:1'), makeTool('b', 's:1'), makeTool('c', 's:1')]
    const out = buildDisplayParts(parts)
    expect(kinds(out)).toEqual(['parallel_tools'])
    if (out[0].kind === 'parallel_tools') {
      expect(out[0].parts).toHaveLength(3)
    }
  })

  it('不同 step_id 的相邻工具各自独立', () => {
    const parts: UiPart[] = [makeTool('a', 's:1'), makeTool('b', 's:2')]
    const out = buildDisplayParts(parts)
    expect(kinds(out)).toEqual(['part', 'part'])
  })
})
