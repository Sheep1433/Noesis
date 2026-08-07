import { describe, expect, it } from 'vitest'
import { normalizeSkillsTreeNodes } from '@/utils/skillsTree'

describe('normalizeSkillsTreeNodes', () => {
  it('保留空目录的 children，避免树组件进入异步加载状态', () => {
    const [directory] = normalizeSkillsTreeNodes([
      {
        key: 'user:',
        label: '个人技能',
        isLeaf: false,
        source: 'user',
        children: [],
      },
    ])

    expect(directory.children).toEqual([])
  })
})
