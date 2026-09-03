import type { ReinterviewFocusView } from '../../api/types'

export function CandidateGrowthPlan({ items }: { items: ReinterviewFocusView[] }) {
  const plan = (items ?? []).slice(0, 3)

  return (
    <section className="report-panel development-panel enterprise-reinterview-panel" aria-labelledby="candidate-growth-title">
      <div className="report-section-heading">
        <div>
          <p className="eyebrow">候选人提升 / 可执行</p>
          <h2 id="candidate-growth-title">候选人成长建议</h2>
        </div>
        <span>{plan.length} 项优先练习</span>
      </div>

      {plan.length > 0 ? (
        <div className="development-grid reinterview-grid">
          {plan.map((item, index) => (
            <article key={`${item.priority}-${item.dimension_name}`} className="reinterview-card">
              <span className="development-index">练习 {String(index + 1).padStart(2, '0')}</span>
              <h3>{item.dimension_name}</h3>

              <div className="reinterview-question">
                <span>当前证据缺口</span>
                <p>{item.reason}</p>
              </div>

              <div className="reinterview-question">
                <span>实战练习</span>
                <p>用一个真实项目、课程项目或可复现实验完成这项任务：{item.question}</p>
              </div>

              {(item.follow_ups ?? []).length > 0 ? (
                <div className="reinterview-followups">
                  <span>复盘重点</span>
                  <ul>{item.follow_ups.map((followUp) => <li key={followUp}>{followUp}</li>)}</ul>
                </div>
              ) : null}

              {(item.pass_criteria ?? []).length > 0 ? (
                <div className="development-acceptance">
                  <strong>验收标准</strong>
                  <span>{item.pass_criteria.join('；')}</span>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      ) : (
        <p className="report-empty">当前没有足够证据生成针对性的成长任务；未验证不等于能力不足。</p>
      )}

      <p className="report-disclaimer">
        成长建议只把本次面试中的证据缺口转成可执行练习，不修改评分，也不把未验证项直接解释为能力不足。
      </p>
    </section>
  )
}

export default CandidateGrowthPlan
