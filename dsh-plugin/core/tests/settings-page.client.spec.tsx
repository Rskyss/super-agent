// @vitest-environment jsdom
// 第六批：设置从弹框改成右边一整页（对照 06 / 07 / 08 / 15 / 16）。
// 用户 11-16 晚：五个分区都露出来；业务流程只读显示现在的开发流程；定时任务只有框架和说明；
// 能力层、积木库读真实资料（现在是空的）；花费不做。
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SettingsPage, type SettingsPageInjected } from '../src/client/SettingsPage.tsx'

afterEach(cleanup)
const TEAM = {
  roles: { tester: 'Codex · GPT-6.1-Sol（极高）', developer: 'agy · Gemini 4 Argon（高）', reviewer: 'Codex · GPT-6.1-Sol（极高）' },
  dispatcher: 'Codex',
  picks: { tester: { tool: 'codex', model: 'gpt-6.1-sol', effort: 'xhigh' }, developer: { tool: 'agy', model: 'gemini-4-argon-high', effort: '' }, reviewer: { tool: 'codex', model: 'gpt-6.1-sol', effort: 'xhigh' } },
  options: { tester: [{ value: 'codex', label: 'Codex' }, { value: 'agy', label: 'agy' }], developer: [{ value: 'codex', label: 'Codex' }, { value: 'agy', label: 'agy' }], reviewer: [{ value: 'codex', label: 'Codex' }, { value: 'agy', label: 'agy' }, { value: 'none', label: '不复查' }] },
  models: { codex: [{ value: 'gpt-6.1-sol', label: 'GPT-6.1-Sol', efforts: ['high', 'xhigh'] }], agy: [{ value: 'gemini-4-argon-high', label: 'Gemini 4 Argon (High · 默认)' }, { value: 'gemini-4-hydrogen-high', label: 'Gemini 4 Hydrogen (High)' }] },
  efforts: [{ value: 'high', label: '高（high · 深度思考）' }, { value: 'xhigh', label: '极高（xhigh · 复杂问题）' }],
}
const ok = <T,>(value: T) => ({ ok: true as const, value })
async function flush() { await act(async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }) }

function setup(over: Partial<SettingsPageInjected> = {}) {
  const face: SettingsPageInjected = {
    abilities: vi.fn(async () => ok([])),
    blocks: vi.fn(async () => ok([])),
    team: vi.fn(async () => ok(TEAM)),
    saveTeamRole: vi.fn(async () => ok(TEAM)),
    workflow: vi.fn(async () => ok({ name: '开发软件', steps: ['写测试', '写代码', '跑全部测试', '复查', '你验收'], maxReviewRounds: 3, version: null })),
    cards: vi.fn(async () => ok([{ id: 'CARD-001' }, { id: 'CARD-002' }] as any)),
    storedKeys: vi.fn(async () => ok([{ id: 'ref:SUPERAGENT_ASSISTANT_API_KEY', label: '超级玩家的大模型', inUse: true }])),
    deleteKey: vi.fn(async () => ok(true)),
    records: vi.fn(async () => ok({ path: '/Users/x/Library/Application Support/超级玩家/home', bytes: 48 * 1024 * 1024 })),
    openRecords: vi.fn(async () => ok(true)),
    theme: { get: () => 'system', set: vi.fn(), subscribe: () => () => {} },
    startChatWithPrompt: vi.fn(async () => {}),
    renderBlocks: () => <div>积木库内容</div>,
    ...over,
  }
  const view = render(<SettingsPage {...face} />)
  return { face, view }
}

it('左边五个分区都露出来，每项带一行小字，默认打开积木库；顶上有可拖条', async () => {
  const { view } = setup()
  await flush()
  const nav = screen.getByRole('navigation', { name: '设置' })
  const names = within(nav).getAllByRole('button').map(b => b.querySelector('[data-nav-title]')?.textContent)
  expect(names).toEqual(['业务流程', '能力层', '积木库', '定时任务', '通用'])
  expect(within(nav).getByText('能力卡串成的流程')).toBeTruthy()
  expect(screen.getByText('积木库内容')).toBeTruthy()
  const strip = view.container.firstElementChild!.firstElementChild as HTMLElement
  expect(strip.hasAttribute('data-window-drag')).toBe(true)
})

