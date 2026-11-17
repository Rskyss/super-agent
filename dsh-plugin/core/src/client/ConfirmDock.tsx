/**
 * 03 / 03c / 19：确认卡贴在输入框上面。接着聊不会被顶走；同一时间只一张（网关按会话只存一张）。
 * 按钮：确认，建任务 / 我要改 / 不对，改成…（19 改类型菜单）。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button, Menu, Tag, type MenuEntry } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { ChatSnapshot, UseChat } from '@deepseek-ai/dsh-client-ui-chat/client'
import css from './ConfirmDock.module.css'
import { canAutoSend, initialPoll, markHandled, receivePoll, type PollState } from '../confirm-state.ts'
import { STEP_LABELS } from '../progress-model.ts'
import { splitAcceptanceCriteria } from '../revision.ts'
import type { CardRow, WorkflowInfo } from '../types.ts'
import type { ConfirmedTurnAnchorActions } from './receipt-anchor-store.ts'
import {
  startBoundedAnchorRecovery,
  type AnchorRecoveryRequest,
  type ReceiptAnchorRecoveryCoordinator,
} from './receipt-anchor-recovery.ts'
import { notifySessionRefresh } from './session-dock-sync.ts'

interface ProposalView {
  readonly id: string
  readonly title: string
  readonly do: string
  readonly notDo: string
  readonly doneWhen: string
  readonly questions: readonly { readonly question: string; readonly suggestion: string }[]
}

export interface ConfirmDockComposerState {
  readonly hasProposal: boolean
  readonly dispatcher: 'ready' | 'missing' | 'unknown'
  readonly menuOpen: boolean
}

/** 确认卡用到的网关能力（由插件注入）。 */
export interface ConfirmDockInjected {
  proposal: (sessionId: string) => Promise<RemoteResult<ProposalView | null>>
  confirm: (sessionId: string, id: string) => Promise<RemoteResult<{ ok: boolean; card: string; planning: boolean; message: string }>>
  dismiss: (sessionId: string, id: string) => Promise<RemoteResult<boolean>>
  dispatcherStatus: () => Promise<RemoteResult<'ready' | 'missing' | 'unknown'>>
  /** 可选读取当前真实工作流与复查轮次上限（未注入或缺失时不编造版本与轮次）。 */
  workflow?: () => Promise<RemoteResult<WorkflowInfo>>
  /** 可选“我要改”成功撤卡后将焦点还给输入框。 */
  focusInput?: () => void
  /** 建卡成功后调用（建任务对话）。 */
  onCreated?: (card: string) => void
  /** 可选读取当前会话真实已建卡片（用于在确认成功后记录真实 createdAt 与确认轮锚点）。 */
  sessionCards?: (sessionId: string) => Promise<RemoteResult<readonly Pick<CardRow, 'id' | 'createdAt'>[]>>
  /** 可选读取当前施工项目路径（Host.status().project），用于按项目维度持久化锚点。 */
  projectStatus?: () => Promise<RemoteResult<{ readonly project: string }>>
  /** 可选由宿主 apply 作用域注入的待锚定恢复协调器（随插件作用域销毁，无模块级单例）。 */
  anchorRecovery?: ReceiptAnchorRecoveryCoordinator
  /** 可选同步当前确认卡存在性、调度员状态与改类型菜单开关到输入栏提示注册表。 */
  onComposerState?: (state: ConfirmDockComposerState) => void
}

/** DSH 给输入框上方条目的标准能力里，我们只用这几个。 */
interface DraftActions {
  setDraft(text: string): void
  submit(): void
}
interface DraftSnapshot { draft: string; attachmentIds: readonly unknown[]; phase: string }
type UseInput = <S>(selector: (input: DraftSnapshot) => S) => S

/** 改类型（19）：选了别的类型，撤掉这张卡并告诉超级助手按新类型重来（保留既有 chat/connect 语义兼容）。 */
export const TYPE_CHANGES: readonly { readonly id: string; readonly label: string; readonly message: string }[] = [
  { id: 'chat', label: '只是聊聊', message: '这不是要干活的，只是聊聊想法，先不用出卡。' },
  { id: 'connect', label: '接零件（接一个工具）', message: '这是要接一个零件（工具），不是干活，请按接零件来。' },
]

