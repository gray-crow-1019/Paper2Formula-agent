import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import { api, ApiError, messageOf } from './api'
import type { Anchor, DocumentRecord, Result, ReviewDecision, ReviewItem, Task } from './types'
import Icon from './Icon'
import PdfViewer from './PdfViewer'
import Report from './Report'
import ReviewPane from './ReviewPane'

const stageLabels: Record<string, string> = {
  queued: '等待处理', inspecting: '检查文档', normalizing: '转换为规范 PDF', parsing: '提取论文结构',
  recognizing: '识别公式', resolving: '解析符号与参数', relating: '整理公式关系', validating: '校验结果',
  waiting_review: '等待人工复核', exporting: '生成报告与批注', completed: '分析完成', failed: '处理失败',
  cancelled: '已取消', cancel_requested: '正在取消', uploaded: '已上传', normalized: '已规范化',
}
const terminal = new Set(['completed', 'failed', 'cancelled'])
const steps = [
  ['inspecting', '文档检查'], ['parsing', '结构提取'], ['recognizing', '公式识别'],
  ['resolving', '参数解析'], ['validating', '校验与复核'], ['exporting', '成果导出'],
] as const

function readLocation() {
  const match = window.location.pathname.match(/^\/documents\/([^/]+)\/viewer\/?$/)
  const params = new URLSearchParams(window.location.search)
  const version = Number(params.get('version'))
  return { documentId: match ? decodeURIComponent(match[1]) : null, anchorId: params.get('anchor'), taskId: params.get('task_id'), version: Number.isInteger(version) && version > 0 ? version : null }
}

function taskStatus(status: string) { return stageLabels[status] ?? status }
function taskDate(value: string) { return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) }

