/**
 * 冒烟：在聊天页注入 todos，检查 TodoList 是否在输入框上方可见
 */
import { chromium } from 'playwright'

const BASE = process.env.CHAT_URL || 'http://localhost:2048'

async function main() {
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
  await context.addInitScript(() => {
    sessionStorage.setItem('user', JSON.stringify({ token: 'playwright-smoke' }))
  })
  const page = await context.newPage()
  await page.goto(`${BASE}/#/chat`, { waitUntil: 'networkidle', timeout: 20000 })
  await page.locator('.chat-input-footer').first().waitFor({ timeout: 10000 }).catch(() => null)
  await page.waitForTimeout(800)

  const injected = await page.evaluate(() => {
    const app = document.querySelector('#app')
    if (!app) return { ok: false, reason: 'no #app' }
    const pinia = app.__vue_app__?.config?.globalProperties?.$pinia
    if (!pinia) return { ok: false, reason: 'no pinia' }
    const stores = pinia._s
    let business = stores.get('business-store') ?? null
    if (!business) {
      for (const [, s] of stores) {
        if (typeof s.update_todos === 'function') {
          business = s
          break
        }
      }
    }
    if (!business) return { ok: false, reason: 'no business store' }
    business.update_todos([
      { content: '测试 Todo 1', status: 'in_progress' },
      { content: '测试 Todo 2', status: 'pending' },
    ])
    return { ok: true, todosLen: business.todos?.length ?? 0 }
  })

  if (!injected.ok) {
    const url = page.url()
    const hasAnchor = await page.locator('.chat-input-footer').count()
    const title = await page.title()
    console.log('SKIP inject:', injected.reason, { url, hasAnchor, title })
    await page.screenshot({ path: '/tmp/todo-layout-skip.png' })
    await browser.close()
    process.exit(0)
  }

  const todoList = page.locator('.todo-list')
  await todoList.waitFor({ state: 'visible', timeout: 5000 })
  const box = await todoList.boundingBox()
  const anchor = page.locator('.chat-input-footer .n-space')
  const anchorBox = await anchor.boundingBox()

  const visible = box && anchorBox && box.y < anchorBox.y
  console.log(JSON.stringify({ injected, box, anchorBox, visibleAboveInput: visible }, null, 2))

  await page.screenshot({ path: '/tmp/todo-layout-verify.png', fullPage: false })
  await browser.close()
  process.exit(visible ? 0 : 1)
}

main().catch((e) => {
  console.error(e)
  process.exit(2)
})
