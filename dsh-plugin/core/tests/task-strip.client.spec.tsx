// @vitest-environment jsdom

import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TaskStrip, type TaskStripInjected } from '../src/client/TaskStrip.tsx'

afterEach(() => { cleanup(); vi.useRealTimers() })

const row = (id: string, phaseCode: string, phaseLabel = phaseCode) => ({ id, title: `卡${id}`, phaseCode, phaseLabel, needsYou: false })

describe('任务卡小条', () => {
  it('当前会话即使被 DSH 认作空任务对话，也不能遮住本会话刚建的任务小条', async () => {
    const face: TaskStripInjected = {
      sessionCards: vi.fn(async () => ({ ok: true as const, value: [row('CARD-010', 'planning', '调度员写方案')] })),
      cardOfSession: vi.fn(async () => ({ ok: true as const, value: row('CARD-009', 'plan_waiting', '等你批准方案') })),
      openTask: vi.fn(async () => {}),
    }
    render(<TaskStrip {...face} sessionId="task-009" />)
    expect(await screen.findByLabelText('任务卡小条 CARD-010')).toBeTruthy()
  })

  it('已完成（done）和不做了（dropped）的卡不占小条（复查第 4 项中等 4）', async () => {
    const face: TaskStripInjected = {
      sessionCards: vi.fn(async () => ({ ok: true as const, value: [row('CARD-003', 'running', '进行中'), row('CARD-002', 'dropped', '不做了'), row('CARD-001', 'done', '已完成')] })),
      cardOfSession: vi.fn(async () => ({ ok: true as const, value: null })),
      openTask: vi.fn(async () => {}),
    }
    render(<TaskStrip {...face} sessionId="s1" />)
    expect(await screen.findByText(/CARD-003/)).toBeTruthy()
    expect(screen.queryByText(/CARD-002/)).toBeNull()
    expect(screen.queryByText(/CARD-001/)).toBeNull()
  })

  it('网络慢（每次回包都比下一轮请求晚）：照样会更新，不会一直空着（复查第 4 项中等 6）', async () => {
    vi.useFakeTimers()
    const pending: ((v: unknown) => void)[] = []
    const face: TaskStripInjected = {
      sessionCards: vi.fn(() => new Promise(r => { pending.push(r as (v: unknown) => void) })) as never,
      cardOfSession: vi.fn(async () => ({ ok: true as const, value: null })),
      openTask: vi.fn(async () => {}),
    }
    render(<TaskStrip {...face} sessionId="s1" />)
    await act(async () => { vi.advanceTimersByTime(3100) })
    // 第 1 轮的回包在第 2 轮请求发出之后才到
    await act(async () => { pending[0]?.({ ok: true, value: [row('CARD-009', 'running', '进行中')] }) })
    expect(screen.getByText(/CARD-009/)).toBeTruthy()
  })

  it('第一次就读不到：说出来，不是一片空白（复查第 4 项第 2 轮中等 3）', async () => {
    const fail = { ok: false as const, error: { code: 'x', message: '断了' } }
    const face: TaskStripInjected = {
      sessionCards: vi.fn(async () => fail) as never,
      cardOfSession: vi.fn(async () => fail) as never,
      openTask: vi.fn(async () => {}),
    }
    render(<TaskStrip {...face} sessionId="s1" />)
    expect(await screen.findByText(/读不到任务卡信息/)).toBeTruthy()
  })

  it('列表读失败、另一项读成功：提示不被误清（复查第 4 项第 2 轮中等 2）', async () => {
    vi.useFakeTimers()
    const face: TaskStripInjected = {
      sessionCards: vi.fn()
        .mockResolvedValueOnce({ ok: true as const, value: [row('CARD-007', 'running', '进行中')] })
        .mockResolvedValue({ ok: false as const, error: { code: 'x', message: '断了' } }) as never,
      // 另一项查询总是比列表晚一点回来、而且是成功的：它不能把列表的失败提示清掉。
      cardOfSession: vi.fn(() => new Promise(r => { setTimeout(() => { r({ ok: true, value: null }) }, 50) })) as never,
      openTask: vi.fn(async () => {}),
    }
    render(<TaskStrip {...face} sessionId="s1" />)
    await act(async () => { await Promise.resolve(); vi.advanceTimersByTime(60); await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(3000); await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(100); await Promise.resolve() })
    expect(screen.getByText(/可能不是最新/)).toBeTruthy()
  })

  it('回包乱序：新的失败不会被旧的成功清掉，新的成功也不会被旧的失败盖住（复查第 4 项第 3 轮中等）', async () => {
    vi.useFakeTimers()
    const later: ((v: unknown) => void)[] = []
    let call = 0
    const face: TaskStripInjected = {
      cardOfSession: vi.fn(async () => ({ ok: true as const, value: null })),
      sessionCards: vi.fn(() => {
        call += 1
        // 第 1 轮慢（成功），第 2 轮快（失败）
        if (call === 1) return new Promise(r => { later.push(r as (v: unknown) => void) })
        return Promise.resolve({ ok: false, error: { code: 'x', message: '断了' } })
      }) as never,
      openTask: vi.fn(async () => {}),
    }
    render(<TaskStrip {...face} sessionId="s1" />)
    await act(async () => { vi.advanceTimersByTime(3100); await Promise.resolve() })
    // 第 2 轮失败已到，第 1 轮的成功这才到：不能把失败提示清掉
    await act(async () => { later[0]?.({ ok: true, value: [] }); await Promise.resolve() })
    expect(screen.getByText(/读不到任务卡信息/)).toBeTruthy()
  })

  it('读进度失败：提示“可能不是最新”', async () => {
    const face: TaskStripInjected = {
      sessionCards: vi.fn(async () => ({ ok: true as const, value: [] })),
      cardOfSession: vi.fn()
        .mockResolvedValueOnce({ ok: true as const, value: row('CARD-005', 'running', '进行中') })
        .mockResolvedValue({ ok: false as const, error: { code: 'x', message: '断了' } }) as never,
      openTask: vi.fn(async () => {}),
    }
    vi.useFakeTimers()
    render(<TaskStrip {...face} sessionId="task-x" />)
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText(/CARD-005/)).toBeTruthy()
    await act(async () => { vi.advanceTimersByTime(3100); await Promise.resolve() })
    expect(screen.getByText(/可能不是最新/)).toBeTruthy()
  })
})
