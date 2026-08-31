import { expect, test } from '@playwright/test'

const API_BASE = process.env.E2E_API_BASE_URL ?? 'http://127.0.0.1:18001/api'

type Box = { left: number; right: number; top: number; bottom: number }

function intersects(left: Box, right: Box): boolean {
  return left.left < right.right && left.right > right.left && left.top < right.bottom && left.bottom > right.top
}

async function rect(locator: import('@playwright/test').Locator): Promise<Box> {
  const box = await locator.boundingBox()
  if (!box) throw new Error(`missing geometry for ${await locator.first().getAttribute('class')}`)
  return {
    left: box.x,
    right: box.x + box.width,
    top: box.y,
    bottom: box.y + box.height,
  }
}

async function assertPublicReportPayload(payload: Record<string, unknown>) {
  const serialized = JSON.stringify(payload)
  for (const token of ['RubricMatch', 'Requirement', 'd03_min_02', 'ev_DEMO_STUDENT', 'role_dim_']) {
    expect(serialized).not.toContain(token)
  }
  const publicKeys = new Set<string>()
  const collectKeys = (value: unknown) => {
    if (Array.isArray(value)) {
      value.forEach(collectKeys)
      return
    }
    if (!value || typeof value !== 'object') return
    Object.entries(value).forEach(([key, child]) => {
      publicKeys.add(key)
      collectKeys(child)
    })
  }
  collectKeys(payload)
  for (const key of ['candidate_id', 'claim_id', 'dimension_id', 'dimension_ids', 'requirement_id', 'evidence_id', 'evidence_ids']) {
    expect(publicKeys).not.toContain(key)
  }
  const transcript = Array.isArray(payload.interview_transcript) ? payload.interview_transcript : []
  const answers = new Map(
    transcript
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
      .map((item) => [String(item.turn_id), String(item.answer ?? '')]),
  )
  const enterprise = payload.enterprise_assessment as Record<string, unknown>
  const excerpts = Array.isArray(enterprise?.evidence_excerpts) ? enterprise.evidence_excerpts : []
  expect(excerpts.length).toBeGreaterThan(0)
  for (const item of excerpts as Record<string, unknown>[]) {
    const quote = String(item.quote ?? '')
    const answer = answers.get(String(item.turn_id)) ?? ''
    expect(quote).not.toBe('')
    expect(answer).toContain(quote)
    expect(quote).not.toBe(answer)
  }
}

async function fetchReport(
  page: import('@playwright/test').Page,
  path: string,
): Promise<Record<string, unknown>> {
  const response = await page.request.get(`${API_BASE}${path}`)
  expect(response.ok()).toBe(true)
  const payload = await response.json() as Record<string, unknown>
  await assertPublicReportPayload(payload)
  return payload
}

async function fetchFrozenDemo(page: import('@playwright/test').Page): Promise<Record<string, unknown>> {
  return fetchReport(page, '/demo/assessment')
}

async function openReport(page: import('@playwright/test').Page) {
  await page.goto('/demo/assessment')
  await expect(page.getByRole('heading', { name: '岗位胜任力报告' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '有条件进入结构化复试' })).toBeVisible()
}

