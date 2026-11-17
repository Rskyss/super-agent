import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, it } from 'vitest'

it('任务总览所有启用的DSH颜色变量在真实主题中有定义，悬停和焦点不依赖空变量', () => {
  const here = dirname(fileURLToPath(import.meta.url))
  const themePath = [
    resolve(here, '../../../client/ui-theme/src/styles/design-platform.css'),
    resolve(here, '../../../../dsh-base/packages/client/ui-theme/src/styles/design-platform.css'),
  ].find(existsSync)
  expect(themePath, '需要同步工程或同级只读DSH主题用于核对').toBeTruthy()
  const theme = readFileSync(themePath!, 'utf8')
    + readFileSync(resolve(dirname(themePath!), 'base.css'), 'utf8')
  const css = readFileSync(resolve(here, '../src/client/TasksProbe.module.css'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
  const used = [...new Set([...css.matchAll(/var\((--dsw-[\w-]+)/g)].map(match => match[1]!))]
  const defined = new Set([...theme.matchAll(/(--dsw-[\w-]+)\s*:/g)].map(match => match[1]!))
  expect(used.filter(token => !defined.has(token))).toEqual([])
})
