import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { JSDOM } from 'jsdom'
import { afterEach, expect, it } from 'vitest'

const opened: JSDOM[] = []
afterEach(() => { for (const dom of opened.splice(0)) dom.window.close() })
async function until(check: () => boolean) {
  const end = Date.now() + 1500
  while (!check()) { if (Date.now() >= end) throw Error('旧版控件没有完成预期动作'); await new Promise(r => setTimeout(r, 10)) }
}

async function setup(confirm: boolean, newerDraft?: string) {
  const root = resolve(process.cwd(), '../super-agent/desktop/static')
  const dom = new JSDOM(readFileSync(resolve(root, 'panel.html'), 'utf8'), { url: 'http://127.0.0.1/?project=/fixture&card=CARD-001&token=fixture&clerk=1', runScripts: 'outside-only' })
  opened.push(dom)
  const posts: any[] = [], confirmations: string[] = []
  const w = dom.window as any
  w.alert = () => {}
  w.confirm = (text: string) => { confirmations.push(text); return confirm }
  const view = { installed: true, card: { id: 'CARD-001', title: '按钮', state: '待验收', phase: { code: 'review', label: '待验收', detail: '', actions: ['revise'] } }, cards: [], stations: [], handoffs: [], now: { text: '待验收' }, accept: { criteria: ['单击保存'], summary: '按钮', changeNotes: [] } }
  w.fetch = async (_url: string, opts?: any) => {
    if (opts?.method === 'POST') {
      const body = JSON.parse(opts.body); posts.push(body)
      if (body.action === 'prepare_revise' && newerDraft) w.document.getElementById('reviseText').value = newerDraft
      return { ok: true, status: 200, json: async () => body.action === 'prepare_revise'
        ? { ok: true, summary: '第1条：单击保存 → 双击保存', criteria_base: ['单击保存'], criteria_changes: [{ replaceNth: 1, text: '双击保存' }] }
        : { ok: true } }
    }
    return { ok: true, status: 200, json: async () => view }
  }
  w.eval(readFileSync(resolve(root, 'panel.js'), 'utf8'))
  await until(() => !!w.document.querySelector('[data-act="revise"]'))
  w.document.querySelector('[data-act="revise"]').click()
  w.document.getElementById('reviseText').value = '第一条改双击保存'
  w.document.getElementById('reviseSend').click()
  await until(() => posts.length > 0)
  await new Promise(r => setTimeout(r, 30))
  return { w, posts, confirmations }
}

it('旧版按钮先展示逐条确认，取消不提交revise并保留草稿', async () => {
  const f = await setup(false)
  expect(f.posts.map(v => v.action)).toEqual(['prepare_revise'])
  expect(f.confirmations).toEqual(['第1条：单击保存 → 双击保存'])
  expect(f.w.document.getElementById('reviseText').value).toBe('第一条改双击保存')
})

it('旧版按钮确认后才发送准备好的基线和修改，成功后清旧草稿', async () => {
  const f = await setup(true)
  expect(f.posts.map(v => v.action)).toEqual(['prepare_revise', 'revise'])
  expect(f.posts[1]).toMatchObject({ criteria_base: ['单击保存'], criteria_changes: [{ replaceNth: 1, text: '双击保存' }], text: '第一条改双击保存' })
  expect(f.w.document.getElementById('reviseText').value).toBe('')
})

it('拆分期间用户又写了新草稿，确认旧修改成功也不清掉新内容', async () => {
  const f = await setup(true, '这是后来新写的要求')
  expect(f.posts[1]).toMatchObject({ action: 'revise', text: '第一条改双击保存' })
  expect(f.w.document.getElementById('reviseText').value).toBe('这是后来新写的要求')
})
