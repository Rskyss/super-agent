/**
 * 这张卡是不是在等接调度员（建卡时还没调度员，卡停在写方案之前，来源.json 标了 deferred_plan）。
 * 左栏、总览和回答进度都用这一处判断，免得一处说“等你接调度员”、另一处说“在写方案”。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { FINISHED_CARD_STATES } from './types.ts'

const CARD_ID = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/

/** @returns 来源.json 是否标了“建卡时没调度员、写方案暂缓”。 */
export function hasCardDeferredPlan(project: string, cardId: string): boolean {
  if (!project || !CARD_ID.test(cardId)) return false
  try {
    const parsed = JSON.parse(readFileSync(join(project, '.collab', 'cards', cardId, '来源.json'), 'utf8')) as unknown
    return Boolean(parsed && typeof parsed === 'object' && (parsed as { deferred_plan?: unknown }).deferred_plan === true)
  } catch {
    return false
  }
}

/** @returns 这张卡现在是不是在等你接调度员。 */
export function cardWaitsForDispatcher(project: string, cardId: string, state: string, phaseCode: string, phaseTone: unknown): boolean {
  return hasCardDeferredPlan(project, cardId)
    && (phaseCode === 'plan_waiting' || phaseCode === 'planning')
    && state !== '已放弃'
    && phaseTone !== 'done'
    && !FINISHED_CARD_STATES.has(state)
}
