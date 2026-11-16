/**
 * 打开（必要时先建）一张卡的任务对话：会话名 task-<项目指纹>-<卡号>、工作模式“超级助手 · 任务”、
 * 工作区 = 项目文件夹、标题“卡号 标题”。建好后把实际会话名记回后台，重开窗口也对得上。
 */
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'

export interface TaskSessionPorts {
  taskSession: (card: string) => Promise<RemoteResult<{ sessionId: string; preferred: string; project: string; title: string }>>
  bindTaskSession: (card: string, sessionId: string) => Promise<RemoteResult<{ ok: boolean; sessionId: string; reason: string }>>
  /** 把项目文件夹登记成 DSH 工作区（登记过就返回原来的）。 */
  ensureWorkspace: (path: string) => Promise<RemoteResult<{ workspaceId: string }>>
  createSession: (request: { workspaceId: string; sessionId: string; agentPreset: string }) => Promise<RemoteResult<{ sessionId: string }>>
  renameSession: (request: { sessionId: string; title: string }) => Promise<RemoteResult<unknown>>
  /** 收起多建的空对话（另一个窗口抢先绑了别的对话时）。 */
  archiveSession: (sessionId: string) => Promise<RemoteResult<unknown>>
}

export const TASK_PRESET = 'superagent-task'
export const CHAT_PRESET = 'superagent-chat'

/** 创建全新“聊天模式”对话所需的最小端口。 */
export interface ChatSessionPorts {
  ensureWorkspace: (path: string) => Promise<RemoteResult<{ workspaceId: string }>>
  createSession: (request: { workspaceId: string; agentPreset: string }) => Promise<RemoteResult<{ sessionId: string }>>
}

/**
 * 在指定项目工作区里直接创建一个全新的“超级助手 · 聊天”对话。
 * 不传 sessionId，不走 DSH 默认的空会话复用，防止复用尚未发送消息的任务对话。
 * @param ports - DSH 工作区与会话创建接口。
 * @param project - 当前项目文件夹路径。
 * @returns 新建会话编号，供主窗口打开。
 */
export async function createChatSession(ports: ChatSessionPorts, project: string): Promise<string> {
  const projectPath = project.trim()
  if (!projectPath) throw new Error('还没有配置当前项目文件夹')
  const workspace = await ports.ensureWorkspace(projectPath)
  if (!workspace.ok) throw new Error(workspace.error.message)
  const created = await ports.createSession({
    workspaceId: workspace.value.workspaceId,
    agentPreset: CHAT_PRESET,
  })
  if (!created.ok) throw new Error(created.error.message)
  return created.value.sessionId
}

/** 同一张卡同时只走一次“查记录→建→记下”（建卡后自动建 + 马上点“查看任务”不会建两个；复查第 4 项严重 1）。 */
const inflight = new Map<string, Promise<string>>()

/** @returns 这张卡的任务对话会话名；建不成时抛出带原因的错误。 */
export function ensureTaskSession(ports: TaskSessionPorts, card: string): Promise<string> {
  const running = inflight.get(card)
  if (running) return running
  const job = ensureOnce(ports, card).finally(() => { inflight.delete(card) })
  inflight.set(card, job)
  return job
}

async function ensureOnce(ports: TaskSessionPorts, card: string): Promise<string> {
  const info = await ports.taskSession(card)
  if (!info.ok) throw new Error(info.error.message)
  if (info.value.sessionId) return info.value.sessionId
  // 先把项目登记成工作区，再在里面建：只给文件夹路径的话，DSH 会把它当成还没选工作区的空对话，发不了消息。
  const workspace = await ports.ensureWorkspace(info.value.project)
  if (!workspace.ok) throw new Error(workspace.error.message)
  const created = await ports.createSession({ workspaceId: workspace.value.workspaceId, sessionId: info.value.preferred, agentPreset: TASK_PRESET })
  if (!created.ok) throw new Error(created.error.message)
  // 先记下再改标题：改标题失败不影响对上卡。后台先到先得：别处（另一个窗口）先记下了，就用那个。
  const bound = await ports.bindTaskSession(card, created.value.sessionId)
  if (!bound.ok) throw new Error(bound.error.message)
  if (!bound.value.ok) throw new Error(`没能记下任务对话：${bound.value.reason}`)
  const sessionId = bound.value.sessionId
  if (sessionId === created.value.sessionId) await ports.renameSession({ sessionId, title: info.value.title })
  // 另一个窗口抢先绑了别的对话：把我们多建的这个收起来，左栏不留两个（复查第 4 项第 2 轮中等 1）。
  else await ports.archiveSession(created.value.sessionId).catch(() => undefined)
  return sessionId
}
