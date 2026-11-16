import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { bindTaskSession, cardOfSession, cardOfTaskSession, cardsFromConversation, readTaskSessions, TaskSessionMapCorrupt, taskSessionIdFor, taskSessionOf } from '../src/task-session.ts'

const project = () => mkdtempSync(join(tmpdir(), 'sa-proj-'))

describe('任务对话的会话名', () => {
  it('带项目指纹：不同项目的同号卡不撞；能认回卡号（老写法也认）', () => {
    const a = taskSessionIdFor('/p/a', 'CARD-001')
    expect(a).not.toBe(taskSessionIdFor('/p/b', 'CARD-001'))
    expect(a).toMatch(/^task-[0-9a-f]{8}-CARD-001$/)
    expect(cardOfSession(a)).toBe('CARD-001')
    expect(cardOfSession('task-CARD-002')).toBe('CARD-002')
    expect(cardOfSession('session-123')).toBe('')
    expect(cardOfSession('task-../x')).toBe('')
  })
  it('别的项目的任务会话名不认成当前项目的卡（复查第 4 项严重 2）', () => {
    const p = project()
    expect(cardOfTaskSession(p, taskSessionIdFor(p, 'CARD-001'))).toBe('CARD-001')
    expect(cardOfTaskSession(p, taskSessionIdFor('/别的项目', 'CARD-001'))).toBe('')
  })
})

describe('卡 ↔ 任务对话的记录', () => {
  it('先到先得：已绑过就返回原来那个，不覆盖', () => {
    const p = project()
    expect(bindTaskSession(p, 'CARD-001', 'session-a')).toEqual({ ok: true, sessionId: 'session-a', existed: false })
    expect(bindTaskSession(p, 'CARD-001', 'session-b')).toEqual({ ok: true, sessionId: 'session-a', existed: true })
    expect(taskSessionOf(p, 'CARD-001')).toBe('session-a')
    expect(cardOfTaskSession(p, 'session-a')).toBe('CARD-001')
  })
  it('拒绝：一个会话绑两张卡、会话名写明属于别的卡、别的项目的会话名、会话名不合规', () => {
    const p = project()
    bindTaskSession(p, 'CARD-001', 'session-a')
    expect(bindTaskSession(p, 'CARD-002', 'session-a')).toMatchObject({ ok: false })
    expect(bindTaskSession(p, 'CARD-002', taskSessionIdFor(p, 'CARD-001'))).toMatchObject({ ok: false })
    expect(bindTaskSession(p, 'CARD-002', taskSessionIdFor('/别的项目', 'CARD-002'))).toMatchObject({ ok: false })
    expect(bindTaskSession(p, 'CARD-002', '../x')).toMatchObject({ ok: false })
    expect(bindTaskSession(p, 'CARD-002', taskSessionIdFor(p, 'CARD-002'))).toMatchObject({ ok: true })
  })
  it('记录里已经存着别的项目的对话名（比如复制过来的项目）：不认，可以重新绑（复查第 4 项第 2 轮严重）', () => {
    const p = project()
    const other = taskSessionIdFor('/别的项目', 'CARD-001')
    mkdirSync(join(p, '.collab'), { recursive: true })
    writeFileSync(join(p, '.collab', 'dsh会话.json'), JSON.stringify({ 'CARD-001': other }))
    expect(cardOfTaskSession(p, other)).toBe('')
    expect(taskSessionOf(p, 'CARD-001')).toBe('')
    const mine = taskSessionIdFor(p, 'CARD-001')
    expect(bindTaskSession(p, 'CARD-001', mine)).toEqual({ ok: true, sessionId: mine, existed: false })
    expect(cardOfTaskSession(p, mine)).toBe('CARD-001')
  })
  it('整个项目被复制到别处：原记录（含 DSH 起的不带指纹的会话名）整份不认（复查第 4 项第 3 轮严重）', () => {
    const original = project()
    bindTaskSession(original, 'CARD-001', 'session-6f1c-uuid')
    expect(taskSessionOf(original, 'CARD-001')).toBe('session-6f1c-uuid')
    const copy = project()
    mkdirSync(join(copy, '.collab'), { recursive: true })
    writeFileSync(join(copy, '.collab', 'dsh会话.json'), readFileSync(join(original, '.collab', 'dsh会话.json')))
    expect(taskSessionOf(copy, 'CARD-001')).toBe('')
    expect(cardOfTaskSession(copy, 'session-6f1c-uuid')).toBe('')
    expect(bindTaskSession(copy, 'CARD-001', 'session-new')).toEqual({ ok: true, sessionId: 'session-new', existed: false })
  })
  it('老格式记录（没写项目）：只认名字里带当前项目指纹的', () => {
    const p = project()
    mkdirSync(join(p, '.collab'), { recursive: true })
    writeFileSync(join(p, '.collab', 'dsh会话.json'), JSON.stringify({ 'CARD-001': taskSessionIdFor(p, 'CARD-001'), 'CARD-002': 'session-uuid' }))
    expect(taskSessionOf(p, 'CARD-001')).toBe(taskSessionIdFor(p, 'CARD-001'))
    expect(taskSessionOf(p, 'CARD-002')).toBe('')
  })
  it('记录文件坏了：不覆盖、拒绝绑定，别的卡的记录保住（复查第 4 项中等 2）', () => {
    const p = project()
    mkdirSync(join(p, '.collab'), { recursive: true })
    writeFileSync(join(p, '.collab', 'dsh会话.json'), '{"CARD-001": "session-a", ')
    expect(() => readTaskSessions(p)).toThrow(TaskSessionMapCorrupt)
    expect(bindTaskSession(p, 'CARD-002', 'session-b')).toMatchObject({ ok: false })
    expect(readFileSync(join(p, '.collab', 'dsh会话.json'), 'utf8')).toBe('{"CARD-001": "session-a", ')
    writeFileSync(join(p, '.collab', 'dsh会话.json'), '{"CARD-001": 42}')
    expect(() => readTaskSessions(p)).toThrow(TaskSessionMapCorrupt)
    writeFileSync(join(p, '.collab', 'dsh会话.json'), '{"sessions": {"CARD-001": "session-a"}}')
    expect(() => readTaskSessions(p)).toThrow(TaskSessionMapCorrupt)
  })
  it('这个聊天里建出的卡：按卡的来源记录筛', () => {
    const p = project()
    for (const [card, conv] of [['CARD-001', 's1'], ['CARD-002', 's2'], ['CARD-003', 's1']] as const) {
      mkdirSync(join(p, '.collab', 'cards', card), { recursive: true })
      writeFileSync(join(p, '.collab', 'cards', card, '来源.json'), JSON.stringify({ conversation: conv }))
    }
    expect(cardsFromConversation(p, 's1', ['CARD-001', 'CARD-002', 'CARD-003', 'CARD-404'])).toEqual(['CARD-001', 'CARD-003'])
  })
})
