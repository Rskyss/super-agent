/**
 * 任务会话输入框上方桥接组件：
 * - 通过 cardOfSession 查找当前会话是否为任务卡对话；
 * - 是任务卡对话时自动打开右栏任务进度，并按优先级挂载：
 *   0) 会话若已有工具出示的“接调度员”确认卡（DispatcherDock），本桥让位不重画；
 *   1) 任务卡仍未写方案（plan_waiting）且调度员未就绪：第一张卡直接显示“接调度员”确认卡；
 *   2) 否则显示“改要求确认条”或“阶段决定卡”；
 *   同一时刻最多只出一张卡！
 * - 通过 DSH 标准 useInput / inputActions 读取最新草稿，且仅在提交期间未被用户改写时才清空；
 * - 普通对话（无任务卡）不打开右栏，也不渲染任何内容。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type {
  CardActionOptions,
  CardActionResult,
  CardRow,
  CardViewPayload,
  DispatcherConnectOptions,
  DispatcherConnectResult,
  DispatcherProposal,
  DispatcherState,
  DispatcherTool,
  ImagePayload,
  PendingChange,
  PrepareRevisionResult,
  PreviewResult,
} from '../types.ts'
import { ChangeDock } from './ChangeDock.tsx'
import { DecisionDock } from './DecisionDock.tsx'
import { DispatcherCardView } from './DispatcherDock.tsx'
import { notifySessionRefresh, subscribeSessionRefresh } from './session-dock-sync.ts'

interface DraftActions {
  setDraft(text: string): void
  removeAttachment?(id: string): void
  submit(): void
}
interface DraftSnapshot {
  draft: string
  attachmentIds: readonly unknown[]
  phase: string
}
type UseInput = <S>(selector: (input: DraftSnapshot) => S) => S

/** 桥接组件需要的会话、调度员接入、改要求与决定卡能力。 */
export interface TaskDockBridgeInjected {
  readonly cardOfSession: (sessionId: string) => Promise<RemoteResult<CardRow | null>>
  readonly dispatcherState?: () => Promise<RemoteResult<DispatcherState>>
  readonly dispatcherProposal?: () => Promise<RemoteResult<DispatcherProposal | null>>
  readonly dispatcherDraft?: (tool: DispatcherTool, card?: string) => Promise<RemoteResult<DispatcherProposal>>
  readonly connectDispatcher?: (
    tool: DispatcherTool,
    options?: DispatcherConnectOptions,
  ) => Promise<RemoteResult<DispatcherConnectResult>>
  readonly pendingChange: (card: string) => Promise<RemoteResult<PendingChange | null>>
  readonly decideChange: (card: string, id: string, confirm: boolean) => Promise<RemoteResult<CardActionResult>>
  readonly view: (card: string) => Promise<RemoteResult<string>>
  readonly plan: (card: string) => Promise<RemoteResult<string>>
  readonly cardAction: (
    card: string,
    action: string,
    text: string,
    options?: CardActionOptions,
  ) => Promise<RemoteResult<CardActionResult>>
  readonly prepareRevision?: (
    card: string,
    text: string,
    sourceDraft?: string,
    attachments?: readonly ImagePayload[],
    sourceAttachmentIds?: readonly string[],
  ) => Promise<RemoteResult<PrepareRevisionResult>>
  readonly readRevisionAttachments?: (ids: readonly string[]) => Promise<ImagePayload[]>
  readonly releaseRevisionAttachments?: (ids: readonly string[]) => void
  readonly preview: (card: string, action: 'start' | 'stop') => Promise<RemoteResult<PreviewResult>>
  readonly openPreview: (url: string) => boolean | void
  readonly openProgress: (card: string) => void
}

type TaskDockBridgeProps = TaskDockBridgeInjected & {
  readonly sessionId: string
  readonly useInput: UseInput
  readonly inputActions: DraftActions
}

function defaultAutoProposal(card: string): DispatcherProposal {
  return {
    id: `auto-${card}`,
    session: '',
    tool: 'codex',
    label: 'Codex',
    card,
    title: '接一个调度员：Codex',
    what: '把电脑里已装好并登录过的 Codex 接进来当调度员，用来写方案、拆步骤和安排执行与复查。',
    how: '在新版的积木库里套用 Codex 预置接法，并做一次很短的连通检测，确认它真能回话（不改项目代码）。',
    needYou: '如果你还没装好或没登录 Codex，请先自己完成安装与登录，再点确认；我不会替你自动安装软件。',
    scope: '只在当前项目设置与新版的积木库保存这份接法，把调度员设置为 Codex；不碰你原有的积木库，也不读其他钥匙串密钥。',
    createdAt: Date.now(),
  }
}

function normalizeAttachmentIds(rawIds: readonly unknown[]): string[] {
  if (!Array.isArray(rawIds) || rawIds.length === 0) return []
  return rawIds.map(id => String(id ?? '')).filter(Boolean)
}

function sameIdList(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false
  for (let i = 0; i < left.length; i += 1) {
    if (left[i] !== right[i]) return false
  }
  return true
}

