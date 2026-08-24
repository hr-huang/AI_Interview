import { useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import type { RadarDimensionView } from '../../api/types'

type RadarChartProps = {
  dimensions: RadarDimensionView[]
  onDimensionSelect?: (dimension: RadarDimensionView) => void
}

const VIEWBOX_WIDTH = 560
const VIEWBOX_HEIGHT = 450
const CENTER_X = 258
const CENTER_Y = 224
const RADIUS = 155
const LABEL_RADIUS = 198

function pointAt(index: number, total: number, radius: number): [number, number] {
  const angle = (Math.PI * 2 * index) / Math.max(total, 1) - Math.PI / 2
  return [CENTER_X + Math.cos(angle) * radius, CENTER_Y + Math.sin(angle) * radius]
}

function pointsAttribute(points: Array<[number, number]>): string {
  return points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
}

function scoreForDimension(dimension: RadarDimensionView): number | null {
  if (dimension.level === 'UNVERIFIED' || dimension.score === null) return null
  return Number.isFinite(dimension.score) ? Math.max(0, Math.min(100, dimension.score)) : null
}

function levelLabel(dimension: RadarDimensionView): string {
  return dimension.level === 'UNVERIFIED' ? '未验证' : dimension.level
}

function confidenceLabel(value: string): string {
  const labels: Record<string, string> = {
    high: '高',
    medium: '中',
    low: '低',
  }
  return labels[value] ?? (value || '未标注')
}

function coverageLabel(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`
}

function dimensionButtonLabel(dimension: RadarDimensionView): string {
  const score = scoreForDimension(dimension)
  return score === null
    ? `${dimension.name}，未验证，覆盖率 ${coverageLabel(dimension.coverage)}，置信度 ${confidenceLabel(dimension.confidence)}`
    : `${dimension.name}，分数 ${Math.round(score)}，等级 ${dimension.level}，覆盖率 ${coverageLabel(dimension.coverage)}，置信度 ${confidenceLabel(dimension.confidence)}`
}

export function RadarChart({ dimensions, onDimensionSelect }: RadarChartProps) {
  const [selectedDimensionId, setSelectedDimensionId] = useState<string | null>(null)
  const [activeDimensionId, setActiveDimensionId] = useState<string | null>(null)
  const total = dimensions.length
  const axisPoints = dimensions.map((_, index) => pointAt(index, total, RADIUS))
  const labelPoints = dimensions.map((_, index) => pointAt(index, total, LABEL_RADIUS))
  const verifiedPoints = dimensions
    .map((dimension, index) => {
      const score = scoreForDimension(dimension)
      if (score === null) return null
      return pointAt(index, total, (RADIUS * score) / 100)
    })
    .filter((point): point is [number, number] => point !== null)

  const verifiedCount = verifiedPoints.length

  function selectDimension(dimension: RadarDimensionView) {
    setSelectedDimensionId(dimension.dimension_id)
    onDimensionSelect?.(dimension)
  }

  function handleAxisKeyDown(event: ReactKeyboardEvent<SVGGElement>, dimension: RadarDimensionView) {
    const isSpace = event.key === ' ' || event.key === 'Space' || event.key === 'Spacebar' || event.code === 'Space'
    if (event.key !== 'Enter' && !isSpace) return
    event.preventDefault()
    selectDimension(dimension)
  }

  return (
    <div className="radar-chart" data-dimension-count={total}>
      <div className="radar-graphic-wrap">
        <svg
          className="radar-svg"
          role="img"
          aria-label="能力雷达图"
          viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
          focusable="false"
        >
          <title>能力雷达图</title>
          {[0.25, 0.5, 0.75, 1].map((scale) => (
            <polygon
              className="radar-grid"
              key={scale}
              points={pointsAttribute(axisPoints.map((_, index) => pointAt(index, total, RADIUS * scale)))}
              aria-hidden="true"
            />
          ))}

          {dimensions.map((dimension, index) => {
            const [x, y] = axisPoints[index]
            const [labelX, labelY] = labelPoints[index]
            const unverified = scoreForDimension(dimension) === null
            const selected = selectedDimensionId === dimension.dimension_id
            const active = activeDimensionId === dimension.dimension_id
            const anchor = labelX < CENTER_X - 8 ? 'end' : labelX > CENTER_X + 8 ? 'start' : 'middle'
            return (
              <g
                key={dimension.dimension_id}
                className={`radar-axis ${unverified ? 'is-unverified' : ''} ${selected ? 'is-selected' : ''} ${active ? 'is-active' : ''}`}
                data-radar-axis={dimension.dimension_id}
                data-radar-unverified={unverified ? 'true' : 'false'}
                data-radar-selected={selected ? 'true' : 'false'}
                data-radar-active={active ? 'true' : 'false'}
                data-radar-index={index}
                role="button"
                tabIndex={0}
                aria-label={dimensionButtonLabel(dimension)}
                aria-pressed={selected}
                style={{ '--radar-index': index } as CSSProperties}
                onClick={() => selectDimension(dimension)}
                onKeyDown={(event) => handleAxisKeyDown(event, dimension)}
                onMouseEnter={() => setActiveDimensionId(dimension.dimension_id)}
                onMouseLeave={() => setActiveDimensionId(null)}
                onFocus={() => setActiveDimensionId(dimension.dimension_id)}
                onBlur={() => setActiveDimensionId(null)}
              >
                <line x1={CENTER_X} y1={CENTER_Y} x2={x} y2={y} />
                <circle
                  className="radar-axis-halo"
                  cx={x}
                  cy={y}
                  r="8"
                  aria-hidden="true"
                  style={{ '--radar-index': index } as CSSProperties}
                />
                <circle className="radar-axis-hit" cx={x} cy={y} r="22" aria-hidden="true" />
                <circle className="radar-axis-point" cx={x} cy={y} r="8" />
                <text x={labelX} y={labelY} textAnchor={anchor}>
                  {dimension.name}
                </text>
              </g>
            )
          })}

          {verifiedPoints.length > 0 ? (
            <polygon
              className={`radar-score-polygon ${verifiedCount < 3 ? 'is-partial' : ''}`}
              data-radar-score-polygon="true"
              points={pointsAttribute(verifiedPoints)}
            />
          ) : null}
          {verifiedPoints.map(([x, y], index) => (
            <circle className="radar-score-point" key={`${x}-${y}-${index}`} cx={x} cy={y} r="7" />
          ))}
        </svg>
        <p className="radar-interaction-hint">
          <span aria-hidden="true">↗</span>
          点击任一能力维度，查看评分依据与原始问答
        </p>
        {verifiedCount < total ? (
          <p className="radar-coverage-note">
            当前已验证 {verifiedCount} / {total} 个维度；未验证维度不会按 0 分绘制。
          </p>
        ) : null}
        <div className="radar-scale-note" aria-hidden="true">
          <span>100</span>
          <span>能力得分 · 仅展示已形成证据的结果</span>
        </div>
      </div>

      <div className="radar-table-wrap">
        <table className="radar-table">
          <caption className="sr-only">能力维度、分数、等级、覆盖率和置信度</caption>
          <thead>
            <tr>
              <th scope="col">维度</th>
              <th scope="col">分数 / 等级</th>
              <th scope="col">覆盖率</th>
              <th scope="col">置信度</th>
            </tr>
          </thead>
          <tbody>
            {dimensions.map((dimension) => {
              const score = scoreForDimension(dimension)
              const selected = selectedDimensionId === dimension.dimension_id
              const active = activeDimensionId === dimension.dimension_id
              return (
                <tr
                  key={dimension.dimension_id}
                  className={`${score === null ? 'is-unverified ' : ''}${selected ? 'is-selected ' : ''}${active ? 'is-active' : ''}`}
                  data-radar-row={dimension.dimension_id}
                  data-radar-selected={selected ? 'true' : 'false'}
                  data-radar-active={active ? 'true' : 'false'}
                >
                  <td>
                    <button
                      className={`radar-dimension-button ${selected ? 'is-selected' : ''} ${active ? 'is-active' : ''}`}
                      type="button"
                      aria-label="查看该能力维度的评分依据"
                      aria-describedby={`${dimension.dimension_id}-label`}
                      onClick={() => selectDimension(dimension)}
                      onMouseEnter={() => setActiveDimensionId(dimension.dimension_id)}
                      onMouseLeave={() => setActiveDimensionId(null)}
                      onFocus={() => setActiveDimensionId(dimension.dimension_id)}
                      onBlur={() => setActiveDimensionId(null)}
                    >
                      <span className="radar-dimension-label" id={`${dimension.dimension_id}-label`}>
                        <span className="radar-row-marker" aria-hidden="true" />
                        {String(dimensions.indexOf(dimension) + 1).padStart(2, '0')} · {dimension.name}
                      </span>
                      <span className="radar-row-action" aria-hidden="true">查看依据 <span>→</span></span>
                    </button>
                  </td>
                  <td>
                    {score === null ? (
                      <span className="radar-score-unverified">未验证</span>
                    ) : (
                      <span className="radar-score-value">{Math.round(score)} <small>{dimension.level}</small></span>
                    )}
                  </td>
                  <td>{coverageLabel(dimension.coverage)}</td>
                  <td>{confidenceLabel(dimension.confidence)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default RadarChart
