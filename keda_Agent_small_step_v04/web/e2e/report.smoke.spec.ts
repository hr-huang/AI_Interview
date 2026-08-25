import { expect, test } from '@playwright/test'
import type { ReportViewModel } from '../src/api/types'

const reportFixture: ReportViewModel = {
  demo: true,
  target_role: 'AI Agent / AI应用工程师',
  role_profile_version: '2026-H2',
  scoring_engine_version: 'v1',
  job_match: {
    raw_score: 86,
    published: true,
    fit_level: '高度匹配',
    coverage: 0.84,
    confidence: 'high',
    limiting_reasons: [],
  },
  radar_dimensions: Array.from({ length: 6 }, (_, index) => ({
    dimension_id: `role_dim_0${index + 1}`,
    name: ['Agent 编排', '任务建模', '上下文工程', 'AI 交付', '可靠性', '持续演进'][index],
    score: 72 + index,
    level: 'L3',
    coverage: 0.8,
    confidence: 'high',
    reasons: [],
  })),
  narrative: {
    executive_summary: '冻结的浏览器验收报告。',
    strengths: [],
    risks: [],
    unverified_areas: [],
    fit_contexts: [],
    development_actions: [],
  },
  interview_path: [],
  interview_transcript: [
    {
      turn_id: 'turn_003',
      sequence_number: 3,
      question: '第三轮问题',
      answer: '第三轮回答',
      question_mode: 'scenario',
      requirement_id: 'req_01',
      requirement_label: 'Agent 编排',
      asked_at: '2026-08-24T10:00:00Z',
      answered_at: '2026-08-24T10:01:00Z',
      evidence_ids: [],
      evidence_status: 'none',
    },
  ],
  claim_verifications: [],
  assessment_limitations: [],
  demo_variant: 'showcase',
}

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

async function openReport(page: import('@playwright/test').Page) {
  await page.route('**/api/demo/assessment', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(reportFixture),
  }))
  await page.goto('/demo/assessment')
  await expect(page.getByRole('heading', { name: '能力雷达' })).toBeVisible()
}

for (const viewportWidth of [390, 840, 960, 1100, 1440]) {
  test(`keeps radar internals and match panel collision-free at ${viewportWidth}px`, async ({ page }) => {
    await page.setViewportSize({ width: viewportWidth, height: 1000 })
    await openReport(page)

    const viewport = await page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }))
    const panel = await rect(page.locator('.report-radar-panel'))
    const match = await rect(page.locator('.report-match-panel'))
    const chart = await rect(page.locator('.radar-chart'))
    const graphic = await rect(page.locator('.radar-graphic-wrap'))
    const table = await rect(page.locator('.radar-table-wrap'))

    expect(await page.evaluate(() => document.body.scrollWidth)).toBeLessThanOrEqual(viewport.width)
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
      expect(labelBox!.x).toBeGreaterThanOrEqual(-1)
      expect(labelBox!.x + labelBox!.width).toBeLessThanOrEqual(viewport.width + 1)
      expect(labelBox!.y).toBeGreaterThanOrEqual(-1)
    }
  })
}

test('selects a radar dimension with keyboard and preserves a static reduced-motion halo', async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 960, height: 1000 },
    reducedMotion: 'reduce',
  })
  const page = await context.newPage()
  await openReport(page)

  const axis = page.locator('[data-radar-axis="role_dim_01"]')
  await axis.focus()
  await axis.press('Enter')

  await expect(axis).toHaveAttribute('data-radar-selected', 'true')
  await expect(page.locator('[data-radar-row="role_dim_01"]')).toHaveAttribute('data-radar-selected', 'true')
  await expect.poll(() => axis.locator('.radar-axis-halo').evaluate((element) => getComputedStyle(element).animationName)).toBe('none')
  await expect.poll(() => axis.locator('.radar-axis-halo').evaluate((element) => getComputedStyle(element).opacity)).toBe('0.42')

  await context.close()
})
