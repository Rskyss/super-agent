/**
 * 12 / 13 / 13b：接调度员确认卡（贴在输入框上方）。
 * - 统一承载两种入口：① 对话里由 propose_dispatcher 工具出示的确认卡；② 任务卡未写方案且缺调度员时的第一张卡。
 * - 先出示 是什么 / 怎么接 / 需要你 / 影响范围，只有点“确认，检测并启用”才运行真实检测；
 * - 失败保留原角色、原卡和输入框内容，不假成功。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { DispatcherConnectOptions, DispatcherConnectResult, DispatcherProposal, DispatcherTool } from '../types.ts'
import { initialPoll, markHandled, receivePoll, type PollState } from '../confirm-state.ts'
import { notifySessionRefresh, subscribeSessionRefresh } from './session-dock-sync.ts'
import css from './ConfirmDock.module.css'

const TOOL_OPTIONS: readonly { readonly id: DispatcherTool; readonly label: string }[] = [
  { id: 'codex', label: 'Codex' },
  { id: 'agy', label: 'agy' },
]

/** 根据选定的白名单工具拼出大白话确认说明，绝不引入未确认命令或其他工具名。 */
export function buildLocalDispatcherCopy(tool: DispatcherTool, base?: DispatcherProposal | null): DispatcherProposal {
  const safeTool: DispatcherTool = tool === 'agy' ? 'agy' : 'codex'
  const label = safeTool === 'agy' ? 'agy' : 'Codex'
  if (base && base.tool === safeTool) return base
  return {
    id: base?.id ?? `draft-${safeTool}`,
    session: base?.session ?? '',
    tool: safeTool,
    label,
    card: base?.card ?? '',
    title: `接一个调度员：${label}`,
    what: `把电脑里已装好并登录过的 ${label} 接进来当调度员，用来写方案、拆步骤和安排执行与复查。`,
    how: `在新版的积木库里套用 ${label} 预置接法，并做一次很短的连通检测，确认它真能回话（不改项目代码）。`,
    needYou: `如果你还没装好或没登录 ${label}，请先自己完成安装与登录，再点确认；我不会替你自动安装软件。`,
    scope: `只在当前项目设置与新版的积木库保存这份接法，把调度员设置为 ${label}；不碰你原有的积木库，也不读其他钥匙串密钥。`,
    createdAt: base?.createdAt ?? Date.now(),
  }
}

export interface DispatcherCardViewProps {
  readonly proposal: DispatcherProposal
  readonly connect: (tool: DispatcherTool, card: string, proposalId?: string) => Promise<DispatcherConnectResult>
  readonly onDismiss: () => Promise<void> | void
  readonly onConnected?: (result: DispatcherConnectResult) => void
  readonly dismissLabel?: string
}

/** 可复用的“接调度员”确认卡视图（按 proposal.id 拆分内层实例并做异步 id 守卫，切换确认卡时不串状态）。 */
export function DispatcherCardView(props: DispatcherCardViewProps): ReactElement {
  return <DispatcherCardInner key={props.proposal.id} {...props} />
}

