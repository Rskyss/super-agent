/**
 * 06 / 06a：设置里的正式“积木库”页。
 * - 顶部“角色”块两行：超级助手（只读当前默认模型公开元数据，不含密钥，支持点“更换”跳转到模型设置）和调度员；
 * - 调度员“接一个 / 更换”不另造配置表单：关闭设置并新建 superagent-chat 会话，通过标准输入控制器填入话术交给对话。
 */
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button, Tag } from '@deepseek-ai/dsh-client-ui-primitives'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { ModelCatalog } from '@deepseek-ai/dsh-api-session-controller/types'
import type { SettingsSectionOwnerProps } from '@deepseek-ai/dsh-client-ui-settings/client'
import type { DispatcherState } from '../types.ts'
import css from './BlocksSettingsSection.module.css'

export interface BlocksSettingsInjected {
  readonly dispatcherState: () => Promise<RemoteResult<DispatcherState>>
  readonly modelCatalog: () => Promise<RemoteResult<ModelCatalog>>
  readonly startDispatcherChat: (mode: 'connect' | 'change', currentLabel: string) => Promise<void>
  readonly openAssistantSetup?: () => boolean
}

export type BlocksSettingsSectionProps = SettingsSectionOwnerProps & BlocksSettingsInjected

function resolveDefaultModelSummary(catalog: ModelCatalog): string {
  const def = catalog.default
  if (!def || (!def.provider && !def.model)) return '暂未配置默认模型'
  const group = catalog.groups.find(g => g.id === def.provider)
  const modelMeta = group?.models.find(m => m.id === def.model)
  const providerLabel = group?.name || def.provider
  const modelLabel = modelMeta?.name || def.model
  const effort = def.reasoningEffort ? ` · 思考档位：${def.reasoningEffort}` : ''
  return `${providerLabel} / ${modelLabel}${effort}`
}

export function BlocksSettingsSection(props: BlocksSettingsSectionProps): ReactElement {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const [dispatcher, setDispatcher] = useState<DispatcherState | null>(null)
  const [assistantModel, setAssistantModel] = useState('正在读取当前模型…')
  const [launching, setLaunching] = useState(false)
  const [actionError, setActionError] = useState('')

  useEffect(() => {
    let alive = true
    void props.dispatcherState().then((res) => {
      if (!alive) return
      if (res.ok) setDispatcher(res.value)
      else setDispatcher({ status: 'unknown', tool: '', label: '', model: '', reason: res.error.message, verified: false })
    }).catch(() => {})

    void props.modelCatalog().then((res) => {
      if (!alive) return
      if (res.ok) setAssistantModel(`当前默认模型：${resolveDefaultModelSummary(res.value)}`)
      else setAssistantModel('暂时读不到当前模型信息')
    }).catch(() => {
      if (alive) setAssistantModel('暂时读不到当前模型信息')
    })

    return () => { alive = false }
  }, [])

  const isDispatcherReady = dispatcher?.status === 'ready'
  const dispatcherLabel = dispatcher?.label || (dispatcher?.tool === 'agy' ? 'agy' : dispatcher?.tool === 'codex' ? 'Codex' : '')

  const handleAssistantChangeClick = () => {
    setActionError('')
    if (props.openAssistantSetup && props.openAssistantSetup()) {
      props.close()
      return
    }
    const modal = rootRef.current?.closest('[data-shortcut-modal="settings"]')
    const navButtons = modal?.querySelectorAll('nav button')
    if (navButtons) {
      for (const btn of Array.from(navButtons)) {
        const text = (btn.textContent ?? '').trim()
        if (text === '模型' || text === 'Models' || text.startsWith('模型')) {
          ;(btn as HTMLButtonElement).click()
          return
        }
      }
    }
    setActionError('没有找到模型设置入口，请点左侧栏的「模型」手动切换')
  }

  const handleDispatcherClick = async () => {
    if (launching) return
    setLaunching(true)
    setActionError('')
    try {
      const mode = isDispatcherReady ? 'change' : 'connect'
      props.close()
      await props.startDispatcherChat(mode, dispatcherLabel)
    } catch (error: unknown) {
      setLaunching(false)
      setActionError(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div ref={rootRef} className={css.section}>
      <div className={css.header}>
        <h2 className={css.title}>积木库</h2>
        <p className={css.intro}>
          管理超级助手与项目调度员。需要接一个或更换调度员时，会新开一段对话，在对话里出示确认卡后检测启用。
        </p>
      </div>

      <section className={css.group} aria-label="角色">
        <h3 className={css.groupTitle}>角色</h3>
        <div className={css.card}>
          <div className={css.row}>
            <div className={css.rowMain}>
              <div className={css.rowTitleLine}>
                <span className={css.rowName}>超级助手</span>
                <Tag>使用当前默认模型</Tag>
              </div>
              <div className={css.rowDesc}>前台搭档：陪你聊需求、出确认卡、盯每步进度。</div>
              <div className={css.rowMeta}>{assistantModel}</div>
            </div>
            <div className={css.rowActions}>
              <Button size="sm" variant="outline" onClick={handleAssistantChangeClick}>
                更换
              </Button>
            </div>
          </div>

          <div className={css.row}>
            <div className={css.rowMain}>
              <div className={css.rowTitleLine}>
                <span className={css.rowName}>调度员</span>
                {isDispatcherReady ? <Tag>已接好</Tag> : <Tag>还没接</Tag>}
              </div>
              <div className={css.rowDesc}>后台总控：负责写方案、拆步骤，并安排后续测试与复查。</div>
              {isDispatcherReady ? (
                <div className={css.rowMeta}>
                  当前使用：{dispatcherLabel}
                  {dispatcher?.model ? `（${dispatcher.model}）` : ''}
                </div>
              ) : (
                <div className={dispatcher?.tool ? css.rowError : css.rowMeta}>
                  {dispatcher?.tool
                    ? `${dispatcherLabel || dispatcher.tool} 未测通：${dispatcher.reason || '请先安装/登录后重新接入'}`
                    : '还没有接调度员（支持订阅工具 Codex 或 agy）'}
                </div>
              )}
            </div>
            <div className={css.rowActions}>
              <Button
                size="sm"
                variant={isDispatcherReady ? 'outline' : 'primary'}
                disabled={launching}
                onClick={() => { void handleDispatcherClick() }}
              >
                {launching ? '正在打开对话…' : isDispatcherReady ? '更换' : '接一个'}
              </Button>
            </div>
          </div>
        </div>
        {actionError ? <div className={css.rowError} role="alert">{actionError}</div> : null}
      </section>

      <div className={css.hintCard}>
        安全说明：调度员设置只保存在新版的积木库与当前项目设置里，只支持本机已安装的 Codex 或 agy，不会读取或改写你原有的积木库和钥匙串密钥。
      </div>
    </div>
  )
}
