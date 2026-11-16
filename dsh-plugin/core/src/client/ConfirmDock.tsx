/**
 * 03 / 03c / 19：确认卡贴在输入框上面。接着聊不会被顶走；同一时间只一张（网关按会话只存一张）。
 * 按钮：确认，建任务 / 先不做 / 不对，改成…（只是聊聊、干活、接零件）。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import css from './ConfirmDock.module.css'
import { canAutoSend, initialPoll, markHandled, receivePoll, type PollState } from '../confirm-state.ts'
import { notifySessionRefresh } from './session-dock-sync.ts'

interface ProposalView {
  readonly id: string
  readonly title: string
  readonly do: string
  readonly notDo: string
  readonly doneWhen: string
  readonly questions: readonly { readonly question: string; readonly suggestion: string }[]
}

/** 确认卡用到的网关能力（由插件注入）。 */
export interface ConfirmDockInjected {
  proposal: (sessionId: string) => Promise<RemoteResult<ProposalView | null>>
  confirm: (sessionId: string, id: string) => Promise<RemoteResult<{ ok: boolean; card: string; planning: boolean; message: string }>>
  dismiss: (sessionId: string, id: string) => Promise<RemoteResult<boolean>>
  dispatcherStatus: () => Promise<RemoteResult<'ready' | 'missing' | 'unknown'>>
  /** 建卡成功后调用（建任务对话）。 */
  onCreated?: (card: string) => void
}

/** DSH 给输入框上方条目的标准能力里，我们只用这几个。 */
interface DraftActions {
  setDraft(text: string): void
  submit(): void
}
interface DraftSnapshot { draft: string; attachmentIds: readonly unknown[]; phase: string }
type UseInput = <S>(selector: (input: DraftSnapshot) => S) => S

/** 改类型（19）：选了别的类型，撤掉这张卡并告诉超级助手按新类型重来。 */
export const TYPE_CHANGES: readonly { readonly id: string; readonly label: string; readonly message: string }[] = [
  { id: 'chat', label: '只是聊聊', message: '这不是要干活的，只是聊聊想法，先不用出卡。' },
  { id: 'connect', label: '接零件（接一个工具）', message: '这是要接一个零件（工具），不是干活，请按接零件来。' },
]

type ConfirmDockProps = ConfirmDockInjected & { sessionId: string; inputActions: DraftActions; useInput: UseInput }

/** 按 sessionId 物理隔离会话实例，切换会话时立刻卸载旧会话状态并清掉异步回调。 */
export function ConfirmDock(props: ConfirmDockProps): ReactElement | null {
  return <ConfirmDockSession key={props.sessionId} {...props} />
}