for (const viewportWidth of [390, 840, 960, 1100, 1440]) {
  test(`keeps enterprise report collision-free at ${viewportWidth}px`, async ({ page }) => {
    const consoleErrors: string[] = []
    const failedResponses: string[] = []
    const externalRequests: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('response', (response) => {
      if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`)
    })
    page.on('request', (request) => {
      if (/api\.openai|anthropic|dashscope|bigmodel|deepseek/i.test(request.url())) externalRequests.push(request.url())
    })
    await page.setViewportSize({ width: viewportWidth, height: viewportWidth === 390 ? 844 : 1000 })
    await fetchFrozenDemo(page)
    await openReport(page)

    const viewport = await page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }))
    const panel = await rect(page.locator('.report-radar-panel'))
    const match = await rect(page.locator('.report-match-panel'))
    const chart = await rect(page.locator('.radar-chart'))
    const graphic = await rect(page.locator('.radar-graphic-wrap'))
    const table = await rect(page.locator('.radar-table-wrap'))
    const decision = await rect(page.locator('.enterprise-decision-brief'))
    const overall = await rect(page.locator('.report-overall-panel'))
    const reinterviewPanel = await rect(page.locator('.enterprise-reinterview-panel'))

    expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(viewport.width)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width)
    expect(intersects(decision, overall)).toBe(false)
    if (viewportWidth === 390) {
      expect(decision.top).toBeLessThanOrEqual(viewport.height)
      const decisionTitle = await rect(page.getByRole('heading', { name: '有条件进入结构化复试' }))
      expect(decisionTitle.bottom).toBeLessThanOrEqual(viewport.height)
    }
    for (const card of await page.locator('.reinterview-card').all()) {
      const cardBox = await rect(card)
      expect(cardBox.left).toBeGreaterThanOrEqual(reinterviewPanel.left - 1)
      expect(cardBox.right).toBeLessThanOrEqual(reinterviewPanel.right + 1)
      expect(cardBox.top).toBeGreaterThanOrEqual(reinterviewPanel.top - 1)
      expect(cardBox.bottom).toBeLessThanOrEqual(reinterviewPanel.bottom + 1)
    }
    for (const child of [chart, graphic, table]) {
      expect(child.left).toBeGreaterThanOrEqual(panel.left - 1)
      expect(child.right).toBeLessThanOrEqual(panel.right + 1)
      expect(child.top).toBeGreaterThanOrEqual(panel.top - 1)
      expect(child.bottom).toBeLessThanOrEqual(panel.bottom + 1)
      expect(intersects(child, match)).toBe(false)
    }

    for (const label of await page.locator('.radar-axis text').all()) {
      const labelBox = await label.boundingBox()
      expect(labelBox).not.toBeNull()
      expect(labelBox!.width).toBeGreaterThan(0)
      expect(labelBox!.height).toBeGreaterThan(0)
      expect(intersects({
        left: labelBox!.x,
        right: labelBox!.x + labelBox!.width,
        top: labelBox!.y,
        bottom: labelBox!.y + labelBox!.height,
      }, match)).toBe(false)
    }

    for (const axis of await page.locator('.radar-axis').all()) {
      await expect(axis).not.toHaveAttribute('data-radar-index')
      await expect(axis.locator('.radar-axis-halo')).toHaveCount(2)
      for (const halo of await axis.locator('.radar-axis-halo').all()) {
        await expect.poll(() => halo.evaluate((element) => getComputedStyle(element).animationName)).toBe('radar-halo-wave')
        await expect.poll(() => halo.evaluate((element) => getComputedStyle(element).animationIterationCount)).toBe('infinite')
      }
    }
    expect(externalRequests).toEqual([])
    expect(consoleErrors).toEqual([])
    expect(failedResponses).toEqual([])
  })
}

test('uses the same enterprise report sections for the real demo and saved report endpoints', async ({ page }) => {
  const requestedPaths: string[] = []
  const consoleErrors: string[] = []
  const failedResponses: string[] = []
  const externalRequests: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('response', (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`)
  })
  page.on('request', (request) => {
    if (request.url().includes('/api/')) requestedPaths.push(new URL(request.url()).pathname)
    if (/api\.openai|anthropic|dashscope|bigmodel|deepseek/i.test(request.url())) externalRequests.push(request.url())
  })

  await fetchFrozenDemo(page)
  await fetchReport(page, '/assessments/ast_e2e_report/report')
  await page.goto('/demo/assessment')
  await expect(page.getByRole('heading', { name: '企业复试计划' })).toBeVisible()
  const sectionSelector = '.report-page > :is(.report-hero, .report-candidate-panel, .enterprise-decision-brief, .report-overall-panel, .report-signals-panel, .report-layout-primary, .enterprise-reinterview-panel, .report-transcript-collapsible, .report-limitations-collapsible)'
  const demoSections = await page.locator(sectionSelector).evaluateAll((elements) => elements.map((element) => element.className))

  await page.goto('/assessments/ast_e2e_report/report')
  await expect(page.getByRole('heading', { name: '企业复试计划' })).toBeVisible()
  const savedSections = await page.locator(sectionSelector).evaluateAll((elements) => elements.map((element) => element.className))

  expect(requestedPaths).toContain('/api/demo/assessment')
  expect(requestedPaths).toContain('/api/assessments/ast_e2e_report/report')
  expect(savedSections).toEqual(demoSections)
  expect(externalRequests).toEqual([])
  expect(consoleErrors).toEqual([])
  expect(failedResponses).toEqual([])
})

test('selects a radar dimension with keyboard and disables both halos under reduced motion', async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 960, height: 1000 },
    reducedMotion: 'reduce',
  })
  const page = await context.newPage()
  await fetchFrozenDemo(page)
  await openReport(page)

  const axis = page.locator('[data-radar-axis="Agent架构与任务编排"]')
  await axis.focus()
  await axis.press('Enter')

  await expect(axis).toHaveAttribute('data-radar-selected', 'true')
  await expect(page.locator('[data-radar-row="Agent架构与任务编排"]')).toHaveAttribute('data-radar-selected', 'true')
  for (const currentAxis of await page.locator('.radar-axis').all()) {
    await expect(currentAxis.locator('.radar-axis-halo')).toHaveCount(2)
    for (const halo of await currentAxis.locator('.radar-axis-halo').all()) {
      await expect.poll(() => halo.evaluate((element) => getComputedStyle(element).animationName)).toBe('none')
      await expect.poll(() => halo.evaluate((element) => getComputedStyle(element).opacity)).toBe('0.42')
    }
  }

  await context.close()
})

test('keeps the intake primary action clear and horizontal-scroll free on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/assessments/new')

  await expect(page.getByRole('heading', { name: '创建岗位胜任力评估' })).toBeVisible()
  await expect(page.getByRole('button', { name: '创建评估' })).toBeVisible()
  await expect(page.getByText('INPUT CONTROL')).not.toBeVisible()
  await expect(page.getByText('READY TO ANALYZE')).not.toBeVisible()
  await expect.poll(() => page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(390)
})
