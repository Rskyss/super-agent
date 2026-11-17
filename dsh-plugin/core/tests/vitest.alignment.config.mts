// 工作区内直接运行插件测试，DSH 源码仅作为只读依赖，不需同步外部目录。
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from '../../../../dsh-base/node_modules/vitest/dist/config.js'
import { standardDecoratorPlugin } from '../../../../dsh-base/vitest.shared.ts'

const base = fileURLToPath(new URL('../../../../dsh-base', import.meta.url))  // 本仓库同级的 dsh-base（11-17 工具分家）
const root = fileURLToPath(new URL('..', import.meta.url))
const require = createRequire(`${base}/package.json`)
const pluginRequire = createRequire(`${base}/packages/superagent/core/package.json`)
const ts = require('typescript')
const tsconfig = ts.parseConfigFileTextToJson(`${base}/tsconfig.base.json`, readFileSync(`${base}/tsconfig.base.json`, 'utf8')).config
const paths = Object.entries(tsconfig.compilerOptions.paths as Record<string, string[]>)
const aliases = paths.flatMap(([name, values]) => {
  if (name.includes('*')) return []
  const target = values[0]!
  return [{ find: name, replacement: target.startsWith('packages/superagent/core/')
    ? resolve(root, target.slice('packages/superagent/core/'.length)) : resolve(base, target) }]
}).sort((a, b) => b.find.length - a.find.length)
for (const name of ['react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom/client', 'react-dom/test-utils', '@testing-library/react', 'react-dom', 'react']) {
  aliases.push({ find: name, replacement: pluginRequire.resolve(name) })
}
aliases.push({ find: 'vitest', replacement: resolve(base, 'node_modules/vitest/dist/index.js') })
export default defineConfig({
  root,
  plugins: [standardDecoratorPlugin()],
  resolve: { alias: aliases },
  oxc: { tsconfig: false, jsx: { runtime: 'automatic' } },
  test: { include: ['tests/**/*.spec.{ts,tsx}'], maxWorkers: 4, testTimeout: 30000,
    cacheDir: resolve(root, 'node_modules/.vitest-cache/alignment') },
})
