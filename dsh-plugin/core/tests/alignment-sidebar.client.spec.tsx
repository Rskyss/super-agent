// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TaskSidebar } from '../src/client/TaskSidebar.tsx'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const ok = <T,>(value: T) => ({ ok: true as const, value })
const now = Date.now()
const card = (id: string, title: string, extra: Record<string, unknown> = {}) => ({
  id, title, state: '待对齐', phaseCode: 'plan_waiting', phaseLabel: '等待写方案', needsYou: false,
  actions: [], updated: new Date(now - 60000).toISOString(), isRunningNow: false, ...extra,
})

function setup(cards = [
  card('CARD-022', '等你批方案', { needsYou: true, phaseCode: 'plan_ready' }),
  card('CARD-023', '暂停等调度员'),
  card('CARD-024', '真正在写代码', { state: '开发中', phaseCode: 'running', isRunningNow: true }),
  card('CARD-025', '做完了的任务', { state: '已合并', phaseCode: 'done' }),
], options: { empty?: boolean; locked?: boolean; drawer?: boolean; selectedTask?: string; blank?: boolean } = {}) {
  const session = { id: 'chat1', title: '聊页面对齐', displayTitle: '聊页面对齐', blank: Boolean(options.blank),
    running: false, updatedAt: now - 12 * 60000, cwd: '/test', retainedBy: { mainView: 1 }, projectionValues: {} }
  const sessions = { phase: 'ready', ids: options.empty ? [] : ['chat1'], byId: options.empty ? {} : { chat1: session } as Record<string, any>, projectionsBySession: {} }
  if (options.selectedTask) {
    session.retainedBy.mainView = 0
    sessions.ids.push('task-active')
    sessions.byId['task-active'] = { ...session, id: 'task-active', retainedBy: { mainView: 1 }, projectionValues: { agentPreset: 'superagent-task' } }
  }
  const workspaces = { phase: 'ready', state: 'idle', error: null, items: [], archivedSessionIds: [], pinnedSessionIds: [] }
  const ports = { cards: vi.fn(async () => ok(cards)), taskSessions: vi.fn(async () => ok(options.selectedTask ? { [options.selectedTask]: 'task-active' } : {})),
    useSessions: (select: any) => select(sessions), useWorkspaces: (select: any) => select(workspaces),
    openSession: vi.fn(), openTask: vi.fn(async () => {}), startChat: vi.fn(async () => {}),
    renameSession: vi.fn(async () => ok({})), archiveSession: vi.fn(async () => ok({})), showOverview: vi.fn(),
    wide: true, expandSidebar: vi.fn(), collapseSidebar: vi.fn(), drawer: Boolean(options.drawer),
    openSettings: vi.fn(), useSetupLocked: () => Boolean(options.locked), version: '0.2.2-rc.3' }
  const view = render(<TaskSidebar {...ports} />)
  return { ...ports, sessions, refresh: () => view.rerender(<TaskSidebar {...ports} />) }
}

// 会抓住“只替换会话子区，品牌/新会话/设置随原 ui-sidebar 一起丢失”的退化。
it('独占左栏具备品牌、新会话和可打开的设置入口', async () => {
  const p = setup()
  expect(screen.getByText('超级玩家', { exact: true })).toBeTruthy()
  expect(screen.getByText('AIsoup Lab', { exact: true })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '新会话' }))
  await act(async () => { await Promise.resolve() })
  expect(p.startChat).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: '设置' }))
  expect(p.openSettings).toHaveBeenCalledTimes(1)
})

