/**
 * 03b 确认建卡后待锚定回执的有界恢复引擎（无模块级单例，绝不无限轮询）。
 * - 处理确认建卡成功但 sessionCards / status 短暂抛错、ok:false、卡尚未落盘或暂缺 createdAt 的情况；
 * - 严格保留确认瞬间同步捕获的 startSeq 与项目归属锁，项目切换后绝不把旧轮锚错绑到另一项目的同号卡；
 * - 宿主 apply 作用域通过工厂函数持有协调器并在 ui.effect 中统一 dispose；独立渲染模式则运行单次有界恢复任务。
 */
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { ConfirmedTurnAnchor, PendingTurnAnchor } from '../types.ts'
import type { TaskCardSummaryRow } from './session-cards-feed.ts'

export type ReadSessionCardsFn = (
  sessionId: string,
) => Promise<RemoteResult<readonly Pick<TaskCardSummaryRow, 'id' | 'createdAt'>[]>>

export type ReadProjectStatusFn = () => Promise<RemoteResult<{ readonly project: string }>>

export interface AnchorRecoveryRequest {
  readonly sessionId: string
  readonly cardId: string
  readonly startSeq: number
  readonly expectedProject?: string | undefined
  readonly readSessionCards: ReadSessionCardsFn
  readonly readProjectStatus: ReadProjectStatusFn
  readonly remember: (project: string, card: string, anchor: ConfirmedTurnAnchor) => void
  readonly stagePending?: ((entry: PendingTurnAnchor) => void) | undefined
}

export interface ReceiptAnchorRecoveryCoordinator {
  schedule(request: AnchorRecoveryRequest): void
  dispose(): void
}

const RECOVERY_RETRY_DELAYS_MS = [500, 1000, 2000, 4000] as const

interface RecoveryTaskHandle {
  readonly sessionId: string
  readonly cardId: string
  lockedProject: string | undefined
  readonly cancel: () => void
  readonly isActive: () => boolean
}

async function safeRemoteCall<T>(fn: () => Promise<RemoteResult<T>>): Promise<RemoteResult<T> | null> {
  try {
    return await fn()
  } catch {
    return null
  }
}

function startRecoveryTask(
  request: AnchorRecoveryRequest,
  isDisposed: () => boolean,
  onFinish: () => void,
): RecoveryTaskHandle {
  const sessionId = request.sessionId.trim()
  const cardId = request.cardId.trim()
  let lockedProject = request.expectedProject?.trim() || undefined
  let timer: ReturnType<typeof setTimeout> | null = null
  let retryIndex = 0
  let inFlight = false
  let settled = false
  let cancelled = false

  if (lockedProject !== undefined) {
    request.stagePending?.({
      project: lockedProject,
      sessionId,
      card: cardId,
      startSeq: request.startSeq,
    })
  }

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  const finish = () => {
    if (settled) return
    settled = true
    clearTimer()
    onFinish()
  }

  const cancel = () => {
    if (cancelled) return
    cancelled = true
    clearTimer()
    settled = true
  }

  const scheduleNext = () => {
    if (settled || cancelled || isDisposed()) {
      finish()
      return
    }
    const delay = RECOVERY_RETRY_DELAYS_MS[retryIndex]
    if (delay === undefined) {
      finish()
      return
    }
    retryIndex += 1
    clearTimer()
    timer = setTimeout(() => {
      timer = null
      void attemptOnce()
    }, delay)
  }

  const attemptOnce = async (): Promise<void> => {
    if (settled || cancelled || isDisposed() || inFlight) return
    if (!sessionId || !cardId || !Number.isInteger(request.startSeq) || request.startSeq < 0) {
      finish()
      return
    }

    inFlight = true
    try {
      const [cardsRes, statusRes] = await Promise.all([
        safeRemoteCall(() => request.readSessionCards(sessionId)),
        safeRemoteCall(() => request.readProjectStatus()),
      ])

      if (settled || cancelled || isDisposed()) {
        finish()
        return
      }

      const currentProject = statusRes && statusRes.ok && typeof statusRes.value?.project === 'string'
        ? statusRes.value.project.trim()
        : ''

      if (currentProject !== '') {
        if (lockedProject === undefined) {
          lockedProject = currentProject
          request.stagePending?.({
            project: lockedProject,
            sessionId,
            card: cardId,
            startSeq: request.startSeq,
          })
        } else if (lockedProject !== currentProject) {
          // 项目已切换，绝不把原项目确认轮锚绑定到新项目的同号卡上。
          finish()
          return
        }
      }

      if (currentProject === '' || !cardsRes || !cardsRes.ok || !Array.isArray(cardsRes.value)) {
        scheduleNext()
        return
      }

      const matched = cardsRes.value.find(c => c.id === cardId)
      const createdAt = typeof matched?.createdAt === 'string' ? matched.createdAt.trim() : ''
      if (!createdAt) {
        scheduleNext()
        return
      }

      finish()
      request.remember(currentProject, cardId, { startSeq: request.startSeq, createdAt })
    } finally {
      inFlight = false
    }
  }

  void attemptOnce()

  return {
    sessionId,
    cardId,
    get lockedProject() {
      return lockedProject
    },
    cancel,
    isActive: () => !settled && !cancelled && !isDisposed(),
  }
}

/**
 * 在无宿主作用域协调器时（如独立组件测试）运行单次有界待锚定恢复任务。
 * 任务仅存在于当次确认闭包中，成功、项目切换或达到有限重试上限后立即结束，不保留模块级单例。
 */
export function startBoundedAnchorRecovery(request: AnchorRecoveryRequest): () => void {
  const handle = startRecoveryTask(request, () => false, () => {})
  return handle.cancel
}

/**
 * 创建宿主 apply 作用域内共享的待锚定恢复协调器（随 ui.effect 统一销毁）。
 */
export function createReceiptAnchorRecoveryCoordinator(): ReceiptAnchorRecoveryCoordinator {
  const tasks = new Map<string, RecoveryTaskHandle>()
  let disposed = false

  return {
    schedule: (request: AnchorRecoveryRequest) => {
      if (disposed) return
      const sessionId = request.sessionId.trim()
      const cardId = request.cardId.trim()
      if (!sessionId || !cardId) return
      const key = `${sessionId}::${cardId}`
      const nextProject = request.expectedProject?.trim() || undefined
      const existing = tasks.get(key)

      if (existing !== undefined && existing.isActive()) {
        // 同一会话同卡仍在恢复中且未发生跨项目切换时保持幂等，保留最初捕获的 startSeq。
        if (nextProject === undefined || existing.lockedProject === undefined || existing.lockedProject === nextProject) {
          return
        }
        existing.cancel()
        tasks.delete(key)
      }

      const handle = startRecoveryTask(
        request,
        () => disposed,
        () => {
          if (tasks.get(key) === handle) {
            tasks.delete(key)
          }
        },
      )
      if (handle.isActive()) {
        tasks.set(key, handle)
      }
    },
    dispose: () => {
      if (disposed) return
      disposed = true
      for (const task of tasks.values()) {
        task.cancel()
      }
      tasks.clear()
    },
  }
}
