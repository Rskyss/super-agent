// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ConfirmDock, type ConfirmDockInjected } from '../src/client/ConfirmDock.tsx'

afterEach(cleanup)

it('切到刚开的会话时不能确认上一会话的卡，新卡只能用当前会话确认', async () => {
  const proposal = (id: string) => ({ id, title: `需求${id}`, do: '显示欢迎', notDo: '不改其他', doneWhen: '能看见欢迎', questions: [] })
  let second: ((v: unknown) => void) | undefined
  const face: ConfirmDockInjected = {
    proposal: vi.fn(async (sid: string) => sid === 'old' ? { ok: true as const, value: proposal('old') } : new Promise(resolve => { second = resolve })) as never,
    confirm: vi.fn(async () => ({ ok: true as const, value: { ok: true as const, card: 'CARD-011', planning: false, message: '' } })),
    dismiss: vi.fn(), dispatcherStatus: vi.fn(async () => ({ ok: true as const, value: 'ready' as const })),
  }
  const inputActions = { setDraft: vi.fn(), submit: vi.fn() }
  const useInput = <T,>(f: (i: { draft: string; attachmentIds: unknown[]; phase: string }) => T) => f({ draft: '', attachmentIds: [], phase: 'plain' })
  const view = render(<ConfirmDock {...face} sessionId="old" inputActions={inputActions} useInput={useInput} />)
  expect(await screen.findByText('需求old')).toBeTruthy()
  view.rerender(<ConfirmDock {...face} sessionId="new" inputActions={inputActions} useInput={useInput} />)
  expect(screen.queryByRole('button', { name: '确认，建任务' })).toBeNull()
  await act(async () => { second?.({ ok: true as const, value: proposal('new') }) })
  expect(await screen.findByText('需求new')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '确认，建任务' }))
  await act(async () => { await Promise.resolve() })
  expect(face.confirm).toHaveBeenCalledWith('new', 'new')
})

it('切会话后迟到的确认回包不能让新会话的确认卡消失或出旧建卡提示', async () => {
  let finish: ((v: unknown) => void) | undefined
  const face: ConfirmDockInjected = {
    proposal: vi.fn(async (sid: string) => ({ ok: true as const, value: { id: sid, title: `需求${sid}`, do: '欢迎', notDo: '其他', doneWhen: '可见', questions: [] } })),
    confirm: vi.fn(() => new Promise(resolve => { finish = resolve })) as never,
    dismiss: vi.fn(), dispatcherStatus: vi.fn(async () => ({ ok: true as const, value: 'ready' as const })),
  }
  const inputActions = { setDraft: vi.fn(), submit: vi.fn() }
  const useInput = <T,>(f: (i: { draft: string; attachmentIds: unknown[]; phase: string }) => T) => f({ draft: '', attachmentIds: [], phase: 'plain' })
  const view = render(<ConfirmDock {...face} sessionId="old" inputActions={inputActions} useInput={useInput} />)
  fireEvent.click(await screen.findByRole('button', { name: '确认，建任务' }))
  view.rerender(<ConfirmDock {...face} sessionId="new" inputActions={inputActions} useInput={useInput} />)
  await act(async () => { await Promise.resolve() })
  await act(async () => { finish?.({ ok: true as const, value: { ok: true as const, card: 'CARD-OLD', planning: true, message: '旧会话已建卡' } }) })
  expect(screen.queryByText('旧会话已建卡')).toBeNull()
  expect(screen.getByText('需求new')).toBeTruthy()
})

it('确认时切走，后台建成的卡仍要建任务对话，但不能把提示带到新会话', async () => {
  let finish: ((v: unknown) => void) | undefined
  const onCreated = vi.fn()
  const face: ConfirmDockInjected = {
    proposal: vi.fn(async (sid: string) => ({ ok: true as const, value: { id: sid, title: `需求${sid}`, do: '欢迎', notDo: '其他', doneWhen: '可见', questions: [] } })),
    confirm: vi.fn(() => new Promise(resolve => { finish = resolve })) as never,
    dismiss: vi.fn(), dispatcherStatus: vi.fn(async () => ({ ok: true as const, value: 'ready' as const })), onCreated,
  }
  const inputActions = { setDraft: vi.fn(), submit: vi.fn() }
  const useInput = <T,>(f: (i: { draft: string; attachmentIds: unknown[]; phase: string }) => T) => f({ draft: '', attachmentIds: [], phase: 'plain' })
  const view = render(<ConfirmDock {...face} sessionId="old" inputActions={inputActions} useInput={useInput} />)
  fireEvent.click(await screen.findByRole('button', { name: '确认，建任务' }))
  view.rerender(<ConfirmDock {...face} sessionId="new" inputActions={inputActions} useInput={useInput} />)
  await act(async () => { finish?.({ ok: true as const, value: { ok: true as const, card: 'CARD-OLD', planning: true, message: '旧会话已建卡' } }) })
  expect(onCreated).toHaveBeenCalledWith('CARD-OLD')
  expect(screen.queryByText('旧会话已建卡')).toBeNull()
})
