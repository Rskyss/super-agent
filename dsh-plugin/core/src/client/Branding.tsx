import type { SidebarBrandMarkOwnerProps } from '@deepseek-ai/dsh-client-ui-sidebar/client'
import bowlMarkUrl from './assets/aisoup-bowl.png'
import css from './Branding.module.css'

/**
 * 超级助手侧栏品牌图标（复用公共 slot `sidebar.brand.mark`）。
 * 原图 `aisoup-bowl.png` 为反白线稿，配合 CSS 在浅色/深色主题下保持高对比度可见。
 */
export function SuperagentBrandMark({ size }: SidebarBrandMarkOwnerProps) {
  return (
    <span className={css.markWrap} style={{ width: size, height: size }}>
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
 * 超级助手侧栏品牌名称（复用公共 slot `sidebar.brand.name`）。
 */
export function SuperagentBrandName() {
  return <span className={css.brandTitle}>超级助手</span>
}
