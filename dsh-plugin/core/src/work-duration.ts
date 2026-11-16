/**
 * 施工时长与严格日期纯计算模块（Host 与 Client 共用，不依赖 Node 或 React）。
 * - 按真实流转事件（开工 / 开跑 → 结束 / 转人工 / 停下 / 放弃 / 合并）累加墙钟干活时长；
 * - 绝不叠加并行角色秒数，绝不把等验收过夜等等待空窗算作干活时间；
 * - 统一导出干活用时格式化函数，空值照实显示未知或指定占位符，不编造 0。
 */
import { FINISHED_CARD_STATES, FINISHED_PHASE_CODES } from './types.ts'

export interface ParsedFlowEvent {
  readonly kind: string
  readonly src: string
  readonly dst: string
  readonly step: string
  readonly recState: string
  readonly timeStr: string
  readonly timeMs: number | null
}

const STRICT_DATE_RE = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T\s](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(?:\s*(Z|[+-]\d{2}:?\d{2}))?)?$/i

/**
 * 严格解析日期时间字符串并校验真实公历合法性（拒绝 99 月 99 日等损坏时间）。
 */
export function parseStrictDateParts(raw: unknown): { readonly ms: number } | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  const match = STRICT_DATE_RE.exec(trimmed)
  if (!match) return null

  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return null
  if (month < 1 || month > 12 || day < 1 || day > 31) return null

  const calendarCheck = new Date(Date.UTC(year, month - 1, day))
  if (
    calendarCheck.getUTCFullYear() !== year
    || calendarCheck.getUTCMonth() !== month - 1
    || calendarCheck.getUTCDate() !== day
  ) {
    return null
  }

  let hour = 0
  let minute = 0
  let second = 0
  if (match[4] !== undefined && match[5] !== undefined) {
    hour = Number(match[4])
    minute = Number(match[5])
    second = match[6] !== undefined ? Number(match[6]) : 0
    if (
      !Number.isInteger(hour) || hour < 0 || hour > 23
      || !Number.isInteger(minute) || minute < 0 || minute > 59
      || !Number.isInteger(second) || second < 0 || second > 59
    ) {
      return null
    }
  }

  let tzSuffix = ''
  if (match[8]) {
    const rawTz = match[8].toUpperCase()
    if (rawTz === 'Z') {
      tzSuffix = 'Z'
    } else {
      const sign = rawTz[0]
      const digits = rawTz.slice(1).replace(':', '')
      const tzHour = Number(digits.slice(0, 2))
      const tzMin = Number(digits.slice(2, 4))
      if (!Number.isInteger(tzHour) || tzHour > 23 || !Number.isInteger(tzMin) || tzMin > 59) {
        return null
      }
      tzSuffix = `${sign}${String(tzHour).padStart(2, '0')}:${String(tzMin).padStart(2, '0')}`
    }
  }

  const frac = match[7] ? `.${match[7].slice(0, 3)}` : ''
  const iso = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}${frac}${tzSuffix}`
  const ms = Date.parse(iso)
  return Number.isFinite(ms) ? { ms } : null
}

/**
 * 解析流转记录时间戳（毫秒）；损坏或缺失返回 null。
 */
export function parseFlowTimestampMs(raw: unknown): number | null {
  return parseStrictDateParts(raw)?.ms ?? null
}

/**
 * 解析 `流转.jsonl` 文本为结构化事件列表（跳过空行与损坏 JSON 行）。
 */
export function parseFlowEvents(flowText: string | null | undefined): ParsedFlowEvent[] {
  if (!flowText) return []
  const events: ParsedFlowEvent[] = []
  for (const rawLine of flowText.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue
    let rec: Record<string, unknown>
    try {
      const parsed = JSON.parse(line) as unknown
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) continue
      rec = parsed as Record<string, unknown>
    } catch {
      continue
    }

    const kind = typeof rec.kind === 'string' ? rec.kind : ''
    const src = typeof rec.src === 'string' ? rec.src : ''
    const dst = typeof rec.dst === 'string' ? rec.dst : ''
    const step = typeof rec.step === 'string' ? rec.step : ''
    const recState = typeof rec.state === 'string' ? rec.state : ''
    const timeStr = typeof rec.time === 'string' ? rec.time.trim() : ''

    events.push({
      kind,
      src,
      dst,
      step,
      recState,
      timeStr,
      timeMs: parseFlowTimestampMs(timeStr),
    })
  }
  return events
}

/**
 * 判断任务卡当前是否仍在主动干活（已放弃、已结束或等你处理时均不把当前墙钟继续算进施工时间）。
 */
export function isCardRunningNow(
  state: string,
  phaseCode: string,
  phaseTone?: unknown,
  needsYou?: boolean,
): boolean {
  const isDiscarded = state === '已放弃' || phaseCode === 'dropped'
  const isFinished = !needsYou && (phaseTone === 'done' || FINISHED_PHASE_CODES.has(phaseCode) || FINISHED_CARD_STATES.has(state))
  return !isDiscarded && !isFinished && !needsYou
}

/**
 * 从结构化流转事件累加真实施工（干活）秒数；没有有效区间时返回 null。
 */
export function computeElapsedFromFlow(
  events: readonly ParsedFlowEvent[],
  isRunningNow: boolean,
  nowMs: number = Date.now(),
): number | null {
  let activeStartMs: number | null = null
  let totalMs = 0
  let intervalCount = 0

  for (const ev of events) {
    if (ev.timeMs === null) continue
    if (ev.kind === '开工') {
      // 新一轮明确开工；若前一轮意外未写结束即重开，仅从明确开工重新计时，不混入中断空窗。
      activeStartMs = ev.timeMs
      continue
    }
    if (ev.kind === '开跑') {
      if (activeStartMs === null) activeStartMs = ev.timeMs
      continue
    }
    if (
      activeStartMs !== null
      && (ev.kind === '结束' || ev.kind === '转人工' || ev.kind === '停下' || ev.kind === '放弃' || ev.kind === '合并')
    ) {
      if (ev.timeMs >= activeStartMs) {
        totalMs += ev.timeMs - activeStartMs
        intervalCount += 1
      }
      activeStartMs = null
    }
  }

  // 当前任务仍在施工中且最后一轮已明确开工但尚未结束时，累加上截止当前时刻的时长
  if (isRunningNow && activeStartMs !== null && nowMs >= activeStartMs) {
    totalMs += nowMs - activeStartMs
    intervalCount += 1
  }

  return intervalCount > 0 ? Math.round(totalMs / 1000) : null
}

/**
 * 直接从 `流转.jsonl` 原文计算真实干活秒数；没有有效记录时返回 null。
 */
export function computeWorkElapsedFromFlowText(
  flowText: string | null | undefined,
  isRunningNow: boolean,
  nowMs: number = Date.now(),
): number | null {
  if (!flowText) return null
  return computeElapsedFromFlow(parseFlowEvents(flowText), isRunningNow, nowMs)
}

/**
 * 统一格式化干活用时（总览与右栏共用）：211 秒显示“3 分”；空值返回指定文案（默认“未知”）。
 */
export function formatWorkDuration(seconds: number | null | undefined, emptyText: string = '未知'): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return emptyText
  const total = Math.round(seconds)
  if (total < 60) return `${total} 秒`
  const totalMinutes = Math.floor(total / 60)
  if (totalMinutes < 60) return `${totalMinutes} 分`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return minutes > 0 ? `${hours} 时 ${minutes} 分` : `${hours} 时`
}
