/** 预览后台任务登记与状态轮询控制（接入 DSH `ctx.jobs`）。 */

import type { JobHooks, JobId, JobOutcome, JobRegistry, JobSpec, JobView } from '@deepseek-ai/dsh-jobs'
import type {} from '@deepseek-ai/dsh-jobs/view'
import type { SessionId } from '@deepseek-ai/dsh-session'

declare module '@deepseek-ai/dsh-jobs/view' {
  interface JobKindMap {
    preview: 'preview'
  }
}

const DEFAULT_POLL_MS = 2000
const MIN_TAIL_OVERLAP_CHARS = 32

/** 停止预览的真实后台调用结果。 */
export interface PreviewStopResponse {
  readonly ok: boolean
  readonly reason?: string
}

/** 查询预览运行状态的真实后台结果。 */
export interface PreviewPollResponse {
  readonly running: boolean
  readonly url?: string
}

/** 允许真实 `JobRegistry` 或单测轻量 mock 的最小登记接口。 */
export interface PreviewJobsPort {
  start(spec: JobSpec): JobId | { readonly id: string }
  kill?(id: JobId, caller?: SessionId, reason?: string): 'requested' | 'already-finished'
  wait?(id: JobId, timeoutMs: number, caller?: SessionId, signal?: AbortSignal): Promise<JobView>
}

/** 登记单个预览后台任务所需参数。 */
export interface RegisterPreviewJobOptions {
  readonly jobs: PreviewJobsPort
  readonly card: string
  readonly owner: SessionId | string
  readonly url: string
  readonly stop: () => Promise<PreviewStopResponse>
  readonly status: () => Promise<PreviewPollResponse>
  readonly readOutput?: () => Promise<string>
  readonly pollMs?: number
  readonly label?: string
  readonly onSettled?: (outcome: JobOutcome) => void
}

function normalizePollMs(pollMs: number | undefined): number {
  if (typeof pollMs !== 'number' || !Number.isFinite(pollMs) || pollMs <= 0) {
    return DEFAULT_POLL_MS
  }
  return Math.max(20, Math.floor(pollMs))
}

function normalizeCancelReason(reason?: string): string {
  const trimmed = reason?.trim() ?? ''
  if (!trimmed) return '预览已停止'
  if (/^cancelled\s+by\s+(?:the\s+)?user$/i.test(trimmed)) {
    return '用户已停止预览'
  }
  return trimmed
}

/**
 * 用于大文件滑动尾窗：在旧输出末尾与新输出开头之间匹配至少 32 字符的重叠，
 * 避免固定尾窗向后滑动时重复输出已展示过的日志。
 */
function findTailWindowOverlap(prev: string, next: string): number {
  if (prev.length < MIN_TAIL_OVERLAP_CHARS || next.length < MIN_TAIL_OVERLAP_CHARS) {
    return 0
  }
  const anchor = next.slice(0, MIN_TAIL_OVERLAP_CHARS)
  let idx = prev.lastIndexOf(anchor)
  while (idx !== -1) {
    const overlap = prev.length - idx
    if (overlap <= next.length && prev.slice(idx) === next.slice(0, overlap)) {
      return overlap
    }
    if (idx === 0) break
    idx = prev.lastIndexOf(anchor, idx - 1)
  }
  return 0
}

/**
 * 将已启动成功的本地网页预览同步登记到 DSH `ctx.jobs`。
 * - 必须传入任务绑定会话 `owner`，绝不做 unowned 兜底；
 * - 同步登记并写入真实启动地址与运行状态；
 * - 支持可选 `readOutput` 增量读取并追加预览进程真实输出，支持截断/轮换说明；
 * - `cancel()` 幂等调用真实 `stop()`，停止失败时如实结算 `failed`；
 * - 后台轮询真实 `status()`，进程自然退出时结算 `completed`。
 */
