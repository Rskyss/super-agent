import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { BackendProcess } from '../src/backend.ts'

/** 假后台：第 failFirst 次以内不报端口（模拟起不来），之后报 PORT= 并一直活着；stubborn 时起不来的那几次不理会 SIGTERM。 */
const fixtureDirs: string[] = []
function fakeServer(failFirst: number, stubborn = false, startupDelay = 0): string {
  const dir = mkdtempSync(join(tmpdir(), 'sa-backend-'))
  fixtureDirs.push(dir)
  const counter = join(dir, 'count')
  writeFileSync(counter, '0')
  const script = join(dir, 'server.py')
  writeFileSync(script, [
    'import signal, sys, time',
    `time.sleep(${startupDelay})`,
    `p = ${JSON.stringify(counter)}`,
    'n = int(open(p).read()) + 1',
    'open(p, "w").write(str(n))',
    `if n <= ${failFirst} and ${stubborn ? 'True' : 'False'}:`,
    '    signal.signal(signal.SIGTERM, signal.SIG_IGN)',
    `if n > ${failFirst}:`,
    '    print("PORT=4%d" % n, flush=True)',
    'while True:',
    '    time.sleep(0.2)',
  ].join('\n'))
  return script
}

const made: BackendProcess[] = []
function make(script: string, startTimeoutMs = 3000, killGraceMs = 300): BackendProcess {
  const backend = new BackendProcess({ python: 'python3', script, cwd: tmpdir(), startTimeoutMs, killGraceMs })
  made.push(backend)
  return backend
}
const waitFor = async (check: () => boolean, ms = 3000) => {
  const end = Date.now() + ms
  while (!check() && Date.now() < end) await new Promise(r => setTimeout(r, 25))
  return check()
}

afterEach(async () => {
  const backends = made.splice(0)
  for (const b of backends) b.stop()
  expect(await waitFor(() => backends.every(b => b.liveCount === 0), 6500)).toBe(true)
  for (const dir of fixtureDirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

describe('BackendProcess', () => {
  it('正常后台启动稍慢时仍能报端口，测试不会把机器忙误判为起不来', async () => {
    const backend = make(fakeServer(0, false, 0.8))
    expect((await backend.ensure()).port).toBeGreaterThan(0)
  })
  it('并发调用共用同一个后台进程', async () => {
    const backend = make(fakeServer(0))
    const [a, b] = await Promise.all([backend.ensure(), backend.ensure()])
    expect(a.port).toBe(b.port)
    expect(backend.liveCount).toBe(1)
    expect(a.token.length).toBeGreaterThan(20)
  })

  it('启动超时后重试，旧进程退出不会把新进程“忘掉”，也不会多开', async () => {
    const backend = make(fakeServer(1))
    await expect(backend.ensure()).rejects.toMatchObject({ code: 'superagent/backend-unavailable' })
    const second = await backend.ensure()
    // 等第一个（超时被关的）进程真正退出，它的退出回调不能清掉第二个。
    expect(await waitFor(() => backend.liveCount === 1)).toBe(true)
    const third = await backend.ensure()
    expect(third.port).toBe(second.port)
    expect(backend.liveCount).toBe(1)
  }, 15000)

  it('启动超时的旧进程赖着不退：新进程照常起，旧的过了宽限期被强杀，不会越积越多', async () => {
    // 给正常解释器启动留足调度余量；强杀宽限比单次启动预算长，避免“旧的已收掉”与验证两进程竞跑。
    const backend = make(fakeServer(1, true), 3000, 4000)
    await expect(backend.ensure()).rejects.toMatchObject({ code: 'superagent/backend-unavailable' })
    const second = await backend.ensure()
    // 新进程已起来时，赖着的旧进程还活着（两个）；宽限期一过，只剩新进程。
    expect(backend.liveCount).toBe(2)
    expect(await waitFor(() => backend.liveCount === 1, 8000)).toBe(true)
    expect((await backend.ensure()).port).toBe(second.port)
  }, 15000)

  it('启动命令根本不存在：每次都报错，但不会越积越多', async () => {
    const backend = new BackendProcess({ python: 'definitely-not-a-command-sa', script: fakeServer(0), cwd: tmpdir(), startTimeoutMs: 400 })
    made.push(backend)
    for (let i = 0; i < 3; i++) {
      await expect(backend.ensure()).rejects.toMatchObject({ code: 'superagent/backend-unavailable' })
    }
    expect(backend.liveCount).toBe(0)
  })

  it('卸载时立刻关掉还在启动中的进程', async () => {
    // 强杀宽限设得很长：必须靠立刻发出的关闭信号退出，而不是等强杀。
    const backend = make(fakeServer(99), 60000, 5000)
    const pending = backend.ensure().catch(() => 'rejected')
    expect(await waitFor(() => backend.liveCount === 1)).toBe(true)
    backend.stop()
    expect(await waitFor(() => backend.liveCount === 0, 1500)).toBe(true)
    expect(await pending).toBe('rejected')
    await expect(backend.ensure()).rejects.toMatchObject({ code: 'superagent/backend-unavailable' })
  })

  it('在用的后台意外退出后，下一次会重新拉起', async () => {
    const backend = make(fakeServer(0))
    const first = await backend.ensure()
    first.child.kill('SIGKILL')
    expect(await waitFor(() => backend.liveCount === 0)).toBe(true)
    const second = await backend.ensure()
    expect(second.port).not.toBe(first.port)
    expect(backend.liveCount).toBe(1)
  })
})