const TYPE_MENU_MESSAGES: Readonly<Record<string, string>> = {
  chat: TYPE_CHANGES[0]!.message,
  'connect-cli': '这是要接一个零件（命令行工具），不是干活，请按接零件来。',
  'connect-llm': '这是要接一个零件（大模型，按量接口），不是干活，请按接零件来。',
  'connect-dispatcher': '这是要接一个零件（调度员，订阅命令行），不是干活，请按接零件来。',
  'connect-mcp': '这是要接一个零件（MCP），不是干活，请按接零件来。',
  ability: '这是要造一张能力卡，不是干活，请按造能力来。',
  workflow: '这是要把能力卡串成流程，不是干活，请按搭流程来。',
}

const TYPE_MENU_ITEMS: readonly MenuEntry[] = [
  {
    id: 'chat',
    label: (
      <span className={css.menuRow}>
        <span className={css.menuLabel}>只是聊聊</span>
        <span className={css.menuDesc}>不建任务</span>
      </span>
    ),
  },
  {
    id: 'work',
    label: (
      <span className={css.menuRow}>
        <span className={css.menuLabel}>干活</span>
        <span className={css.menuDesc}>用现成流程做一件事</span>
      </span>
    ),
  },
  { type: 'separator', id: 'sep-connect' },
  { type: 'label', id: 'connect-group', text: '接零件（往积木库里加东西）' },
  {
    id: 'connect-cli',
    label: (
      <span className={`${css.menuRow} ${css.menuRowIndented}`}>
        <span className={css.menuLabel}>命令行工具</span>
        <span className={css.menuDesc}>如高德、小红书</span>
      </span>
    ),
  },
  {
    id: 'connect-llm',
    label: (
      <span className={`${css.menuRow} ${css.menuRowIndented}`}>
        <span className={css.menuLabel}>大模型（按量接口）</span>
        <span className={css.menuDesc}>如千问、豆包</span>
      </span>
    ),
  },
  {
    id: 'connect-dispatcher',
    label: (
      <span className={`${css.menuRow} ${css.menuRowIndented}`}>
        <span className={css.menuLabel}>调度员（订阅命令行）</span>
        <span className={css.menuDesc}>如 Codex</span>
      </span>
    ),
  },
  {
    id: 'connect-mcp',
    label: (
      <span className={`${css.menuRow} ${css.menuRowIndented}`}>
        <span className={css.menuLabel}>MCP</span>
        <span className={css.menuDesc}>别人做好的工具接口</span>
      </span>
    ),
  },
  { type: 'separator', id: 'sep-ability' },
  {
    id: 'ability',
    label: (
      <span className={css.menuRow}>
        <span className={css.menuLabel}>造能力</span>
        <span className={css.menuDesc}>做一张能力卡</span>
      </span>
    ),
  },
  {
    id: 'workflow',
    label: (
      <span className={css.menuRow}>
        <span className={css.menuLabel}>搭流程</span>
        <span className={css.menuDesc}>把能力卡串成流程</span>
      </span>
    ),
  },
]

const TYPE_MENU_FOOTER: readonly MenuEntry[] = [
  { type: 'label', id: 'type-hint', text: '选了别的，超级玩家按新类型重写这张卡' },
]

const CIRCLE_NUMS = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
const DEFAULT_EXECUTION_STEPS = STEP_LABELS
  .filter(spec => spec.key === 'test' || spec.key === 'dev' || spec.key === 'check' || spec.key === 'rev' || spec.key === 'accept')
  .map(spec => spec.label)

function splitCriteriaLines(doneWhen: string): string[] {
  const items = splitAcceptanceCriteria(doneWhen)
  if (items.length > 0) return items
  const trimmed = doneWhen.trim()
  return trimmed && trimmed !== '（待填）' ? [trimmed] : []
}

