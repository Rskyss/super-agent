// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmDock, type ConfirmDockInjected } from '../src/client/ConfirmDock.tsx'

afterEach(cleanup)

const PROPOSAL = { id: 'p1', title: '加 undo', do: '加 undo 命令', notDo: '不改别的', doneWhen: '① 能撤销', questions: [] }

function setup() {
  let resolveDismiss: (v: { ok: true; value: boolean }) => void = () => {}
  const face: ConfirmDockInjected = {
    proposal: vi.fn(() => Promise.resolve({ ok: true as const, value: PROPOSAL })),
    confirm: vi.fn(),
    dismiss: vi.fn(() => new Promise(r => { resolveDismiss = r as typeof resolveDismiss })),
    dispatcherStatus: vi.fn(() => Promise.resolve({ ok: true as const, value: 'ready' as const })),
  } as unknown as ConfirmDockInjected
  const actions = { setDraft: vi.fn(), submit: vi.fn() }
  let input = { draft: '', attachmentIds: [] as unknown[], phase: 'plain' }
  const useInput = <S,>(selector: (i: typeof input) => S): S => selector(input)
  const view = render(<ConfirmDock {...face} sessionId="s1" inputActions={actions} useInput={useInput} />)
  const typeDraft = (draft: string) => {
    input = { ...input, draft }
    view.rerender(<ConfirmDock {...face} sessionId="s1" inputActions={actions} useInput={useInput} />)
  }
  return { face, actions, typeDraft, resolve: (v: boolean) => resolveDismiss({ ok: true, value: v }) }
}

async function openMenuAndPickChat() {
  await act(async () => { await Promise.resolve() })
  fireEvent.click(await screen.findByText('不对，改成…'))
  fireEvent.click(screen.getByText('只是聊聊'))
}

describe('ConfirmDock 改类型', () => {
  it('输入框一直空着：撤卡后替你发那句话', async () => {
    const t = setup()
    await openMenuAndPickChat()
    await act(async () => { t.resolve(true); await Promise.resolve() })
    expect(t.actions.setDraft).toHaveBeenCalledWith(expect.stringContaining('只是聊聊'))
    expect(t.actions.submit).toHaveBeenCalledTimes(1)
  })

  it('点了改类型、撤卡还没回来时你开始打字：不覆盖、不替你发', async () => {
    const t = setup()
    await openMenuAndPickChat()
    t.typeDraft('我还在想')
    await act(async () => { t.resolve(true); await Promise.resolve() })
    expect(t.actions.setDraft).not.toHaveBeenCalled()
    expect(t.actions.submit).not.toHaveBeenCalled()
    expect(screen.getByText(/没替你发/)).toBeTruthy()
  })
})

it('当前接入仅支持订阅工具，缺调度员提示不承诺未实现的接口接入',async()=>{const face={proposal:vi.fn(async()=>({ok:true as const,value:PROPOSAL})),confirm:vi.fn(),dismiss:vi.fn(),dispatcherStatus:vi.fn(async()=>({ok:true as const,value:'missing' as const}))};render(<ConfirmDock {...face} sessionId="missing" inputActions={{setDraft:vi.fn(),submit:vi.fn()}} useInput={((selector:any)=>selector({draft:'',attachmentIds:[],phase:'plain'})) as any}/>);await screen.findByText('！调度员');expect(document.body.textContent).not.toContain('按量付费的大模型都行')})
