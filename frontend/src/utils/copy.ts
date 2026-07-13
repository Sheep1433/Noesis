/** 复制文本到剪贴板（仅安全上下文 Clipboard API；HTTPS 域名部署后可用） */
export async function copyToClipboard(text: string): Promise<void> {
  if (!navigator.clipboard?.writeText || !window.isSecureContext) {
    throw new Error('Clipboard API unavailable (requires HTTPS)')
  }
  await navigator.clipboard.writeText(text)
}