function formatWorkflowLine(info: WorkflowInfo | null): string {
  const name = info?.name?.trim() || '开发软件'
  const versionPart = typeof info?.version === 'number' && Number.isFinite(info.version)
    ? ` · 第 ${info.version} 版`
    : ''
  const steps = info?.steps && info.steps.length > 0 ? info.steps : DEFAULT_EXECUTION_STEPS
  const stepsText = steps
    .map(step => (
      step === '复查' && typeof info?.maxReviewRounds === 'number' && Number.isFinite(info.maxReviewRounds)
        ? `复查（最多 ${info.maxReviewRounds} 轮）`
        : step
    ))
    .join(' → ')
  return `${name}${versionPart}：${stepsText}`
}

const NOOP_USE_CHAT: (selector: (snapshot: ChatSnapshot) => number | undefined) => number | undefined = () => undefined

function selectLastTurnStartSeq(snapshot: ChatSnapshot): number | undefined {
  const lastTurn = snapshot.timeline.turnOrder.at(-1)
  if (lastTurn === undefined) return undefined
  const seq = snapshot.timeline.turns.get(lastTurn)?.start?.seq
  return typeof seq === 'number' && Number.isInteger(seq) && seq >= 0 ? seq : undefined
}

type ConfirmDockProps = ConfirmDockInjected & {
  sessionId: string
  inputActions: DraftActions
  useInput: UseInput
  useChat?: UseChat
  actions?: ConfirmedTurnAnchorActions
}

/** 按 sessionId 物理隔离会话实例，切换会话时立刻卸载旧会话状态并清掉异步回调。 */
export function ConfirmDock(props: ConfirmDockProps): ReactElement | null {
  return <ConfirmDockSession key={props.sessionId} {...props} />
}

