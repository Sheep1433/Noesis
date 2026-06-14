/** 复制文本到剪贴板（含非安全上下文降级） */
export async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }

  const element = document.createElement('textarea')
  const previouslyFocusedElement = document.activeElement

  element.value = text
  element.setAttribute('readonly', '')
  element.style.contain = 'strict'
  element.style.position = 'absolute'
  element.style.left = '-9999px'
  element.style.fontSize = '12pt'

  const selection = document.getSelection()
  const originalRange = selection && selection.rangeCount > 0
    ? selection.getRangeAt(0)
    : null

  document.body.appendChild(element)
  element.select()
  element.selectionStart = 0
  element.selectionEnd = text.length
  document.execCommand('copy')
  document.body.removeChild(element)

  if (originalRange && selection) {
    selection.removeAllRanges()
    selection.addRange(originalRange)
  }

  if (previouslyFocusedElement instanceof HTMLElement) {
    previouslyFocusedElement.focus()
  }
}
