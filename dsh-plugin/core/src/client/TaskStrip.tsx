/**
 * 03b（旧 input.dock 兼容保留组件）：聊天里点确认建出的任务卡，在输入框上面显示“需求摘要（可展开）+ 任务卡小条”。
 * 正式聊天流内联回执已迁移至 TaskReceipt（conversation.chat.turnTail）；本组件保留供旧兼容与单测使用，
 * 内部直接复用 session-cards-feed 与 TaskReceiptCardPair，保持按 sessionId 隔离与全部安全行为。
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactElement } from 'react'
import { Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { DispatcherState } from '../types.ts'
import css from './ConfirmDock.module.css'
import {
  createSessionCardsFeed,
  type TaskCardSummaryRow,
} from './session-cards-feed.ts'
import { TaskReceiptCardPair } from './TaskReceipt.tsx'

type Row = TaskCardSummaryRow

export interface TaskStripInjected {
  sessionCards: (sessionId: string) => Promise<RemoteResult<Row[]>>
  cardOfSession: (sessionId: string) => Promise<RemoteResult<Row | null>>
  openTask: (card: string) => Promise<void>
  /** 可选读取当前真实调度员状态（仅 ready 且有有效名称时展示，未知省略）。 */
  dispatcherState?: () => Promise<RemoteResult<DispatcherState>>
}

/** 已结束的卡不再占小条：done 已完成、dropped 不做了（后台 phase.py 的叫法；复查第 4 项中等 4）。 */
const FINISHED = new Set(['done', 'dropped'])

type TaskStripProps = TaskStripInjected & { sessionId: string }

/** 按 sessionId 物理隔离会话实例，切换会话时清掉旧会话的任务条/自卡状态。 */
export function TaskStrip(props: TaskStripProps): ReactElement | null {
  return <TaskStripSession key={props.sessionId} {...props} />
}

function TaskStripSession(props: TaskStripProps): ReactElement | null {
  const { sessionId } = props
  const propsRef = useRef(props)
  propsRef.current = props
  const aliveRef = useRef(true)

  const [expandedIds, setExpandedIds] = useState<ReadonlySet<string>>(new Set())
  /* 旧版收起小条状态保留注释备用：const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set()) */
  const [error, setError] = useState('')

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  const hasDispatcher = props.dispatcherState !== undefined
  const feed = useMemo(() => createSessionCardsFeed(sessionId, {
    sessionCards: sid => propsRef.current.sessionCards(sid),
    cardOfSession: sid => propsRef.current.cardOfSession(sid),
    ...(hasDispatcher ? { dispatcherState: () => propsRef.current.dispatcherState!() } : {}),
  }), [sessionId, hasDispatcher])

  useEffect(() => () => { feed.dispose() }, [feed])

  const snap = useSyncExternalStore(feed.subscribe, feed.getSnapshot, feed.getSnapshot)
  const { rows, self, dispatcherLabel, selfFailed, rowsFailed } = snap
  const stale = selfFailed || rowsFailed

  const toggleExpanded = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleOpenTask = (cardId: string) => {
    const targetSession = sessionId
    setError('')
    void propsRef.current.openTask(cardId).catch((e: unknown) => {
      if (aliveRef.current && propsRef.current.sessionId === targetSession) {
        setError(`没打开：${e instanceof Error ? e.message : String(e)}`)
      }
    })
  }

  const visible = rows.filter(r => !FINISHED.has(r.phaseCode) && r.id !== self?.id).slice(0, 3)
  // 什么都没有：读得到就不显示；读不到要说出来，免得分不清“没有任务”和“读取失败”（复查第 4 项第 2 轮中等 3）。
  if (!self && visible.length === 0) {
    return stale ? <div className={css.dock}><div className={css.note}>读不到任务卡信息，稍后会自动再试。</div></div> : null
  }
  return (
    <div className={css.dock}>
      {self ? (
        <div className={css.card} style={visible.length > 0 ? { marginBottom: 6 } : undefined} aria-label={`任务卡 ${self.id}`}>
          <div className={css.head}>
            <span className={css.title}>{self.id} {self.title}</span>
            <Tag>任务卡</Tag>
            <span className={css.status}>{self.phaseLabel}</span>
          </div>
          {stale ? <div className={css.note}>读不到最新进度，上面的状态可能不是最新的。</div> : null}
          {/* 旧版任务会话普通说明框保留注释备用：
          <div className={css.note}>{stale ? '读不到最新进度，上面的状态可能不是最新的。' : '这是这张卡自己的对话：问进度、要改要求都在这里说，不影响别的对话。'}</div>
          */}
        </div>
      ) : null}
      {visible.map(r => (
        <TaskReceiptCardPair
          key={r.id}
          row={r}
          dispatcherLabel={dispatcherLabel}
          expanded={expandedIds.has(r.id)}
          onToggleExpand={toggleExpanded}
          onOpenTask={handleOpenTask}
          variant="dock"
        />
      ))}
      {error ? <div className={css.note}>{error}</div> : null}
      {!self && stale ? <div className={css.note}>读不到最新进度，状态可能不是最新的。</div> : null}
    </div>
  )
}
