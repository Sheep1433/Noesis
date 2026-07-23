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

async function main() {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(`console: ${msg.text()}`)
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

  // navigate settings
  await page.goto(`${BASE}/settings?s=profile`, { waitUntil: 'networkidle', timeout: 60000 })
  await page.waitForTimeout(800)

  const textarea = page.locator('textarea').first()
  await textarea.waitFor({ timeout: 15000 })
  const before = await textarea.inputValue()
  console.log('profile content length', before.length)

  if (errors.some((e) => e.includes("reading 'content'"))) {
    fail(errors.join('\n'))
    await browser.close()
    return
  }

  const stamp = `\n\n<!-- smoke ${Date.now()} -->\n`
  await textarea.fill(`${before}${stamp}`)
  await page.getByRole('button', { name: '保存' }).click()
  await page.waitForTimeout(1000)

  const toastOrError = errors.filter((e) => e.includes("reading 'updated_at'") || e.includes("reading 'content'"))
  if (toastOrError.length) {
    fail(toastOrError.join('\n'))
    await browser.close()
    return
  }

  // memory section
  await page.goto(`${BASE}/settings?s=memory`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
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

  const critical = errors.filter(
    (e) =>
      e.includes("Cannot read properties of undefined")
      || e.includes('API error')
      || e.includes('Failed to fetch'),
  )
  if (critical.length) {
    fail(critical.join('\n'))
  } else {
    console.log('OK settings smoke passed')
    if (errors.length) console.log('non-critical errors:', errors.slice(0, 5))
  }

  await page.screenshot({ path: '/tmp/noesis-settings-smoke.png', fullPage: true })
  await browser.close()
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
