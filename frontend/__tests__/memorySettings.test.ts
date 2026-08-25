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

describe('machine memory settings removal baseline', () => {
  it('does not retain daily files or automatic dream controls', () => {
    expect(section).not.toContain('整理记忆')
    expect(section).not.toContain('按日')
    expect(section).not.toContain('Dream')
    expect(api).not.toContain('/memory/dream')
    expect(api).not.toContain('/memory/daily')
  })
})
