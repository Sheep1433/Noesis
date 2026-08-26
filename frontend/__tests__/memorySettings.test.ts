import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const section = readFileSync(
  fileURLToPath(new URL('../src/views/settings/sections/MemoryEditorSection.vue', import.meta.url)),
  'utf8',
)
const api = readFileSync(
  fileURLToPath(new URL('../src/api/settings.ts', import.meta.url)),
  'utf8',
)

describe('memory cortex removal baseline', () => {
  it('does not retain cortex governance in settings UI or API', () => {
    expect(section).not.toContain('cortex')
    expect(section).not.toContain('MachineMemory')
    expect(api).not.toContain('/memory/cortex')
    expect(api).not.toContain('MachineMemory')
  })

  it('keeps explicit USER.md / AGENTS.md file management', () => {
    expect(section).toContain('getUserMemoryFile')
    expect(section).toContain('putUserMemoryFile')
    expect(api).toContain('/api/user/memory/')
  })
})
