import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import { Button, Modal } from '@deepseek-ai/dsh-client-ui-primitives'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import type { CardRow, CardTrajectory, ImagePayload } from '../types.ts'
import { TaskTrajectory } from './TaskTrajectory.tsx'
import styles from './ConversationHeader.module.css'

const POLL_INTERVAL_MS = 2000

export interface SuperagentConversationHeaderSlotInjected {
  readonly cardOfSession: (sessionId: SessionId) => Promise<RemoteResult<CardRow | null>>
  readonly trajectory: (card: string) => Promise<CardTrajectory>
  readonly cardAttachment: (card: string, name: string) => Promise<ImagePayload>
}

export type SuperagentConversationHeaderSlotProps =
  PropsRuntime<'conversation.header'> & SuperagentConversationHeaderSlotInjected

export interface SuperagentConversationHeaderTask {
  readonly id: string
  readonly title: string
}

export interface SuperagentConversationHeaderProps {
  readonly title: string
  readonly blank: boolean
  readonly task: SuperagentConversationHeaderTask | null
  readonly onTrajectory: () => void
}

/**
 * 将类似 CARD-022 的任务卡号格式化为简短展示编号（如 22），非 CARD 编号原样保留。
 */
export function displayTaskNumber(id: string): string {
  const match = /^CARD-0*(\d+)$/i.exec(id.trim())
  return match?.[1] ?? id
}

/**
 * 超级玩家对话标题栏纯展示组件。
 * 普通空对话渲染空；普通非空对话仅展示标题与“超级玩家”；
 * 任务对话无论是否空会话，均展示卡号标题、“超级玩家 · 任务”与任务轨迹按钮。
 */
export function SuperagentConversationHeader({
  title,
  blank,
  task,
  onTrajectory,
}: SuperagentConversationHeaderProps) {
  if (!task && blank) {
    return null
  }

  const taskNumber = task ? displayTaskNumber(task.id) : ''
  const displayTitle = task
    ? (task.title.trim() ? `${taskNumber} 号：${task.title}` : `${taskNumber} 号`)
    : title
  const subtitle = task ? '超级玩家 · 任务' : '超级玩家'

  return (
    <header className={styles.root}>
      <div className={styles.titleBlock}>
        <div className={styles.title} title={displayTitle}>
          {displayTitle}
        </div>
        <div className={styles.subtitle}>{subtitle}</div>
      </div>
      {task ? (
        <div className={styles.actions}>
          <Button size="sm" variant="outline" onClick={onTrajectory}>
            任务轨迹
          </Button>
        </div>
      ) : null}
    </header>
  )
}

interface HeaderTaskState {
  readonly sessionId: SessionId | undefined
  readonly task: SuperagentConversationHeaderTask | null
}

const INITIAL_HEADER_TASK_STATE: HeaderTaskState = {
  sessionId: undefined,
  task: null,
}

/**
 * 对话标题栏插槽容器：按当前 sessionId 轮询绑定任务卡并在瞬时失败后按 2000ms 周期恢复，
 * 同会话错误时保留已绑定的任务身份，切会话或卸载时立即隔离旧结果并清理定时器。
 */
