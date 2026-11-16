// Claude 最终验收（第 14–21 项）真卡 CARD-020：“继续改”只说“改成截图里那样，第 1、2 条跟着改”，
// 拆分的模型不知道附了截图（也看不到图），只能返回空，验收标准没改，旧标准“只输出数字”和截图打架，返工 3 次后停下。
// 改成：告诉拆分模型附了几张截图、它看不到；用户要按截图改某条时，那条写成“……（以本次附的截图为准）”，不编造图里内容。
import { expect, it, vi } from 'vitest'
import { prepareRevisionOnHost } from '../src/revision-host.ts'
import { buildRevisionPrompt } from '../src/revision.ts'

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]).toString('base64')

it('附了截图时，提示里写明张数、模型看不到图、按截图改的那条要注明以截图为准', () => {
  const withImage = buildRevisionPrompt(['只输出一个数字'], '改成截图里那样，第 1 条跟着改', 1)
  expect(withImage.user).toContain('用户这次附了 1 张截图')
  expect(withImage.system).toContain('以本次附的截图为准')
  expect(withImage.system).toMatch(/不要编造|不要猜/)
  const none = buildRevisionPrompt(['只输出一个数字'], '第 1 条改成输出两位小数')
  expect(none.user).not.toContain('截图')
  expect(none.system).not.toContain('截图')
})

it('主机拆分时把附图张数交给提示（只交张数，不交图片内容）', async () => {
  const host: any = { projectPath: '/tmp/superagent-no-real-project', isSubmitting: () => false, view: vi.fn(async () => JSON.stringify({ card: { state: '待验收', phase: { code: 'review', actions: ['revise'] } }, accept: { criteria: ['只输出一个数字'] } })), cardAction: vi.fn(async () => ({ ok: true, message: '' })), requestChange: vi.fn() }
  const stream = vi.fn(async function* (_args: unknown) { yield { type: 'text-delta', index: 0, text: '{"changes":[{"replaceNth":1,"text":"输出格式（以本次附的截图为准）"}]}' } })
  const ctx: any = { agents: { get: vi.fn(() => ({ model: { current: () => ({ provider: 'p', model: 'm' }) } })) }, llm: { stream } }
  const r = await prepareRevisionOnHost(host, ctx, { card: 'CARD-020', sessionId: 'task-CARD-020', text: '改成截图里那样，第 1 条跟着改', attachments: [{ mediaType: 'image/png', data: PNG }] } as any)
  expect(r).toMatchObject({ ok: true, pending: true })
  const sent = JSON.stringify(stream.mock.calls[0]![0])
  expect(sent).toContain('用户这次附了 1 张截图')
  expect(sent).not.toContain(PNG)
})