function ConfirmDockSession(props: ConfirmDockProps): ReactElement | null {
  const { sessionId } = props
  const propsRef = useRef(props)
  propsRef.current = props
  const aliveRef = useRef(true)
  const menuAnchorRef = useRef<HTMLSpanElement>(null)
  const changeBtnRef = useRef<HTMLButtonElement>(null)
  const useChatHook = useRef<
    (selector: (snapshot: ChatSnapshot) => number | undefined) => number | undefined
  >(props.useChat ?? NOOP_USE_CHAT).current
  const lastTurnStartSeq = useChatHook(selectLastTurnStartSeq)
  const lastTurnStartSeqRef = useRef<number | undefined>(undefined)
  lastTurnStartSeqRef.current = lastTurnStartSeq

  const [poll, setPoll] = useState<PollState<ProposalView>>(initialPoll)
  const proposal = poll.current
  // 每次渲染都更新：撤卡请求回来、真要替你发的那一刻，看的是输入框“现在”的样子（复查第 3 项第 2 轮严重）。
  const autoSendNow = useRef(false)
  autoSendNow.current = props.useInput(canAutoSend)
  const [dispatcher, setDispatcher] = useState<'ready' | 'missing' | 'unknown'>('unknown')
  const [workflow, setWorkflow] = useState<WorkflowInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [menu, setMenu] = useState(false)
  /* 旧版收起状态保留注释备用：const [folded, setFolded] = useState(false) */
  const hasProposal = proposal !== null
  const onComposerState = props.onComposerState

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!onComposerState) return
    onComposerState({
      hasProposal,
      dispatcher,
      menuOpen: hasProposal && menu,
    })
  }, [onComposerState, hasProposal, dispatcher, menu])

  useEffect(() => {
    if (!onComposerState) return
    return () => {
      onComposerState({
        hasProposal: false,
        dispatcher: 'unknown',
        menuOpen: false,
      })
    }
  }, [onComposerState, sessionId])

  const isActive = (targetSession: string) => aliveRef.current && propsRef.current.sessionId === targetSession

  useEffect(() => {
    let alive = true
    let seq = 0
    let dispatcherSeq = 0
    let ticks = 0
    const load = () => {
      const mine = ++seq
      void propsRef.current.proposal(sessionId).then((r) => {
        if (alive && r.ok) setPoll(s => receivePoll(s, mine, r.value))
      }).catch(() => {})
      // 调度员状态每 10 秒重查一次：网络恢复、刚接好调度员后提醒会自己消失。
      if (ticks++ % 7 === 0) {
        const dispatcherMine = ++dispatcherSeq
        void propsRef.current.dispatcherStatus().then((r) => {
          if (alive && r.ok && dispatcherMine === dispatcherSeq) setDispatcher(r.value)
        }).catch(() => {})
      }
    }
    load()
    const timer = setInterval(load, 1500)
    return () => { alive = false; clearInterval(timer) }
  }, [sessionId])

  useEffect(() => {
    const readWorkflow = propsRef.current.workflow
    if (!readWorkflow) return
    let alive = true
    void readWorkflow().then((r) => {
      if (alive && r.ok) setWorkflow(r.value)
    }).catch(() => {})
    return () => { alive = false }
  }, [sessionId])

  if (!proposal) return note ? <div className={css.dock}><div className={css.card}><div className={css.note}>{note}</div></div></div> : null

  const confirm = () => {
    if (busy) return
    // 在发起远程 confirm 请求前同步捕获当前 timeline 最后一轮的 start.seq（即使仍为 open 轮）。
    const capturedStartSeq = lastTurnStartSeqRef.current
    const targetSession = sessionId
    const targetId = proposal.id
    const onCreated = propsRef.current.onCreated
    const targetActions = propsRef.current.actions
    const readSessionCards = propsRef.current.sessionCards
    const readProjectStatus = propsRef.current.projectStatus
    const recoveryCoordinator = propsRef.current.anchorRecovery
    setBusy(true)
    setNote('正在建卡…')
    void propsRef.current.confirm(targetSession, targetId).then((r) => {
      if (r.ok && r.value.ok) {
        onCreated?.(r.value.card)
        notifySessionRefresh(targetSession)
        const cardId = r.value.card.trim()
        if (
          cardId
          && capturedStartSeq !== undefined
          && targetActions !== undefined
          && readSessionCards !== undefined
          && readProjectStatus !== undefined
        ) {
          const request: AnchorRecoveryRequest = {
            sessionId: targetSession,
            cardId,
            startSeq: capturedStartSeq,
            readSessionCards,
            readProjectStatus,
            remember: targetActions.remember,
            stagePending: targetActions.stagePending,
          }
          if (recoveryCoordinator !== undefined) {
            recoveryCoordinator.schedule(request)
          } else {
            startBoundedAnchorRecovery(request)
          }
        }
      }
      if (!isActive(targetSession)) return
      setBusy(false)
      if (!r.ok) { setNote(`没建成：${r.error.message}`); return }
      if (!r.value.ok) { setNote(`没建成：${r.value.message}`); return }
      setPoll(s => markHandled(s, targetId))
      // 确认成功后不再展示长说明卡，由原确认轮聊天流中的 TaskReceipt 真实任务条承接结果；失败保留短错误。
      setNote('')
    }).catch((error: unknown) => {
      if (!isActive(targetSession)) return
      setBusy(false)
      setNote(`没建成：${error instanceof Error ? error.message : String(error)}`)
    })
  }

  const revise = () => {
    if (busy) return
    const targetSession = sessionId
    const targetId = proposal.id
    setMenu(false)
    setBusy(true)
    void propsRef.current.dismiss(targetSession, targetId).then((r) => {
      if (!isActive(targetSession)) return
      setBusy(false)
      if (!(r.ok && r.value)) {
        setNote('没撤掉确认卡，请稍后再试')
        return
      }
      setPoll(s => markHandled(s, targetId))
      setNote('')
      propsRef.current.focusInput?.()
    }).catch(() => {
      if (!isActive(targetSession)) return
      setBusy(false)
      setNote('没撤掉确认卡，请稍后再试')
    })
  }

  const dismissWithTypeChange = (then: string) => {
    if (busy) return
    const targetSession = sessionId
    const targetId = proposal.id
    setMenu(false)
    void propsRef.current.dismiss(targetSession, targetId).then((r) => {
      if (!isActive(targetSession)) return
      if (!(r.ok && r.value)) return
      setPoll(s => markHandled(s, targetId))
      // 只在输入框空着且无附件时替你发；正在输入或有附件就不动它，提示你自己说（复查第 3 项严重 2）。
      if (autoSendNow.current) {
        propsRef.current.inputActions.setDraft(then)
        propsRef.current.inputActions.submit()
      } else {
        setNote(`已撤掉确认卡。你输入框里还有内容，没替你发，可以自己跟超级玩家说：“${then}”`)
      }
    }).catch(() => {})
  }

  const handleTypeSelect = (id: string) => {
    if (id === 'work') {
      setMenu(false)
      return
    }
    const message = TYPE_MENU_MESSAGES[id]
    if (!message) {
      setMenu(false)
      return
    }
    dismissWithTypeChange(message)
  }

  const criteriaItems = splitCriteriaLines(proposal.doneWhen)

  return (
    <div
      className={menu ? `${css.dock} ${css.dockMenuOpen}` : css.dock}
      data-menu-open={menu ? 'true' : 'false'}
    >
      <div className={`${css.card} ${css.proposalCard}`} role="group" aria-label={`确认卡：${proposal.title}`}>
        <span ref={menuAnchorRef} className={css.menuAnchor} aria-hidden="true" />
        <div className={css.head}>
          <span className={css.title}>{proposal.title}</span>
          <Tag>干活 · 开发软件</Tag>
          <Menu
            open={menu}
            side="right"
            portal
            autoFocus
            selectedId="work"
            items={TYPE_MENU_ITEMS}
            footer={TYPE_MENU_FOOTER}
            anchorRef={menuAnchorRef}
            returnFocus={() => changeBtnRef.current}
            listClassName={css.typeMenuList}
            onSelect={handleTypeSelect}
            onClose={() => { setMenu(false) }}
            anchor={(
              <Button
                ref={changeBtnRef}
                size="sm"
                variant="ghost"
                className={css.proposalChange}
                disabled={busy}
                aria-haspopup="menu"
                aria-expanded={menu}
                onClick={() => { setMenu(open => !open) }}
              >
                不对，改成…
              </Button>
            )}
          />
          <Tag tone="info" className={css.status}>等你确认</Tag>
          {/* 旧版收起按钮保留注释备用（恢复时取消注释并恢复 folded 状态）：
          <Button size="sm" variant="ghost" onClick={() => { setFolded(!folded) }}>{folded ? '展开' : '收起'}</Button>
          */}
        </div>
        <div className={css.rows}>
          <span className={css.label}>要做什么</span>
          <span className={css.value}>{proposal.do}</span>
          <span className={css.label}>验收标准</span>
          <ol className={css.criteriaList} aria-label="验收标准">
            {criteriaItems.map((item, idx) => (
              <li key={`${idx}-${item}`}>
                {idx < CIRCLE_NUMS.length ? CIRCLE_NUMS[idx] : `${idx + 1}.`} {item}
              </li>
            ))}
          </ol>
          <span className={css.label}>不做什么</span>
          <span className={css.value}>{proposal.notDo}</span>
          {proposal.questions.length > 0 ? (
            <>
              <span className={css.warn}>？要你拍板的</span>
              <div className={css.questionList}>
                {proposal.questions.map((q) => (
                  <div key={q.question} className={css.questionItem}>
                    <span>{q.question}</span>
                    {q.suggestion ? <span className={css.ask}>（建议：{q.suggestion}）</span> : null}
                  </div>
                ))}
              </div>
            </>
          ) : null}
          <span className={css.label}>用哪条流程</span>
          <span className={css.value}>{formatWorkflowLine(workflow)}</span>
          {dispatcher !== 'missing' ? null : (
            <>
              <span className={css.warn}>！调度员</span>
              <span className={css.value}>还没有。建卡后我先在任务卡里帮你接一个（订阅的 Codex 或 agy），接好才开始写方案。</span>
            </>
          )}
        </div>
        <div className={css.actions}>
          <Button size="sm" variant="primary" disabled={busy} onClick={confirm}>确认，建任务</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={revise}>我要改</Button>
          {/* 旧版“先不做”按钮保留注释备用（恢复时取消注释并恢复无参数 dismiss 即可）：
          <Button size="sm" variant="outline" disabled={busy} onClick={() => { dismiss() }}>先不做</Button>
          */}
        </div>
        {note ? <div className={css.note}>{note}</div> : null}
        {/* {proposal.questions.length ? <div className={css.note}>问号不答也能直接确认，按建议走。</div> : null} */}
      </div>
    </div>
  )
}
