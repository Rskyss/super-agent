/**
 * macOS 钥匙串存取封装（仅供 Host 侧凭据插件调用）：
 * - 服务名严格限定在 `com.aisoup.superagent` 命名空间，拒绝任何旧应用或外部服务名。
 * - `set` 的密钥值只通过子进程 `stdin`（JSON）传入，绝不进入命令行参数、环境变量或错误日志。
 * - 若未传入测试 `helperPath`，只在首次实际读写时把 `native/keychain-helper.swift` 编译到
 *   `/tmp/` 专有缓存目录；非 macOS 直接明确拒绝，绝不回退明文文件。
 */
import { createHash } from 'node:crypto'
import { execFile, spawn } from 'node:child_process'
import { accessSync, constants as fsConstants, existsSync, readFileSync } from 'node:fs'
import { chmod, mkdir, rename, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

/** 本产品默认钥匙串服务名。 */
export const DEFAULT_KEYCHAIN_SERVICE = 'com.aisoup.superagent'

/** 服务名合法格式：仅允许自家命名空间及子域，拒绝外部或旧应用服务名。 */
export const KEYCHAIN_SERVICE_PATTERN = /^com\.aisoup\.superagent(?:\.[A-Za-z0-9][A-Za-z0-9._-]*)*$/

const DEFAULT_HELPER_TIMEOUT_MS = 10_000
const COMPILE_TIMEOUT_MS = 30_000
const MIN_HELPER_TIMEOUT_MS = 100
const MAX_ERROR_LEN = 240

const compileInflight = new Map<string, Promise<string>>()

/** 钥匙串存取构造选项。 */
export interface MacKeychainStoreOptions {
  /** 钥匙串服务名，必须属于 `com.aisoup.superagent` 命名空间。 */
  readonly service: string
  /** 可选自定义辅助可执行文件路径（单元测试假 helper 使用，不触发编译与真实钥匙串）。 */
  readonly helperPath?: string | undefined
  /** 子进程执行超时（毫秒），防止无响应挂死，默认 10000ms。 */
  readonly timeoutMs?: number | undefined
}

/**
 * 校验并返回合规的服务名；遇到 `old-real-app` 或非法字符立即拒绝。
 */
export function validateKeychainServiceName(service: string): string {
  if (typeof service !== 'string' || service.length === 0 || service.length > 128) {
    throw new Error('钥匙串服务名无效：必须为非空的 com.aisoup.superagent 命名空间名称')
  }
  if (service.includes('..') || !KEYCHAIN_SERVICE_PATTERN.test(service)) {
    throw new Error(`拒绝访问非自家命名空间的钥匙串服务 "${service.slice(0, 64)}"`)
  }
  return service
}

/** 校验账户键名（非空、无控制字符、长度受限）。 */
function validateAccountName(account: string): string {
  if (typeof account !== 'string' || account.length === 0 || account.length > 256) {
    throw new Error('钥匙串条目名无效：必须为非空字符串')
  }
  for (let i = 0; i < account.length; i += 1) {
    const code = account.charCodeAt(i)
    if (code < 0x20 || code === 0x7f) {
      throw new Error('钥匙串条目名包含非法控制字符')
    }
  }
  return account
}

/**
 * 对密钥生成不可反推的展示遮罩：
 * - 短密钥（或隐藏字符不足 8 位时）全部遮罩，绝不露出原文片段；
 * - 足够长的密钥仅保留前 3 位与后 4 位用于核对。
 */
export function maskSecret(value: string): string {
  if (typeof value !== 'string' || value.length === 0) return ''
  const chars = Array.from(value)
  const prefixLen = 3
  const suffixLen = 4
  const hiddenCount = chars.length - prefixLen - suffixLen
  if (hiddenCount < 8) {
    return '•'.repeat(Math.max(4, chars.length))
  }
  const prefix = chars.slice(0, prefixLen).join('')
  const suffix = chars.slice(-suffixLen).join('')
  return `${prefix}${'•'.repeat(hiddenCount)}${suffix}`
}

/** 从错误文本中抹除已知密钥并限制长度，避免原样透出系统异常文本。 */
export function redactSecretsInText(message: string, secrets: readonly string[] = []): string {
  let out = String(message || '')
  for (const rawSecret of secrets) {
    if (!rawSecret) continue
    const candidates = new Set<string>([rawSecret, rawSecret.trim()])
    try {
      candidates.add(encodeURIComponent(rawSecret))
      candidates.add(encodeURIComponent(rawSecret.trim()))
    } catch {
      // 忽略极端编码错误
    }
    for (const candidate of candidates) {
      if (candidate.length >= 2) {
        out = out.split(candidate).join('***')
      }
    }
  }
  // 顺手遮蔽常见 Bearer / sk- 形态令牌，防意外透传
  out = out.replace(/Bearer\s+[A-Za-z0-9._~+/=-]{4,}/gi, 'Bearer ***')
  out = out.replace(/\bsk-[A-Za-z0-9._-]{4,}/g, 'sk-***')
  out = out.replace(/[\r\n\t]+/g, ' ').trim()
  if (out.length > MAX_ERROR_LEN) {
    out = `${out.slice(0, MAX_ERROR_LEN)}...`
  }
  return out
}

/** 把辅助程序的通用错误码转成大白话原因。 */
function mapHelperErrorCode(stderrText: string, exitCode: number | null, signal: NodeJS.Signals | null): string {
  if (signal) {
    return 'Mac 钥匙串操作超时或被中断，请确认系统钥匙串可用后重试（已禁止交互弹窗）'
  }
  const match = /keychain_helper_error:([a-z0-9_-]+)/i.exec(stderrText)
  const code = match?.[1] ?? ''
  if (code === 'interaction_not_allowed' || code === 'os_status_-25308') {
    return 'Mac 钥匙串当前处于锁定或未授权静默访问状态，请先解锁登录钥匙串后重试（不会降级存为明文文件）'
  }
  if (code === 'auth_failed' || code === 'os_status_-25293') {
    return 'Mac 钥匙串权限验证失败，已拒绝访问（不会降级存为明文文件）'
  }
  if (code === 'invalid_service') {
    return '钥匙串服务名不属于本产品命名空间，已拒绝操作'
  }
  if (code === 'invalid_account') {
    return '钥匙串条目名称无效，已拒绝操作'
  }
  if (code === 'empty_secret' || code === 'invalid_stdin_json') {
    return '密钥内容为空或格式无效，已拒绝写入钥匙串'
  }
  if (code) {
    return `Mac 钥匙串读写失败（${code}，不会回退为明文文件）`
  }
  return `Mac 钥匙串辅助程序执行失败（退出码 ${exitCode ?? '未知'}，不会回退为明文文件）`
}

/** 定位插件自带的 Swift 辅助程序源码。 */
export function resolveKeychainHelperSourcePath(): string {
  const candidates = [
    resolve(import.meta.dirname, '..', 'native', 'keychain-helper.swift'),
    resolve(import.meta.dirname, '..', '..', 'native', 'keychain-helper.swift'),
    resolve(import.meta.dirname, 'native', 'keychain-helper.swift'),
  ]
  for (const path of candidates) {
    if (existsSync(path)) return path
  }
  throw new Error('缺少 Mac 钥匙串辅助程序源码 native/keychain-helper.swift，无法启用钥匙串存储')
}

async function ensureCompiledHelperBinary(): Promise<string> {
  if (process.platform !== 'darwin') {
    throw new Error('Mac 钥匙串仅支持在 macOS 系统使用，当前平台不支持（已拒绝回退明文文件）')
  }
  const sourcePath = resolveKeychainHelperSourcePath()
  const sourceBytes = readFileSync(sourcePath)
  const hash = createHash('sha256').update(sourceBytes).digest('hex').slice(0, 16)
  const cacheDir = join(tmpdir(), 'superagent-keychain-cache', hash)
  const binaryPath = join(cacheDir, 'keychain-helper')

  try {
    accessSync(binaryPath, fsConstants.X_OK)
    return binaryPath
  } catch {
    // 缓存不存在或不可执行，继续按需编译到 /tmp/ 专有缓存
  }

  const inflight = compileInflight.get(hash)
  if (inflight) return inflight

  const compilePromise = (async () => {
    try {
      accessSync(binaryPath, fsConstants.X_OK)
      return binaryPath
    } catch {
      // 需要编译
    }
    await mkdir(cacheDir, { recursive: true, mode: 0o700 })
    const tmpBinary = join(cacheDir, `keychain-helper.tmp.${process.pid}.${Date.now()}`)
    try {
      await execFileAsync(
        '/usr/bin/swiftc',
        ['-O', sourcePath, '-o', tmpBinary],
        { timeout: COMPILE_TIMEOUT_MS, maxBuffer: 512 * 1024 },
      )
      await chmod(tmpBinary, 0o700)
      await rename(tmpBinary, binaryPath)
      return binaryPath
    } catch (error: unknown) {
      await rm(tmpBinary, { force: true }).catch(() => {})
      const detail = error instanceof Error ? redactSecretsInText(error.message) : '编译失败'
      throw new Error(`无法准备 Mac 钥匙串辅助程序（${detail}），请确认系统已安装 Swift 命令行工具`)
    } finally {
      compileInflight.delete(hash)
    }
  })()

  compileInflight.set(hash, compilePromise)
  return compilePromise
}

/** 基于 Swift Security.framework 辅助程序的 macOS 钥匙串存储器。 */
export class MacKeychainStore {
  readonly service: string
  private readonly customHelperPath: string | undefined
  private readonly timeoutMs: number

  constructor(options: MacKeychainStoreOptions) {
    this.service = validateKeychainServiceName(options.service)
    this.customHelperPath = options.helperPath ? resolve(options.helperPath) : undefined
    const rawTimeout = options.timeoutMs ?? DEFAULT_HELPER_TIMEOUT_MS
    this.timeoutMs = Number.isFinite(rawTimeout) && rawTimeout >= MIN_HELPER_TIMEOUT_MS
      ? Math.floor(rawTimeout)
      : DEFAULT_HELPER_TIMEOUT_MS
  }

  private async resolveExecutable(): Promise<string> {
    if (this.customHelperPath !== undefined) {
      return this.customHelperPath
    }
    return ensureCompiledHelperBinary()
  }

  private async runHelper(
    args: readonly string[],
    stdinPayload?: string,
    secretsToRedact: readonly string[] = [],
  ): Promise<string> {
    const executable = await this.resolveExecutable()
    return new Promise<string>((resolvePromise, rejectPromise) => {
      const child = spawn(executable, args, {
        stdio: ['pipe', 'pipe', 'pipe'],
        shell: false,
      })

      let settled = false
      let timedOut = false
      const stdoutChunks: Buffer[] = []
      const stderrChunks: Buffer[] = []
      let totalBytes = 0
      const maxOutputBytes = 1024 * 1024

      const finish = (error: Error | null, output?: string) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        if (error) {
          rejectPromise(error)
        } else {
          resolvePromise(output ?? '')
        }
      }

      const timer = setTimeout(() => {
        timedOut = true
        try {
          child.kill('SIGKILL')
        } catch {
          // 忽略已退出进程
        }
        finish(new Error('Mac 钥匙串辅助程序响应超时，已中止操作（已禁止弹出阻塞授权窗）'))
      }, this.timeoutMs)

      child.stdout.on('data', (chunk: Buffer) => {
        totalBytes += chunk.length
        if (totalBytes <= maxOutputBytes) stdoutChunks.push(chunk)
      })
      child.stderr.on('data', (chunk: Buffer) => {
        totalBytes += chunk.length
        if (totalBytes <= maxOutputBytes) stderrChunks.push(chunk)
      })

      let stdinFailureMessage = ''
      child.stdin.on('error', (error: unknown) => {
        const code = (error as NodeJS.ErrnoException)?.code
        const safe = redactSecretsInText(
          error instanceof Error ? error.message : String(error),
          secretsToRedact,
        )
        stdinFailureMessage = code === 'EPIPE' || code === 'ERR_STREAM_DESTROYED'
          ? 'Mac 钥匙串辅助程序提前结束，未接收完写入数据'
          : `向 Mac 钥匙串辅助程序传递数据失败：${safe || '写入通道异常'}`
      })

      child.on('error', (error: unknown) => {
        const safe = redactSecretsInText(
          error instanceof Error ? error.message : String(error),
          secretsToRedact,
        )
        finish(new Error(`无法启动 Mac 钥匙串辅助程序：${safe || '未知启动错误'}`))
      })

      child.on('close', (code, signal) => {
        if (timedOut) return
        const stdoutText = Buffer.concat(stdoutChunks).toString('utf8')
        const stderrText = redactSecretsInText(Buffer.concat(stderrChunks).toString('utf8'), secretsToRedact)
        if (code === 0 && !stdinFailureMessage) {
          finish(null, stdoutText)
          return
        }
        if (code !== 0) {
          finish(new Error(stderrText ? mapHelperErrorCode(stderrText, code, signal) : (stdinFailureMessage || mapHelperErrorCode(stderrText, code, signal))))
          return
        }
        finish(new Error(stdinFailureMessage))
      })

      try {
        if (stdinPayload !== undefined) {
          child.stdin.write(stdinPayload, 'utf8', (writeError) => {
            if (writeError && !settled && !stdinFailureMessage) {
              const code = (writeError as NodeJS.ErrnoException)?.code
              const safe = redactSecretsInText(writeError.message, secretsToRedact)
              stdinFailureMessage = code === 'EPIPE' || code === 'ERR_STREAM_DESTROYED'
                ? 'Mac 钥匙串辅助程序提前结束，未接收完写入数据'
                : `向 Mac 钥匙串辅助程序传递数据失败：${safe || '写入通道异常'}`
            }
          })
        }
        child.stdin.end()
      } catch (error: unknown) {
        const safe = redactSecretsInText(
          error instanceof Error ? error.message : String(error),
          secretsToRedact,
        )
        try {
          if (child.exitCode === null) child.kill('SIGKILL')
        } catch {
          // 忽略已退出进程
        }
        finish(new Error(`向 Mac 钥匙串辅助程序传递数据失败：${safe || '写入通道异常'}`))
      }
    })
  }

  /** 按服务名与条目名读取密钥（只在 Host 内存返回，找不到时返回 undefined）。 */
  async get(account: string): Promise<string | undefined> {
    const validAccount = validateAccountName(account)
    const stdout = await this.runHelper(['get', '--service', this.service, '--account', validAccount])
    const trimmed = stdout.trim()
    if (!trimmed) return undefined

    let parsed: unknown
    try {
      parsed = JSON.parse(trimmed)
    } catch {
      throw new Error('Mac 钥匙串返回数据格式异常，无法解析')
    }
    if (typeof parsed !== 'object' || parsed === null) {
      throw new Error('Mac 钥匙串返回数据结构无效')
    }
    const record = parsed as { found?: unknown; value?: unknown }
    if (record.found === false) return undefined
    if (typeof record.value !== 'string') {
      throw new Error('Mac 钥匙串返回条目内容无效')
    }
    return record.value.trim().length === 0 ? undefined : record.value
  }

  /** 将非空密钥写入钥匙串；密钥只通过 stdin JSON 传入，绝不进命令行参数。 */
  async set(account: string, value: string): Promise<void> {
    const validAccount = validateAccountName(account)
    if (typeof value !== 'string' || value.length === 0 || value.trim().length === 0) {
      throw new Error('拒绝把空密钥写入 Mac 钥匙串')
    }
    const payload = JSON.stringify({ value })
    await this.runHelper(
      ['set', '--service', this.service, '--account', validAccount],
      payload,
      [value, payload],
    )
  }

  /** 从钥匙串删除指定条目（不存在时幂等成功）。 */
  async delete(account: string): Promise<void> {
    const validAccount = validateAccountName(account)
    await this.runHelper(['delete', '--service', this.service, '--account', validAccount])
  }
}

export { dirname }
