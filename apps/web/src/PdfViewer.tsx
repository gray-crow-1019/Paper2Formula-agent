import { useEffect, useMemo, useRef, useState } from 'react'
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import type { PageViewport } from 'pdfjs-dist/types/src/display/display_utils'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api, messageOf } from './api'
import type { Anchor } from './types'
import Icon from './Icon'

GlobalWorkerOptions.workerSrc = workerUrl

export default function PdfViewer({ documentId, renditionId, anchor, pageCount }: {
  documentId: string
  renditionId?: string | null
  anchor: Anchor | null
  pageCount: number | null
}) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [zoom, setZoom] = useState(1)
  const [width, setWidth] = useState(550)
  const [viewport, setViewport] = useState<PageViewport | null>(null)
  const [pageView, setPageView] = useState<number[] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [rendering, setRendering] = useState(false)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const renderRef = useRef<RenderTask | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let destroyed = false
    let loaded: PDFDocumentProxy | null = null
    let loadingTask: ReturnType<typeof getDocument> | null = null
    setPdf(null)
    setViewport(null)
    setLoading(true)
    setPageNumber(1)
    setError('')
    api.pdf(documentId, controller.signal).then(async (buffer) => {
      if (destroyed) return
      loadingTask = getDocument({ data: new Uint8Array(buffer), isEvalSupported: false })
      loaded = await loadingTask.promise
      if (!destroyed) setPdf(loaded)
    }).catch((cause: unknown) => {
      if (!destroyed) setError(messageOf(cause))
    }).finally(() => {
      if (!destroyed) setLoading(false)
    })
    return () => {
      destroyed = true
      controller.abort()
      renderRef.current?.cancel()
      void (loadingTask?.destroy() ?? loaded?.destroy())
    }
  }, [documentId, renditionId])

  useEffect(() => {
    if (!containerRef.current) return
    const observer = new ResizeObserver((entries) => {
      const available = entries[0]?.contentRect.width
      if (available) setWidth(Math.max(240, available - 42))
    })
    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (anchor && (!renditionId || anchor.rendition_id === renditionId)) {
      setPageNumber(anchor.page)
    }
  }, [anchor, renditionId])

  useEffect(() => {
    if (!pdf || !canvasRef.current) return
    let stale = false
    setRendering(true)
    setError('')
    setViewport(null)
    const render = async () => {
      const previous = renderRef.current
      previous?.cancel()
      if (previous) await previous.promise.catch(() => undefined)
      const page = await pdf.getPage(pageNumber)
      if (stale || !canvasRef.current) return
      const baseViewport = page.getViewport({ scale: 1 })
      const nextViewport = page.getViewport({ scale: width / baseViewport.width * zoom })
      const canvas = canvasRef.current
      const context = canvas.getContext('2d')
      if (!context) throw new Error('浏览器无法创建 PDF 画布。')
      const pixelRatio = window.devicePixelRatio || 1
      canvas.width = Math.round(nextViewport.width * pixelRatio)
      canvas.height = Math.round(nextViewport.height * pixelRatio)
      canvas.style.width = `${nextViewport.width}px`
      canvas.style.height = `${nextViewport.height}px`
      const renderingTask = page.render({
        canvas, canvasContext: context, viewport: nextViewport,
        transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
      })
      renderRef.current = renderingTask
      await renderingTask.promise
      if (!stale) {
        setPageView(page.view)
        setViewport(nextViewport)
        setRendering(false)
      }
    }
    void render().catch((cause: unknown) => {
      if (!stale && !(cause instanceof Error && cause.name === 'RenderingCancelledException')) {
        setError(messageOf(cause))
        setRendering(false)
      }
    })
    return () => { stale = true; renderRef.current?.cancel() }
  }, [pdf, pageNumber, width, zoom])

  const highlight = useMemo(() => {
    if (!anchor || !viewport || !pageView || anchor.page !== pageNumber) return null
    if (renditionId && anchor.rendition_id !== renditionId) return null
    // Anchors are unrotated, CropBox-relative, top-left PDF points.
    // PDF.js consumes absolute bottom-left PDF coordinates; its viewport applies
    // the CropBox offset, page rotation, and display scale in one transformation.
    const [x0, y0, x1, y1] = anchor.bbox
    const rect = viewport.convertToViewportRectangle([
      pageView[0] + x0, pageView[3] - y0,
      pageView[0] + x1, pageView[3] - y1,
    ])
    return {
      left: Math.min(rect[0], rect[2]), top: Math.min(rect[1], rect[3]),
      width: Math.abs(rect[2] - rect[0]), height: Math.abs(rect[3] - rect[1]),
    }
  }, [anchor, viewport, pageView, pageNumber, renditionId])

  const total = pdf?.numPages ?? pageCount ?? 1
  return <section className="source-panel" aria-label="论文原文阅读器">
    <div className="source-header"><div><span className="eyebrow">SOURCE DOCUMENT</span><h2>原文证据</h2></div><span className="pill neutral">规范 PDF</span></div>
    <div className="pdf-toolbar">
      <div className="page-controls">
        <button className="icon-button" aria-label="上一页" disabled={pageNumber <= 1 || !pdf} onClick={() => setPageNumber((n) => n - 1)}><Icon name="chevron" style={{ transform: 'rotate(180deg)' }} size={14} /></button>
        <label><span className="sr-only">页码</span><input type="number" min={1} max={total} value={pageNumber} disabled={!pdf} onChange={(event) => { const n = Number(event.target.value); if (n >= 1 && n <= total) setPageNumber(n) }} /></label>
        <span>/ {total}</span>
        <button className="icon-button" aria-label="下一页" disabled={pageNumber >= total || !pdf} onClick={() => setPageNumber((n) => n + 1)}><Icon name="chevron" size={14} /></button>
      </div>
      <div className="zoom-controls"><button className="icon-button" aria-label="缩小" disabled={zoom <= .65} onClick={() => setZoom((n) => Math.max(.65, n - .15))}>−</button><span>{Math.round(zoom * 100)}%</span><button className="icon-button" aria-label="放大" disabled={zoom >= 2} onClick={() => setZoom((n) => Math.min(2, n + .15))}>+</button></div>
    </div>
    <div className="pdf-scroll" ref={containerRef}>
      {loading && <div className="viewer-message"><span className="spinner" />正在加载原文…</div>}
      {error && <div className="viewer-message error-text"><Icon name="warning" />{error}</div>}
      <div className={`pdf-page ${rendering ? 'is-rendering' : ''}`} style={viewport ? { width: viewport.width, height: viewport.height } : undefined}>
        <canvas ref={canvasRef} aria-label={`论文原文第 ${pageNumber} 页`} />
        {highlight && <div className="anchor-highlight" style={highlight}><span>{anchor?.id}</span></div>}
      </div>
    </div>
    <div className="source-evidence">
      <div className="evidence-title"><span className="evidence-dot" /><strong>{anchor ? `第 ${anchor.page} 页 · ${anchor.id}` : '选择一条证据以定位原文'}</strong></div>
      {anchor ? <><blockquote>{anchor.quote || '此区域未提取到可显示的原文摘录。'}</blockquote><small>坐标 [{anchor.bbox.map((n) => n.toFixed(1)).join(', ')}] pt · 文档版本 {anchor.document_version}</small></> : <p>点击报告中的页码与证据编号，查看对应区域及原文摘录。</p>}
    </div>
  </section>
}