function DispatcherCardInner(props: DispatcherCardViewProps): ReactElement {
  const [selectedTool, setSelectedTool] = useState<DispatcherTool>(props.proposal.tool === 'agy' ? 'agy' : 'codex')
  const [folded, setFolded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errorNote, setErrorNote] = useState('')
  const [infoNote, setInfoNote] = useState('')
  const aliveRef = useRef(true)
  const proposalIdRef = useRef(props.proposal.id)
  proposalIdRef.current = props.proposal.id

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  useEffect(() => {
    setSelectedTool(props.proposal.tool === 'agy' ? 'agy' : 'codex')
    setErrorNote('')
    setInfoNote('')
  }, [props.proposal.id, props.proposal.tool])

  const shown = buildLocalDispatcherCopy(selectedTool, props.proposal)

  const handleConfirm = async () => {
    if (busy) return
    const activeId = props.proposal.id
    setBusy(true)
    setErrorNote('')
    setInfoNote(`正在用真实小任务检测 ${shown.label}，请稍候…`)
    try {
      const res = await props.connect(selectedTool, props.proposal.card, activeId)
      if (!aliveRef.current || proposalIdRef.current !== activeId) return
      setBusy(false)
      if (!res.ok || res.status !== 'ready') {
        setInfoNote('')
        setErrorNote(res.reason || `没接通 ${shown.label}，已保留原有角色和输入，请先确认安装并登录后重试`)
        return
      }
      setInfoNote(
        res.startedPlan
          ? `${shown.label} 已接好，已开始为 ${res.card || '这张卡'} 写方案。`
          : (res.planNote || `${shown.label} 已接好并设为调度员。`),
      )
      props.onConnected?.(res)
    } catch (error: unknown) {
      if (!aliveRef.current || proposalIdRef.current !== activeId) return
      setBusy(false)
      setInfoNote('')
      setErrorNote(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div className={css.dock}>
      <div className={css.card} role="group" aria-label={`接调度员确认卡：${shown.label}`}>
        <div className={css.head}>
          <span className={css.title}>{shown.title}</span>
          <Tag>接调度员</Tag>
          <span className={css.status}>{busy ? '正在检测…' : '等你确认'}</span>
          <Button size="sm" variant="ghost" onClick={() => { setFolded(!folded) }}>
            {folded ? '展开' : '收起'}
          </Button>
        </div>
        {folded ? null : (
          <div className={css.rows}>
            <span className={css.label}>订阅工具</span>
            <div className={css.toolTabs} role="radiogroup" aria-label="选择调度员工具">
              {TOOL_OPTIONS.map(opt => (
                <button
                  key={opt.id}
                  type="button"
                  disabled={busy}
                  aria-pressed={selectedTool === opt.id}
                  className={`${css.toolTab} ${selectedTool === opt.id ? css.toolTabActive : ''}`}
                  onClick={() => {
                    setSelectedTool(opt.id)
                    setErrorNote('')
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <span className={css.label}>是什么</span>
            <span className={css.value}>{shown.what}</span>
            <span className={css.label}>怎么接</span>
            <span className={css.value}>{shown.how}</span>
            <span className={css.label}>需要你</span>
            <span className={css.value}>{shown.needYou}</span>
            <span className={css.label}>影响范围</span>
            <span className={css.value}>{shown.scope}</span>
          </div>
        )}
        <div className={css.actions}>
          <Button size="sm" variant="primary" disabled={busy} onClick={() => { void handleConfirm() }}>
            {busy ? '正在检测并启用…' : '确认，检测并启用'}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => { void props.onDismiss() }}>
            {props.dismissLabel ?? '先不做'}
          </Button>
        </div>
        {infoNote ? <div className={css.note}>{infoNote}</div> : null}
        {errorNote ? <div className={css.errorNote} role="alert">{errorNote}</div> : null}
      </div>
    </div>
  )
}

/** 网关注入给会话“接调度员”确认卡的能力。 */
export interface DispatcherDockInjected {
  readonly dispatcherProposal: (sessionId: string) => Promise<RemoteResult<DispatcherProposal | null>>
  readonly dismissDispatcherProposal: (sessionId: string, id: string) => Promise<RemoteResult<boolean>>
  readonly connectDispatcher: (
    tool: DispatcherTool,
    options?: DispatcherConnectOptions,
  ) => Promise<RemoteResult<DispatcherConnectResult>>
}

type DispatcherDockProps = DispatcherDockInjected & { readonly sessionId: string }

/** 按 sessionId 物理隔离会话确认卡，承接对话里由 propose_dispatcher 出示的确认卡。 */
export function DispatcherDock(props: DispatcherDockProps): ReactElement | null {
  return <DispatcherDockSession key={props.sessionId} {...props} />
}

function DispatcherDockSession(props: DispatcherDockProps): ReactElement | null {
  const { sessionId } = props
  const propsRef = useRef(props)
  propsRef.current = props
  const aliveRef = useRef(true)
  const noteTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const [poll, setPoll] = useState<PollState<DispatcherProposal>>(initialPoll)
  const [note, setNote] = useState('')
  const proposal = poll.current

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      clearTimeout(noteTimerRef.current)
    }
  }, [])

  useEffect(() => {
    let alive = true
    let seq = 0
    const load = () => {
      const mine = ++seq
      void propsRef.current.dispatcherProposal(sessionId).then((r) => {
        if (alive && r.ok) setPoll(s => receivePoll(s, mine, r.value))
      }).catch(() => {})
    }
    load()
    const timer = setInterval(load, 1500)
    const unsubscribe = subscribeSessionRefresh(sessionId, load)
    return () => {
      alive = false
      clearInterval(timer)
      unsubscribe()
    }
  }, [sessionId])

  if (!proposal) {
    return note ? <div className={css.dock}><div className={css.card}><div className={css.note}>{note}</div></div></div> : null
  }

  const handleConnect = async (tool: DispatcherTool, card: string, proposalId?: string): Promise<DispatcherConnectResult> => {
    const res = await propsRef.current.connectDispatcher(tool, {
      sessionId,
      card,
      ...(proposalId === undefined ? {} : { proposalId }),
    })
    if (!res.ok) {
      return {
        ok: false, status: 'missing', tool, label: tool === 'agy' ? 'agy' : 'Codex',
        model: '', reason: res.error.message, startedPlan: false, planNote: '', card,
      }
    }
    return res.value
  }

  const handleDismiss = async () => {
    const targetId = proposal.id
    const res = await propsRef.current.dismissDispatcherProposal(sessionId, targetId)
    if (!aliveRef.current || !(res.ok && res.value)) return
    setPoll(s => markHandled(s, targetId))
    notifySessionRefresh(sessionId)
  }

  return (
    <DispatcherCardView
      proposal={proposal}
      connect={handleConnect}
      onDismiss={handleDismiss}
      onConnected={(result) => {
        if (!aliveRef.current) return
        setPoll(s => markHandled(s, proposal.id))
        notifySessionRefresh(sessionId)
        clearTimeout(noteTimerRef.current)
        setNote(
          result.startedPlan
            ? `${result.label} 已接好，并已为 ${result.card || '这张卡'} 启动写方案。`
            : `${result.label} 已接好并设为当前项目调度员。`,
        )
        noteTimerRef.current = setTimeout(() => {
          if (aliveRef.current) setNote('')
        }, 5000)
      }}
    />
  )
}
