/**
 * 补齐 DSH 原生“新建会话”入口的聊天模式边界（第 19 项）：
 * - 原生无参调用（全局快捷键 Alt+Meta+N / Cmd+N、品牌区“新建会话”等）在传入 fresh 时，
 *   直接走明确的“新建聊天”路径，绕过 DSH 默认的空会话复用（避免误复用空任务对话，包括已被误标聊天的空任务）；
 * - 未提供 fresh 时（如单元测试或兜底场景），无参调用补齐 `agentPreset: superagent-chat` 后交给原方法；
 * - 显式指定 preset 或携带工作区/草稿参数的调用保留原语义，仅在省略 `agentPreset` 时补齐 `superagent-chat`；
 * - 通过 Cordis 原始引用与原型链定位 `startSession` 的真实定义者（owner）做可逆包装，保留 `this` 语义，
 *   并支持多个安装生命周期交叠卸载，不破坏后来或外部的包装。
 */
import type { StartSessionOptions } from '@deepseek-ai/dsh-client-ui-workspace/client'
import type { WorkspaceId } from '@deepseek-ai/dsh-workspace/types'
import { CHAT_PRESET } from './task-session-client.ts'

/** 对外暴露的最小导航能力接口（兼容真实 UiWorkspace 服务与测试替身）。 */
export interface ChatNavigationTarget {
  startSession(workspaceId?: WorkspaceId | string, options?: StartSessionOptions): unknown
}

type StartSessionMethod = (
  this: unknown,
  workspaceId?: WorkspaceId | string,
  options?: StartSessionOptions,
) => unknown

interface WrapperNode {
  readonly owner: object
  readonly key: PropertyKey
  readonly wrapper: StartSessionMethod
  readonly originalDesc: PropertyDescriptor
  prev: StartSessionMethod
  prevNode: WrapperNode | undefined
  disposed: boolean
}

const CORDIS_ORIGINAL = Symbol.for('cordis.original')
const wrapperNodes = new WeakMap<Function, WrapperNode>()

/** 沿着 Cordis 的 `cordis.original` 标记逐层解包，拿到真实服务实例或原始对象。 */
function unwrapCordisTarget(target: object): object {
  let current: unknown = target
  const visited = new Set<unknown>()
  while (
    current !== null
    && (typeof current === 'object' || typeof current === 'function')
    && !visited.has(current)
  ) {
    visited.add(current)
    const original = (current as Record<PropertyKey, unknown>)[CORDIS_ORIGINAL]
    if (original === null || (typeof original !== 'object' && typeof original !== 'function')) {
      break
    }
    current = original
  }
  return current as object
}

/** 沿着原型链（不含 Object.prototype）定位真正定义指定属性的对象与描述符。 */
function findMethodOwner(
  target: object,
  key: PropertyKey,
): { readonly owner: object; readonly desc: PropertyDescriptor } | null {
  let current: object | null = target
  while (current !== null && current !== Object.prototype) {
    const desc = Object.getOwnPropertyDescriptor(current, key)
    if (desc !== undefined) {
      return { owner: current, desc }
    }
    current = Object.getPrototypeOf(current) as object | null
  }
  return null
}

/** 把方法值写入 owner，尽量保留原有的可枚举与可配置特征。 */
function writeMethodProperty(
  owner: object,
  key: PropertyKey,
  baseDesc: PropertyDescriptor,
  fn: StartSessionMethod,
): void {
  if (baseDesc.configurable) {
    Object.defineProperty(owner, key, { ...baseDesc, value: fn })
    return
  }
  if (baseDesc.writable) {
    ;(owner as Record<PropertyKey, unknown>)[key] = fn
    return
  }
  throw new TypeError(`无法包装只读且不可配置的方法 ${String(key)}`)
}

/** 判断调用选项是否等效于未传任何有效参数（所有自有属性均未定义）。 */
function isEmptyStartOptions(options: StartSessionOptions | undefined): boolean {
  if (options === undefined) return true
  if (typeof options !== 'object' || options === null) return false
  return Reflect.ownKeys(options).every(
    key => (options as Record<PropertyKey, unknown>)[key] === undefined,
  )
}

function isPromiseLike(value: unknown): value is PromiseLike<unknown> {
  return (
    value !== null
    && (typeof value === 'object' || typeof value === 'function')
    && typeof (value as PromiseLike<unknown>).then === 'function'
  )
}

/** 安全执行全新聊天回调，确保同步或异步异常都被捕获并如实报告，不产生未处理 Promise。 */
function invokeFresh(
  fresh: () => unknown,
  onError?: (error: unknown) => void,
): void {
  const report = (error: unknown): void => {
    if (onError) {
      try {
        onError(error)
      } catch (reportError: unknown) {
        console.warn('超级助手新会话错误提示回调失败:', reportError)
      }
      return
    }
    console.warn('超级助手新会话创建失败:', error)
  }

  try {
    const result = fresh()
    if (isPromiseLike(result)) {
      void Promise.resolve(result).then(undefined, report)
    }
  } catch (error: unknown) {
    report(error)
  }
}

