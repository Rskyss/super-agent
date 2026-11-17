/**
 * 03b：挂载于 conversation.chat.turnTail 的内联任务回执（需求摘要 + 任务卡小条）。
 * - 锚定在建卡确认那一轮尾部，后续新消息与刷新保持原位，不随最新轮移动；
 * - 订阅同会话共享 SessionCardsFeed，不重复发起轮询请求风暴；
 * - 不过滤已结束/第 4+ 张卡，但绝不在该流重复渲染任务会话自身的 self 卡头/说明框。
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactElement } from 'react'
import { Button, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { ChatSnapshot } from '@deepseek-ai/dsh-client-ui-chat/client'
import type { InjectFace, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import { buildReceiptTurnWindows, resolveReceiptTurn } from '../receipt-anchor.ts'
import { FINISHED_CARD_STATES, FINISHED_PHASE_CODES } from '../types.ts'
import type { ConfirmedTurnAnchorStoreProps } from './receipt-anchor-store.ts'
import {
  displayTaskNumber,
  formatCreatedClock,
  splitCriteriaItems,
  summarizeRequirementDo,
  type SessionCardsFeed,
  type TaskCardSummaryRow,
} from './session-cards-feed.ts'
import dockCss from './ConfirmDock.module.css'
import receiptCss from './TaskReceipt.module.css'

const CIRCLE_NUMS = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'

export interface TaskReceiptInjected {
  readonly feed: SessionCardsFeed
  readonly openTask: (card: string) => Promise<void>
}

export type TaskReceiptProps = PropsRuntime<'conversation.chat.turnTail'>
  & ConfirmedTurnAnchorStoreProps
  & InjectFace<TaskReceiptInjected>

const selectTimeline = (snapshot: ChatSnapshot) => snapshot.timeline
const selectLastTurnNumber = (snapshot: ChatSnapshot): number | undefined => snapshot.timeline.turnOrder.at(-1)

function isFinishedRow(row: TaskCardSummaryRow): boolean {
  return FINISHED_PHASE_CODES.has(row.phaseCode) || FINISHED_CARD_STATES.has(row.state ?? '')
}

function statusTagView(row: TaskCardSummaryRow): { readonly label: string; readonly tone: 'success' | 'neutral' | 'warning' | 'info' } {
  if (isFinishedRow(row)) {
    const done = row.phaseCode === 'done' || row.state === '已合并'
    return {
      label: row.phaseLabel || row.state || (done ? '已完成' : '已放弃'),
      tone: done ? 'success' : 'neutral',
    }
  }
  return {
    label: row.needsYou
      ? row.phaseLabel.startsWith('等你')
        ? row.phaseLabel
        : `等你处理 · ${row.phaseLabel}`
      : row.phaseLabel.startsWith('进行中')
        ? row.phaseLabel
        : `进行中 · ${row.phaseLabel}`,
    tone: row.needsYou ? 'warning' : 'info',
  }
}

export interface TaskReceiptCardPairProps {
  readonly row: TaskCardSummaryRow
  readonly dispatcherLabel: string
  readonly expanded: boolean
  readonly onToggleExpand: (id: string) => void
  readonly onOpenTask: (id: string) => void
  /** inline 为聊天流内联 03b 样式；dock 为旧版 TaskStrip 兼容样式。 */
  readonly variant: 'inline' | 'dock'
}

/**
 * 共享任务回执展示块（需求摘要卡 + 任务卡小条），供 TaskReceipt 与旧兼容组件 TaskStrip 共用。
 */