it('能力层：没有能力卡时如实显示空，右上可以跟超级玩家说一个新能力', async () => {
  const { face } = setup()
  fireEvent.click(screen.getByRole('button', { name: /能力层/ }))
  await flush()
  expect(screen.getByRole('heading', { name: '能力层' })).toBeTruthy()
  expect(screen.getByText('还没有能力卡')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '+ 跟超级玩家说一个新能力' }))
  await flush()
  expect(face.startChatWithPrompt).toHaveBeenCalledWith('我想要一个新能力：')
})

it('能力层：有卡时五列照图，跑过的写用过 N 次，没跑过写草稿，坏卡写原因', async () => {
  setup({ abilities: vi.fn(async () => ok([
    { id: 'a', title: '写文章', inputs: '题目 + 要求', output: '一段文字', who: 'DeepSeek · v4', check: '字数 100–300', runs: 9, draft: false, problem: '' },
    { id: 'b', title: '画图', inputs: '画面', output: '一张图片', who: 'Codex', check: '—', runs: 0, draft: true, problem: '' },
    { id: 'bad', title: 'bad', problem: '卡片读不了' },
  ])) })
  fireEvent.click(screen.getByRole('button', { name: /能力层/ }))
  await flush()
  const table = screen.getByRole('table')
  expect(within(table).getAllByRole('columnheader').map(c => c.textContent)).toEqual(['能力卡', '收什么 → 交什么', '谁来跑', '怎么算做对', ''])
  expect(within(table).getByText('题目 + 要求 → 一段文字')).toBeTruthy()
  expect(within(table).getByText('用过 9 次')).toBeTruthy()
  expect(within(table).getByText('草稿')).toBeTruthy()
  expect(within(table).getByText('卡片读不了')).toBeTruthy()
})

it('业务流程：只读显示现在的开发流程，谁来跑读真实分工，不能改', async () => {
  const { face } = setup()
  fireEvent.click(screen.getByRole('button', { name: /业务流程/ }))
  await flush()
  expect(screen.getByRole('heading', { name: '业务流程 · 开发软件' })).toBeTruthy()
  expect(screen.getByText('跑过 2 次')).toBeTruthy()
  for (const step of ['写测试', '写代码', '跑全部测试', '复查', '你验收']) expect(screen.getAllByText(step).length).toBeGreaterThan(0)
  expect(screen.getByText('能力卡 · agy · Gemini 4 Argon（高）')).toBeTruthy()
  expect(screen.getByText('过不了怎么办')).toBeTruthy()
  expect(screen.getByText('带着问题清单退回写代码，最多 3 轮')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '改这条流程' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '用它做一个新需求' }))
  await flush()
  expect(face.startChatWithPrompt).toHaveBeenCalledWith('')
})

it('定时任务：只有框架和说明，列表为空，不造样例、不能新建', async () => {
  setup()
  fireEvent.click(screen.getByRole('button', { name: /定时任务/ }))
  await flush()
  expect(screen.getByRole('heading', { name: '定时任务' })).toBeTruthy()
  expect(screen.getByText('还没有定时任务')).toBeTruthy()
  expect(screen.getByText('设一条定时任务时，超级玩家会问你这几件事')).toBeTruthy()
  expect(screen.queryByText(/盯竞品价格/)).toBeNull()
  expect(screen.queryByRole('button', { name: /说一个新的/ })).toBeNull()
})

