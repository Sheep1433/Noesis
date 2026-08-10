import { describe, expect, it } from 'vitest'
import MarkdownInstance from '@/components/MarkdownPreview/plugins/markdown'

describe('markdown external links', () => {
  it('renders prompt-generated citations with safe external-link attributes', () => {
    const html = MarkdownInstance.render('[AgentScope](https://github.com/agentscope-ai/agentscope)')

    expect(html).toContain('href="https://github.com/agentscope-ai/agentscope"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('referrerpolicy="no-referrer"')
  })

  it('does not add external-link attributes to relative links', () => {
    const html = MarkdownInstance.render('[local](/knowledge-base)')

    expect(html).not.toContain('target="_blank"')
    expect(html).not.toContain('rel="noopener noreferrer"')
  })
})
