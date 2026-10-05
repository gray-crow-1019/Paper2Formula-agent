import type { Anchor, DocumentRecord, Result, ReviewDecision, ReviewItem, Task } from './types'

const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')
const devToken = import.meta.env.DEV ? import.meta.env.VITE_DEV_AUTH_TOKEN : undefined

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message)
  }
}

async function request(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers)
  if (devToken) headers.set('Authorization', `Bearer ${devToken}`)
  const response = await fetch(`${base}${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(body?.error?.code || 'HTTP_ERROR', body?.error?.message || `请求失败 (${response.status})`, response.status)
  }
  return response
}

async function json<T>(path: string, options?: RequestInit): Promise<T> {
  return (await request(path, options)).json()
}

function post(body?: unknown, signal?: AbortSignal): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal }
}

async function allReviews(id: string, signal?: AbortSignal): Promise<{ items: ReviewItem[]; revision: number; total: number }> {
  const items: ReviewItem[] = []
  const pageSize = 500
  const maxPages = 200
  let revision: number | null = null
  let total: number | null = null
  for (let page = 0; page < maxPages; page += 1) {
    const response = await json<{ items: ReviewItem[]; revision: number; total: number }>(`/tasks/${encodeURIComponent(id)}/reviews?offset=${items.length}&limit=${pageSize}`, { signal })
    if (revision === null) { revision = response.revision; total = response.total }
    if (response.revision !== revision || response.total !== total) {
      throw new ApiError('REVISION_CONFLICT', '复核内容已更新，正在重新读取。', 409)
    }
    if (!Number.isInteger(total) || total! < 0 || total! > pageSize * maxPages) {
      throw new ApiError('REVIEW_LIMIT', '复核项数量超出单次读取范围。', 413)
    }
    items.push(...response.items)
    if (items.length === total) return { items, revision, total }
    if (!response.items.length || items.length > total!) {
      throw new ApiError('INVALID_REVIEW_PAGE', '复核分页结果不完整，请刷新后重试。', 502)
    }
  }
  throw new ApiError('REVIEW_LIMIT', '复核分页超过读取上限。', 413)
}

export const api = {
  documents: (signal?: AbortSignal) => json<{ items: DocumentRecord[] }>('/documents', { signal }),
  document: (id: string, signal?: AbortSignal) => json<DocumentRecord>(`/documents/${encodeURIComponent(id)}`, { signal }),
  upload: (file: File) => {
    const body = new FormData()
    body.set('file', file)
    return json<DocumentRecord>('/documents', { method: 'POST', body })
  },
  start: (id: string) => json<Task>(`/documents/${encodeURIComponent(id)}/tasks`, {
    ...post(), headers: { 'Idempotency-Key': crypto.randomUUID() },
  }),
  task: (id: string, signal?: AbortSignal) => json<Task>(`/tasks/${encodeURIComponent(id)}`, { signal }),
  tasks: (id: string, signal?: AbortSignal) => json<{ items: Task[] }>(`/documents/${encodeURIComponent(id)}/tasks`, { signal }),
  result: (task: Task, signal?: AbortSignal, version?: number | null) => json<Result>(`/tasks/${encodeURIComponent(task.id)}/results?version=${version ?? task.result_version ?? 1}${task.status === 'waiting_review' || version != null ? '&draft=true' : ''}`, { signal }),
  reviews: allReviews,
  decide: (taskId: string, reviewId: string, body: ReviewDecision) => json<{ revision: number; review: ReviewItem; task: Task }>(`/tasks/${encodeURIComponent(taskId)}/reviews/${encodeURIComponent(reviewId)}/decisions`, post(body)),
  retry: (id: string) => json<Task>(`/tasks/${encodeURIComponent(id)}/retry`, post()),
  cancel: (id: string) => json<Task>(`/tasks/${encodeURIComponent(id)}/cancel`, post()),
  anchor: (documentId: string, anchorId: string, version: number, signal?: AbortSignal, taskId?: string | null) => json<Anchor>(`/documents/${encodeURIComponent(documentId)}/anchors/${encodeURIComponent(anchorId)}?version=${version}${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ''}`, { signal }),
  pdf: async (id: string, signal?: AbortSignal) => (await request(`/documents/${encodeURIComponent(id)}/pdf`, { signal })).arrayBuffer(),
  download: async (taskId: string, kind: string, version: number, filename: string) => {
    const response = await request(`/tasks/${encodeURIComponent(taskId)}/artifacts/${kind}?version=${version}`)
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    const disposition = response.headers.get('Content-Disposition') || ''
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
    const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
    let suggested = plain || filename
    if (encoded) { try { suggested = decodeURIComponent(encoded) } catch { suggested = filename } }
    link.download = suggested.replace(/[/\\]/g, '_')
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
  },
}

export function messageOf(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} · ${error.code}`
  if (error instanceof Error && error.name === 'AbortError') return ''
  if (error instanceof TypeError) return '无法连接服务，请检查后端服务是否已启动。'
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}
