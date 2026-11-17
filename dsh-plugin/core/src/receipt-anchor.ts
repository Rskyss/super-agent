/**
 * 03b 聊天回执轮次锚定纯计算模块（Host 与 Client 共用，不依赖 Node、React 或 DOM）。
 * - 有精确 savedStartSeq 时，仅匹配已载入窗口中相同的 startSeq；未载入则返回 null，绝不回退到最新轮；
 * - 无精确锚时，按 flow.py 整秒精度构造 [secStart, secStart + 1000) 不确定区间，
 *   仅当整段区间唯一落在某已结束轮 endTime 与下一轮 startTime（末轮为未来）之间时恢复该轮。
 */
import { parseStrictDateParts } from './work-duration.ts'

/** 单轮对话在时间轴上的边界窗口。 */
export interface ReceiptTurnWindow {
  readonly startSeq: number
  readonly startTime: number
  readonly endTime: number | null
}

/** 兼容 DSH ConversationTimelineSnapshot 的最小只读结构。 */
export interface ReceiptTimelineTurnLike {
  readonly start?: { readonly seq: number; readonly time: number } | undefined
  readonly end?: { readonly seq: number; readonly time: number } | undefined
}

export interface ReceiptTimelineLike {
  readonly turnOrder: readonly number[]
  readonly turns: ReadonlyMap<number, ReceiptTimelineTurnLike>
}

/**
 * 将 DSH 公共 ChatSnapshot.timeline 转换为供 resolveReceiptTurn 使用的有序轮次窗口。
 * 若某轮缺少 start 事件，保留 NaN 占位窗口使相邻前轮感知后继边界不确定，绝不跳过截断轮误连下一轮。
 */
export function buildReceiptTurnWindows(timeline: ReceiptTimelineLike): ReceiptTurnWindow[] {
  const windows: ReceiptTurnWindow[] = []
  for (const turnNumber of timeline.turnOrder) {
    const location = timeline.turns.get(turnNumber)
    if (!location) continue
    const start = location.start
    const end = location.end
    const endTime = end !== undefined && Number.isFinite(end.time) ? end.time : null
    if (start === undefined || !Number.isInteger(start.seq) || start.seq < 0 || !Number.isFinite(start.time)) {
      windows.push({
        startSeq: -1,
        startTime: Number.NaN,
        endTime,
      })
      continue
    }
    windows.push({
      startSeq: start.seq,
      startTime: start.time,
      endTime,
    })
  }
  return windows
}

/**
 * 解析任务卡回执应当归属的轮次 startSeq；无法唯一确定或对应轮尚未载入时返回 null。
 */
export function resolveReceiptTurn(
  createdAt: string | null | undefined,
  savedStartSeq: number | undefined,
  windows: readonly ReceiptTurnWindow[],
): number | null {
  if (savedStartSeq !== undefined) {
    if (!Number.isInteger(savedStartSeq) || savedStartSeq < 0) return null
    for (const win of windows) {
      if (win.startSeq === savedStartSeq) return savedStartSeq
    }
    return null
  }

  if (typeof createdAt !== 'string' || !createdAt.trim()) return null
  const parsed = parseStrictDateParts(createdAt)
  if (parsed === null) return null

  // 需求卡首条时间精度只到整秒（即使带毫秒也保守按整秒不确定区间处理）。
  const secStart = Math.floor(parsed.ms / 1000) * 1000
  const secEnd = secStart + 1000

  let matchedSeq: number | null = null
  for (let i = 0; i < windows.length; i += 1) {
    const win = windows[i]!
    if (!Number.isInteger(win.startSeq) || win.startSeq < 0) continue
    if (!Number.isFinite(win.startTime)) continue
    if (win.endTime === null || !Number.isFinite(win.endTime) || win.endTime < win.startTime) continue

    const next = i + 1 < windows.length ? windows[i + 1] : undefined
    if (next !== undefined && (!Number.isFinite(next.startTime) || next.startTime < win.endTime)) {
      continue
    }

    // 确认整秒区间必须严格在已结束轮之后（排除同秒边界），且整个位于下一轮开始之前。
    if (win.endTime < secStart && (next === undefined || secEnd <= next.startTime)) {
      if (matchedSeq !== null) return null
      matchedSeq = win.startSeq
    }
  }

  return matchedSeq
}
