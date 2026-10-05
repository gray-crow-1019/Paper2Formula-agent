import katex from 'katex'
import type { Anchor, Equation, Result } from './types'
import Icon from './Icon'

const statusLabels: Record<string, string> = {
  pass: '通过', fail: '需核对', unknown: '未知', verified: '已确认', confirmed: '已确认',
  pending: '待复核', resolved: '已处理', excluded: '已排除', source_explicit: '原文明示',
  user_supplied: '用户补充', derived: '推导', inferred: '推断', recognized: '已识别',
}

export function labelFor(value: string | null | undefined) { return value ? (statusLabels[value] ?? value) : '未记录' }

export function MathFormula({ latex }: { latex: string }) {
  const markup = katex.renderToString(latex, {
    throwOnError: false, displayMode: true, trust: false, strict: 'ignore', maxExpand: 1000,
  })
  return <div className="math-formula" dangerouslySetInnerHTML={{ __html: markup }} />
}

export function EvidenceLinks({ ids, anchors, onSelect }: { ids: string[]; anchors: Anchor[]; onSelect: (id: string) => void }) {
  if (!ids.length) return <span className="missing-evidence">暂无原文证据</span>
  return <span className="evidence-links">{ids.map((id) => {
    const anchor = anchors.find((entry) => entry.id === id)
    return <button className="evidence-link" key={id} disabled={!anchor} onClick={() => onSelect(id)} title={anchor?.quote}><Icon name="external" size={12} />{anchor ? `p.${anchor.page}` : id}<span>{id}</span></button>
  })}</span>
}

function EquationCard({ equation, result, active, onSelect, onEvidence }: {
  equation: Equation; result: Result; active: boolean; onSelect: () => void; onEvidence: (id: string) => void
}) {
  const checks = [['syntax', '公式语法'], ['definitions', '符号定义'], ['units', '单位校验']] as const
  const pendingCount = result.review_items.filter((review) => review.equation_id === equation.id && review.status === 'pending').length
  return <article className={`equation-card ${active ? 'active' : ''}`} onClick={onSelect}>
    <div className="equation-heading"><div><span className="equation-id">{equation.id}</span>{equation.original_number && <span className="original-number">原文 ({equation.original_number.replace(/^\(|\)$/g, '')})</span>}</div>{equation.status === 'excluded' || equation.excluded ? <span className="pill neutral">已排除</span> : pendingCount ? <span className="pill warning">{pendingCount} 项待复核</span> : <span className="pill success">已解析</span>}</div>
    <div className="formula-box"><MathFormula latex={equation.latex} /><EvidenceLinks ids={equation.anchor_ids} anchors={result.anchors} onSelect={onEvidence} /></div>
    <p className="explanation">{equation.explanation || '尚未得到有原文依据的公式说明。'}</p>
    {equation.symbols.length > 0 && <div className="symbol-table-wrap"><table className="symbol-table"><thead><tr><th>符号</th><th>含义与来源</th><th>数值 / 单位</th><th>证据</th></tr></thead><tbody>{equation.symbols.map((symbol) => <tr key={symbol.id}>
      <td><span className="symbol-name">{symbol.name}</span><small className="muted">{symbol.id}</small></td>
      <td><span className={symbol.meaning ? '' : 'unknown-text'}>{symbol.meaning || '定义未知'}</span><small className="provenance">{labelFor(symbol.provenance)}{symbol.reasons?.length ? ` · ${symbol.reasons.join('；')}` : ''}</small></td>
      <td>{symbol.values.length ? symbol.values.map((value) => <div className="parameter-value" key={value.id}><span>{value.raw_value ?? '数值未知'} <span className={!value.raw_unit ? 'unknown-text' : ''}>{value.raw_unit ?? '单位未知'}</span></span><small className="provenance">{labelFor(value.provenance)}</small>{value.evidence_anchor_ids?.length > 0 && <EvidenceLinks ids={value.evidence_anchor_ids} anchors={result.anchors} onSelect={onEvidence} />}</div>) : <span className="unknown-text">未给出数值</span>}</td>
      <td><EvidenceLinks ids={symbol.evidence_anchor_ids} anchors={result.anchors} onSelect={onEvidence} /></td>
    </tr>)}</tbody></table></div>}
    <div className="validation-row">{checks.map(([key, title]) => {
      const finding = equation.validation[key]
      return <span key={key} className={`validation ${finding.status}`} title={finding.reasons.join('；')}><span />{title} · {labelFor(finding.status)}</span>
    })}</div>
  </article>
}

export default function Report({ result, activeEquation, query, onSelect, onEvidence }: {
  result: Result; activeEquation: string | null; query: string; onSelect: (id: string) => void; onEvidence: (id: string) => void
}) {
  const equations = result.equations.filter((equation) => `${equation.id} ${equation.latex} ${equation.explanation ?? ''} ${equation.symbols.map((s) => `${s.name} ${s.meaning ?? ''}`).join(' ')}`.toLowerCase().includes(query.toLowerCase()))
  return <div className="report-content">
    {result.status !== 'published' && <div className="draft-notice"><Icon name="warning" size={16} /><span>报告草稿 · 完成复核并统一发布后可下载最终成果。</span></div>}
    {!result.equations.length && <div className="content-empty"><Icon name="search" size={30} /><h3>未检测到符合首版范围的公式</h3><p>报告仍保留处理信息与来源记录。可查看导出的结构化结果。</p></div>}
    {result.equations.length > 0 && !equations.length && <div className="content-empty"><Icon name="search" size={30} /><h3>没有匹配的公式</h3><p>尝试搜索公式编号、符号或定义。</p></div>}
    {equations.map((equation) => <EquationCard key={equation.id} equation={equation} result={result} active={equation.id === activeEquation} onSelect={() => onSelect(equation.id)} onEvidence={onEvidence} />)}
    {!!result.relations?.length && <div className="relations-card"><h3>原文明示的公式关系</h3>{result.relations.map((relation, index) => <div key={index}><span>{relation.from_equation_id ?? '未知公式'}</span><Icon name="arrow" size={14} /><span>{relation.to_equation_id ?? '未知公式'}</span><small>{relation.relation_type}</small><EvidenceLinks ids={relation.evidence_anchor_ids ?? []} anchors={result.anchors} onSelect={onEvidence} /></div>)}</div>}
    <div className="report-footer"><Icon name="layers" size={14} />结果版本 v{result.result_version} · {result.equations.length} 个公式 · {result.anchors.length} 个证据锚点</div>
  </div>
}
