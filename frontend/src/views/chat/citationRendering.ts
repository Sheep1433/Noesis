import type { KbCitationAnnotation } from './messageParts'

export interface NumberedCitation {
  annotation: KbCitationAnnotation
  number: number
  insertionIndex: number
}

function safeInsertionIndex(points: string[], rawIndex: number): number {
  let index = rawIndex
  const before = points.slice(0, index).join('')
  const after = points.slice(index).join('')
  if (after.startsWith('](')) {
    const close = after.indexOf(')')
    if (close >= 0) {
      return index + close + 1
    }
  }
  for (const delimiter of ['**', '__', '~~', '`']) {
    if (after.startsWith(delimiter)) {
      return index + Array.from(delimiter).length
    }
  }
  if ((before.match(/`/g)?.length ?? 0) % 2 === 1) {
    const close = after.indexOf('`')
    if (close >= 0) {
      index += close + 1
    }
  }
  return index
}

export function numberedCitations(
  content: string,
  annotations: KbCitationAnnotation[],
): NumberedCitation[] {
  const points = Array.from(content)
  return annotations
    .filter((item) => item.end_index > item.start_index && item.end_index <= points.length)
    .sort((a, b) => a.end_index - b.end_index || a.citation_id.localeCompare(b.citation_id))
    .map((annotation, index) => ({
      annotation,
      number: index + 1,
      insertionIndex: safeInsertionIndex(points, annotation.end_index),
    }))
}

export function injectCitationMarkers(
  content: string,
  annotations: KbCitationAnnotation[],
): string {
  const source = Array.from(content)
  numberedCitations(content, annotations)
    .sort((a, b) => b.insertionIndex - a.insertionIndex || b.number - a.number)
    .forEach(({ annotation, number, insertionIndex }) => {
      const id = encodeURIComponent(annotation.citation_id)
      const marker = `<button type="button" class="kb-citation-marker" data-citation-id="${id}" aria-label="查看来源 ${number}">${number}</button>`
      source.splice(insertionIndex, 0, marker)
    })
  return source.join('')
}