it('通用：主题三档、Key 只列名字可删（删正在用的要提醒）、记录位置大小可打开；没有花费', async () => {
  const set = vi.fn()
  const { face } = setup({ theme: { get: () => 'system', set, subscribe: () => () => {} } })
  fireEvent.click(screen.getByRole('button', { name: /通用/ }))
  await flush()
  expect(screen.queryByText(/花费/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '深色' }))
  expect(set).toHaveBeenCalledWith('dark')
  expect(screen.getByText(/1 个：超级玩家的大模型/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '管理' }))
  fireEvent.click(screen.getByRole('button', { name: '删除 超级玩家的大模型' }))
  expect(screen.getByText(/删了以后要重新配置大模型/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
  await flush()
  expect(face.deleteKey).toHaveBeenCalledWith('ref:SUPERAGENT_ASSISTANT_API_KEY')
  expect(screen.getByText(/占用 48 MB/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '打开文件夹' }))
  await flush()
  expect(face.openRecords).toHaveBeenCalled()
})

it('读不到数据时照实报错，不当成空', async () => {
  setup({ abilities: vi.fn(async () => ({ ok: false as const, error: { message: '后台没起来' } as any })) })
  fireEvent.click(screen.getByRole('button', { name: /能力层/ }))
  await flush()
  expect(screen.getByRole('alert').textContent).toContain('后台没起来')
  expect(screen.queryByText('还没有能力卡')).toBeNull()
})

it('业务流程：写测试、写代码、复查三步能选谁来跑（过渡期照旧版），改了才出现保存，保存后显示新的', async () => {
  const saved = { ...TEAM, roles: { ...TEAM.roles, developer: 'agy · Gemini 4 Hydrogen（高）' }, picks: { ...TEAM.picks, developer: { tool: 'agy', model: 'gemini-4-hydrogen-high', effort: '' } } }
  const saveTeamRole = vi.fn(async () => ok(saved))
  setup({ saveTeamRole })
  fireEvent.click(screen.getByRole('button', { name: /业务流程/ }))
  await flush()
  expect(screen.getByRole('combobox', { name: '写测试用哪个工具' })).toBeTruthy()
  expect(screen.getByRole('combobox', { name: '复查用哪个工具' })).toBeTruthy()
  expect(screen.queryByRole('combobox', { name: '跑全部测试用哪个工具' })).toBeNull()
  expect((screen.getByRole('combobox', { name: '写测试的思考强度' }) as HTMLSelectElement).value).toBe('xhigh')
  expect(screen.queryByRole('combobox', { name: '写代码的思考强度' })).toBeNull()
  expect(screen.queryByRole('button', { name: '保存' })).toBeNull()
  fireEvent.change(screen.getByRole('combobox', { name: '写代码用哪个模型' }), { target: { value: 'gemini-4-hydrogen-high' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await flush()
  expect(saveTeamRole).toHaveBeenCalledWith('developer', 'agy', 'gemini-4-hydrogen-high', '')
  expect(screen.getByText(/已保存，下一张任务卡起按新的跑/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: '保存' })).toBeNull()
})

it('业务流程：换工具时模型换成那家的第一个；复查可以选不复查；保存失败照实说', async () => {
  const saveTeamRole = vi.fn(async () => ({ ok: false as const, error: { message: '有任务卡正在跑，等它停下再换' } as any }))
  setup({ saveTeamRole })
  fireEvent.click(screen.getByRole('button', { name: /业务流程/ }))
  await flush()
  fireEvent.change(screen.getByRole('combobox', { name: '写测试用哪个工具' }), { target: { value: 'agy' } })
  expect((screen.getByRole('combobox', { name: '写测试用哪个模型' }) as HTMLSelectElement).value).toBe('gemini-4-argon-high')
  fireEvent.change(screen.getByRole('combobox', { name: '复查用哪个工具' }), { target: { value: 'none' } })
  expect(screen.queryByRole('combobox', { name: '复查用哪个模型' })).toBeNull()
  const saves = screen.getAllByRole('button', { name: '保存' })
  fireEvent.click(saves[1]!)
  await flush()
  expect(saveTeamRole).toHaveBeenCalledWith('reviewer', 'none', '', '')
  expect(screen.getByRole('alert').textContent).toContain('有任务卡正在跑')
})
