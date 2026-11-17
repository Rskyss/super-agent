// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
const modules = import.meta.glob('../src/client/ConversationHeader.tsx', { eager: true }) as Record<string, Record<string, any>>
afterEach(() => { cleanup(); vi.useRealTimers() })
function header(props: Record<string, unknown>) {
  const C = modules['../src/client/ConversationHeader.tsx']?.SuperagentConversationHeader
  expect(C, '缺少覆盖DSH标题栏的超级玩家标题组件').toBeTypeOf('function')
  return render(<C {...props} />)
}
it('普通对话只显示标题和超级玩家，不渲染旧标签、菜单或面板按钮', () => {
  header({ title: '讨论发布文案', blank: false, task: null, onTrajectory: vi.fn() })
  expect(screen.getByText('讨论发布文案', { exact: true })).toBeTruthy()
  expect(screen.getByText('超级玩家', { exact: true })).toBeTruthy()
  expect(screen.queryAllByRole('tab')).toHaveLength(0)
  expect(screen.queryAllByRole('button')).toHaveLength(0)
})
it('空首页不冒出普通对话标题，空白任务仍显示编号标题和临时轨迹入口', () => {
  const v = header({ title: '新会话', blank: true, task: null, onTrajectory: vi.fn() })
  expect(v.container.textContent).toBe('')
  v.unmount()
  const open = vi.fn()
  header({ title: '旧会话名', blank: true, task: { id: 'CARD-022', title: '增加问候命令' }, onTrajectory: open })
  expect(screen.getByText('22 号：增加问候命令', { exact: true })).toBeTruthy()
  expect(screen.getByText('超级玩家 · 任务', { exact: true })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '任务轨迹' }))
  expect(open).toHaveBeenCalledTimes(1)
})

function taskSlot(lookup: ReturnType<typeof vi.fn>) {
  const C = modules['../src/client/ConversationHeader.tsx']?.SuperagentConversationHeaderSlot
  expect(C, '任务绑定查询需要可恢复的真实标题slot组件').toBeTypeOf('function')
  return render(<C sessionId="task-22" useSession={(s: any) => s({ blank: true })}
    useSessions={(s: any) => s({ ids: ['task-22'], byId: { 'task-22': { title: '任务对话', displayTitle: '任务对话' } } })}
    cardOfSession={lookup} trajectory={vi.fn(async () => ({}))} cardAttachment={vi.fn()} />)
}

it('打开任务的首次绑定查询短暂失败后，无需切会话便恢复标题与轨迹入口', async () => {
  vi.useFakeTimers()
  const lookup = vi.fn().mockRejectedValueOnce(new Error('短暂不可用'))
    .mockResolvedValue({ ok: true, value: { id: 'CARD-022', title: '增加问候命令' } })
  taskSlot(lookup)
  await act(async () => { await Promise.resolve() })
  await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
  expect(lookup.mock.calls.length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('22 号：增加问候命令')).toBeTruthy()
  expect(screen.getByRole('button', { name: '任务轨迹' })).toBeTruthy()
})

it('已确认的任务身份在一次刷新失败时仍保留标题和轨迹入口', async () => {
  vi.useFakeTimers()
  const lookup = vi.fn().mockResolvedValueOnce({ ok: true, value: { id: 'CARD-022', title: '增加问候命令' } })
    .mockRejectedValue(new Error('短暂不可用'))
  taskSlot(lookup)
  await act(async () => { await Promise.resolve() })
  await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
  expect(lookup.mock.calls.length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('22 号：增加问候命令')).toBeTruthy()
  expect(screen.getByRole('button', { name: '任务轨迹' })).toBeTruthy()
})
