export function safeWebUrl(raw: string | undefined): string | null {
  if (!raw) {
    return null
  }
  try {
    const url = new URL(raw)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : null
  } catch {
    return null
  }
}
