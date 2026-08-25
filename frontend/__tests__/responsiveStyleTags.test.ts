import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const responsiveFiles = [
  'src/components/KnowledgeBase/ChunkDetailPanel.vue',
  'src/components/KnowledgeBase/DocumentDrawer.vue',
  'src/components/KnowledgeBase/KbScopeSelector.vue',
  'src/components/KnowledgeBase/KbSearchPanel.vue',
  'src/views/DefaultPage.vue',
  'src/views/TableModal.vue',
  'src/views/chat.vue',
  'src/views/extensions/Extensions.vue',
  'src/views/knowledge-base/CollectionDetail.vue',
  'src/views/knowledge-base/KnowledgeBase.vue',
  'src/views/settings/SettingsNav.vue',
  'src/views/settings/SettingsShell.vue',
  'src/views/skills/SkillsManagement.vue',
]

describe('responsive breakpoint styles', () => {
  it.each(responsiveFiles)('%s processes $bp variables as SCSS', (relativePath) => {
    const source = readFileSync(resolve(process.cwd(), relativePath), 'utf8')
    const styleBlocks = [...source.matchAll(/<style([^>]*)>([\s\S]*?)<\/style>/g)]
    const responsiveBlocks = styleBlocks.filter(([, , content]) => content.includes('$bp-'))

    expect(responsiveBlocks.length).toBeGreaterThan(0)
    expect(responsiveBlocks.every(([, attributes]) => /lang=["']scss["']/.test(attributes))).toBe(true)
  })
})
