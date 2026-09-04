import { describe, expect, it } from 'vitest'
import { canonicalUrl } from '@/utils/canonicalUrl'

// 共享用例集：与 backend/tests/test_research_source_provenance.py 的
// CANONICAL_URL_CASES 完全一致（前后端规则对齐，两侧须同步修改）。
const SHARED_CASES: Array<[string, string]> = [
  ['https://Example.com/A/?utm_source=x&b=2&a=1', 'https://example.com/A?a=1&b=2'],
  ['http://example.com:80/a', 'https://example.com/a'],
  ['https://example.com:443/a', 'https://example.com/a'],
  ['https://example.com/a#section', 'https://example.com/a'],
  ['https://example.com/a/', 'https://example.com/a'],
  ['https://example.com/a?fbclid=abc', 'https://example.com/a'],
  ['https://example.com/a?utm_term=hello&q=llm', 'https://example.com/a?q=llm'],
  ['https://example.com', 'https://example.com/'],
  ['example.com/a', 'https://example.com/a'],
  ['https://EXAMPLE.com:8443/Deep/path/', 'https://example.com:8443/Deep/path'],
  ['', ''],
]

describe('canonicalUrl（与后端共享规则）', () => {
  it.each(SHARED_CASES)('%s → %s', (raw, expected) => {
    expect(canonicalUrl(raw)).toBe(expected)
  })
})
