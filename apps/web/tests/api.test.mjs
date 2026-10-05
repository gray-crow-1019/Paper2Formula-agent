import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import ts from 'typescript'

// Exercise the real client logic with controlled HTTP responses. Vite normally
// supplies import.meta.env; this isolated Node test uses the same API defaults.
const source = (await readFile(new URL('../src/api.ts', import.meta.url), 'utf8')).replaceAll('import.meta.env', '({ DEV: false })')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const { api, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

test('loads all review pages rather than stopping at the first 100 items', async () => {
  const reviews = Array.from({ length: 1201 }, (_, index) => ({ id: `CHECK-${index}`, equation_id: null, symbol_id: null, kind: 'no_equations', reasons: [], status: 'pending', anchor_ids: [] }))
  const offsets = []
  globalThis.fetch = async (path) => {
    const params = new URL(path, 'http://localhost').searchParams
    const offset = Number(params.get('offset'))
    const limit = Number(params.get('limit'))
    offsets.push(offset)
    return Response.json({ items: reviews.slice(offset, offset + limit), revision: 7, total: reviews.length })
  }
  const response = await api.reviews('task')
  assert.deepEqual(response.items, reviews)
  assert.deepEqual(offsets, [0, 500, 1000])
  assert.equal(response.revision, 7)
})

test('discards partial pages when the review revision changes', async () => {
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    return Response.json({ items: [{ id: `CHECK-${calls}` }], revision: calls, total: 2 })
  }
  await assert.rejects(api.reviews('task'), (error) => error instanceof ApiError && error.code === 'REVISION_CONFLICT')
  assert.equal(calls, 2)
})

test('rejects an incomplete page instead of silently hiding remaining checks', async () => {
  globalThis.fetch = async () => Response.json({ items: [], revision: 1, total: 2 })
  await assert.rejects(api.reviews('task'), (error) => error.code === 'INVALID_REVIEW_PAGE')
})

test('bounds total review loading and handles the empty result', async () => {
  globalThis.fetch = async () => Response.json({ items: [], revision: 1, total: 100001 })
  await assert.rejects(api.reviews('task'), (error) => error.code === 'REVIEW_LIMIT')
  globalThis.fetch = async () => Response.json({ items: [], revision: 4, total: 0 })
  assert.deepEqual(await api.reviews('task'), { items: [], revision: 4, total: 0 })
})

test('historical result and source queries bind the requested task and version', async () => {
  const paths = []
  globalThis.fetch = async (path) => { paths.push(new URL(path, 'http://localhost')); return Response.json({}) }
  await api.result({ id: 'task-a', status: 'completed', result_version: 3 }, undefined, 1)
  assert.equal(paths[0].searchParams.get('version'), '1')
  assert.equal(paths[0].searchParams.get('draft'), 'true')
  await api.anchor('doc', 'anchor', 1, undefined, 'task-a')
  assert.equal(paths[1].searchParams.get('task_id'), 'task-a')
  assert.equal(paths[1].searchParams.get('version'), '1')
})
