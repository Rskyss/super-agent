import { describe, expect, it, vi } from 'vitest'
import { ensureTaskSession, type TaskSessionPorts } from '../src/client/task-session-client.ts'

function ports(over: Partial<TaskSessionPorts> = {}) {
  const calls: string[] = []
  let boundTo = ''
  const base: TaskSessionPorts = {
    taskSession: vi.fn(async () => ({ ok: true as const, value: { sessionId: boundTo, preferred: 'task-abcd1234-CARD-001', project: '/p', title: 'CARD-001 加 undo' } })),
    bindTaskSession: vi.fn(async (_c: string, sid: string) => {
      calls.push(`bind:${sid}`)
      if (!boundTo) boundTo = sid
      return { ok: true as const, value: { ok: true, sessionId: boundTo, reason: '' } }
    }),
    ensureWorkspace: vi.fn(async (path: string) => { calls.push(`workspace:${path}`); return { ok: true as const, value: { workspaceId: 'ws-1' } } }),
    createSession: vi.fn(async (r) => { calls.push(`create:${r.sessionId}:${r.agentPreset}:${r.workspaceId}`); return { ok: true as const, value: { sessionId: 'session-real' } } }),
    renameSession: vi.fn(async (r) => { calls.push(`rename:${r.title}`); return { ok: true as const, value: {} } }),
    archiveSession: vi.fn(async (sid: string) => { calls.push(`archive:${sid}`); return { ok: true as const, value: {} } }),
    ...over,
  }
  return { ports: base, calls }
}

describe('打开 / 新建任务对话', () => {
  it('已经有：直接用，不再新建', async () => {
    const t = ports({ taskSession: vi.fn(async () => ({ ok: true as const, value: { sessionId: 'session-old', preferred: 'x', project: '/p', title: 't' } })) })
    expect(await ensureTaskSession(t.ports, 'CARD-001')).toBe('session-old')
    expect(t.calls).toEqual([])
  })
  it('没有：登记工作区 → 用“任务”模式建 → 先记下再改标题', async () => {
    const t = ports()
    expect(await ensureTaskSession(t.ports, 'CARD-001')).toBe('session-real')
    expect(t.calls).toEqual(['workspace:/p', 'create:task-abcd1234-CARD-001:superagent-task:ws-1', 'bind:session-real', 'rename:CARD-001 加 undo'])
  })
  it('同一张卡同时打开两次（建卡后自动建 + 马上点查看任务）：只建一次（复查第 4 项严重 1）', async () => {
    const t = ports()
    const [a, b] = await Promise.all([ensureTaskSession(t.ports, 'CARD-001'), ensureTaskSession(t.ports, 'CARD-001')])
    expect(a).toBe(b)
    expect(t.ports.createSession).toHaveBeenCalledTimes(1)
  })
  it('别处（另一个窗口）先记下了别的会话：改用那个，不改它的标题', async () => {
    const t = ports({ bindTaskSession: vi.fn(async () => ({ ok: true as const, value: { ok: true, sessionId: 'session-other-window', reason: '' } })) })
    expect(await ensureTaskSession(t.ports, 'CARD-001')).toBe('session-other-window')
    expect(t.ports.renameSession).not.toHaveBeenCalled()
    // 我们多建的那个收起来，左栏不留两个（复查第 4 项第 2 轮中等 1）
    expect(t.calls).toContain('archive:session-real')
  })
  it('后台拒绝绑定或新建失败：报错', async () => {
    const t1 = ports({ bindTaskSession: vi.fn(async () => ({ ok: true as const, value: { ok: false, sessionId: '', reason: '记录坏了' } })) })
    await expect(ensureTaskSession(t1.ports, 'CARD-001')).rejects.toThrow('记录坏了')
    const t2 = ports({ createSession: vi.fn(async () => ({ ok: false as const, error: { code: 'x', message: '建不了' } as never })) })
    await expect(ensureTaskSession(t2.ports, 'CARD-001')).rejects.toThrow('建不了')
  })
})
