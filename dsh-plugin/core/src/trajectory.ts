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
export function readSafeCardFile(realCardDir: string, relativePath: unknown): string {
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
    return sanitizeTrajectoryText(readFileSync(resolvedFile, 'utf-8'))
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
 * 同步读取指定任务卡的流转轨迹与明确耗时分布。
 * 仅统计明确记录了非负 `seconds` 的步骤，绝不编造用时或用量。
 */
export function readCardTrajectory(project: string, card: string): CardTrajectory {
  const safeCard = assertSafeCardId(card)
  const cleanProject = typeof project === 'string' ? project.trim() : ''
  if (!cleanProject) return { rows: [], distribution: [] }

  const cardsRoot = resolve(cleanProject, '.collab', 'cards')
  if (!existsSync(cardsRoot)) return { rows: [], distribution: [] }

  let realCardsRoot: string
  try {
    realCardsRoot = realpathSync(cardsRoot)
  } catch {
    return { rows: [], distribution: [] }
  }

  const cardDir = join(realCardsRoot, safeCard)
  if (!existsSync(cardDir)) return { rows: [], distribution: [] }

  let realCardDir: string
  try {
    realCardDir = realpathSync(cardDir)
    if (!isInsideDir(realCardsRoot, realCardDir) || !statSync(realCardDir).isDirectory()) {
      return { rows: [], distribution: [] }
    }
  } catch {
    return { rows: [], distribution: [] }
  }

  const flowContent = readSafeCardFile(realCardDir, '流转.jsonl')
  if (!flowContent) return { rows: [], distribution: [] }

  const rows: TrajectoryRow[] = []
  const actorSeconds = new Map<string, number>()

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
      ...(attachments.length > 0 ? { attachments } : {}),
    })

    if (seconds !== null) {
      actorSeconds.set(actor, (actorSeconds.get(actor) ?? 0) + seconds)
    }
  }

  const distribution: TrajectoryDistributionItem[] = Array.from(actorSeconds.entries()).map(
    ([actor, seconds]) => ({ actor, seconds }),
  )

  return { rows, distribution }
}
