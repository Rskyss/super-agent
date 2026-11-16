/**
 * 超级助手全局模型同步到已有会话（第 11 项第 3 轮）：
 * - 当钥匙串模式下更换或完成配置后，让已有普通/任务对话通过 DSH 公开 `selectModel` 接口跟随新模型，
 *   完整保留对话历史与草稿，绝不另开新会话或篡改底层文件。
 * - 任何失败必须抛出真实安全错误提示，绝不静默假装成功；并在输入框上方展示可重试提示条。
 */
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import { redactOnboardingSecret } from '../onboarding.ts'

/** 目标助手模型选型（必须使用真实 provider 路由 ID，不能用引导页预置名）。 */
export interface AssistantModelSelection {
  readonly provider: string
  readonly model: string
  readonly reasoningEffort?: string | undefined
}

/** 同步已有会话所需的最小公开接口。 */
export interface AssistantModelSyncPorts {
  readonly selectModel: (request: {
    readonly sessionId: SessionId
    readonly provider: string
    readonly model: string
    readonly reasoningEffort?: string | undefined
  }) => Promise<RemoteResult<unknown>>
}

/**
 * 将最新的超级助手全局模型同步到指定的已有会话列表。
 * 任一会话同步失败时抛出包含真实安全原因的错误，绝不假装已同步成功。
 */
export async function syncAssistantModelToSessions(
  ports: AssistantModelSyncPorts,
  selection: AssistantModelSelection,
  sessionIds: readonly SessionId[],
): Promise<void> {
  const provider = selection.provider.trim()
  const model = selection.model.trim()
  if (!provider || !model) {
    throw new Error('缺少有效的模型路由或模型标识，无法同步已有对话')
  }

  const uniqueIds = Array.from(new Set(sessionIds.filter((id): id is SessionId => typeof id === 'string' && id.trim().length > 0)))
  if (uniqueIds.length === 0) return

  const failures: string[] = []
  for (const sessionId of uniqueIds) {
    const request = {
      sessionId,
      provider,
      model,
      ...(selection.reasoningEffort === undefined ? {} : { reasoningEffort: selection.reasoningEffort }),
    }
    try {
      const result = await ports.selectModel(request)
      if (!result.ok) {
        const msg = redactOnboardingSecret(result.error.message || '模型选择接口返回失败')
        failures.push(msg)
      }
    } catch (error: unknown) {
      const msg = redactOnboardingSecret(error instanceof Error ? error.message : String(error))
      failures.push(msg || '模型同步请求异常')
    }
  }

  if (failures.length > 0) {
    throw new Error(failures[0]!)
  }
}

/** 按会话维度的轻量同步失败状态存储（供输入框上方重试提示条订阅）。 */
export class ModelSyncFailureStore {
  private errors = new Map<string, string>()
  private readonly listeners = new Set<() => void>()

  getError(sessionId: string): string {
    return this.errors.get(sessionId) ?? ''
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private emit(): void {
    for (const listener of this.listeners) {
      listener()
    }
  }

  setError(sessionId: string, message: string): void {
    const next = message.trim()
    if (!next) {
      this.clearError(sessionId)
      return
    }
    if (this.errors.get(sessionId) === next) return
    const cloned = new Map(this.errors)
    cloned.set(sessionId, next)
    this.errors = cloned
    this.emit()
  }

  clearError(sessionId: string): void {
    if (!this.errors.has(sessionId)) return
    const cloned = new Map(this.errors)
    cloned.delete(sessionId)
    this.errors = cloned
    this.emit()
  }

  clearAll(): void {
    if (this.errors.size === 0) return
    this.errors = new Map()
    this.emit()
  }
}