export default function App() {
  const initialRoute = useRef(readLocation())
  const [routeRevision, setRouteRevision] = useState(0)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [loadingDocs, setLoadingDocs] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(initialRoute.current.documentId)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(initialRoute.current.version)
  const [tasks, setTasks] = useState<Task[]>([])
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<Task | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const [resultTaskId, setResultTaskId] = useState<string | null>(null)
  const [reviews, setReviews] = useState<ReviewItem[]>([])
  const [reviewRevision, setReviewRevision] = useState<number | null>(null)
  const [anchor, setAnchor] = useState<Anchor | null>(null)
  const [selectedAnchorId, setSelectedAnchorId] = useState<string | null>(initialRoute.current.anchorId)
  const [activeEquation, setActiveEquation] = useState<string | null>(null)
  const [view, setView] = useState<'report' | 'review'>('report')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [dragging, setDragging] = useState(false)
  const [serviceConnected, setServiceConnected] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const reloadRef = useRef<(() => Promise<void>) | null>(null)
  const document = documents.find((entry) => entry.id === selectedId) ?? null
  const pendingCount = reviews.filter((review) => review.status === 'pending').length

  const loadDocuments = useCallback(async (signal?: AbortSignal) => {
    setLoadingDocs(true)
    try {
      const response = await api.documents(signal)
      setDocuments(response.items)
      setServiceConnected(true)
      setSelectedId((current) => current ?? response.items[0]?.id ?? null)
    } catch (cause) {
      const message = messageOf(cause)
      if (message) { setError(message); setServiceConnected(false) }
    } finally { if (!signal?.aborted) setLoadingDocs(false) }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void loadDocuments(controller.signal)
    return () => controller.abort()
  }, [loadDocuments])

  useEffect(() => {
    function navigate() {
      const route = readLocation()
      initialRoute.current = route
      setSelectedId(route.documentId)
      setSelectedVersion(route.version)
      setSelectedAnchorId(route.anchorId)
      setRouteRevision((revision) => revision + 1)
    }
    window.addEventListener('popstate', navigate)
    return () => window.removeEventListener('popstate', navigate)
  }, [])

  useEffect(() => {
    setTask(null)
    setActiveTaskId(null)
    setTasks([])
    setResult(null)
    setResultTaskId(null)
    setReviews([])
    setReviewRevision(null)
    setAnchor(null)
    setActiveEquation(null)
    setQuery('')
    if (!selectedId) return
    const controller = new AbortController()
    void Promise.all([api.document(selectedId, controller.signal), api.tasks(selectedId, controller.signal)]).then(([metadata, response]) => {
      if (controller.signal.aborted) return
      setDocuments((items) => items.some((entry) => entry.id === metadata.id) ? items.map((entry) => entry.id === metadata.id ? metadata : entry) : [metadata, ...items])
      const sorted = [...response.items].sort((a, b) => b.created_at.localeCompare(a.created_at))
      setTasks(sorted)
      const route = initialRoute.current
      let selectedTask: Task | undefined
      if (route.taskId) {
        selectedTask = sorted.find((entry) => entry.id === route.taskId)
        if (!selectedTask) { setError('链接指定的任务不属于当前文档或已不存在。'); return }
      } else if (route.version) {
        const candidates = sorted.filter((entry) => entry.result_version !== null && entry.result_version >= route.version!)
        if (candidates.length > 1) { setError('该文档的多个分析任务包含此版本。请在任务列表中选择任务，或使用包含 task_id 的证据链接。'); return }
        selectedTask = candidates[0]
        if (!selectedTask) { setError('当前文档没有可读取的指定结果版本。'); return }
      } else selectedTask = sorted[0]
      if (selectedTask) { setTask(selectedTask); setActiveTaskId(selectedTask.id) }
    }).catch((cause: unknown) => { const message = messageOf(cause); if (message) setError(message) })
    return () => controller.abort()
  }, [selectedId, routeRevision])

  useEffect(() => {
    if (!activeTaskId) { reloadRef.current = null; return }
    const controller = new AbortController()
    let timer: number | undefined
    let previousBundle = ''
    let refreshing = false
    async function refresh() {
      if (refreshing || controller.signal.aborted) return
      refreshing = true
      let current: Task | null = null
      let snapshotMismatch = false
      try {
        current = await api.task(activeTaskId!, controller.signal)
        if (controller.signal.aborted) return
        setTask(current)
        setServiceConnected(true)
        setTasks((items) => items.map((entry) => entry.id === current!.id ? current! : entry))
        if (['waiting_review', 'completed'].includes(current.status)) {
          const bundleKey = `${current.id}:${current.revision}:${selectedVersion ?? current.result_version}`
          if (bundleKey !== previousBundle) {
            const [report, reviewResponse, metadata] = await Promise.all([api.result(current, controller.signal, selectedVersion), api.reviews(current.id, controller.signal), api.document(current.document_id, controller.signal)])
            if (controller.signal.aborted) return
            const requestedVersion = selectedVersion ?? current.result_version ?? 1
            if (reviewResponse.revision !== current.revision || report.result_version !== requestedVersion) {
              snapshotMismatch = true
              return
            }
            previousBundle = bundleKey
            setResult(report)
            setResultTaskId(current.id)
            setReviews(report.result_version === current.result_version ? reviewResponse.items : report.review_items)
            setReviewRevision(current.revision)
            setDocuments((items) => items.map((entry) => entry.id === metadata.id ? metadata : entry))
            setActiveEquation((id) => id ?? report.equations[0]?.id ?? null)
            setSelectedAnchorId((id) => id ?? report.equations[0]?.anchor_ids[0] ?? null)
          }
        }
      } catch (cause) {
        if (cause instanceof ApiError && cause.code === 'REVISION_CONFLICT') snapshotMismatch = true
        else {
          const message = messageOf(cause)
          if (message) { setError(message); setServiceConnected(!(cause instanceof TypeError)) }
        }
      } finally {
        refreshing = false
        if (!controller.signal.aborted && (!current || !terminal.has(current.status) || snapshotMismatch)) {
          timer = window.setTimeout(() => void refresh(), documentHidden() ? 5000 : 1500)
        }
      }
    }
    function documentHidden() { return window.document.hidden }
    reloadRef.current = async () => { window.clearTimeout(timer); previousBundle = ''; await refresh() }
    void refresh()
    return () => { controller.abort(); window.clearTimeout(timer); reloadRef.current = null }
  }, [activeTaskId, selectedVersion])

  useEffect(() => {
    if (!result || !selectedAnchorId) { setAnchor(null); return }
    const local = result.anchors.find((entry) => entry.id === selectedAnchorId)
    if (local) { setAnchor(local); return }
    const controller = new AbortController()
    if (!resultTaskId) return
    void api.anchor(result.document_id, selectedAnchorId, result.result_version, controller.signal, resultTaskId).then((entry) => {
      if (!controller.signal.aborted) setAnchor(entry)
    }).catch((cause: unknown) => { const message = messageOf(cause); if (message) setError(message) })
    return () => controller.abort()
  }, [result, selectedAnchorId, resultTaskId])

  function chooseDocument(id: string) {
    setSelectedId(id)
    setSelectedVersion(null)
    setSelectedAnchorId(null)
    setView('report')
    setError('')
    initialRoute.current = { documentId: id, version: null, anchorId: null, taskId: null }
    setRouteRevision((revision) => revision + 1)
    window.history.replaceState(null, '', '/')
  }

  function chooseEvidence(id: string) {
    setSelectedAnchorId(id)
    if (result && resultTaskId) {
      window.history.replaceState(null, '', `/documents/${encodeURIComponent(result.document_id)}/viewer?version=${result.result_version}&anchor=${encodeURIComponent(id)}&task_id=${encodeURIComponent(resultTaskId)}`)
    }
  }

  function chooseTask(id: string) {
    const version = activeTaskId === null ? selectedVersion : null
    const anchorId = activeTaskId === null ? selectedAnchorId : null
    setSelectedVersion(version)
    setSelectedAnchorId(anchorId)
    setResult(null)
    setResultTaskId(null)
    setReviews([])
    setReviewRevision(null)
    setActiveTaskId(id)
    setError('')
    initialRoute.current = { documentId: selectedId, taskId: id, version, anchorId }
    if (selectedId) {
      const params = new URLSearchParams({ task_id: id })
      if (version) params.set('version', String(version))
      if (anchorId) params.set('anchor', anchorId)
      window.history.replaceState(null, '', `/documents/${encodeURIComponent(selectedId)}/viewer?${params}`)
    }
  }

  async function uploadFile(file: File) {
    if (busy) return
    if (!/\.(pdf|docx)$/i.test(file.name)) { setError('请选择 PDF 或 DOCX 文件。真实文件类型将由服务端检查。'); return }
    setBusy('upload')
    setError('')
    try {
      const uploaded = await api.upload(file)
      setDocuments((items) => [uploaded, ...items.filter((entry) => entry.id !== uploaded.id)])
      chooseDocument(uploaded.id)
      const created = await api.start(uploaded.id)
      setTask(created)
      setTasks([created])
      setActiveTaskId(created.id)
    } catch (cause) { setError(messageOf(cause)) } finally {
      setBusy('')
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) { if (event.target.files?.[0]) void uploadFile(event.target.files[0]) }
  function onDrop(event: DragEvent) { event.preventDefault(); setDragging(false); if (event.dataTransfer.files[0]) void uploadFile(event.dataTransfer.files[0]) }

  async function act(action: 'start' | 'retry' | 'cancel') {
    if (busy || !selectedId) return
    setBusy(action)
    setError('')
    try {
      const updated = action === 'start' ? await api.start(selectedId) : action === 'retry' ? await api.retry(task!.id) : await api.cancel(task!.id)
      setTask(updated)
      setSelectedVersion(null)
      setTasks((items) => [updated, ...items.filter((entry) => entry.id !== updated.id)])
      setActiveTaskId(updated.id)
      await reloadRef.current?.()
    } catch (cause) { setError(messageOf(cause)) } finally { setBusy('') }
  }

  async function decide(review: ReviewItem, decision: ReviewDecision) {
    if (!task || busy || !reviewWritable) return
    setBusy('review')
    setError('')
    try {
      const updated = await api.decide(task.id, review.id, decision)
      setTask(updated.task)
      setSelectedVersion(null)
      setReviews((items) => items.map((entry) => entry.id === review.id ? updated.review : entry))
      await reloadRef.current?.()
    } catch (cause) {
      setError(messageOf(cause))
      if (cause instanceof ApiError && cause.status === 409) await reloadRef.current?.()
    } finally { setBusy('') }
  }

  async function download(kind: 'markdown' | 'json' | 'annotated_pdf') {
    if (!result || !resultTaskId || !task || busy) return
    setBusy(kind)
    setError('')
    try {
      const filename = { markdown: 'report.md', json: 'result.json', annotated_pdf: 'annotated.pdf' }[kind]
      await api.download(resultTaskId, kind, result.result_version, filename)
    } catch (cause) { setError(messageOf(cause)) } finally { setBusy('') }
  }

  const published = result?.status === 'published' && task?.status === 'completed' && resultTaskId === task.id
  const progress = Math.max(0, Math.min(100, task?.progress ?? 0))
  const hasViewer = document && (result || document.rendition_id)
  const historicalResult = !!(result && task && result.result_version !== task.result_version)
  const reviewWritable = !!(result && task?.status === 'waiting_review' && resultTaskId === task.id && !historicalResult && reviewRevision === task.revision)
  const reviewTask = task && reviewRevision !== null ? { ...task, revision: reviewRevision } : task
  const readOnlyReason = historicalResult ? '正在查看历史结果版本，复核决定只能提交到当前版本。' : task?.status !== 'waiting_review' ? '任务当前不处于人工复核阶段。' : '正在同步最新复核内容，请稍候。'

  return <div className="app-shell">
    <input ref={fileRef} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" className="sr-only" onChange={onFileChange} disabled={!!busy} />
    <aside className="sidebar">
      <a className="brand" href="/" onClick={(event) => { event.preventDefault(); chooseDocument(selectedId ?? '') }} aria-label="Paper2Formula 首页"><span className="brand-mark">∑</span><div>Paper2Formula<small>RESEARCH WORKSPACE</small></div></a>
      <div className="workspace-label"><span className="workspace-dot" /><span>论文公式工作台</span><span className="version-label">v0.1</span></div>
      <button className="upload-sidebar button" disabled={!!busy} onClick={() => fileRef.current?.click()}>{busy === 'upload' ? <span className="spinner" /> : <Icon name="plus" />}上传论文</button>
      <div className="nav-title"><span>我的论文</span><span>{documents.length.toString().padStart(2, '0')}</span></div>
      <nav className="document-list" aria-label="文档列表">
        {loadingDocs && <p className="sidebar-note">正在读取文档…</p>}
        {!loadingDocs && documents.length === 0 && <p className="sidebar-note">上传第一篇论文，开始分析。</p>}
        {documents.map((entry) => <button key={entry.id} className={`document-item ${entry.id === selectedId ? 'selected' : ''}`} onClick={() => chooseDocument(entry.id)} disabled={busy === 'upload'}><span className="document-icon"><Icon name="file" size={19} /></span><span className="document-item-body"><strong>{entry.filename}</strong><small>{entry.mime?.includes('pdf') ? 'PDF' : 'DOCX'}{entry.page_count ? ` · ${entry.page_count} 页` : ''}</small></span>{entry.id === selectedId && <span className="selected-dot" />}</button>)}
      </nav>
      <div className="sidebar-bottom"><div className="sidebar-note-card"><Icon name="book" size={20} /><strong>从公式，回到原文。</strong><p>每条解释关联证据。未知内容明确保留，修订过程可追溯。</p></div><div className="service-status"><span className={serviceConnected ? 'connected' : ''} />{serviceConnected ? '服务已连接' : '等待服务连接'}<button className="icon-button" title="刷新连接" aria-label="刷新服务连接" disabled={loadingDocs} onClick={() => { setError(''); void loadDocuments() }}><Icon name="refresh" size={14} /></button></div></div>
    </aside>
    <main className="main-area">
      <header className="topbar"><div className="breadcrumb"><Icon name="grid" size={16} /><span>工作空间</span><Icon name="chevron" size={12} /><strong>{document ? '论文分析' : '概览'}</strong></div><div className="topbar-note"><span className="small-dot" />证据驱动 · 人工复核</div></header>
      {error && <div className="error-banner" role="alert"><Icon name="warning" size={18} /><span>{error}</span><button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><Icon name="close" size={16} /></button></div>}
      {!document ? <div className="welcome-page">
        <div className="welcome-heading"><span className="eyebrow">PAPER TO INSIGHT</span><h1>读懂公式，<br /><span>看清每一条依据。</span></h1><p>把论文中的公式、符号与参数整理为可复核的报告。<br />从分析到原文定位，在一个工作空间里完成。</p></div>
        <div className={`upload-zone ${dragging ? 'dragging' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={onDrop}><span className="upload-large"><Icon name="upload" size={28} /></span><h2>{busy === 'upload' ? '正在上传与检查文档…' : '将论文拖放到这里'}</h2><p>支持文本型 PDF 与 DOCX</p><button className="button primary" disabled={!!busy} onClick={() => fileRef.current?.click()}>{busy === 'upload' ? <span className="spinner" /> : <Icon name="plus" size={16} />}选择论文文件</button><small>DOCX 将转换为规范 PDF 后分析 · 首版暂不支持扫描件</small></div>
        <div className="feature-grid"><div><span>01</span><Icon name="search" size={22} /><h3>提取与解释</h3><p>识别公式，整理符号定义、参数值与单位。</p></div><div><span>02</span><Icon name="book" size={22} /><h3>原文对照</h3><p>点击证据定位论文页面，核对每条事实的来源。</p></div><div><span>03</span><Icon name="download" size={22} /><h3>统一导出</h3><p>生成 Markdown 报告、JSON 与独立批注 PDF。</p></div></div>
        <div className="welcome-footer"><span>Paper2Formula</span><span>让论文中的数学表达更清晰。</span></div>
      </div> : <>
        <section className="document-heading"><div><span className="eyebrow">DOCUMENT ANALYSIS</span><h1 title={document.filename}>{document.filename}</h1><p><span>{document.mime?.includes('pdf') ? 'PDF 文档' : 'Word 文档'}</span><span className="meta-separator">/</span><span>{document.page_count ? `${document.page_count} 页` : '页数检查中'}</span><span className="meta-separator">/</span><span>{task ? taskStatus(task.status) : '等待开始分析'}</span>{result && <><span className="meta-separator">/</span><span>结果版本 v{result.result_version}</span></>}</p></div><button className="button secondary compact" disabled={!!busy} onClick={() => fileRef.current?.click()}><Icon name="plus" size={15} />上传新论文</button></section>
        {task && <section className={`task-progress ${task.status === 'failed' ? 'failed' : ''}`} aria-label="任务进度"><div className="progress-top"><span className={`pill ${task.status === 'waiting_review' ? 'warning' : task.status === 'completed' ? 'success' : 'neutral'}`}>{!terminal.has(task.status) && task.status !== 'waiting_review' && <span className="spinner" />}{taskStatus(task.status)}</span><div className="progress-detail">{task.status === 'failed' ? task.error?.message : task.status === 'waiting_review' ? '请处理复核项，随后继续校验与导出。' : task.status === 'completed' ? '三类成果已发布，可随时下载。' : '任务在后台运行，可切换文档查看。'}</div>{!terminal.has(task.status) && <button className="text-button" disabled={!!busy} onClick={() => void act('cancel')}>取消任务</button>}{task.status === 'failed' && task.error?.retryable && <button className="button secondary compact" disabled={!!busy} onClick={() => void act('retry')}><Icon name="refresh" size={14} />重试</button>}<span className="progress-number">{Math.round(progress)}%</span></div><div className="progress-track"><span style={{ width: `${progress}%` }} /></div><div className="progress-steps">{steps.map(([status, label], index) => { const order = ['queued', 'inspecting', 'normalizing', 'parsing', 'recognizing', 'resolving', 'relating', 'validating', 'waiting_review', 'exporting', 'completed']; const done = order.indexOf(task.status) > order.indexOf(status); return <span key={status} className={done ? 'done' : task.status === status ? 'current' : ''}><i>{done ? <Icon name="check" size={10} /> : index + 1}</i>{label}</span> })}</div></section>}
        {!task && <div className="start-analysis"><Icon name="file" size={24} /><div><strong>论文已保存</strong><p>开始提取公式与参数，生成可追溯的报告。</p></div><button className="button primary" disabled={!!busy} onClick={() => void act('start')}>开始分析<Icon name="arrow" size={16} /></button></div>}
        <div className="workspace-toolbar"><div className="tabs" role="tablist" aria-label="报告与复核"><button role="tab" aria-selected={view === 'report'} onClick={() => setView('report')}>公式报告{result && <span>{result.equations.length}</span>}</button><button role="tab" aria-selected={view === 'review'} onClick={() => setView('review')}>人工复核{pendingCount > 0 && <span className="warning-count">{pendingCount}</span>}</button></div><div className="workspace-actions">{tasks.length > 1 && <label className="task-selector"><span className="sr-only">历史任务</span><select value={activeTaskId ?? ''} onChange={(event) => chooseTask(event.target.value)}>{!activeTaskId && <option value="" disabled>请选择分析任务</option>}{tasks.map((entry) => <option key={entry.id} value={entry.id}>{taskDate(entry.created_at)} · {taskStatus(entry.status)}</option>)}</select></label>}{published && <><button className="button secondary compact" disabled={!!busy} onClick={() => void download('markdown')}><Icon name="download" size={14} />报告</button><button className="button secondary compact" disabled={!!busy} onClick={() => void download('json')}>JSON</button><button className="button primary compact" disabled={!!busy} onClick={() => void download('annotated_pdf')}><Icon name="download" size={14} />批注 PDF</button></>}</div></div>
        <div className={`analysis-workspace ${hasViewer ? '' : 'no-viewer'}`}>
          <section className="report-panel" aria-label={view === 'report' ? '公式报告' : '人工复核'}>
            {view === 'report' && result && <div className="report-filter"><div><strong>公式与参数</strong><span>{result.equations.length} 个公式</span></div><label className="search-field"><Icon name="search" size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索公式、符号或定义" aria-label="搜索公式" /></label></div>}
            {result && task ? view === 'report' ? <Report result={result} activeEquation={activeEquation} query={query} onSelect={setActiveEquation} onEvidence={chooseEvidence} /> : <ReviewPane result={result} reviews={reviews} task={reviewTask!} readOnly={!reviewWritable} readOnlyReason={readOnlyReason} submitting={busy === 'review'} onDecision={decide} onEvidence={chooseEvidence} /> : <div className="content-empty"><span className={`empty-analysis-icon ${task && !terminal.has(task.status) ? 'working' : ''}`}><Icon name="layers" size={34} /></span><h3>{task?.status === 'failed' ? '分析未完成' : task?.status === 'cancelled' ? '任务已取消' : task ? '正在准备公式报告' : '等待开始分析'}</h3><p>{task?.error ? `${task.error.message} (${task.error.code})` : task?.status === 'cancelled' ? '原文仍然保留，可创建新的分析任务。' : '完成识别与校验后，公式、符号和原文证据将在这里展示。'}</p>{task?.status === 'cancelled' && <button className="button primary" disabled={!!busy} onClick={() => void act('start')}>重新分析</button>}{view === 'review' && <small>复核项会在校验结束后出现。</small>}</div>}
          </section>
          {hasViewer && <PdfViewer key={document.id} documentId={document.id} renditionId={result?.rendition_id ?? document.rendition_id} anchor={anchor} pageCount={document.page_count} />}
        </div>
      </>}
    </main>
  </div>
}
