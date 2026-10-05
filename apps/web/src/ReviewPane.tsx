import { useEffect, useState } from 'react'
import type { Result, ReviewDecision, ReviewItem, Task } from './types'
import { EvidenceLinks, MathFormula } from './Report'
import Icon from './Icon'

const kindLabels: Record<string, string> = {
  recognition: '公式识别', syntax: '公式语法', definition: '符号定义', missing_definition: '定义缺失',
  definitions: '符号定义', unit: '单位核对', units: '单位核对', missing_unit: '单位缺失',
  evidence: '来源证据', conflict: '定义冲突', unknown: '待确认内容',
  no_equations: '未识别到公式',
}

export default function ReviewPane({ result, reviews, task, submitting, readOnly = false, readOnlyReason, onDecision, onEvidence }: {
  result: Result; reviews: ReviewItem[]; task: Task; submitting: boolean; readOnly?: boolean; readOnlyReason?: string
  onDecision: (review: ReviewItem, decision: ReviewDecision) => Promise<void>
  onEvidence: (id: string) => void
}) {
  const pending = reviews.filter((review) => review.status === 'pending')
  const [reviewId, setReviewId] = useState<string | null>(null)
  const review = pending.find((item) => item.id === reviewId) ?? pending[0]
  const equation = result.equations.find((entry) => entry.id === review?.equation_id)
  const symbol = equation?.symbols.find((entry) => entry.id === review?.symbol_id)
  const parameter = symbol?.values.find((entry) => entry.id === review?.parameter_id) ?? symbol?.values[0]
  const [editing, setEditing] = useState(false)
  const [latex, setLatex] = useState('')
  const [explanation, setExplanation] = useState('')
  const [meaning, setMeaning] = useState('')
  const [rawValue, setRawValue] = useState('')
  const [rawUnit, setRawUnit] = useState('')
  const [excluded, setExcluded] = useState(false)
  const [evidenceIds, setEvidenceIds] = useState<string[]>([])
  const locked = submitting || readOnly

  useEffect(() => {
    setEditing(false)
    setLatex(equation?.latex ?? '')
    setExplanation(equation?.explanation ?? '')
    setMeaning(symbol?.meaning ?? '')
    setRawValue(parameter?.raw_value ?? '')
    setRawUnit(parameter?.raw_unit ?? '')
    setExcluded(false)
    setEvidenceIds(review?.anchor_ids ?? [])
  }, [review?.id, task.revision, equation?.latex, equation?.explanation, symbol?.meaning, parameter?.raw_value, parameter?.raw_unit])

  async function submit(action: ReviewDecision['action']) {
    if (!review || locked) return
    const patch: ReviewDecision['patch'] = {}
    if (action === 'edit') {
      if (latex !== equation?.latex) patch.latex = latex
      if (explanation !== (equation?.explanation ?? '')) patch.explanation = explanation
      if (symbol && meaning !== (symbol.meaning ?? '')) patch.meaning = meaning
      if (symbol && rawValue !== (parameter?.raw_value ?? '')) patch.raw_value = rawValue
      if (symbol && rawUnit !== (parameter?.raw_unit ?? '')) patch.raw_unit = rawUnit
      if (excluded) patch.excluded = true
    }
    await onDecision(review, { expected_revision: task.revision, action, patch, evidence_anchor_ids: action === 'keep_unknown' ? [] : evidenceIds })
  }

  if (!review) return <div className="content-empty review-done"><span className="completion-icon"><Icon name="check" size={28} /></span><h3>当前复核项已全部处理</h3><p>{readOnly ? '此版本没有待处理的复核项。' : '工作流将重新校验，并生成同一版本的三类成果。'}</p></div>

  const availableAnchors = result.anchors.filter((anchor) => review.anchor_ids.includes(anchor.id) || equation?.anchor_ids.includes(anchor.id) || symbol?.evidence_anchor_ids.includes(anchor.id) || parameter?.evidence_anchor_ids.includes(anchor.id))
  const changed = latex !== equation?.latex || explanation !== (equation?.explanation ?? '') || (symbol && (meaning !== (symbol.meaning ?? '') || rawValue !== (parameter?.raw_value ?? '') || rawUnit !== (parameter?.raw_unit ?? ''))) || excluded
  const canConfirm = !!equation && (!['missing_definition', 'definition', 'definitions'].includes(review.kind) || !!symbol?.meaning) && (review.kind !== 'units' || equation.validation.units.status === 'pass')

  return <div className="review-content">
    <div className="review-summary"><span className="eyebrow">HUMAN REVIEW</span><h3>让每条解释都有依据</h3><p>确认识别结果、修订内容，或明确保留未知。你的决定会保存到审计记录。</p><div className="review-count"><strong>{pending.length}</strong><span>待处理</span><span className="divider" /><strong>{reviews.length - pending.length}</strong><span>已处理</span></div></div>
    {readOnly && <div className="draft-notice"><Icon name="book" size={16} /><span>{readOnlyReason || '当前版本仅供查看。'}</span></div>}
    <label className="review-picker">选择复核项<select value={review.id} disabled={submitting} onChange={(event) => { setReviewId(event.target.value); const next = pending.find((item) => item.id === event.target.value); if (next?.anchor_ids[0]) onEvidence(next.anchor_ids[0]) }}>{pending.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.equation_id ?? '文档检查'} · {kindLabels[item.kind] ?? item.kind}</option>)}</select></label>
    <article className="review-card">
      <div className="equation-heading"><span className="equation-id">{review.id}</span><span className="pill warning">{kindLabels[review.kind] ?? review.kind}</span></div>
      {equation && <div className="formula-box"><MathFormula latex={equation.latex} /><span className="muted">{equation.id}{symbol ? ` · ${symbol.id} (${symbol.name})` : ''}</span></div>}
      <div className="review-reasons"><strong>需要复核的原因</strong><ul>{review.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></div>
      {symbol && <div className="current-definition"><span>当前定义</span><p>{symbol.meaning || '未找到明确的定义'}</p>{parameter && <small>数值：{parameter.raw_value ?? '未知'} · 单位：{parameter.raw_unit ?? '未知'}</small>}</div>}
      <div className="review-evidence"><strong>关联原文</strong><EvidenceLinks ids={review.anchor_ids} anchors={result.anchors} onSelect={onEvidence} /></div>
      <button className="text-button edit-toggle" disabled={locked || !equation} onClick={() => setEditing((value) => !value)}>{editing ? '收起修订内容' : '修订识别或补充定义'}<Icon name="chevron" size={14} style={{ transform: editing ? 'rotate(-90deg)' : 'rotate(90deg)' }} /></button>
      {editing && <div className="edit-fields">
        <label>LaTeX 公式<textarea rows={3} value={latex} onChange={(event) => setLatex(event.target.value)} disabled={locked} spellCheck={false} /></label>
        <label>公式说明<textarea rows={3} value={explanation} onChange={(event) => setExplanation(event.target.value)} disabled={locked} /></label>
        {symbol && <label>符号 {symbol.name} 的定义<input value={meaning} onChange={(event) => setMeaning(event.target.value)} disabled={locked} /></label>}
        {symbol && <div className="field-row"><label>原文数值<input value={rawValue} onChange={(event) => setRawValue(event.target.value)} disabled={locked} /></label><label>原文单位<input value={rawUnit} onChange={(event) => setRawUnit(event.target.value)} disabled={locked} /></label></div>}
        <label className="checkbox-label"><input type="checkbox" checked={excluded} onChange={(event) => setExcluded(event.target.checked)} disabled={locked} />识别错误，从最终公式列表中排除此项</label>
        <p className="edit-note">无原文证据的补充将记录为“用户补充”。空白单位仍表示未知，不会被视为无量纲。</p>
      </div>}
      {availableAnchors.length > 0 && <fieldset className="evidence-choice"><legend>决定所依据的证据</legend>{availableAnchors.map((anchor) => <label className="checkbox-label" key={anchor.id}><input type="checkbox" disabled={locked} checked={evidenceIds.includes(anchor.id)} onChange={(event) => setEvidenceIds((ids) => event.target.checked ? [...ids, anchor.id] : ids.filter((id) => id !== anchor.id))} /><span>{anchor.id} · 第 {anchor.page} 页 <small>{anchor.quote.slice(0, 100)}</small></span><button className="icon-button" type="button" aria-label={`定位 ${anchor.id}`} onClick={() => onEvidence(anchor.id)}><Icon name="external" size={14} /></button></label>)}</fieldset>}
      <div className="review-actions"><button className="button primary" disabled={locked || (editing ? !changed : !canConfirm)} onClick={() => void submit(editing ? 'edit' : 'confirm')}>{submitting ? <span className="spinner" /> : <Icon name="check" size={16} />}{editing ? '保存修订' : '确认当前内容'}</button><button className="button secondary" disabled={locked} onClick={() => void submit('keep_unknown')}>保留未知</button></div>
    </article>
  </div>
}
