/** 本地 fuzzy：子序列匹配（大小写不敏感） */

export function fuzzyScore(haystack: string, needle: string): number {
  const h = haystack.toLowerCase()
  const n = needle.toLowerCase().trim()
  if (!n) {
    return 1
  }
  if (h.includes(n)) {
    return 100 - Math.min(50, h.indexOf(n))
  }
  let hi = 0
  let score = 0
  for (let ni = 0; ni < n.length; ni++) {
    const ch = n[ni]
    const found = h.indexOf(ch, hi)
    if (found < 0) {
      return 0
    }
    score += 1
    if (found === hi) {
      score += 2
    }
    hi = found + 1
  }
  return score
}

export function fuzzyFilter<T>(
  items: T[],
  needle: string,
  getText: (item: T) => string,
): T[] {
  const n = needle.trim()
  if (!n) {
    return items
  }
  return items
    .map((item) => ({ item, score: fuzzyScore(getText(item), n) }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((x) => x.item)
}
