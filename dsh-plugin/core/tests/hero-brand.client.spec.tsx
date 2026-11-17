// @vitest-environment jsdom
/// <reference types="vite/client" />
import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import type { ComponentType } from 'react'
const modules = import.meta.glob('../src/client/Branding.tsx', { eager: true }) as Record<string, Record<string, any>>
afterEach(cleanup)

function occupants() {
  const got = new Map<string, ComponentType<any>>()
  const f = modules['../src/client/Branding.tsx']!.registerSuperagentBrandSlots
  expect(f, '缺少统一品牌公开slot注册，空首页仍使用鲸鱼回退').toBeTypeOf('function')
  f({ inject: (_name: string, factory: () => unknown) => factory(), register: (def: { name: string }, C: ComponentType<any>) => { got.set(def.name, C); return () => {} } })
  return got
}

it('空首页注册空占位阻止鲸鱼回退，左栏继续使用自己的碗图标', () => {
  const got = occupants()
  expect([...got.keys()].sort()).toEqual(['conversation.hero.brand.mark', 'sidebar.brand.mark', 'sidebar.brand.name'])
  const Hero = got.get('conversation.hero.brand.mark')!, Sidebar = got.get('sidebar.brand.mark')!
  const h = render(<Hero size={34} className="host-hero-geometry" />)
  expect(h.container.querySelector('img')).toBeNull()
  expect(h.container.querySelector('svg')).toBeNull()
  expect(h.container.textContent).toBe('')
  h.unmount()
  const s = render(<Sidebar size={24} />)
  expect(s.container.querySelector('img')?.getAttribute('src')).toMatch(/aisoup-bowl\.png/)
})

it('首页空占位不留下原图标的尺寸和间距', () => {
  const Hero = occupants().get('conversation.hero.brand.mark')!
  const { container } = render(<Hero size={34} className="host-hero-geometry" />)
  expect(container.querySelector('.host-hero-geometry')).toBeNull()
  expect(container.children).toHaveLength(0)
})
