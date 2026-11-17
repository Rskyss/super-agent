/**
 * 第七批 7-3：右栏底“本次运行 · 改了 N 个文件 · 守住 N 条规矩”（对照 04 / 05）。
 * 只读流转记录里程序自己做的检查，每项取最新一次；没检查过的项不算、不编。
 */
import type { RunSummary, RunRule } from './types.ts'

const PASS = '过'
const FILES_RE = /（(\d+) 个文件）/

/** 每种自动检查：看哪类记录、怎么算通过、给用户看的叫法。 */
const RULES: readonly { kind: string; label: string; ok: (ev: Record<string, unknown>) => boolean }[] = [
  { kind: '范围', label: '只改方案允许的地方', ok: ev => ev.ok === true },
  { kind: '老测试', label: '相关老测试没被改坏', ok: ev => ev.result === PASS },
  { kind: '页面检查', label: '页面检查通过', ok: ev => ev.result === PASS },
]

/** @returns 本次运行的摘要；一项检查都没做过时返回 undefined（右栏不显示这一行）。 */
export function extractRunSummary(rawEvents: readonly Record<string, unknown>[]): RunSummary | undefined {
  const latest = new Map<string, Record<string, unknown>>()
  let changedFiles: number | null = null
  for (const ev of rawEvents) {
    if (!ev || typeof ev.kind !== 'string') continue
    if (RULES.some(rule => rule.kind === ev.kind)) latest.set(ev.kind, ev)
    if (ev.kind === '范围' && typeof ev.summary === 'string') {
      const match = FILES_RE.exec(ev.summary)
      if (match) changedFiles = Number(match[1])
    }
  }
  const rules: RunRule[] = []
  for (const rule of RULES) {
    const ev = latest.get(rule.kind)
    if (!ev) continue
    rules.push({ label: rule.label, ok: rule.ok(ev), detail: typeof ev.summary === 'string' ? ev.summary : '' })
  }
  if (rules.length === 0 && changedFiles === null) return undefined
  return { changedFiles, rules }
}
