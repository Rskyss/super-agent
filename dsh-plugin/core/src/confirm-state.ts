/**
 * 确认卡的轮询状态（纯逻辑，好测；复查第 3 项中等 2）：
 * - 回包按请求序号只认最新的，迟到的旧回包丢掉；
 * - 已处理过（建了卡 / 撤了）的确认卡编号，旧回包里再出现也不显示。
 */
export interface PollState<T extends { id: string }> {
  readonly current: T | null
  readonly latestSeq: number
  readonly handled: ReadonlySet<string>
}

export function initialPoll<T extends { id: string }>(): PollState<T> {
  return { current: null, latestSeq: 0, handled: new Set() }
}

/** 收到第 seq 次轮询的回包。 */
export function receivePoll<T extends { id: string }>(state: PollState<T>, seq: number, value: T | null): PollState<T> {
  if (seq <= state.latestSeq) return state
  const current = value !== null && state.handled.has(value.id) ? null : value
  return { ...state, current, latestSeq: seq }
}

/** 某张确认卡处理完了：记下编号；只有它还是当前那张时才清掉（不误清后来的新卡）。 */
export function markHandled<T extends { id: string }>(state: PollState<T>, id: string): PollState<T> {
  const handled = new Set(state.handled)
  handled.add(id)
  return { ...state, handled, current: state.current?.id === id ? null : state.current }
}

/** “不对，改成…”能不能替你发那句话：输入框空着、没有附件、不在发送中才发，免得覆盖你正在写的。 */
export function canAutoSend(input: { draft: string; attachmentIds: readonly unknown[]; phase: string }): boolean {
  return input.draft.trim() === '' && input.attachmentIds.length === 0 && input.phase === 'plain'
}