function ConfirmDockSession(props: ConfirmDockProps): ReactElement | null {
  const { sessionId } = props
  const propsRef = useRef(props)
  propsRef.current = props
  const aliveRef = useRef(true)
  const noteTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const [poll, setPoll] = useState<PollState<ProposalView>>(initialPoll)
  const proposal = poll.current
  // 每次渲染都更新：撤卡请求回来、真要替你发的那一刻，看的是输入框“现在”的样子（复查第 3 项第 2 轮严重）。
  const autoSendNow = useRef(false)
  autoSendNow.current = props.useInput(canAutoSend)
  const [dispatcher, setDispatcher] = useState<'ready' | 'missing' | 'unknown'>('unknown')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [menu, setMenu] = useState(false)
  const [folded, setFolded] = useState(false)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      clearTimeout(noteTimerRef.current)
    }
  }, [])

  const isActive = (targetSession: string) => aliveRef.current && propsRef.current.sessionId === targetSession

  useEffect(() => {
    let alive = true
    let seq = 0
    let ticks = 0
    const load = () => {
      const mine = ++seq
      void propsRef.current.proposal(sessionId).then((r) => {
        if (alive && r.ok) setPoll(s => receivePoll(s, mine, r.value))
      }).catch(() => {})
      // 调度员状态每 10 秒重查一次：网络恢复、刚接好调度员后提醒会自己消失。
      if (ticks++ % 7 === 0) {
        void propsRef.current.dispatcherStatus().then((r) => {
          if (alive && r.ok) setDispatcher(r.value)
        }).catch(() => {})
      }
    }
    load()
    const timer = setInterval(load, 1500)
    return () => { alive = false; clearInterval(timer) }
  }, [sessionId])

  if (!proposal) return note ? <div className={css.dock}><div className={css.card}><div className={css.note}>{note}</div></div></div> : null

  const confirm = () => {
    if (busy) return
    const targetSession = sessionId
    const targetId = proposal.id
    const onCreated = propsRef.current.onCreated
    clearTimeout(noteTimerRef.current)
    setBusy(true)
    setNote('正在建卡…')
    void propsRef.current.confirm(targetSession, targetId).then((r) => {
      if (r.ok && r.value.ok) onCreated?.(r.value.card)
      if (!isActive(targetSession)) return
      setBusy(false)
      if (!r.ok) { setNote(`没建成：${r.error.message}`); return }
      if (!r.value.ok) { setNote(`没建成：${r.value.message}`); return }
      setPoll(s => markHandled(s, targetId))
      notifySessionRefresh(targetSession)
      setNote(r.value.message || `已建卡 ${r.value.card}${r.value.planning ? '，调度员开始写方案' : '，稍后开始写方案'}。上面那条“查看任务”能打开它自己的对话。`)
      noteTimerRef.current = setTimeout(() => {
        if (isActive(targetSession)) setNote('')
      }, 6000)
    }).catch((error: unknown) => {
      if (!isActive(targetSession)) return
      setBusy(false)
      setNote(`没建成：${error instanceof Error ? error.message : String(error)}`)
    })
  }
  const dismiss = (then?: string) => {
    const targetSession = sessionId
    const targetId = proposal.id
    setMenu(false)
    void propsRef.current.dismiss(targetSession, targetId).then((r) => {
      if (!isActive(targetSession)) return
      if (!(r.ok && r.value)) return
      setPoll(s => markHandled(s, targetId))
      if (!then) return
      // 只在输入框空着时替你发；你正在写东西就不动它，提示你自己说（复查第 3 项严重 2）。
      if (autoSendNow.current) { propsRef.current.inputActions.setDraft(then); propsRef.current.inputActions.submit() }
      else setNote(`已撤掉确认卡。你输入框里还有内容，没替你发，可以自己跟超级助手说：“${then}”`)
    }).catch(() => {})
  }

  return (
    <div className={css.dock}>
      <div className={css.card} role="group" aria-label={`确认卡：${proposal.title}`}>
        <div className={css.head}>
          <span className={css.title}>{proposal.title}</span>
          <Tag>干活</Tag>
          <span className={css.status}>等你确认</span>
          <Button size="sm" variant="ghost" onClick={() => { setFolded(!folded) }}>{folded ? '展开' : '收起'}</Button>
        </div>
        {folded ? null : (
          <div className={css.rows}>
            <span className={css.label}>要做什么</span><span className={css.value}>{proposal.do}</span>
            <span className={css.label}>验收标准</span><span className={css.value}>{proposal.doneWhen}</span>
            <span className={css.label}>不做什么</span><span className={css.value}>{proposal.notDo}</span>
            {proposal.questions.map(q => (
              <QuestionRow key={q.question} question={q.question} suggestion={q.suggestion} />
            ))}
            {dispatcher !== 'missing' ? null : (
              <>
                <span className={css.warn}>！调度员</span>
                <span className={css.value}>还没有。建卡后我先在任务卡里帮你接一个（订阅的 Codex 或 agy），接好才开始写方案。</span>
              </>
            )}
          </div>
        )}
        <div className={css.actions}>
          <Button size="sm" variant="primary" disabled={busy} onClick={confirm}>确认，建任务</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => { dismiss() }}>先不做</Button>
          <Button size="sm" variant="ghost" className={css.change} disabled={busy} onClick={() => { setMenu(!menu) }}>不对，改成…</Button>
          {menu ? (
            <div className={css.menu} role="menu">
              <span className={css.note}>现在是：干活</span>
              {TYPE_CHANGES.map(t => (
                <button key={t.id} type="button" role="menuitem" className={css.menuItem} onClick={() => { dismiss(t.message) }}>{t.label}</button>
              ))}
            </div>
          ) : null}
        </div>
        {note ? <div className={css.note}>{note}</div> : null}
        {proposal.questions.length ? <div className={css.note}>问号不答也能直接确认，按建议走。</div> : null}
      </div>
    </div>
  )
}

function QuestionRow({ question, suggestion }: { question: string; suggestion: string }): ReactElement {
  return (
    <>
      <span className={css.warn}>？</span>
      <span className={css.value}>{question}<span className={css.ask}>{suggestion ? `（建议：${suggestion}）` : ''}</span></span>
    </>
  )
}
