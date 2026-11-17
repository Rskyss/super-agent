/**
 * 3-3：消息区域外观收敛。
 * - 用 conversation.chat.node keyed='turn-process' priority=-100 空覆盖隐藏已完成轮次用时；
 * - 保留 turn-error 等其他节点以及后台推理/工具/统计服务，取消注册即可恢复原生已用时。
 */
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-ui-chat/client'

export type ChatChromeSlotsRegistry = Pick<ClientContext['slots'], 'inject' | 'register'>

function HiddenTurnProcessNode(): null {
  return null
}

/**
 * 注册聊天节点精简插槽，并返回清理函数。
 * @param slots - DSH 客户端 slots 服务。
 */
export function registerSuperagentChatChrome(slots: ChatChromeSlotsRegistry): () => void {
  return slots.inject('conversation.chat.node', () => slots.register(
    {
      name: 'conversation.chat.node',
      key: 'turn-process',
      priority: -100,
    },
    HiddenTurnProcessNode,
  ))
}
