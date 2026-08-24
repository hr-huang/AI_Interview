import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import type { RadarDimensionView } from '../../api/types'
import { RadarChart } from './RadarChart'

const dimensions: RadarDimensionView[] = [
  {
    dimension_id: 'custom_a',
    name: 'Agent 编排',
    score: 86,
    level: 'L3',
    coverage: 0.8,
    confidence: 'high',
    reasons: [],
  },
  {
    dimension_id: 'custom_b',
    name: '待核验能力',
    score: null,
    level: 'UNVERIFIED',
    coverage: 0,
    confidence: 'low',
    reasons: [],
  },
]

describe('RadarChart', () => {
  test('renders dimensions from data and never converts unverified to zero', () => {
    render(<RadarChart dimensions={dimensions} />)

    expect(screen.getByText('Agent 编排')).toBeVisible()
    expect(screen.getByText('待核验能力')).toBeVisible()
    expect(screen.getByText('未验证')).toBeVisible()
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument()

    const svg = screen.getByRole('img', { name: '能力雷达图' })
    expect(svg.querySelectorAll('[data-radar-axis]')).toHaveLength(2)
    expect(svg.querySelector('[data-radar-unverified="true"]')).toBeInTheDocument()
    expect(svg.querySelector('[data-radar-score-polygon]')).toBeInTheDocument()
  })

  test('keeps every dimension keyboard reachable through the companion table', () => {
    render(<RadarChart dimensions={dimensions} />)

    expect(screen.getByRole('button', { name: /Agent 编排/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /待核验能力/ })).toBeVisible()
  })
})
