import type { KbCitationAnnotation } from '@/views/chat/messageParts'

import { describe, expect, it } from 'vitest'
import { injectCitationMarkers, numberedCitations } from '@/views/chat/citationRendering'

function citation(id: string, start: number, end: number): KbCitationAnnotation {
  return {
    type: 'kb_citation', citation_id: id, start_index: start, end_index: end,
    document_id: 'doc', document_version_id: 'docv', segment_id: 'seg',
    title: '需求.md', excerpt: '证据', verification: 'structural',
  }
}

describe('citationRendering', () => {
  it('uses stable numbers for multiple sources on the same range', () => {
    const result = numberedCitations('五分钟', [citation('cit_b', 0, 3), citation('cit_a', 0, 3)])
    expect(result.map((item) => [item.annotation.citation_id, item.number])).toEqual([
      ['cit_a', 1], ['cit_b', 2],
    ])
  })

  it('inserts after markdown emphasis delimiter', () => {
    const output = injectCitationMarkers('**五分钟**', [citation('cit_1', 2, 5)])
    expect(output).toContain('五分钟**<button')
  })

  it('does not inject inside inline code or link syntax', () => {
    expect(injectCitationMarkers('`5 min`', [citation('cit_code', 1, 6)]))
      .toContain('`5 min`<button')
    expect(injectCitationMarkers('[文档](https://example.com)', [citation('cit_link', 1, 3)]))
      .toContain('https://example.com)<button')
  })

  it('ignores invalid and out-of-range annotations', () => {
    expect(injectCitationMarkers('正文', [citation('bad', 0, 99)])).toBe('正文')
  })
})
