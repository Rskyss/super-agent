/**
 * 同会话内确认卡与任务条之间的轻量刷新通知（按 sessionId 隔离，不跨会话触发）。
 */
const listenersBySession = new Map<string, Set<() => void>>()

/**
 * 订阅指定会话的刷新通知（确认建卡后立即刷新当前会话的任务条）。
 * @param sessionId - 目标会话编号。
 * @param listener - 收到通知时触发的刷新回调。
 * @returns 幂等的取消订阅函数。
 */
export function subscribeSessionRefresh(sessionId: string, listener: () => void): () => void {
  let set = listenersBySession.get(sessionId)
  if (set === undefined) {
    set = new Set()
    listenersBySession.set(sessionId, set)
  }
  set.add(listener)
  return () => {
    const current = listenersBySession.get(sessionId)
    if (current === undefined) return
    current.delete(listener)
    if (current.size === 0) listenersBySession.delete(sessionId)
  }
}

/**
 * 通知指定会话的任务条立即刷新；不影响其他会话。
 * @param sessionId - 目标会话编号。
 */
export function notifySessionRefresh(sessionId: string): void {
  const set = listenersBySession.get(sessionId)
  if (set === undefined) return
  for (const listener of [...set]) listener()
}
