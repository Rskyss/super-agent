/**
 * 03b 聊天回执持久 UI 定位锚 Store 声明。
 * - 严格只导出工厂函数，绝不在模块级导出已创建的 handle 单例；
 * - 仅声明 init / persist / 纯 actions，无副作用；
 * - 仅存 UI 定位（project -> card -> { startSeq, createdAt }），不存需求、Key、内容或凭据。
 */
import { defineStore } from '@deepseek-ai/dsh-client-store'
import type { BoundActions, PropsStore } from '@deepseek-ai/dsh-client-ui-slots'
import type { ConfirmedTurnAnchor, ConfirmedTurnAnchorState, PendingTurnAnchor } from '../types.ts'

export const CONFIRMED_TURN_ANCHORS_PERSIST_KEY = 'superagent.confirmed-turn-anchors.v1'

function pendingAnchorKey(project: string, sessionId: string, card: string): string {
  return `${project}::${sessionId}::${card}`
}

function deleteMatchingPendingForProjectCard(
  pending: Record<string, PendingTurnAnchor> | undefined,
  project: string,
  card: string,
): void {
  if (!pending) return
  for (const [key, entry] of Object.entries(pending)) {
    if (entry.project === project && entry.card === card) {
      delete pending[key]
    }
  }
}

/**
 * 创建确认回执轮次定位 Store Handle（应在插件 apply 内调用一次并在同 scope 槽位间共享）。
 */
export function createConfirmedTurnAnchorStore() {
  return defineStore({
    init: (): ConfirmedTurnAnchorState => ({ records: {}, pending: {} }),
    persist: CONFIRMED_TURN_ANCHORS_PERSIST_KEY,
    actions: {
      remember: (d, project: string, card: string, anchor: ConfirmedTurnAnchor) => {
        const projKey = project.trim()
        const cardKey = card.trim()
        const createdAt = anchor.createdAt.trim()
        if (!projKey || !cardKey || !createdAt) return
        if (!Number.isInteger(anchor.startSeq) || anchor.startSeq < 0) return

        deleteMatchingPendingForProjectCard(d.pending, projKey, cardKey)
        const bucket = d.records[projKey] ?? (d.records[projKey] = {})
        const existing = bucket[cardKey]
        // 同一创建指纹只记首次锚，不被后续消息或重复刷新挪动；同号卡复用（createdAt 变化）时才更新。
        if (existing !== undefined && existing.createdAt === createdAt) return
        bucket[cardKey] = { startSeq: anchor.startSeq, createdAt }
      },
      stagePending: (d, entry: PendingTurnAnchor) => {
        const projKey = entry.project.trim()
        const sessionKey = entry.sessionId.trim()
        const cardKey = entry.card.trim()
        // 必须同时具备明确项目、会话与卡号才允许持久化，防止跨项目错绑同号卡。
        if (!projKey || !sessionKey || !cardKey) return
        if (!Number.isInteger(entry.startSeq) || entry.startSeq < 0) return

        const pending = d.pending ?? (d.pending = {})
        const key = pendingAnchorKey(projKey, sessionKey, cardKey)
        if (pending[key] !== undefined) return
        pending[key] = {
          project: projKey,
          sessionId: sessionKey,
          card: cardKey,
          startSeq: entry.startSeq,
        }
      },
      resolvePending: (d, project: string, sessionId: string, card: string, createdAt: string) => {
        const projKey = project.trim()
        const sessionKey = sessionId.trim()
        const cardKey = card.trim()
        const cleanCreatedAt = createdAt.trim()
        if (!projKey || !sessionKey || !cardKey || !cleanCreatedAt) return

        const pending = d.pending
        const key = pendingAnchorKey(projKey, sessionKey, cardKey)
        const entry = pending?.[key]
        if (!entry) return
        delete pending[key]

        const bucket = d.records[projKey] ?? (d.records[projKey] = {})
        const existing = bucket[cardKey]
        if (existing !== undefined && existing.createdAt === cleanCreatedAt) return
        bucket[cardKey] = { startSeq: entry.startSeq, createdAt: cleanCreatedAt }
      },
    },
  })
}

export type ConfirmedTurnAnchorStoreHandle = ReturnType<typeof createConfirmedTurnAnchorStore>
export type ConfirmedTurnAnchorActions = BoundActions<ConfirmedTurnAnchorStoreHandle>
export type ConfirmedTurnAnchorStoreProps = PropsStore<ConfirmedTurnAnchorStoreHandle>
