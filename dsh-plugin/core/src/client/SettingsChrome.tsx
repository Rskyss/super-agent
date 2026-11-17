/**
 * 06a：设置窗口公开 slot 外壳裁剪。
 * - 通过 settings.section.icon 为 superagent-blocks 贡献 DSH 图标，并在正式限制模式挂载自有 data-superagent-settings-entry 标记；
 * - 仅在限制标记存在时，通过 CSS :has() 隐藏 nav 中其余导航按钮，非 keychain 兼容分支不限制原导航；
 * - 通过 settings.action 高优先级空组件覆盖 open-document（打开配置文件），不删原组件与服务。
 */
import type { ReactElement } from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { SettingsSectionIconOwnerProps } from '@deepseek-ai/dsh-client-ui-settings/client'
import { IconSettingsOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import css from './SettingsChrome.module.css'

function defaultRestrictedHook(): boolean {
  return true
}

function HiddenOpenDocumentAction(): null {
  return null
}

export function registerSuperagentSettingsChrome(
  slots: Pick<ClientContext['slots'], 'inject' | 'register'>,
  useRestrictedSettings: () => boolean = defaultRestrictedHook,
): () => void {
  function BlocksSectionIcon(props: SettingsSectionIconOwnerProps): ReactElement {
    const restricted = useRestrictedSettings()
    const icon = <IconSettingsOutlineRegular size={props.size} />
    if (!restricted) return icon
    return (
      <span className={css.entry} data-superagent-settings-entry="">
        {icon}
      </span>
    )
  }

  const disposeIcon = slots.inject('settings.section.icon', () => slots.register(
    {
      name: 'settings.section.icon',
      id: 'superagent-blocks',
    },
    BlocksSectionIcon,
  ))

  // 覆盖原 open-document（低优先级原注册保留，取消本注册即可恢复“打开配置文件”按钮）
  const disposeAction = slots.inject('settings.action', () => slots.register(
    {
      name: 'settings.action',
      id: 'open-document',
      priority: -100,
    },
    HiddenOpenDocumentAction,
  ))

  return () => {
    disposeIcon()
    disposeAction()
  }
}
