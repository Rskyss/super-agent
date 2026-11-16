/**
 * 看管现有超级助手的本机服务进程（desktop/server.py）：起、认端口、退出、关干净。
 * 单独成一个不依赖 DSH 的小模块，好写自动化测试（第 1 项复查第 1 轮）。
 */
import { spawn, type ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { RemoteError } from '@deepseek-ai/dsh-typert-protocol'

declare module '@deepseek-ai/dsh-typert-protocol' {
  interface RemoteErrorDetailsMap {
    /** 现有超级助手后台没起来或连不上。 */
    'superagent/backend-unavailable': { readonly reason: string }
  }
}

/** 一个起好的后台：端口和一次性口令。 */
export interface Backend {
  readonly child: ChildProcess
  readonly port: number
  readonly token: string
}

export interface BackendOptions {
  /** 启动命令（一般是 python3）。 */
  readonly python: string
  /** 后台脚本路径。 */
  readonly script: string
  readonly cwd: string
  /** 等它报出 PORT= 的最长时间。 */
  readonly startTimeoutMs: number
  /** 卸载后多久还没退就强杀。 */
  readonly killGraceMs?: number
  /** 传给子进程的额外环境变量（不改动父进程环境）。 */
  readonly env?: Readonly<Record<string, string | undefined>>
}

export class BackendProcess {
  private backend: Promise<Backend> | undefined
  /** 当前在用的那个进程；旧进程退出时只有它才能清掉 backend。 */
  private activeChild: ChildProcess | undefined
  /** 起过、还没退出的所有进程，卸载时一起关（含还在启动中的）。 */
  private readonly children = new Set<ChildProcess>()
  private stopped = false

  constructor(private readonly options: BackendOptions) {}

  /** 还活着的进程数（测试用）。 */
  get liveCount(): number {
    return this.children.size
  }

  /** 拿到在跑的后台；没有就起一个。并发调用共用同一次启动；启动失败下次再试。 */
  ensure(): Promise<Backend> {
    if (this.stopped) return Promise.reject(new RemoteError('superagent/backend-unavailable', '插件已卸载', { reason: 'stopped' }))
    if (this.backend === undefined) {
      const starting = this.start()
      this.backend = starting
      starting.catch(() => { if (this.backend === starting) this.backend = undefined })
    }
    return this.backend
  }

  /** 卸载：立刻关掉所有进程（含还在启动中的），killGraceMs 内没退就强杀。 */
  stop(): void {
    this.stopped = true
    this.backend = undefined
    this.activeChild = undefined
    for (const child of this.children) this.terminate(child)
  }

  /** 请它退；宽限期内没退就强杀。启动超时和卸载都走这里（复查第 2 轮中等 2）。 */
  private terminate(child: ChildProcess): void {
    child.kill('SIGTERM')
    const force = setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
    }, this.options.killGraceMs ?? 3000)
    force.unref()
  }

  private start(): Promise<Backend> {
    const { python, script, cwd, startTimeoutMs } = this.options
    if (!existsSync(script)) {
      return Promise.reject(new RemoteError('superagent/backend-unavailable', `找不到 ${script}`, { reason: 'missing-server' }))
    }
    const token = randomBytes(24).toString('base64url')
    const child = spawn(python, [script, '--port', '0', '--watch-parent'], {
      cwd,
      env: { ...process.env, ...this.options.env, SUPERAGENT_CHAT_TOKEN: token },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    this.children.add(child)
    this.activeChild = child
    return new Promise<Backend>((resolve, reject) => {
      let settled = false
      let buffer = ''
      const fail = (reason: string) => {
        if (settled) return
        settled = true
        this.terminate(child)
        reject(new RemoteError('superagent/backend-unavailable', `后台没起来：${reason}`, { reason }))
      }
      const timer = setTimeout(() => { fail('等端口超时') }, startTimeoutMs)
      child.stdout?.on('data', (chunk: Buffer) => {
        buffer += chunk.toString('utf8')
        const match = /PORT=(\d+)/.exec(buffer)
        if (match && !settled) {
          settled = true
          clearTimeout(timer)
          resolve({ child, port: Number(match[1]), token })
        }
      })
      child.stderr?.on('data', () => {})
      /** 进程没了（正常退出，或命令根本起不来）：从记录里移除；只有当前在用的才清 backend。 */
      const gone = () => {
        this.children.delete(child)
        if (this.activeChild === child) {
          this.activeChild = undefined
          this.backend = undefined
        }
      }
      child.on('error', (error) => {
        clearTimeout(timer)
        // 命令不存在或无法执行时只有 error、没有 exit（复查第 3 轮中等）。
        if (child.pid === undefined) gone()
        fail(error.message)
      })
      child.on('exit', (code) => {
        clearTimeout(timer)
        gone()
        if (!settled) fail(`进程退出 ${code ?? ''}`)
      })
    })
  }
}