/** 按 sessionId 物理隔离实例，确保切换会话不串状态、不误开右栏。 */
export function TaskDockBridge(props: TaskDockBridgeProps): ReactElement | null {
  return <TaskDockBridgeSession key={props.sessionId} {...props} />
}

function TaskDockBridgeSession(props: TaskDockBridgeProps): ReactElement | null {
  const { sessionId } = props
  const draft = props.useInput(input => input.draft)
  const attachmentIds = props.useInput(input => input.attachmentIds)
  const draftRef = useRef(draft)
  draftRef.current = draft
  const attachmentIdsRef = useRef<readonly string[]>([])
  attachmentIdsRef.current = normalizeAttachmentIds(attachmentIds)
  const propsRef = useRef(props)
  propsRef.current = props
  const openedRef = useRef(false)
  const aliveRef = useRef(true)

  const [card, setCard] = useState<string | null>(null)
  const cardRef = useRef<string | null>(null)
  cardRef.current = card
  const [changeStatus, setChangeStatus] = useState<'loading' | 'active' | 'none'>('loading')

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])
  const [phaseCode, setPhaseCode] = useState<string>('')
  const [dispatcherStatus, setDispatcherStatus] = useState<'loading' | 'ready' | 'missing'>('loading')
  const [hasToolDispatcherProposal, setHasToolDispatcherProposal] = useState(false)
  const [dismissedAuto, setDismissedAuto] = useState(false)
  const [autoDraft, setAutoDraft] = useState<DispatcherProposal | null>(null)
  const [changeRefreshTick, setChangeRefreshTick] = useState(0)

  useEffect(() => {
    setChangeStatus('loading')
    setPhaseCode('')
    setDismissedAuto(false)
    setAutoDraft(null)
    setChangeRefreshTick(0)
  }, [card])

  useEffect(() => {
    let alive = true
    let retryTimer: ReturnType<typeof setTimeout> | undefined

    const resolveSessionCard = () => {
      void propsRef.current.cardOfSession(sessionId).then(
        (result) => {
          if (!alive) return
          if (!result.ok) {
            retryTimer = setTimeout(resolveSessionCard, 3000)
            return
          }
          const foundCard = result.value?.id ?? null
          setCard(foundCard)
          if (foundCard && !openedRef.current) {
            openedRef.current = true
            propsRef.current.openProgress(foundCard)
          }
        },
        () => {
          if (alive) retryTimer = setTimeout(resolveSessionCard, 3000)
        },
      )
    }

    resolveSessionCard()
    return () => {
      alive = false
      if (retryTimer !== undefined) clearTimeout(retryTimer)
    }
  }, [sessionId])

  useEffect(() => {
    if (!card) return
    let alive = true

    const pollTaskGates = () => {
      if (propsRef.current.dispatcherState) {
        void propsRef.current.dispatcherState().then((res) => {
          if (!alive || !res.ok) return
          setDispatcherStatus(res.value.status === 'ready' ? 'ready' : res.value.status === 'missing' ? 'missing' : 'loading')
        }).catch(() => {})
      } else {
        setDispatcherStatus('ready')
      }

      if (propsRef.current.dispatcherProposal) {
        void propsRef.current.dispatcherProposal().then((res) => {
          if (alive && res.ok) setHasToolDispatcherProposal(Boolean(res.value))
        }).catch(() => {})
      }

      void propsRef.current.view(card).then((res) => {
        if (!alive || !res.ok) return
        try {
          const parsed = JSON.parse(res.value) as CardViewPayload
          setPhaseCode(String(parsed.card?.phase?.code ?? ''))
        } catch {
          // 忽略解析异常，交由既有卡展示。
        }
      }).catch(() => {})
    }

    pollTaskGates()
    const timer = setInterval(pollTaskGates, 2500)
    const unsubscribe = subscribeSessionRefresh(sessionId, pollTaskGates)
    return () => {
      alive = false
      clearInterval(timer)
      unsubscribe()
    }
  }, [card, sessionId])

  const shouldShowAutoDispatcher = Boolean(
    card
    && !hasToolDispatcherProposal
    && !dismissedAuto
    && phaseCode === 'plan_waiting'
    && dispatcherStatus === 'missing'
    && props.connectDispatcher,
  )

  useEffect(() => {
    if (!shouldShowAutoDispatcher || !card || autoDraft) return
    let alive = true
    if (propsRef.current.dispatcherDraft) {
      void propsRef.current.dispatcherDraft('codex', card).then((res) => {
        if (!alive) return
        setAutoDraft(res.ok ? res.value : defaultAutoProposal(card))
      }).catch(() => {
        if (alive) setAutoDraft(defaultAutoProposal(card))
      })
    } else {
      setAutoDraft(defaultAutoProposal(card))
    }
    return () => { alive = false }
  }, [shouldShowAutoDispatcher, card, autoDraft])

  if (!card) return null
  if (hasToolDispatcherProposal) return null

  if (shouldShowAutoDispatcher && props.connectDispatcher) {
    const connectFn = props.connectDispatcher
    const proposal = autoDraft ?? defaultAutoProposal(card)
    return (
      <DispatcherCardView
        proposal={proposal}
        dismissLabel="暂不启用"
        connect={async (tool, targetCard) => {
          const res = await connectFn(tool, { sessionId, card: targetCard })
          if (!res.ok) {
            return {
              ok: false, status: 'missing', tool, label: tool === 'agy' ? 'agy' : 'Codex',
              model: '', reason: res.error.message, startedPlan: false, planNote: '', card: targetCard,
            }
          }
          return res.value
        }}
        onDismiss={() => {
          if (!aliveRef.current) return
          setDismissedAuto(true)
        }}
        onConnected={() => {
          if (!aliveRef.current) return
          setDispatcherStatus('ready')
          notifySessionRefresh(sessionId)
        }}
      />
    )
  }

  const clearSubmittedInput = (targetCard: string, snapDraft: string, snapIds: readonly string[]) => {
    if (!aliveRef.current || cardRef.current !== targetCard) {
      return
    }
    if (draftRef.current !== snapDraft || !sameIdList(attachmentIdsRef.current, snapIds)) {
      return
    }
    propsRef.current.inputActions.setDraft('')
    for (const id of snapIds) {
      propsRef.current.inputActions.removeAttachment?.(id)
    }
    if (snapIds.length > 0) {
      propsRef.current.releaseRevisionAttachments?.(snapIds)
    }
  }

  const readSnapshotAttachments = async (snapIds: readonly string[]): Promise<ImagePayload[]> => {
    if (snapIds.length === 0) return []
    const reader = propsRef.current.readRevisionAttachments
    if (!reader) {
      throw new Error('当前无法读取截图附件，不能只转交文字修改意见')
    }
    const images = await reader(snapIds)
    if (!Array.isArray(images) || images.length !== snapIds.length) {
      throw new Error('截图数量与草稿不一致，请重新选择截图')
    }
    return images
  }

  return (
    <>
      <ChangeDock
        card={card}
        pendingChange={props.pendingChange}
        decideChange={props.decideChange}
        onStatusChange={setChangeStatus}
        refreshTick={changeRefreshTick}
        onDecided={(change, confirm) => {
          if (!aliveRef.current) return
          if (confirm && change.sourceDraft !== undefined) {
            clearSubmittedInput(change.card, change.sourceDraft, change.sourceAttachmentIds ?? [])
          }
          notifySessionRefresh(sessionId)
        }}
      />
      {changeStatus === 'none' ? (
        <DecisionDock
          card={card}
          view={props.view}
          plan={props.plan}
          supportsAttachments={Boolean(props.readRevisionAttachments)}
          cardAction={async (targetCard: string, action: string, text: string, options?: CardActionOptions) => {
            if (action !== 'revise') {
              return propsRef.current.cardAction(targetCard, action, text, options)
            }
            const snapDraft = draftRef.current
            const snapIds = attachmentIdsRef.current
            const images = await readSnapshotAttachments(snapIds)
            if (!aliveRef.current || cardRef.current !== targetCard) {
              return { ok: true, value: { ok: false, message: '当前会话或任务卡已切换，已取消本次修改' } }
            }
            const nextOptions: CardActionOptions | undefined = images.length > 0
              ? { ...(options ?? {}), attachments: images }
              : options
            const res = nextOptions !== undefined
              ? await propsRef.current.cardAction(targetCard, action, text, nextOptions)
              : await propsRef.current.cardAction(targetCard, action, text)
            if (aliveRef.current && cardRef.current === targetCard && res.ok && res.value.ok) {
              clearSubmittedInput(targetCard, snapDraft, snapIds)
              notifySessionRefresh(sessionId)
            }
            return res
          }}
          {...(props.prepareRevision ? {
            prepareRevision: async (targetCard: string, text: string) => {
              const snapDraft = draftRef.current
              const snapIds = attachmentIdsRef.current
              const images = await readSnapshotAttachments(snapIds)
              if (!aliveRef.current || cardRef.current !== targetCard) {
                return { ok: true, value: { ok: false, pending: false, message: '当前会话或任务卡已切换，已取消本次修改' } }
              }
              const res = snapIds.length > 0
                ? await propsRef.current.prepareRevision!(targetCard, text, snapDraft, images, snapIds)
                : await propsRef.current.prepareRevision!(targetCard, text, snapDraft)
              if (aliveRef.current && cardRef.current === targetCard && res.ok && res.value.ok && !res.value.pending) {
                clearSubmittedInput(targetCard, snapDraft, snapIds)
              }
              return res
            },
          } : {})}
          onRevisionPrepared={() => {
            if (!aliveRef.current) return
            setChangeRefreshTick(n => n + 1)
            notifySessionRefresh(sessionId)
          }}
          preview={props.preview}
          openPreview={props.openPreview}
          readDraft={() => draftRef.current}
          clearDraft={() => {}}
          hasAttachments={() => attachmentIdsRef.current.length > 0}
        />
      ) : null}
    </>
  )
}