export function registerPreviewJob(
  opts: RegisterPreviewJobOptions,
): ReturnType<JobRegistry['start']> | { readonly id: string } {
  const owner = typeof opts.owner === 'string' ? opts.owner.trim() : ''
  if (!owner) {
    throw new Error('预览任务缺少所属任务会话，不能登记后台任务')
  }
  const label = opts.label?.trim() || `预览：${opts.card}`
  const pollIntervalMs = normalizePollMs(opts.pollMs)

  return opts.jobs.start({
    kind: 'preview',
    owner: owner as SessionId,
    label,
    run(job): JobHooks {
      let currentUrl = opts.url
      let lastOutput = ''
      let outputPending: Promise<void> | null = null
      let settled = false
      let cancelled = false
      let timer: ReturnType<typeof setTimeout> | null = null

      let resolveDone!: (outcome: JobOutcome) => void
      const done = new Promise<JobOutcome>((resolve) => {
        resolveDone = resolve
      })

      const clearPollTimer = (): void => {
        if (timer !== null) {
          clearTimeout(timer)
          timer = null
        }
      }

      const applyOutputDelta = (next: string): void => {
        if (settled || cancelled) return
        if (typeof next !== 'string' || next === lastOutput) return

        if (next.startsWith(lastOutput)) {
          const delta = next.slice(lastOutput.length)
          lastOutput = next
          if (delta) job.append(delta)
          return
        }

        const overlap = findTailWindowOverlap(lastOutput, next)
        if (overlap > 0) {
          const delta = next.slice(overlap)
          lastOutput = next
          if (delta) job.append(delta)
          return
        }

        if (!next) {
          lastOutput = ''
          job.append('（预览输出日志已清空或轮换）\n')
          return
        }

        lastOutput = next
        job.append(`（预览输出日志已截断或轮换，以下为最新输出）\n${next}`)
      }

      const flushOutput = (): Promise<void> => {
        if (!opts.readOutput || settled || cancelled) return Promise.resolve()
        if (outputPending) return outputPending

        const runRead = async (): Promise<void> => {
          if (!opts.readOutput || settled || cancelled) return
          try {
            const content = await opts.readOutput()
            applyOutputDelta(content)
          } catch {
            // 读取日志异常不等价于预览进程退出，留待下次轮询重试。
          }
        }

        const p = runRead().finally(() => {
          if (outputPending === p) outputPending = null
        })
        outputPending = p
        return p
      }

      const finish = (outcome: JobOutcome): void => {
        if (settled) return
        settled = true
        clearPollTimer()
        try {
          opts.onSettled?.(outcome)
        } finally {
          resolveDone(outcome)
        }
      }

      const schedulePoll = (): void => {
        if (settled || cancelled) return
        clearPollTimer()
        timer = setTimeout(() => {
          timer = null
          if (settled || cancelled) return
          void opts.status().then(
            async (res) => {
              if (settled || cancelled) return
              await flushOutput()
              if (settled || cancelled) return
              if (res.url && res.url !== currentUrl) {
                currentUrl = res.url
                job.append(`预览地址更新：${currentUrl}\n`)
              }
              if (!res.running) {
                job.append('预览进程已自然退出\n')
                finish({ status: 'completed', detail: '预览已自然退出' })
                return
              }
              schedulePoll()
            },
            async () => {
              if (settled || cancelled) return
              await flushOutput()
              if (!settled && !cancelled) {
                schedulePoll()
              }
            },
          )
        }, pollIntervalMs)
        if (typeof timer === 'object' && timer !== null && 'unref' in timer && typeof timer.unref === 'function') {
          timer.unref()
        }
      }

      // 同步写入真实启动地址与初始运行状态，随后立即拉取一次已有日志
      job.append(`预览地址：${currentUrl}\n状态：运行中\n`)
      void flushOutput()
      schedulePoll()

      const cancel = (reason?: string): void => {
        if (settled || cancelled) return
        cancelled = true
        clearPollTimer()

        void Promise.resolve()
          .then(() => opts.stop())
          .then(
            (res) => {
              if (res && res.ok) {
                const detail = normalizeCancelReason(reason)
                job.append('预览已停止\n')
                finish({ status: 'killed', detail })
                return
              }
              const detail = res?.reason?.trim() || '停止预览失败'
              job.append(`停止预览失败：${detail}\n`)
              finish({ status: 'failed', detail })
            },
            (err: unknown) => {
              const detail = err instanceof Error && err.message ? err.message : '停止预览异常'
              job.append(`停止预览失败：${detail}\n`)
              finish({ status: 'failed', detail })
            },
          )
      }

      return { cancel, done }
    },
  })
}