/**
 * 为原生工作区导航安装“新会话默认走聊天模式”的可逆边界包装。
 * @param navigation - 工作区导航服务或等效对象。
 * @param fresh - 可选的明确新建聊天回调；传入时原生无参调用直接走该回调，绕过空会话复用。
 * @param onError - 可选的错误呈现回调，用于将 `fresh` 同步或异步失败如实展示到界面。
 * @returns 幂等的卸载函数，卸载时安全恢复原方法而不破坏交叠或外部包装。
 */
export function installChatNavigationDefaults(
  navigation: ChatNavigationTarget,
  fresh?: () => unknown,
  onError?: (error: unknown) => void,
): () => void {
  if (navigation === null || (typeof navigation !== 'object' && typeof navigation !== 'function')) {
    throw new TypeError('无效的导航服务对象，无法安装聊天默认模式包装')
  }

  const rawTarget = unwrapCordisTarget(navigation as object)
  const located = findMethodOwner(rawTarget, 'startSession')
  if (!located || typeof located.desc.value !== 'function') {
    throw new TypeError('未找到可包装的 startSession 方法')
  }

  const { owner, desc } = located
  const currentFn = desc.value as StartSessionMethod
  const existingNode = wrapperNodes.get(currentFn)
  const prevNode = existingNode && existingNode.owner === owner && existingNode.key === 'startSession'
    ? existingNode
    : undefined

  const node: WrapperNode = {
    owner,
    key: 'startSession',
    wrapper: undefined as unknown as StartSessionMethod,
    originalDesc: desc,
    prev: currentFn,
    prevNode,
    disposed: false,
  }

  const wrapped: StartSessionMethod = function (
    this: unknown,
    workspaceId?: WorkspaceId | string,
    callOptions?: StartSessionOptions,
  ): unknown {
    if (node.disposed) {
      return Reflect.apply(node.prev, this, arguments)
    }
    if (fresh && workspaceId === undefined && isEmptyStartOptions(callOptions)) {
      invokeFresh(fresh, onError)
      return undefined
    }
    if (callOptions?.agentPreset !== undefined) {
      return Reflect.apply(node.prev, this, arguments)
    }
    const nextOptions: StartSessionOptions = callOptions && typeof callOptions === 'object'
      ? { ...callOptions, agentPreset: CHAT_PRESET }
      : { agentPreset: CHAT_PRESET }
    return node.prev.call(this, workspaceId, nextOptions)
  }

  ;(node as { wrapper: StartSessionMethod }).wrapper = wrapped
  wrapperNodes.set(wrapped, node)
  writeMethodProperty(owner, 'startSession', desc, wrapped)

  return () => {
    if (node.disposed) return
    node.disposed = true

    const headDesc = Object.getOwnPropertyDescriptor(owner, 'startSession')
    const headFn = headDesc?.value as Function | undefined
    if (headFn === wrapped) {
      writeMethodProperty(owner, 'startSession', node.originalDesc, node.prev)
      return
    }

    // 栈顶不是当前包装时，沿着我们自己的包装链向下查找并摘除，避免误伤后来的其他包装。
    let cursor = headFn ? wrapperNodes.get(headFn) : undefined
    while (cursor !== undefined) {
      if (cursor.prevNode === node) {
        cursor.prev = node.prev
        cursor.prevNode = node.prevNode
        return
      }
      cursor = cursor.prevNode
    }
  }
}

const TRANSPORT_FAILURE_RE = /\b(?:HTTP|fetch|network|timeout|timed\s*out|econnrefused|econnreset|enotfound|etimedout|socket|aborted)\b|transport\s+failure|client\s+API|\/api\/|superagent\/status|session\/create|workspace\/create|failed\s+to\s+fetch|connection\s+(?:refused|reset|lost|closed)|\b[45]\d{2}\b|连不上|连接失败|网络异常|请求超时/i

/**
 * 将新建会话失败转换为产品经理能直接看懂的大白话提示：
 * - 传输、网关或 HTTP 类故障统一收敛为连接提示，绝不暴露路径、协议词或状态码；
 * - 保留明确的中文恢复指引（如先完成首次模型配置、先选择项目文件夹）；
 * - 其他未知内部报错或提供方原文不直接透传给用户，统一给出通用的项目设置检查提示。
 */
export function formatChatNavigationError(error: unknown): string {
  const raw = (error instanceof Error ? error.message : typeof error === 'string' ? error : String(error ?? '')).trim()

  if (TRANSPORT_FAILURE_RE.test(raw)) {
    return '没建上新对话：暂时连不上助手，请稍后再试'
  }
  if (raw.includes('请先完成超级助手首次模型配置')) {
    return '没建上新对话：请先完成超级助手首次模型配置'
  }
  if (raw.includes('默认工作区尚未就绪，请先选择项目文件夹')) {
    return '没建上新对话：默认工作区尚未就绪，请先选择项目文件夹'
  }
  if (raw.includes('还没有配置当前项目文件夹')) {
    return '没建上新对话：还没有配置当前项目文件夹'
  }
  if (
    raw.includes('读取超级助手配置状态失败')
    || raw.includes('未能确认配置是否生效')
    || raw.includes('默认模型或钥匙串未就绪')
  ) {
    return '没建上新对话：超级助手模型配置尚未就绪，请先完成配置或点重试'
  }
  return '没建上新对话：请检查当前项目设置或稍后再试'
}
