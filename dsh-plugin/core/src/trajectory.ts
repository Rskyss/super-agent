/** 任务卡流转轨迹读取与安全路径校验（Host 侧）。 */

import { existsSync, lstatSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { isAbsolute, join, resolve, sep } from 'node:path'
import { extractTrajectoryAttachments } from './card-attachments.ts'
import type { CardTrajectory, TrajectoryDistributionItem, TrajectoryRow } from './types.ts'

const CARD_PATTERN = /^CARD-\d{3,}$/
const MAX_TRAJECTORY_FILE_BYTES = 512 * 1024

/** 检查任务卡编号，拒绝路径穿越与非法字符。 */
export function assertSafeCardId(card: string): string {
  const trimmed = typeof card === 'string' ? card.trim() : ''
  if (!CARD_PATTERN.test(trimmed)) {
    throw new Error(`任务卡编号不对：${String(card ?? '')}`)
  }
  return trimmed
}

/** 清理不该出现在界面上的模型厂商旧称。 */
export function sanitizeTrajectoryText(raw: string): string {
  return raw
    .replace(/\bClaude\s+Code\b/gi, '调度员')
    .replace(/\bClaude\b/gi, '调度员')
    .replace(/\bAnthropic\b/gi, '后台')
}

function formatTrajectoryTime(raw: unknown): string {
  if (typeof raw !== 'string') return ''
  const trimmed = raw.trim()
  if (!trimmed) return ''
  if (/^\d{2}:\d{2}(:\d{2})?$/.test(trimmed)) return trimmed
  const parsed = new Date(trimmed)
  if (Number.isNaN(parsed.getTime())) return sanitizeTrajectoryText(trimmed)
  const hh = String(parsed.getHours()).padStart(2, '0')
  const mm = String(parsed.getMinutes()).padStart(2, '0')
  const ss = String(parsed.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

function isInsideDir(baseRealDir: string, targetRealPath: string): boolean {
  return targetRealPath === baseRealDir || targetRealPath.startsWith(baseRealDir + sep)
}

/**
 * 在任务卡真实目录内安全读取相对路径文本。
 * 拒绝空路径、绝对路径、`..` 穿越以及任何跳出卡片目录的软链接。
 */
/**
 * @param raw - true 时逐字返回（“看改动”的代码原文不能换叫法，复查第 1 轮中等 15）。
 */
export function readSafeCardFile(realCardDir: string, relativePath: unknown, raw = false): string {
  if (typeof relativePath !== 'string') return ''
  const trimmed = relativePath.trim()
  if (!trimmed || trimmed.includes('\0')) return ''
  if (isAbsolute(trimmed) || trimmed.startsWith('/') || trimmed.startsWith('\\') || /^[a-zA-Z]:/.test(trimmed)) {
    return ''
  }
  const segments = trimmed.split(/[/\\]+/).filter(Boolean)
  if (segments.length === 0) return ''
  for (const seg of segments) {
    if (seg === '.' || seg === '..') return ''
  }

  let current = realCardDir
  for (const seg of segments) {
    current = join(current, seg)
    if (!existsSync(current)) return ''
    try {
      const linkStat = lstatSync(current)
      if (linkStat.isSymbolicLink()) {
        const resolvedSeg = realpathSync(current)
        if (!isInsideDir(realCardDir, resolvedSeg)) return ''
      }
    } catch {
      return ''
    }
  }

  try {
    const resolvedFile = realpathSync(current)
    if (!isInsideDir(realCardDir, resolvedFile)) return ''
    const fileStat = statSync(resolvedFile)
    if (!fileStat.isFile() || fileStat.size > MAX_TRAJECTORY_FILE_BYTES) return ''
    const text = readFileSync(resolvedFile, 'utf-8')
    return raw ? text : sanitizeTrajectoryText(text)
  } catch {
    return ''
  }
}

function readRowRawText(realCardDir: string, record: Record<string, unknown>): string {
  const parts: string[] = []
  for (const field of [record.text, record.live]) {
    const content = readSafeCardFile(realCardDir, field)
    if (content && !parts.includes(content)) parts.push(content)
  }
  return parts.join('\n---\n')
}

function parseOptionalSeconds(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null
  return value
}

function parseExplicitEventTimeMs(raw: unknown): number | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (!trimmed || !/\d{4}-\d{2}-\d{2}/.test(trimmed)) return null
  const ms = Date.parse(trimmed)
  return Number.isFinite(ms) ? ms : null
}

function diffValidSeconds(startMs: number | null, endMs: number | null): number | null {
  if (startMs === null || endMs === null || endMs < startMs) return null
  return Math.round((endMs - startMs) / 1000)
}

function mapExplicitStepToStage(rawStep: unknown): string | null {
  if (typeof rawStep !== 'string') return null
  const step = rawStep.trim()
  if (!step) return null
  if (step === '写测试') return '写测试'
  if (step === '开发' || step === '写代码' || step === '返工') return '写代码'
  if (step === '复查') return '复查'
  if (step === '写方案') return '写方案'
  if (step === '跑测试' || step === '全量') return '跑测试'
  return sanitizeTrajectoryText(step)
}

function addDistributionSeconds(
  buckets: Map<string, number>,
  label: string,
  seconds: number | null,
): void {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return
  if (seconds === 0 && !buckets.has(label)) return
  buckets.set(label, (buckets.get(label) ?? 0) + seconds)
}

function parseOptionalUsage(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null
  return value
}

function pickActor(record: Record<string, unknown>): string {
  for (const key of ['src', 'actor', 'dst'] as const) {
    const raw = record[key]
    if (typeof raw === 'string' && raw.trim()) {
      return sanitizeTrajectoryText(raw.trim())
    }
  }
  return '系统'
}

function pickSummary(record: Record<string, unknown>): string {
  for (const key of ['summary', 'kind', 'tool'] as const) {
    const raw = record[key]
    if (typeof raw === 'string' && raw.trim()) {
      return sanitizeTrajectoryText(raw.trim())
    }
  }
  return '流转记录'
}

/**
 * 安全定位任务卡的真实目录路径，拒绝非法卡号、目录逃逸与指向卡库外部的软链接。
 */
export function resolveSafeCardDir(project: string, card: string): string | null {
  const safeCard = assertSafeCardId(card)
  const cleanProject = typeof project === 'string' ? project.trim() : ''
  if (!cleanProject) return null

  const cardsRoot = resolve(cleanProject, '.collab', 'cards')
  if (!existsSync(cardsRoot)) return null

  let realCardsRoot: string
  try {
    realCardsRoot = realpathSync(cardsRoot)
  } catch {
    return null
  }

  const cardDir = join(realCardsRoot, safeCard)
  if (!existsSync(cardDir)) return null

  try {
    const linkStat = lstatSync(cardDir)
    const realCardDir = realpathSync(cardDir)
    if (linkStat.isSymbolicLink() && !isInsideDir(realCardsRoot, realCardDir)) {
      return null
    }
    if (!isInsideDir(realCardsRoot, realCardDir) || !statSync(realCardDir).isDirectory()) {
      return null
    }
    return realCardDir
  } catch {
    return null
  }
}

/**
 * 同步读取指定任务卡的流转轨迹与明确耗时分布。
 * 仅统计明确记录了非负 `seconds` 的步骤，绝不编造用时或用量。
 */
export function readCardTrajectory(project: string, card: string): CardTrajectory {
  const realCardDir = resolveSafeCardDir(project, card)
  if (!realCardDir) return { rows: [], distribution: [] }

  const flowContent = readSafeCardFile(realCardDir, '流转.jsonl')
  if (!flowContent) return { rows: [], distribution: [] }

  const entries: Array<{ lineIndex: number; record: Record<string, unknown> }> = []
  const bySeq = new Map<string, Record<string, unknown>>()

  const lines = flowContent.split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]?.trim() ?? ''
    if (!line) continue

    let parsed: unknown
    try {
      parsed = JSON.parse(line)
    } catch {
      continue
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) continue

    const record = parsed as Record<string, unknown>
    entries.push({ lineIndex: i, record })
    if (typeof record.seq === 'number' || typeof record.seq === 'string') {
      bySeq.set(String(record.seq), record)
    }
  }

  const rows: TrajectoryRow[] = []
  const stageSeconds = new Map<string, number>()
  let planWriteStartMs: number | null = null
  let awaitApprovalStartMs: number | null = null
  let fullTestStartMs: number | null = null

  for (const { lineIndex: i, record } of entries) {
    const seq = record.seq
    const id = typeof seq === 'number' || typeof seq === 'string'
      ? `${String(seq)}-${i}`
      : `row-${i}`
    const actor = pickActor(record)
    const time = formatTrajectoryTime(record.time)
    const summary = pickSummary(record)
    const seconds = parseOptionalSeconds(record.seconds)
    const usage = parseOptionalUsage(record.usage)
    const raw = readRowRawText(realCardDir, record)
    const directOutput = readSafeCardFile(realCardDir, record.live)
    const replyKey = typeof record.reply_to === 'number' || typeof record.reply_to === 'string'
      ? String(record.reply_to)
      : ''
    const replyLive = !directOutput && replyKey ? bySeq.get(replyKey)?.live : undefined
    const output = directOutput || readSafeCardFile(realCardDir, replyLive)
    const diff = readSafeCardFile(realCardDir, record.diff, true)
    const attachments = record.kind === '追加要求'
      ? extractTrajectoryAttachments(realCardDir, record.attachments)
      : []

    rows.push({
      id,
      time,
      actor,
      summary,
      seconds,
      usage,
      raw,
      ...(output ? { output } : {}),
      ...(diff ? { diff } : {}),
      ...(attachments.length > 0 ? { attachments } : {}),
    })

    const kind = typeof record.kind === 'string' ? record.kind.trim() : ''
    const phase = typeof record.phase === 'string' ? record.phase.trim() : ''
    const evTimeMs = parseExplicitEventTimeMs(record.time)

    if (kind === '追加要求' || kind === '开跑' || kind === '开工' || kind === '放弃') {
      planWriteStartMs = null
      awaitApprovalStartMs = null
      fullTestStartMs = null
    }

    if (kind === '写方案') {
      planWriteStartMs = evTimeMs
      awaitApprovalStartMs = null
      continue
    }

    if (kind === '方案' || kind === '方案失败') {
      const planSec = seconds ?? diffValidSeconds(planWriteStartMs, evTimeMs)
      planWriteStartMs = null
      addDistributionSeconds(stageSeconds, '写方案', planSec)
      awaitApprovalStartMs = kind === '方案' ? evTimeMs : null
      continue
    }

    if (kind === '批准') {
      const waitSec = seconds ?? diffValidSeconds(awaitApprovalStartMs, evTimeMs)
      awaitApprovalStartMs = null
      addDistributionSeconds(stageSeconds, '等你批', waitSec)
      continue
    }

    if (kind === '全量') {
      if (phase === '开始') {
        fullTestStartMs = evTimeMs
      } else if (phase === '结果' || phase === '停' || phase === '失败' || phase === '结束') {
        const testRunSec = seconds ?? diffValidSeconds(fullTestStartMs, evTimeMs)
        fullTestStartMs = null
        addDistributionSeconds(stageSeconds, '跑测试', testRunSec)
      }
      continue
    }

    if (seconds !== null) {
      const stage = mapExplicitStepToStage(record.step) ?? actor
      if (seconds === 0 && !stageSeconds.has(stage)) {
        stageSeconds.set(stage, 0)
      } else {
        addDistributionSeconds(stageSeconds, stage, seconds)
      }
    }
  }

  const distribution: TrajectoryDistributionItem[] = Array.from(stageSeconds.entries()).map(
    ([actor, seconds]) => ({ actor, seconds }),
  )

  return { rows, distribution }
}
