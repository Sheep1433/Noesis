import { copyToClipboard } from '@/utils/copy'

export function useCopyCode() {
  const timeoutIdMap: WeakMap<HTMLElement, NodeJS.Timeout> = new WeakMap()
  window.addEventListener('click', (e) => {
    const el = e.target as HTMLElement
    if (!el.matches('div[class*="language-"] button.markdown-code-copy')) {
      return
    }

    const parent = el.parentElement
    const sibling = parent?.nextElementSibling
    if (!parent || !sibling) {
      return
    }

    const isShell = /language-(shellscript|shell|bash|sh|zsh)/.test(
      parent.className,
    )

    const ignoredNodes = []

    // Clone the node and remove the ignored nodes
    const clone = sibling.cloneNode(true) as HTMLElement
    if (ignoredNodes.length) {
      clone
        .querySelectorAll(ignoredNodes.join(','))
        .forEach((node) => node.remove())
    }

    let text = clone.textContent || ''

    if (isShell) {
      text = text.replace(/^ *(\$|>) /gm, '').trim()
    }

    void copyToClipboard(text).then(() => {
      el.classList.add('copied')
      clearTimeout(timeoutIdMap.get(el))
      const timeoutId = setTimeout(() => {
        el.classList.remove('copied')
        el.blur()
        timeoutIdMap.delete(el)
      }, 2000)
      timeoutIdMap.set(el, timeoutId)
    })
  })
}
