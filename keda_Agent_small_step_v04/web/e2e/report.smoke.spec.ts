import { expect, test } from '@playwright/test'
import type { ReportViewModel } from '../src/api/types'

const reportFixture: ReportViewModel = {
  demo: true,
  target_role: 'AI Agent / AI应用工程师',
  role_profile_version: '2026-H2',
  scoring_engine_version: 'v1',
  candidate_overview: {
    candidate_name: '林予安',
    target_role: 'AI Agent / AI应用工程师',
    education_summary: '计算机相关专业本科',
    experience_summary: '完成课程项目与开源协作，具备 Agent 工作流实践。',
    jd_focus: ['Agent 编排', '生产稳定性'],
    interview_rounds: 3,
    generated_at: '2026-08-25T10:00:00Z',
  },
  enterprise_assessment: {
    decision: 'CONDITIONAL_PROCEED',
    decision_label: '有条件进入结构化复试',
    provisional_score: 82,
    confidence: 'medium',
    conditions: ['补充生产故障演练，确认恢复边界。'],
    decision_reasons: ['核心能力已有支持证据，仍需验证复杂场景迁移。'],
    overall_assessment: '候选人能拆分 Agent 状态与工具边界，适合进入结构化复试；生产压力下的恢复与观测能力仍需单独核验。',
    strengths: [{
      title: '状态边界清楚',
      text: '能够说明状态流转与人工介入节点。',
      dimension_names: ['Agent 编排'],
      confidence: 'high',
    }],
    risks: [{
      title: '复杂故障迁移待确认',
      text: '当前回答覆盖局部恢复流程，尚未证明压力场景下的完整闭环。',
      dimension_names: ['可靠性'],
      confidence: 'medium',
    }],
    unknowns: [{
      title: '成本与延迟取舍待确认',
      text: '尚未形成可复核的优化指标与回滚边界。',
      dimension_names: ['持续演进'],
      confidence: 'low',
    }],
    reinterview_plan: [
      {
        priority: 1,
        dimension_name: '可靠性',
        reason: '需要确认复杂故障下的排查、恢复与复盘边界。',
        question: '请设计一次生产故障演练，并说明如何判断恢复完成。',
        follow_ups: ['如果指标恢复但用户仍受影响，你会如何继续定位？'],
        positive_signals: ['能给出可观测指标与回滚边界。'],
        risk_signals: ['只描述工具，不说明失败条件。'],
        pass_criteria: ['明确输入、输出、失败条件与验证方式。'],
        suggested_minutes: 12,
      },
      {
        priority: 2,
        dimension_name: '持续演进',
        reason: '需要确认成本、延迟与质量回归之间的工程取舍。',
        question: '当调用成本上升且延迟超标时，你会如何定位并验证优化结果？',
        follow_ups: ['哪些指标会触发回滚？'],
        positive_signals: ['能说明基线、实验和回滚条件。'],
        risk_signals: ['只给出换模型而没有验证设计。'],
        pass_criteria: ['给出可量化指标与质量护栏。'],
        suggested_minutes: 10,
      },
      {
        priority: 3,
        dimension_name: '上下文工程',
        reason: '需要确认记忆冲突与工具结果校验策略。',
        question: '当历史记忆与实时状态冲突时，你会如何决定优先级并验证结果？',
        follow_ups: ['如何处理工具返回过期数据？'],
        positive_signals: ['能说明生命周期与冲突处理。'],
        risk_signals: ['只描述检索流程，不说明失效边界。'],
        pass_criteria: ['明确冲突策略、观测指标和失败回退。'],
        suggested_minutes: 10,
      },
    ],
    evidence_excerpts: [{
      turn_id: 'turn_003',
      conclusion: '回答支持当前能力判断。',
      quote: '节点只返回增量更新',
      interpretation: '能够说明状态更新与合并职责。',
      limitation: '尚未证明更大规模场景下的迁移能力。',
    }],
  },
  job_match: {
    raw_score: 82,
    published: true,
    fit_level: '有条件匹配',
    coverage: 0.84,
    confidence: 'medium',
    limiting_reasons: [{
      reason_type: 'unverified',
      text: '生产压力下的恢复边界尚未通过独立场景验证。',
      sources: [],
    }],
  },
  radar_dimensions: Array.from({ length: 6 }, (_, index) => ({
    dimension_id: `role_dim_0${index + 1}`,
    name: ['Agent 编排', '任务建模', '上下文工程', 'AI 交付', '可靠性', '持续演进'][index],
    score: [88, 84, 79, 81, 68, 62][index],
    level: 'L3',
    coverage: 0.8,
    confidence: index >= 4 ? 'low' : 'medium',
    reasons: index === 0 ? [{
      reason_type: 'strength',
      text: '能够解释状态流转。',
      sources: [{
        turn_id: 'turn_003',
        conclusion: '回答支持当前能力判断。',
        quote: '节点只返回增量更新',
        interpretation: '回答包含可复核的状态边界。',
        limitation: '尚未证明更大规模场景下的迁移能力。',
      }],
    }] : [],
  })),
  narrative: {
    executive_summary: '当前证据支持进入结构化复试，但复杂生产场景仍需核验。',
    strengths: [],
    risks: [{ text: '复杂故障迁移待确认。', dimension_names: ['可靠性'] }],
    unverified_areas: [{ text: '成本与延迟取舍待确认。', dimension_names: ['持续演进'] }],
    fit_contexts: [],
    development_actions: [],
  },
  interview_path: [{ turn_id: 'turn_003', question_mode: 'scenario', outcome: 'supporting' }],
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
      evidence_status: 'none',
      evidence_cta: '查看本轮依据',
    },
  ],
  claim_verifications: [],
  assessment_limitations: ['仅基于当前面试证据，最终判断需结合人工复试。'],
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

