/**
 * Smoke: settings memory / automation / channels via Playwright.
 * Usage: node scripts/smoke-settings.mjs
 */
import { chromium } from 'playwright'

const BASE = process.env.NOESIS_BASE || 'http://127.0.0.1:4173'
const USER = process.env.NOESIS_USER || 'admin'
const PASS = process.env.NOESIS_PASS || '123456'

function fail(msg) {
  console.error('FAIL:', msg)
  process.exitCode = 1
}

function assert(condition, msg) {
  if (!condition) {
    fail(msg)
  }
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      errors.push(`console: ${msg.text()}`)
    }
  })

  console.log('goto', BASE)
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 60000 })

  // login if needed
  if (page.url().includes('login') || (await page.getByPlaceholder(/用户|账号|username/i).count()) > 0) {
    const userInput = page.locator('input').first()
    await userInput.fill(USER)
    const passInput = page.locator('input[type="password"]').first()
    await passInput.fill(PASS)
    await page.getByRole('button', { name: /登录|登陆|Login/i }).click()
    await page.waitForTimeout(1500)
  }

  // illegal deep link falls back to overview without breaking the shell
  await page.goto(`${BASE}/settings?s=unknown`, { waitUntil: 'networkidle', timeout: 60000 })
  await page.waitForTimeout(300)
  assert((await page.getByRole('heading', { name: '概览' }).count()) === 1, 'invalid section did not fall back')

  // navigation search matches keywords and keeps stable section links
  const search = page.getByRole('searchbox', { name: '搜索设置' })
  const settingsNav = page.getByRole('navigation', { name: '设置导航' })
  await search.fill('Telegram')
  assert((await settingsNav.getByRole('button', { name: /通讯/ }).count()) === 1, 'settings keyword search failed')
  await search.fill('')

  // keyboard navigation follows the visible section order
  const overviewNav = settingsNav.getByRole('button', { name: /概览/ })
  await overviewNav.focus()
  await overviewNav.press('ArrowDown')
  assert(await settingsNav.getByRole('button', { name: /模型/ }).evaluate((el) => el === document.activeElement), 'arrow navigation failed')

  // navigate settings
  await page.goto(`${BASE}/settings?s=profile`, { waitUntil: 'networkidle', timeout: 60000 })
  await page.waitForTimeout(800)

  console.log('profile url', page.url())
  await page.getByText('Markdown 原文', { exact: true }).click()
  if ((await page.getByRole('button', { name: '编辑' }).count()) > 0) {
    await page.getByRole('button', { name: '编辑' }).click()
  }
  const textarea = page.locator('textarea').first()
  await textarea.waitFor({ timeout: 15000 })
  const before = await textarea.inputValue()
  console.log('profile content length', before.length)

  if (errors.some((e) => e.includes('reading \'content\''))) {
    fail(errors.join('\n'))
    await browser.close()
    return
  }

  const stamp = `\n\n<!-- smoke ${Date.now()} -->\n`
  await textarea.fill(`${before}${stamp}`)
  await page.getByRole('button', { name: '保存' }).click()
  await page.waitForTimeout(1000)

  const toastOrError = errors.filter((e) => e.includes('reading \'updated_at\'') || e.includes('reading \'content\''))
  if (toastOrError.length) {
    fail(toastOrError.join('\n'))
    await browser.close()
    return
  }

  // restore source content so smoke is repeatable
  await page.getByRole('button', { name: '编辑' }).click()
  await textarea.fill(before)
  await page.getByRole('button', { name: '保存' }).click()
  await page.waitForTimeout(500)

  // memory section
  await page.goto(`${BASE}/settings?s=memory`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  if ((await page.getByRole('button', { name: '编辑' }).count()) > 0) {
    await page.getByRole('button', { name: '编辑' }).click()
  }
  const mem = page.locator('textarea').first()
  await mem.waitFor({ timeout: 15000 })
  console.log('memory content length', (await mem.inputValue()).length)

  // automation
  await page.goto(`${BASE}/settings?s=automation`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  console.log('automation heading', await page.getByRole('heading', { name: '自动化' }).count())

  // channels
  await page.goto(`${BASE}/settings?s=channels`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  console.log('channels heading', await page.getByRole('heading', { name: '通讯通道' }).count())

  // mobile shell remains searchable and horizontally navigable
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload({ waitUntil: 'networkidle' })
  assert((await page.getByRole('searchbox', { name: '搜索设置' }).count()) === 1, 'mobile settings search missing')

  const critical = errors.filter(
    (e) =>
      e.includes('Cannot read properties of undefined')
      || e.includes('API error')
      || e.includes('Failed to fetch'),
  )
  if (critical.length) {
    fail(critical.join('\n'))
  } else {
    console.log('OK settings smoke passed')
    if (errors.length) {
      console.log('non-critical errors:', errors.slice(0, 5))
    }
  }

  await page.screenshot({ path: '/tmp/noesis-settings-smoke.png', fullPage: true })
  await browser.close()
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