export function TaskReceiptCardPair({
  row,
  dispatcherLabel,
  expanded,
  onToggleExpand,
  onOpenTask,
  variant,
}: TaskReceiptCardPairProps): ReactElement {
  const styles = variant === 'inline' ? receiptCss : dockCss
  const taskNum = displayTaskNumber(row.id)
  const req = row.requirement ?? null
  const doSummary = req?.do ? summarizeRequirementDo(req.do) : ''
  const criteriaItems = req?.doneWhen ? splitCriteriaItems(req.doneWhen) : []
  const cardDispatcher = row.dispatcher?.trim() || dispatcherLabel
  const metaParts = [
    row.flow?.trim() ?? '',
    cardDispatcher ? `调度员 ${cardDispatcher}` : '',
    formatCreatedClock(row.createdAt),
  ].filter(Boolean)
  const tagInfo = statusTagView(row)
  const stripClass = variant === 'inline'
    ? (isFinishedRow(row) ? receiptCss.card : `${receiptCss.card} ${receiptCss.stripCardActive}`)
    : `${dockCss.card} ${dockCss.taskStripCard}`

  return (
    <div className={variant === 'inline' ? receiptCss.pairGroup : undefined}>
      {req ? (
        <div
          className={variant === 'inline' ? receiptCss.card : `${dockCss.card} ${dockCss.reqSummaryCard}`}
          aria-label={`需求摘要 ${row.id}`}
        >
          <div className={styles.reqHead}>
            <span className={styles.reqTitle}>
              {variant === 'inline' ? `需求 · ${taskNum} 号` : `需求 · ${taskNum} 号 · 已确认`}
            </span>
            {variant === 'inline' ? <Tag tone="success">已确认</Tag> : null}
          </div>
          <div className={styles.reqSummaryRow}>
            {doSummary ? <span className={styles.reqDoSummary}>{doSummary}</span> : null}
            {criteriaItems.length > 0 ? (
              <span className={styles.reqMeta}>{`· ${criteriaItems.length} 条验收标准`}</span>
            ) : null}
            {req.questions.map(q => (
              <span key={q.question} className={styles.reqMeta}>
                {`· ${q.question}${q.suggestion ? `（按建议：${q.suggestion}）` : ''}`}
              </span>
            ))}
            <Button
              size="sm"
              variant="ghost"
              className={styles.reqExpandBtn}
              onClick={() => { onToggleExpand(row.id) }}
            >
              {expanded ? '收起' : '展开'}
            </Button>
          </div>
          {expanded ? (
            <div className={variant === 'inline' ? receiptCss.rows : `${dockCss.rows} ${dockCss.reqExpandedBody}`}>
              {req.do ? (
                <>
                  <span className={styles.label}>要做什么</span>
                  <span className={styles.value}>{req.do}</span>
                </>
              ) : null}
              {criteriaItems.length > 0 ? (
                <>
                  <span className={styles.label}>验收标准</span>
                  <ol className={styles.criteriaList} aria-label="验收标准">
                    {criteriaItems.map((item, idx) => (
                      <li key={`${idx}-${item}`}>
                        {idx < CIRCLE_NUMS.length ? CIRCLE_NUMS[idx] : `${idx + 1}.`} {item}
                      </li>
                    ))}
                  </ol>
                </>
              ) : null}
              {req.notDo ? (
                <>
                  <span className={styles.label}>不做什么</span>
                  <span className={styles.value}>{req.notDo}</span>
                </>
              ) : null}
              {req.questions.length > 0 ? (
                <>
                  <span className={styles.label}>要你拍板的</span>
                  <div className={styles.questionList}>
                    {req.questions.map(q => (
                      <div key={q.question} className={styles.questionItem}>
                        <span>{q.question}</span>
                        {q.suggestion ? <span className={styles.ask}>{`（建议：${q.suggestion}）`}</span> : null}
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {variant === 'inline' ? (
        <p className={styles.nextStep}>
          {row.phaseLabel?.includes('接调度员')
            ? '任务卡建好了。先在任务卡里接一个调度员，接好后写方案给你批，你批了才开工。这边我们可以接着聊别的。'
            : '任务卡建好了。调度员先写一份方案，写好了我在任务卡里拿给你批，你批了才开工。这边我们可以接着聊别的。'}
        </p>
      ) : null}
      <div className={stripClass} aria-label={`任务卡小条 ${row.id}`}>
        <div className={styles.taskStripRow}>
          <span className={styles.taskBadge}>{taskNum}</span>
          <div className={styles.taskStripMain}>
            <div className={styles.taskStripTitleRow}>
              <span className={styles.title}>{variant === 'inline' ? row.title : `${taskNum} 号 · ${row.title}`}</span>
              <Tag tone={tagInfo.tone}>{tagInfo.label}</Tag>
            </div>
            {metaParts.length > 0 ? (
              <div className={styles.taskStripMeta}>{metaParts.join(' · ')}</div>
            ) : null}
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => { onOpenTask(row.id) }}
          >
            查看任务
          </Button>
          {/* 旧版关闭小条按钮保留注释备用：
          <Button size="sm" variant="ghost" aria-label="收起这条" onClick={() => { setHidden(new Set([...hidden, r.id])) }}>×</Button>
          */}
        </div>
      </div>
    </div>
  )
}

/**
 * 聊天流每轮末尾挂载的 03b 内联回执槽位组件。
 */
export function TaskReceipt(props: TaskReceiptProps): ReactElement | null {
  const { sessionId, turn, feed, useChat, useStore } = props
  const propsRef = useRef(props)
  propsRef.current = props
  const aliveRef = useRef(true)

  const [expandedIds, setExpandedIds] = useState<ReadonlySet<string>>(new Set())
  const [error, setError] = useState('')

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  useEffect(() => {
    setError('')
    setExpandedIds(new Set())
  }, [sessionId])

  const feedSnap = useSyncExternalStore(feed.subscribe, feed.getSnapshot, feed.getSnapshot)
  const timeline = useChat(selectTimeline)
  const lastTurnNumber = useChat(selectLastTurnNumber)
  const project = feedSnap.project
  const projectRecords = useStore(state => (project ? state.records[project] : undefined))
  const pendingAnchors = useStore(state => state.pending)

  useEffect(() => {
    if (!project || !pendingAnchors) return
    const resolvePending = propsRef.current.actions?.resolvePending
    if (typeof resolvePending !== 'function') return
    for (const entry of Object.values(pendingAnchors)) {
      if (entry.sessionId !== sessionId || entry.project !== project) continue
      const row = feedSnap.rows.find(r => r.id !== feedSnap.self?.id && r.id === entry.card)
      const rowCreatedAt = typeof row?.createdAt === 'string' ? row.createdAt.trim() : ''
      if (!rowCreatedAt) continue
      resolvePending(project, sessionId, entry.card, rowCreatedAt)
    }
  }, [feedSnap.rows, feedSnap.self?.id, pendingAnchors, project, sessionId])

  const windows = useMemo(() => buildReceiptTurnWindows(timeline), [timeline])
  const currentTurnStartSeq = turn.start?.seq

  const matchedRows = useMemo(() => {
    if (currentTurnStartSeq === undefined) return [] as TaskCardSummaryRow[]
    const candidates = feedSnap.rows.filter(r => r.id !== feedSnap.self?.id)
    const matched: TaskCardSummaryRow[] = []
    for (const row of candidates) {
      const saved = projectRecords?.[row.id]
      const rowCreatedAt = typeof row.createdAt === 'string' ? row.createdAt.trim() : ''
      const savedStartSeq = (
        saved !== undefined
        && rowCreatedAt !== ''
        && saved.createdAt === rowCreatedAt
      )
        ? saved.startSeq
        : undefined
      const resolvedStartSeq = resolveReceiptTurn(row.createdAt, savedStartSeq, windows)
      if (resolvedStartSeq === currentTurnStartSeq) {
        matched.push(row)
      }
    }
    return matched
  }, [currentTurnStartSeq, feedSnap.rows, feedSnap.self?.id, projectRecords, windows])

  const isLastTurn = lastTurnNumber !== undefined && turn.turn === lastTurnNumber

  if (matchedRows.length === 0) {
    if (feedSnap.rowsFailed && isLastTurn) {
      return (
        <div className={receiptCss.inlineRoot}>
          <div className={receiptCss.note}>读不到任务卡信息，稍后会自动再试。</div>
        </div>
      )
    }
    return null
  }

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

  return (
    <div className={receiptCss.inlineRoot}>
      {matchedRows.map(row => (
        <TaskReceiptCardPair
          key={row.id}
          row={row}
          dispatcherLabel={feedSnap.dispatcherLabel}
          expanded={expandedIds.has(row.id)}
          onToggleExpand={toggleExpanded}
          onOpenTask={handleOpenTask}
          variant="inline"
        />
      ))}
      {error ? <div className={receiptCss.note}>{error}</div> : null}
      {feedSnap.rowsFailed ? <div className={receiptCss.note}>读不到最新进度，状态可能不是最新的。</div> : null}
    </div>
  )
}