async function openReport(page: import('@playwright/test').Page, path = '/demo/assessment') {
  await page.route('**/api/demo/assessment', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(reportFixture),
  }))
  await page.goto(path)
  await expect(page.getByRole('heading', { name: '能力雷达' })).toBeVisible()
}

for (const viewportWidth of [390, 840, 960, 1100, 1440]) {
  test(`keeps radar internals and match panel collision-free at ${viewportWidth}px`, async ({ page }) => {
    await page.setViewportSize({ width: viewportWidth, height: viewportWidth === 390 ? 844 : 1000 })
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
      expect(labelBox!.x).toBeGreaterThanOrEqual(-1)
      expect(labelBox!.x + labelBox!.width).toBeLessThanOrEqual(viewport.width + 1)
      expect(labelBox!.y).toBeGreaterThanOrEqual(-1)
      expect(intersects({
        left: labelBox!.x,
        right: labelBox!.x + labelBox!.width,
        top: labelBox!.y,
        bottom: labelBox!.y + labelBox!.height,
      }, match)).toBe(false)
    }

    for (const axis of await page.locator('.radar-axis').all()) {
      await expect(axis.locator('.radar-axis-halo')).toHaveCount(2)
      for (const halo of await axis.locator('.radar-axis-halo').all()) {
        await expect.poll(() => halo.evaluate((element) => getComputedStyle(element).animationName)).toBe('radar-halo-wave')
        await expect.poll(() => halo.evaluate((element) => getComputedStyle(element).animationIterationCount)).toBe('infinite')
      }
    }
  })
}

test('uses the same enterprise report sections for demo and saved report endpoints', async ({ page }) => {
  const requestedPaths: string[] = []
  const consoleErrors: string[] = []
  const failedResponses: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('response', (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`)
  })
  page.on('request', (request) => {
    if (request.url().includes('/api/')) requestedPaths.push(new URL(request.url()).pathname)
  })
  await page.route('**/api/demo/assessment', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(reportFixture),
  }))
  await page.route('**/api/assessments/ast_001/report', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ ...reportFixture, demo: false }),
  }))

  await page.goto('/demo/assessment')
  await expect(page.getByRole('heading', { name: '企业复试计划' })).toBeVisible()
  const demoSections = await page.locator('.report-page > :is(.report-hero, .report-candidate-panel, .enterprise-decision-brief, .report-overall-panel, .report-layout-primary, .report-signals-panel, .enterprise-reinterview-panel, .report-transcript-collapsible, .report-limitations-collapsible)').evaluateAll((elements) => elements.map((element) => element.className))

  await page.goto('/assessments/ast_001/report')
  await expect(page.getByRole('heading', { name: '企业复试计划' })).toBeVisible()
  const savedSections = await page.locator('.report-page > :is(.report-hero, .report-candidate-panel, .enterprise-decision-brief, .report-overall-panel, .report-layout-primary, .report-signals-panel, .enterprise-reinterview-panel, .report-transcript-collapsible, .report-limitations-collapsible)').evaluateAll((elements) => elements.map((element) => element.className))

  expect(requestedPaths).toContain('/api/demo/assessment')
  expect(requestedPaths).toContain('/api/assessments/ast_001/report')
  expect(savedSections).toEqual(demoSections)
  expect(consoleErrors).toEqual([])
  expect(failedResponses).toEqual([])
})

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
  await expect.poll(() => axis.locator('.radar-axis-halo').first().evaluate((element) => getComputedStyle(element).animationName)).toBe('none')
  await expect.poll(() => axis.locator('.radar-axis-halo').first().evaluate((element) => getComputedStyle(element).opacity)).toBe('0.42')

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
