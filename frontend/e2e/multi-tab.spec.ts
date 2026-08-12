import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'

const SESSION_ID = process.env.E2E_SESSION_ID || ''
const SESSION_URL = `/chat/${encodeURIComponent(SESSION_ID)}`
const READY = Boolean(process.env.E2E_BASE_URL && process.env.E2E_STORAGE_STATE && SESSION_ID)

function lastAssistant(page: Page) {
  return page.getByTestId('assistant-message').last()
}

async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').locator('textarea').fill(text)
  await page.getByTestId('send-button').click()
  await expect(lastAssistant(page)).toBeVisible({ timeout: 15_000 })
}

async function waitForTerminal(page: Page) {
  await expect(page.getByTestId('streaming-indicator')).toHaveCount(0, { timeout: 60_000 })
  return ((await lastAssistant(page).textContent()) || '').trim()
}

test.describe('双 Tab SSE 多标签页', () => {
  test.skip(!READY, '需要 E2E_BASE_URL、E2E_STORAGE_STATE 与 E2E_SESSION_ID')

  test('中途加入并关闭创建 Tab 后，另一 Tab 得到同一终态', async ({ context }) => {
    const tabA = await context.newPage()
    const tabB = await context.newPage()
    await tabA.goto(SESSION_URL)
    await send(tabA, '分十段解释可靠消息推送，每段之间稍作思考。')

    await tabB.goto(SESSION_URL)
    await expect(lastAssistant(tabB)).toBeVisible({ timeout: 15_000 })
    const assistantId = await lastAssistant(tabA).getAttribute('data-assistant-message-id')
    await expect(lastAssistant(tabB)).toHaveAttribute('data-assistant-message-id', assistantId || '')

    await tabA.close()
    expect(await waitForTerminal(tabB)).toBeTruthy()
  })

  test('单 Tab stream 断开后用 active snapshot 恢复且不重复正文', async ({ context }) => {
    const tabA = await context.newPage()
    const tabB = await context.newPage()
    let aborted = false
    await tabB.route('**/api/chat/runs/*/stream*', async (route) => {
      if (!aborted) {
        aborted = true
        await route.abort('internetdisconnected')
      } else {
        await route.continue()
      }
    })
    await tabA.goto(SESSION_URL)
    await send(tabA, '分十段说明 SSE 重连机制，每段一句。')
    await tabB.goto(SESSION_URL)
    await expect.poll(() => aborted).toBe(true)
    await tabB.unroute('**/api/chat/runs/*/stream*')
    await tabB.reload()

    const [aText, bText] = await Promise.all([waitForTerminal(tabA), waitForTerminal(tabB)])
    expect(bText).toBe(aText)
  })

  test('任意 Tab stop 后两个 Tab 收到相同 partial 结果', async ({ context }) => {
    const tabA = await context.newPage()
    const tabB = await context.newPage()
    await tabA.goto(SESSION_URL)
    await send(tabA, '持续输出一篇很长的可靠推送分析。')
    await tabB.goto(SESSION_URL)
    await expect(lastAssistant(tabB)).toBeVisible({ timeout: 15_000 })
    await tabB.getByTestId('stop-button').click()

    const [aText, bText] = await Promise.all([waitForTerminal(tabA), waitForTerminal(tabB)])
    expect(bText).toBe(aText)
  })

  test('任意 Tab HITL resume 后两个 Tab 收到相同终态', async ({ context }) => {
    test.skip(!process.env.E2E_HITL_QUERY, '需要 E2E_HITL_QUERY 触发可审批工具')
    const tabA = await context.newPage()
    const tabB = await context.newPage()
    await tabA.goto(SESSION_URL)
    await send(tabA, process.env.E2E_HITL_QUERY!)
    await tabB.goto(SESSION_URL)
    await expect(tabA.getByTestId('hitl-panel')).toBeVisible({ timeout: 30_000 })
    await expect(tabB.getByTestId('hitl-panel')).toBeVisible({ timeout: 30_000 })
    await tabB.getByTestId('hitl-panel').getByRole('button', { name: '允许一次' }).click()

    const [aText, bText] = await Promise.all([waitForTerminal(tabA), waitForTerminal(tabB)])
    expect(bText).toBe(aText)
  })
})