it('首配未完成时新会话锁住，空对话文案与草图一致', async () => {
  const p = setup([], { empty: true, locked: true })
  expect(await screen.findByText('还没有对话', { exact: true })).toBeTruthy()
  const button = screen.getByRole('button', { name: '新会话' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.click(button)
  expect(p.startChat).not.toHaveBeenCalled()
})

it('对话行显示相对时间并保持当前对话和导航', async () => {
  const p = setup()
  const title = await screen.findByText('聊页面对齐', { exact: true })
  const row = title.closest('button')!
  expect(row.getAttribute('aria-current')).toBe('page')
  expect(screen.getByText('12 分钟', { exact: true })).toBeTruthy()
  fireEvent.click(row)
  expect(p.openSession).toHaveBeenCalledWith('chat1')
})

it('任务编号读作号，暂停卡不伪装运行，分组显示数量', async () => {
  setup()
  await act(async () => { await Promise.resolve() })
  fireEvent.click(screen.getByRole('tab', { name: /^任务/ }))
  expect(await screen.findByText('23 号', { exact: true })).toBeTruthy()
  expect(screen.queryByText('CARD-023', { exact: true })).toBeNull()
  const paused = screen.getByText('暂停等调度员').closest('button')!
  expect(within(paused).getByText('暂停', { exact: true })).toBeTruthy()
  expect(paused.querySelector('[data-state="ongoing"]')).toBeNull()
  expect(screen.getByText('进行中', { exact: true }).parentElement!.textContent).toMatch(/进行中\s*2/)
  expect(screen.getByText('等你处理', { exact: true }).parentElement!.textContent).toMatch(/等你处理\s*1/)
})

it('没有等你处理时任务标签没有零，空分组不占位置', async () => {
  setup([card('CARD-023', '暂停等调度员')])
  await act(async () => { await Promise.resolve() })
  const tab = screen.getByRole('tab', { name: /^任务/ })
  expect(tab.textContent?.trim()).toBe('任务')
  fireEvent.click(tab)
  expect(screen.queryByText('等你处理', { exact: true })).toBeNull()
  expect(screen.queryByText('已完成', { exact: true })).toBeNull()
  expect(screen.queryByText('暂无', { exact: true })).toBeNull()
})

it('从任务列表点新会话成功后左栏同步回到对话标签', async () => {
  setup()
  await act(async () => { await Promise.resolve() })
  fireEvent.click(screen.getByRole('tab', { name: /^任务/ }))
  fireEvent.click(screen.getByRole('button', { name: '新会话' }))
  await act(async () => { await Promise.resolve() })
  expect(screen.getByRole('tab', { name: '对话' }).getAttribute('aria-selected')).toBe('true')
  expect(screen.getByText('聊页面对齐', { exact: true })).toBeTruthy()
})

it('展开的左栏沿用宿主的收起操作', async () => {
  const p = setup()
  fireEvent.click(screen.getByRole('button', { name: '收起侧边栏' }))
  expect(p.collapseSidebar).toHaveBeenCalledTimes(1)
})

it('窄屏抽屉沿用DSH的模态语义与Escape关闭', async () => {
  const p = setup([], { drawer: true })
  const dialog = screen.getByRole('dialog', { name: '侧边栏' })
  expect(dialog.getAttribute('aria-modal')).toBe('true')
  expect(dialog.getAttribute('data-shortcut-modal')).toBe('sidebar')
  expect(dialog.contains(document.activeElement)).toBe(true)
  fireEvent.keyDown(document, { key: 'Escape', code: 'Escape' })
  expect(p.collapseSidebar).toHaveBeenCalledTimes(1)
})

it('左右键切换标签并建立标签与面板关联，只有当前标签进入Tab顺序', async () => {
  setup()
  await act(async () => { await Promise.resolve() })
  const chats = screen.getByRole('tab', { name: '对话' })
  const tasks = screen.getByRole('tab', { name: /^任务/ })
  chats.focus()
  fireEvent.keyDown(chats, { key: 'ArrowRight', code: 'ArrowRight' })
  expect(tasks.getAttribute('aria-selected')).toBe('true')
  expect(document.activeElement).toBe(tasks)
  expect(tasks.tabIndex).toBe(0)
  expect(chats.tabIndex).toBe(-1)
  const panelId = tasks.getAttribute('aria-controls')
  expect(panelId).toBeTruthy()
  const panel = document.getElementById(panelId!)!
  expect(panel.getAttribute('role')).toBe('tabpanel')
  expect(panel.getAttribute('aria-labelledby')).toBe(tasks.id)
  fireEvent.keyDown(tasks, { key: 'Home', code: 'Home' })
  expect(chats.getAttribute('aria-selected')).toBe('true')
})

it('打开的任务会话在左栏标为当前任务', async () => {
  setup(undefined, { selectedTask: 'CARD-023' })
  await act(async () => { await Promise.resolve() })
  fireEvent.click(screen.getByRole('tab', { name: /^任务/ }))
  const row = screen.getByText('暂停等调度员', { exact: true }).closest('button')!
  expect(row.getAttribute('aria-current')).toBe('page')
})

it('还没发消息的空白会话不冒充历史对话', async () => {
  setup([], { blank: true })
  expect(await screen.findByText('还没有对话', { exact: true })).toBeTruthy()
  expect(screen.queryByText('聊页面对齐', { exact: true })).toBeNull()
})

it('首配锁定时按00a显示还没有对话，不露缓存里的历史列表', async () => {
  setup([], { locked: true })
  expect(await screen.findByText('还没有对话', { exact: true })).toBeTruthy()
  expect(screen.queryByText('聊页面对齐', { exact: true })).toBeNull()
})

it('打开或恢复任务对话后，左栏同步选中任务标签', async () => {
  setup(undefined, { selectedTask: 'CARD-023' })
  await act(async () => { await Promise.resolve() })
  expect(screen.getByRole('tab', { name: /^任务/ }).getAttribute('aria-selected')).toBe('true')
})

it('从外部导航到普通对话时同步切回对话标签', async () => {
  const p = setup(undefined, { selectedTask: 'CARD-023' })
  await act(async () => { await Promise.resolve() })
  fireEvent.click(screen.getByRole('tab', { name: /^任务/ }))
  p.sessions.byId['task-active']!.retainedBy.mainView = 0
  p.sessions.byId['chat1']!.retainedBy.mainView = 1
  p.refresh()
  await act(async () => { await Promise.resolve() })
  expect(screen.getByRole('tab', { name: '对话' }).getAttribute('aria-selected')).toBe('true')
})

it('归档当前对话后打开默认新会话，不留下已藏工作区选择器的不可用首页', async () => {
  const p=setup()
  await act(async()=>{await Promise.resolve()})
  fireEvent.click(screen.getByRole('button',{name:'归档 聊页面对齐'}))
  await act(async()=>{await Promise.resolve()})
  expect(p.archiveSession).toHaveBeenCalledWith('chat1')
  expect(p.startChat).toHaveBeenCalledTimes(1)
})

it('归档后台对话不抢走当前打开的任务', async () => {
  const p=setup(undefined,{selectedTask:'CARD-023'})
  await act(async()=>{await Promise.resolve()})
  fireEvent.click(screen.getByRole('tab',{name:'对话'}))
  fireEvent.click(screen.getByRole('button',{name:'归档 聊页面对齐'}))
  await act(async()=>{await Promise.resolve()})
  expect(p.startChat).not.toHaveBeenCalled()
})

it('等待归档期间已切到另一对话时，不再自动开新会话覆盖导航', async () => {
  const p=setup()
  let finish!:(result:any)=>void
  p.archiveSession.mockImplementation(()=>new Promise(resolve=>{finish=resolve}))
  await act(async()=>{await Promise.resolve()})
  fireEvent.click(screen.getByRole('button',{name:'归档 聊页面对齐'}))
  p.sessions.byId['chat1']!.retainedBy.mainView=0
  p.sessions.ids.push('chat2')
  p.sessions.byId['chat2']={...p.sessions.byId['chat1'],id:'chat2',retainedBy:{mainView:1}}
  p.refresh()
  await act(async()=>{finish(ok({}));await Promise.resolve()})
  expect(p.startChat).not.toHaveBeenCalled()
})
