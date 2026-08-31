/**
 * 来源身份归一：canonical URL 规则。
 *
 * 与后端 noesis/chat/event_mapping/retrieval.py 的 canonical_url 为同一规则的
 * 两侧实现（用例集见 __tests__/canonicalUrl.test.ts 与
 * backend/tests/test_research_source_provenance.py，须保持对齐）：
 * - 协议统一 https（http 归一并入 https 身份）、host 小写、去默认端口
 * - 去 fragment
 * - 去 tracking 参数（utm_* 前缀 + 已知点击归因参数），其余 query 保序排序
 * - 路径去尾部冗余分隔符
 */

const TRACKING_PARAM_PREFIXES = ['utm_']
const TRACKING_PARAMS = new Set([
  'fbclid', 'gclid', 'dclid', 'msclkid', 'igshid', 'mc_cid', 'mc_eid',
  'spm', 'scm', 'yclid', 'twclid', '_hsenc', '_hsmi', 'vero_id', 'wickedid',
])

function isTrackingParam(name: string): boolean {
  const lowered = name.toLowerCase()
  return TRACKING_PARAMS.has(lowered) || TRACKING_PARAM_PREFIXES.some((p) => lowered.startsWith(p))
}

export function canonicalUrl(raw: string | undefined | null): string {
  const value = (raw || '').trim()
  if (!value) {
    return ''
  }
  let url: URL
  try {
    url = new URL(value)
  } catch {
    try {
      url = new URL(`https://${value}`)
    } catch {
      return value
    }
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return value
  }
  const host = url.hostname.toLowerCase()
  if (!host) {
    return value
  }
  const port = url.port ? Number(url.port) : null
  const netloc = port && port !== 80 && port !== 443 ? `${host}:${port}` : host
  let path = url.pathname || '/'
  if (path.length > 1) {
    path = path.replace(/\/+$/, '')
  }
  const pairs: Array<[string, string]> = []
  url.searchParams.forEach((val, key) => {
    if (!isTrackingParam(key)) {
      pairs.push([key, val])
    }
  })
  pairs.sort((a, b) => (a[0] === b[0] ? (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0) : (a[0] < b[0] ? -1 : 1)))
  const params = new URLSearchParams()
  for (const [key, val] of pairs) {
    params.append(key, val)
  }
  const query = params.toString()
  return `https://${netloc}${path}${query ? `?${query}` : ''}`
}
