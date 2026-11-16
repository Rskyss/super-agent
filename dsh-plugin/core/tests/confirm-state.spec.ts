import { describe, expect, it } from 'vitest'
import { canAutoSend, initialPoll, markHandled, receivePoll } from '../src/confirm-state.ts'

const card = (id: string) => ({ id })

describe('确认卡轮询', () => {
  it('迟到的旧回包丢掉', () => {
    let s = initialPoll<{ id: string }>()
    s = receivePoll(s, 2, card('B'))
    s = receivePoll(s, 1, card('A'))
    expect(s.current?.id).toBe('B')
  })
  it('处理过的卡，旧回包里再出现也不显示', () => {
    let s = receivePoll(initialPoll<{ id: string }>(), 1, card('A'))
    s = markHandled(s, 'A')
    expect(s.current).toBeNull()
    s = receivePoll(s, 2, card('A'))
    expect(s.current).toBeNull()
    s = receivePoll(s, 3, card('B'))
    expect(s.current?.id).toBe('B')
  })
  it('处理旧卡不会误清已经显示的新卡', () => {
    let s = receivePoll(initialPoll<{ id: string }>(), 1, card('B'))
    s = markHandled(s, 'A')
    expect(s.current?.id).toBe('B')
  })
})

describe('改类型时能不能替你发话', () => {
  it('输入框空、没附件、没在发送才发', () => {
    expect(canAutoSend({ draft: '  ', attachmentIds: [], phase: 'plain' })).toBe(true)
    expect(canAutoSend({ draft: '我正在写', attachmentIds: [], phase: 'plain' })).toBe(false)
    expect(canAutoSend({ draft: '', attachmentIds: ['a'], phase: 'plain' })).toBe(false)
    expect(canAutoSend({ draft: '', attachmentIds: [], phase: 'sending' })).toBe(false)
  })
})