export function SuperagentConversationHeaderSlot(
  props: SuperagentConversationHeaderSlotProps,
) {
  const session = props.useSession(s => s)
  const sessionId = props.sessionId
  const summary = props.useSessions(s =>
    sessionId !== undefined ? s.byId[sessionId] : undefined,
  )
  const rawTitle = summary?.displayTitle || summary?.title
  const title = typeof rawTitle === 'string' ? rawTitle : ''
  const blank = session ? Boolean(session.blank) : true

  // 将查询结果与所属 sessionId 绑定，切会话时同步推导阶段立即归零，避免闪出旧卡或旧轨迹。
  const [taskState, setTaskState] = useState<HeaderTaskState>(INITIAL_HEADER_TASK_STATE)
  const [trajectorySessionId, setTrajectorySessionId] = useState<SessionId | null>(null)

  const activeSessionRef = useRef<SessionId | undefined>(sessionId)
  activeSessionRef.current = sessionId
  const reqSeqRef = useRef(0)

  const cardOfSessionRef = useRef(props.cardOfSession)
  cardOfSessionRef.current = props.cardOfSession
  const trajectoryRef = useRef(props.trajectory)
  trajectoryRef.current = props.trajectory
  const cardAttachmentRef = useRef(props.cardAttachment)
  cardAttachmentRef.current = props.cardAttachment

  useEffect(() => {
    if (!sessionId) {
      reqSeqRef.current += 1
      setTaskState(INITIAL_HEADER_TASK_STATE)
      setTrajectorySessionId(null)
      return
    }

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let inFlight = false
    const targetSessionId = sessionId

    // 切到新会话时立即清理旧任务态，防止快切会话闪出旧卡；同会话则保留既有状态。
    setTaskState(prev =>
      prev.sessionId === targetSessionId ? prev : { sessionId: targetSessionId, task: null },
    )
    setTrajectorySessionId(prev => (prev === targetSessionId ? prev : null))

    const clearPollTimer = () => {
      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }
    }

    const scheduleNextPoll = () => {
      clearPollTimer()
      if (cancelled || activeSessionRef.current !== targetSessionId) return
      timer = setTimeout(() => {
        timer = null
        void pollOnce()
      }, POLL_INTERVAL_MS)
    }

    const pollOnce = async () => {
      if (cancelled || inFlight || activeSessionRef.current !== targetSessionId) return
      inFlight = true
      const seq = ++reqSeqRef.current
      try {
        const res = await cardOfSessionRef.current(targetSessionId)
        if (cancelled || activeSessionRef.current !== targetSessionId || reqSeqRef.current !== seq) {
          return
        }
        if (!res.ok) {
          // 瞬时错误时保留同会话最近一次成功绑定的任务，等待下一次轮询恢复。
          setTaskState(prev =>
            prev.sessionId === targetSessionId ? prev : { sessionId: targetSessionId, task: null },
          )
          return
        }
        if (!res.value || typeof res.value.id !== 'string' || !res.value.id.trim()) {
          setTaskState(prev =>
            prev.sessionId === targetSessionId && prev.task === null
              ? prev
              : { sessionId: targetSessionId, task: null },
          )
          return
        }
        const nextTask: SuperagentConversationHeaderTask = {
          id: res.value.id.trim(),
          title: typeof res.value.title === 'string' ? res.value.title : '',
        }
        setTaskState(prev => {
          if (
            prev.sessionId === targetSessionId &&
            prev.task &&
            prev.task.id === nextTask.id &&
            prev.task.title === nextTask.title
          ) {
            return prev
          }
          return { sessionId: targetSessionId, task: nextTask }
        })
      } catch {
        if (cancelled || activeSessionRef.current !== targetSessionId || reqSeqRef.current !== seq) {
          return
        }
        // 轮询异常时同样保留同会话已成功绑定的任务身份。
        setTaskState(prev =>
          prev.sessionId === targetSessionId ? prev : { sessionId: targetSessionId, task: null },
        )
      } finally {
        inFlight = false
        if (!cancelled && activeSessionRef.current === targetSessionId && reqSeqRef.current === seq) {
          scheduleNextPoll()
        }
      }
    }

    void pollOnce()

    return () => {
      cancelled = true
      reqSeqRef.current += 1
      clearPollTimer()
    }
  }, [sessionId])

  const currentTask = sessionId && taskState.sessionId === sessionId ? taskState.task : null
  const trajectoryOpen = Boolean(sessionId && currentTask && trajectorySessionId === sessionId)

  const handleOpenTrajectory = useCallback(() => {
    if (sessionId && currentTask) {
      setTrajectorySessionId(sessionId)
    }
  }, [sessionId, currentTask])

  const handleCloseTrajectory = useCallback(() => {
    setTrajectorySessionId(null)
  }, [])

  const cardOfSessionForModal = useCallback(async () => {
    if (!sessionId) return ''
    const res = await cardOfSessionRef.current(sessionId)
    if (!res.ok) throw new Error(res.error.message || '查询当前会话任务卡失败')
    return res.value?.id ?? ''
  }, [sessionId])

  const trajectoryPort = useCallback(
    (card: string) => trajectoryRef.current(card),
    [],
  )

  const cardAttachmentPort = useCallback(
    (card: string, name: string) => cardAttachmentRef.current(card, name),
    [],
  )

  const showHeader = Boolean(currentTask || !blank)

  if (!showHeader) {
    return null
  }

  const taskNumber = currentTask ? displayTaskNumber(currentTask.id) : ''
  const modalTitle = currentTask
    ? (currentTask.title.trim()
        ? `${taskNumber} 号：${currentTask.title} · 任务轨迹`
        : `${taskNumber} 号 · 任务轨迹`)
    : '任务轨迹'

  return (
    <Fragment>
      <div className={styles.slotRow}>
        <SuperagentConversationHeader
          title={title}
          blank={blank}
          task={currentTask}
          onTrajectory={handleOpenTrajectory}
        />
      </div>
      {sessionId ? (
        <Modal
          open={trajectoryOpen}
          onClose={handleCloseTrajectory}
          title={modalTitle}
          closeLabel="关闭任务轨迹"
          className={styles.trajectoryModal ?? ''}
          contentClassName={styles.trajectoryModalContent ?? ''}
        >
          {trajectoryOpen ? (
            <TaskTrajectory
              sessionId={sessionId}
              cardOfSession={cardOfSessionForModal}
              trajectory={trajectoryPort}
              cardAttachment={cardAttachmentPort}
            />
          ) : null}
        </Modal>
      ) : null}
    </Fragment>
  )
}
