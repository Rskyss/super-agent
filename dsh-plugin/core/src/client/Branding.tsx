import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-ui-slots'
import type { HeroBrandMarkOwnerProps } from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { SidebarBrandMarkOwnerProps } from '@deepseek-ai/dsh-client-ui-sidebar/client'
import bowlMarkUrl from './assets/aisoup-bowl.png'
import css from './Branding.module.css'

export type BrandSlotsRegistry = Pick<ClientContext['slots'], 'inject' | 'register'>

function renderBrandMark(size: number, className?: string) {
  const wrapClass = className ? `${css.markWrap} ${className}` : css.markWrap
  return (
    <span className={wrapClass} style={{ width: size, height: size }}>
      <img
        className={css.markImg}
        src={bowlMarkUrl}
        alt=""
        width={size}
        height={size}
        draggable={false}
      />
    </span>
  )
}

/**
 * 超级玩家侧栏品牌图标（复用公共 slot `sidebar.brand.mark`）。
 * 原图 `aisoup-bowl.png` 为反白线稿，配合 CSS 在浅色/深色主题下保持高对比度可见。
 */
export function SuperagentBrandMark({ size }: SidebarBrandMarkOwnerProps) {
  return renderBrandMark(size)
}

/**
 * 超级玩家空对话首页品牌图标（复用公共 slot `conversation.hero.brand.mark`）。
 * 复用与侧栏相同的图像资源，并保留宿主传入的尺寸与几何样式类。
 */
export function SuperagentHeroBrandMark({ size, className }: HeroBrandMarkOwnerProps) {
  return renderBrandMark(size, className)
}

/**
 * 超级玩家空对话首页空占位品牌标记（复用公共 slot `conversation.hero.brand.mark`）。
 * 占住槽位防止宿主回退到默认像素鲸鱼图标，同时不渲染任何可见图标或尺寸间距。
 */
export function SuperagentEmptyHeroBrandMark(_props: HeroBrandMarkOwnerProps) {
  return null
}

/**
 * 超级玩家侧栏品牌名称（复用公共 slot `sidebar.brand.name`）。
 */
export function SuperagentBrandName() {
  return <span className={css.brandTitle}>超级玩家</span>
}

/**
 * 统一注册超级玩家对外占用的 DSH 公共品牌插槽（左栏图标/名称与空对话首页图标）。
 */
export function registerSuperagentBrandSlots(slots: BrandSlotsRegistry): void {
  slots.inject('sidebar.brand.mark', () => slots.register(
    { name: 'sidebar.brand.mark' },
    SuperagentBrandMark,
  ))
  slots.inject('sidebar.brand.name', () => slots.register(
    { name: 'sidebar.brand.name' },
    SuperagentBrandName,
  ))
  // 1-4 首页精简：大字前不出任何图标，保留旧注册注释以便按《已藏清单》恢复
  // slots.inject('conversation.hero.brand.mark', () => slots.register(
  //   { name: 'conversation.hero.brand.mark' },
  //   SuperagentHeroBrandMark,
  // ))
  slots.inject('conversation.hero.brand.mark', () => slots.register(
    { name: 'conversation.hero.brand.mark', priority: -100 },
    SuperagentEmptyHeroBrandMark,
  ))
}
