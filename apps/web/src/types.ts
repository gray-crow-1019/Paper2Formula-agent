export interface DocumentRecord {
  id: string
  document_id?: string
  filename: string
  mime: string
  page_count: number | null
  status: string
  rendition_id?: string | null
  document_version?: number
  created_at?: string
}

export interface Task {
  id: string
  task_id?: string
  document_id: string
  status: string
  node: string
  revision: number
  progress: number
  error: { code: string; message: string; retryable: boolean } | null
  result_version: number | null
  created_at: string
  updated_at: string
}

export interface Anchor {
  id: string
  page: number
  bbox: [number, number, number, number]
  page_width: number
  page_height: number
  rotation: number
  crop?: [number, number, number, number] | null
  quote: string
  rendition_id: string
  document_version: number
}

export interface Parameter {
  id: string
  raw_value: string | null
  normalized_value: string | null
  raw_unit: string | null
  normalized_unit: string | null
  evidence_anchor_ids: string[]
  provenance: string
  status: string
  reasons: string[]
}

export interface SymbolBinding {
  id: string
  name: string
  meaning: string | null
  status: string
  provenance: string
  evidence_anchor_ids: string[]
  reasons: string[]
  values: Parameter[]
}

export interface Finding { status: string; reasons: string[] }

export interface Equation {
  id: string
  latex: string
  original_number: string | null
  explanation: string | null
  anchor_ids: string[]
  symbols: SymbolBinding[]
  validation: { syntax: Finding; definitions: Finding; units: Finding }
  status: string
  excluded?: boolean
}

export interface ReviewItem {
  id: string
  equation_id: string | null
  symbol_id: string | null
  parameter_id?: string | null
  kind: string
  reasons: string[]
  status: 'pending' | 'resolved'
  anchor_ids: string[]
  decision?: unknown
}

export interface Result {
  schema_version: string
  document_id: string
  rendition_id: string
  result_version: number
  status: string
  anchors: Anchor[]
  equations: Equation[]
  review_items: ReviewItem[]
  relations: { from_equation_id?: string; to_equation_id?: string; relation_type?: string; evidence_anchor_ids?: string[] }[]
  manifest: unknown
}

export interface ReviewDecision {
  expected_revision: number
  action: 'confirm' | 'edit' | 'keep_unknown'
  patch: { latex?: string; explanation?: string; meaning?: string; raw_value?: string; raw_unit?: string; excluded?: boolean }
  evidence_anchor_ids: string[]
}
