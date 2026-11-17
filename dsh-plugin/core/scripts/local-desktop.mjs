#!/usr/bin/env node
/**
 * 制作本地可运行的临时桌面版“超级玩家”（macOS Electron 本地开发壳）。
 *
 * 严格约束：
 * - import 本模块无任何磁盘或环境副作用；仅暴露 `desktopLayout`、`desktopLaunchEnvironment`、`prepareLocalDesktop`。
 * - 严格限定所有产物落入 `os.tmpdir()` 或 `/private/tmp` 下的本次独立临时目录，拒绝 `/Applications`、旧目录或 symlink 逃逸。
 * - 不修改 DSH 核心、不修改旧 App/旧设置/真实 Keychain/真实积木库，也不把宿主 `process.env` 序列化进产物。
 */

import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import {
  closeSync,
  constants as fsConstants,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs'
import net from 'node:net'
import { homedir, tmpdir } from 'node:os'
import { basename, dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

/** 应用外壳名字（第 22 项改名“超级玩家”；旧版仍叫超级助手） */
export const APP_DISPLAY_NAME = '超级玩家'
const LAUNCHER_EXECUTABLE_NAME = 'SuperAgentLocal'
const OWN_MARKER_FILENAME = '.superagent-local-desktop.json'
const OWN_MARKER_TYPE = 'superagent-local-desktop'
const PLUGIN_PACKAGE_NAME = '@superagent/core'

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url))
const DEFAULT_PLUGIN_CORE_DIR = resolve(SCRIPT_DIR, '..')
const DEFAULT_REPO_ROOT = resolve(DEFAULT_PLUGIN_CORE_DIR, '..', '..')
const DEFAULT_DSH_ROOT = resolve(DEFAULT_REPO_ROOT, '..', 'dsh-base')

const PARENT_ENV_ALLOWLIST = [
  'PATH',
  'HOME',
  'SHELL',
  'LANG',
  'LC_ALL',
  'LC_CTYPE',
  'TMPDIR',
  'USER',
  'LOGNAME',
  'TERM',
  'COLORTERM',
  'DISPLAY',
  'XDG_RUNTIME_DIR',
  '__CF_USER_TEXT_ENCODING',
]

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`
}

function allowedTempRoots() {
  const roots = new Set()
  const systemTmp = tmpdir()
  if (existsSync(systemTmp)) {
    roots.add(realpathSync.native(systemTmp))
  } else {
    roots.add(resolve(systemTmp))
  }
  if (existsSync('/private/tmp')) {
    roots.add(realpathSync.native('/private/tmp'))
  } else {
    roots.add('/private/tmp')
  }
  return [...roots]
}

function tryLstat(targetPath) {
  try {
    return lstatSync(targetPath)
  } catch (error) {
    if (error && (error.code === 'ENOENT' || error.code === 'ENOTDIR')) {
      return null
    }
    throw error
  }
}

/**
 * 解析目标路径的真实规范路径（即使末段目录尚未创建，也能捕获任意祖先目录中的 symlink 逃逸）。
 */
function resolveRealPathPreservingTail(targetPath) {
  const abs = resolve(targetPath)
  const pending = []
  let probe = abs
  while (tryLstat(probe) === null) {
    const parent = dirname(probe)
    if (parent === probe) {
      return abs
    }
    pending.unshift(basename(probe))
    probe = parent
  }
  const realExisting = realpathSync.native(probe)
  return pending.length === 0 ? realExisting : join(realExisting, ...pending)
}

function isStrictSubpath(candidate, parentDir) {
  const normalizedParent = parentDir.endsWith(sep) ? parentDir : parentDir + sep
  return candidate !== parentDir && candidate.startsWith(normalizedParent)
}

/**
 * 在不做任何磁盘写入前，逐段验证目标路径及其既有祖先全部位于 realRoot 内，拒绝任何符号链接（含悬空链接）。
 */
function assertPathWithinRoot(realRoot, targetPath, expectType = 'any') {
  const absTarget = resolve(targetPath)
  if (!isStrictSubpath(absTarget, realRoot)) {
    throw new Error(`local-desktop: 写入路径越出本次临时根目录: ${absTarget}`)
  }
  const rel = relative(realRoot, absTarget)
  const segments = rel.split(sep).filter(Boolean)
  let current = realRoot
  for (let i = 0; i < segments.length; i += 1) {
    current = join(current, segments[i])
    const stat = tryLstat(current)
    if (stat === null) {
      break
    }
    if (stat.isSymbolicLink()) {
      throw new Error(`local-desktop: 拒绝符号链接越权写入（含悬空链接）: ${current}`)
    }
    const isLeaf = i === segments.length - 1
    if (!isLeaf) {
      if (!stat.isDirectory()) {
        throw new Error(`local-desktop: 既有祖先路径非目录，拒绝写入: ${current}`)
      }
      continue
    }
    if (expectType === 'dir' && !stat.isDirectory()) {
      throw new Error(`local-desktop: 既有目标路径非目录: ${current}`)
    }
    if (expectType === 'file' && !stat.isFile()) {
      throw new Error(`local-desktop: 既有目标路径非普通文件: ${current}`)
    }
  }
}

function assertLayoutPathsSafe(layout) {
  const root = layout.root
  const checks = [
    [join(root, OWN_MARKER_FILENAME), 'file'],
    [layout.home, 'dir'],
    [layout.agentsHome, 'dir'],
    [layout.workspace, 'dir'],
    [layout.profileDir, 'dir'],
    [join(layout.profileDir, 'package.json'), 'file'],
    [join(layout.profileDir, 'pnpm-workspace.yaml'), 'file'],
    [join(layout.profileDir, 'cordis.patch.yml'), 'file'],
    [join(layout.profileDir, 'node_modules'), 'dir'],
    [join(layout.profileDir, 'node_modules', '@superagent'), 'dir'],
    [layout.userData, 'dir'],
    [layout.blocksHome, 'dir'],
    [layout.logs, 'dir'],
    [layout.runtimeProject, 'dir'],
    [join(layout.runtimeProject, 'package.json'), 'file'],
    [join(layout.runtimeProject, 'desktop-runtime.json'), 'file'],
    [join(layout.runtimeProject, 'pnpm-workspace.yaml'), 'file'],
    [join(layout.runtimeProject, 'node_modules'), 'dir'],
    [join(layout.runtimeProject, 'node_modules', '@superagent'), 'dir'],
    [layout.bootstrapDir, 'dir'],
    [join(layout.bootstrapDir, 'package.json'), 'file'],
    [join(layout.bootstrapDir, 'config.json'), 'file'],
    [join(layout.bootstrapDir, 'index.mjs'), 'file'],
    [layout.bundle, 'dir'],
    [join(layout.bundle, 'Contents', 'Info.plist'), 'file'],
    [join(layout.bundle, 'Contents', 'Resources', 'electron.icns'), 'file'],
    [layout.executable, 'file'],
    [join(root, '.icon-build'), 'dir'],
    [layout.readmePath, 'file'],
  ]
  for (const [targetPath, expectType] of checks) {
    assertPathWithinRoot(root, targetPath, expectType)
  }

  // 检查 runtime-project/node_modules 下既有 @scope 目录本身不得为 symlink（允许其内部叶子依赖为只读 symlink）
  const runtimeModules = join(layout.runtimeProject, 'node_modules')
  const runtimeModulesStat = tryLstat(runtimeModules)
  if (runtimeModulesStat && runtimeModulesStat.isDirectory()) {
    for (const entry of readdirSync(runtimeModules, { withFileTypes: true })) {
      if (entry.name.startsWith('@') && entry.isSymbolicLink()) {
        throw new Error(`local-desktop: 拒绝 scoped 依赖目录符号链接: ${join(runtimeModules, entry.name)}`)
      }
    }
  }
}

function assertSafeTempRoot(rawRoot) {
  if (typeof rawRoot !== 'string' || rawRoot.trim() === '') {
    throw new Error('local-desktop: root 必须是非空临时目录路径')
  }
  const resolved = resolve(rawRoot)
  const resolvedStat = tryLstat(resolved)
  if (resolvedStat) {
    if (resolvedStat.isSymbolicLink()) {
      throw new Error(`local-desktop: 拒绝符号链接根目录 ${resolved}`)
    }
    if (!resolvedStat.isDirectory()) {
      throw new Error(`local-desktop: root 不是目录: ${resolved}`)
    }
  }
  const realRoot = resolveRealPathPreservingTail(resolved)
  const roots = allowedTempRoots()
  if (!roots.some(base => isStrictSubpath(realRoot, base))) {
    throw new Error(`local-desktop: 仅允许位于系统临时目录下的独立子目录，已拒绝 ${resolved}`)
  }

  assertOwnedIfNonEmpty(realRoot)
  return realRoot
}

/** 自用版（第 17 项）位置固定：~/Library/Application Support/超级玩家（第 22 项由“超级助手新版”改名）。不在旧版资料夹里，不接受别的位置。 */
export const SELF_USE_DIRNAME = '超级玩家'
export const SELF_USE_KEYCHAIN_SERVICE = 'com.aisoup.superagent'

/** 只有经 assertSelfUseRoot 核对过的自用版位置才记在这里，构建时据此放行（不放宽到任意持久目录）。 */
const approvedSelfUseRoots = new Set()

function assertSelfUseRoot(home) {
  const base = resolve(home ?? homedir(), 'Library', 'Application Support')
  // 上级路径里有符号链接就拒绝：否则写入会落到链接指向的别处（第 16/17 项复查）
  let realBase
  try {
    realBase = realpathSync.native(base)
  } catch {
    throw new Error(`local-desktop: 找不到 ${base}`)
  }
  if (realBase !== base) {
    throw new Error(`local-desktop: 自用版位置的上级路径经过符号链接，拒绝 ${base} -> ${realBase}`)
  }
  const root = join(base, SELF_USE_DIRNAME)
  const stat = tryLstat(root)
  if (stat && (stat.isSymbolicLink() || !stat.isDirectory())) {
    throw new Error(`local-desktop: 自用版位置必须是普通文件夹，拒绝 ${root}`)
  }
  assertOwnedIfNonEmpty(root)
  approvedSelfUseRoots.add(root)
  return root
}

/** 目标目录已存在且非空时，必须带有自家普通文件标记，严禁覆盖非自家已有产物或跟随标记 symlink。 */
function assertOwnedIfNonEmpty(realRoot) {
  if (tryLstat(realRoot)) {
    const entries = readdirSync(realRoot)
    if (entries.length > 0) {
      const markerPath = join(realRoot, OWN_MARKER_FILENAME)
      const markerStat = tryLstat(markerPath)
      if (!markerStat) {
        throw new Error(`local-desktop: 拒绝覆盖非自家已有非空目录 ${realRoot}`)
      }
      if (markerStat.isSymbolicLink() || !markerStat.isFile()) {
        throw new Error(`local-desktop: 自家标记必须是普通文件，拒绝符号链接或异常类型 ${markerPath}`)
      }
      let marker
      try {
        marker = JSON.parse(readFileSync(markerPath, 'utf8'))
      } catch {
        throw new Error(`local-desktop: 自家标记损坏，拒绝覆盖 ${realRoot}`)
      }
      if (!marker || marker.owner !== OWN_MARKER_TYPE) {
        throw new Error(`local-desktop: 目标目录标记不匹配，拒绝覆盖 ${realRoot}`)
      }
    }
  }
  return realRoot
}

function pickExistingOrDefault(candidates) {
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate
  }
  return candidates[0]
}

/**
 * 计算本地临时桌面版“超级玩家”的完整隔离目录布局（纯计算，若省略 root 则新建临时目录）。
 */
export function desktopLayout(options = {}) {
  const selfUse = options.selfUse === true
  if (selfUse && options.root !== undefined) {
    throw new Error('local-desktop: 自用版位置固定（~/Library/Application Support/超级玩家），不接受 --root')
  }
  const root = selfUse
    ? assertSelfUseRoot(options.home)
    : options.root === undefined
      ? mkdtempSync(join(realpathSync.native(tmpdir()), 'superagent-desktop-local-'))
      : assertSafeTempRoot(options.root)

  const dshRoot = resolve(options.dshRoot ?? DEFAULT_DSH_ROOT)
  const dshDesktopDir = join(dshRoot, 'apps', 'desktop')
  const home = join(root, 'home')
  const agentsHome = join(home, 'agents')
  const workspace = join(home, 'workspaces')
  const userData = join(root, 'user-data')
  const blocksHome = join(root, 'blocks-home')
  const logs = join(root, 'logs')
  const bundle = join(root, `${APP_DISPLAY_NAME}.app`)
  const executable = join(bundle, 'Contents', 'MacOS', LAUNCHER_EXECUTABLE_NAME)
  const runtimeProject = join(root, 'runtime-project')
  const profileDir = join(home, 'profiles', 'desktop')
  const bootstrapDir = join(root, 'app-bootstrap')
  const readmePath = join(root, 'LOCAL_BUNDLE_README.txt')

  const project = options.project !== undefined && String(options.project).trim() !== ''
    ? resolve(String(options.project))
    : workspace

  const rootHash = createHash('sha256').update(root).digest('hex').slice(0, 12)
  // 自用版用正式钥匙串名字（旧版模型 Key 已复制到这里），不设测试条目名；测试版照旧隔离
  const bundleId = selfUse ? 'com.aisoup.superagent.newapp' : `com.aisoup.superagent.local.${rootHash}`
  const keychainService = selfUse ? SELF_USE_KEYCHAIN_SERVICE : `com.aisoup.superagent.test.local.${rootHash}`
  const keychainTestRef = selfUse ? '' : `SUPERAGENT_TEST_LOCAL_${rootHash.toUpperCase()}`

  const portBase = 20000 + (Number.parseInt(rootHash.slice(0, 4), 16) % 20000)
  const mainInspectPort = portBase
  const rendererDebugPort = portBase + 1
  const hostInspectPort = portBase + 2

  const primaryRuntimeDir = pickExistingOrDefault([
    join(dshDesktopDir, '.desktop-build', 'targets', 'mac-arm64', 'runtime', 'primary-runtime'),
    join(dshDesktopDir, 'targets', 'mac-arm64', 'runtime', 'primary-runtime'),
  ])
  const sourceDevelopmentProject = pickExistingOrDefault([
    join(dshDesktopDir, '.desktop-build', 'development', 'project'),
    join(dshRoot, '.desktop-build', 'development', 'project'),
  ])
  const pnpmEntry = join(dshDesktopDir, 'node_modules', 'pnpm', 'bin', 'pnpm.mjs')

  const layout = {
    root,
    selfUse,
    rootHash,
    bundle,
    bundleId,
    executable,
    home,
    agentsHome,
    workspace,
    userData,
    blocksHome,
    logs,
    runtimeProject,
    profileDir,
    bootstrapDir,
    readmePath,
    project,
    dshRoot,
    dshDesktopDir,
    pnpmEntry,
    primaryRuntimeDir,
    sourceDevelopmentProject,
    keychainService,
    keychainTestRef,
    mainInspectPort,
    rendererDebugPort,
    hostInspectPort,
  }

  assertLayoutPathsSafe(layout)
  return layout
}

/**
 * 构造隔离启动环境变量：只保留白名单父环境变量，显式写入本次临时实例的独立 DSH/SuperAgent 变量。
 * 绝不透传任意外部环境变量或旧 SUPER_AGENT_BLOCKS_HOME。
 */
export function desktopLaunchEnvironment(layout, parentEnv = {}) {
  const env = {}
  if (parentEnv && typeof parentEnv === 'object') {
    for (const key of PARENT_ENV_ALLOWLIST) {
      const value = parentEnv[key]
      if (typeof value === 'string') {
        env[key] = value
      }
    }
  }

  env.DSH_HOME = layout.home
  env.DSH_AGENTS_HOME = layout.agentsHome ?? join(layout.home, 'agents')
  env.DSH_DESKTOP_USER_DATA_DIR = layout.userData
  env.SUPERAGENT_DSH_BLOCKS_HOME = layout.blocksHome
  env.SUPERAGENT_PROJECT = layout.project
  env.SUPERAGENT_DESKTOP = '1'   // 第 16 项：DSH 补丁据此不弹 DeepSeek Harness 欢迎页
  env.SUPERAGENT_KEYCHAIN = '1'
  env.SUPERAGENT_KEYCHAIN_SERVICE = layout.keychainService
  if (layout.keychainTestRef) env.SUPERAGENT_KEYCHAIN_TEST_REF = layout.keychainTestRef
  env.DSH_DESKTOP_DSH_DIR = layout.runtimeProject
  env.DSH_DESKTOP_PNPM_ENTRY = layout.pnpmEntry
  env.DSH_DESKTOP_PRIMARY_RUNTIME_DIR = layout.primaryRuntimeDir
  env.DSH_DESKTOP_OPEN_DEVTOOLS = '0'
  env.DSH_DESKTOP_HOST_INSPECT_PORT = String(layout.hostInspectPort)
  env.DSH_TELEMETRY_DISABLED = '1'
  env.ELECTRON_ENABLE_LOGGING = '1'

  return env
}

function readJson(filePath) {
  return JSON.parse(readFileSync(filePath, 'utf8'))
}

function ensureDirWithinRoot(realRoot, dirPath, mode = 0o700) {
  const absDir = resolve(dirPath)
  if (absDir === realRoot) {
    if (approvedSelfUseRoots.has(realRoot)) {
      // 自用版位置（第 17 项）：父目录 ~/Library/Application Support 一定在，这里只建它自己
      assertOwnedIfNonEmpty(realRoot)
      if (tryLstat(realRoot) === null) mkdirSync(realRoot, { mode })
      return realRoot
    }
    assertSafeTempRoot(realRoot)
    return realRoot
  }
  assertPathWithinRoot(realRoot, absDir, 'dir')
  const rel = relative(realRoot, absDir)
  let current = realRoot
  for (const segment of rel.split(sep).filter(Boolean)) {
    current = join(current, segment)
    const stat = tryLstat(current)
    if (stat === null) {
      mkdirSync(current, { mode })
      continue
    }
    if (stat.isSymbolicLink() || !stat.isDirectory()) {
      throw new Error(`local-desktop: 拒绝通过符号链接或非目录创建子目录: ${current}`)
    }
  }
  const actual = realpathSync.native(absDir)
  if (!isStrictSubpath(actual, realRoot)) {
    throw new Error(`local-desktop: 目录真实路径越出临时根目录: ${actual}`)
  }
  return absDir
}

function writeFileNoFollow(realRoot, filePath, data, mode = 0o600) {
  const absFile = resolve(filePath)
  assertPathWithinRoot(realRoot, absFile, 'file')
  ensureDirWithinRoot(realRoot, dirname(absFile), 0o700)
  const flags = fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_TRUNC | fsConstants.O_NOFOLLOW
  const fd = openSync(absFile, flags, mode)
  try {
    writeFileSync(fd, data)
  } finally {
    closeSync(fd)
  }
}

function writeJsonNoFollow(realRoot, filePath, data, mode = 0o600) {
  writeFileNoFollow(realRoot, filePath, `${JSON.stringify(data, null, 2)}\n`, mode)
}

function copyFileNoFollow(realRoot, sourcePath, destPath, mode = 0o600) {
  writeFileNoFollow(realRoot, destPath, readFileSync(sourcePath), mode)
}

function assertExists(targetPath, label) {
  if (!existsSync(targetPath)) {
    throw new Error(`local-desktop: 缺少必需只读产物 (${label}): ${targetPath}`)
  }
}

function linkEntry(realRoot, source, destination) {
  const absDest = resolve(destination)
  if (!isStrictSubpath(absDest, realRoot)) {
    throw new Error(`local-desktop: 符号链接创建路径越出本次临时根目录: ${absDest}`)
  }
  ensureDirWithinRoot(realRoot, dirname(absDest), 0o700)
  const target = realpathSync(source)
  const existing = tryLstat(absDest)
  if (existing) {
    if (existing.isSymbolicLink()) {
      unlinkSync(absDest)
    } else {
      rmSync(absDest, { recursive: true, force: true })
    }
  }
  symlinkSync(target, absDest, 'dir')
}

function isPortAvailable(port) {
  return new Promise((resolvePromise) => {
    const server = net.createServer()
    server.unref()
    server.once('error', () => { resolvePromise(false) })
    server.once('listening', () => {
      server.close(() => { resolvePromise(true) })
    })
    server.listen(port, '127.0.0.1')
  })
}

async function allocateFreePort(preferred, usedPorts) {
  let candidate = preferred
  for (let offset = 0; offset < 200; offset += 1) {
    const port = candidate + offset
    if (port >= 65000) break
    if (usedPorts.has(port) || [9222, 9229, 9230].includes(port)) continue
    if (await isPortAvailable(port)) {
      usedPorts.add(port)
      return port
    }
  }
  return await new Promise((resolvePromise, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : preferred
      server.close(() => {
        usedPorts.add(port)
        resolvePromise(port)
      })
    })
  })
}

function selectPluginPackageDir(dshRoot, localPluginDir) {
  const syncedPluginDir = join(dshRoot, 'packages', 'superagent', 'core')
  const hasBuilt = dir => existsSync(join(dir, 'lib', 'index.js')) && existsSync(join(dir, 'lib', 'client.js'))
  if (hasBuilt(syncedPluginDir)) {
    return realpathSync(syncedPluginDir)
  }
  if (hasBuilt(localPluginDir)) {
    return realpathSync(localPluginDir)
  }
  throw new Error(
    `local-desktop: 未找到已构建的 @superagent/core 产物（已检查 ${syncedPluginDir}/lib 与 ${localPluginDir}/lib）。请先构建插件后再制作本地临时桌面版。`,
  )
}

function prepareRuntimeProject(layout, pluginDir) {
  const sourceDir = layout.sourceDevelopmentProject
  const sourceManifestPath = join(sourceDir, 'package.json')
  const sourceRuntimePath = join(sourceDir, 'desktop-runtime.json')
  const sourceWorkspacePath = join(sourceDir, 'pnpm-workspace.yaml')
  const sourceModulesDir = join(sourceDir, 'node_modules')

  assertExists(sourceManifestPath, 'development/project/package.json')
  assertExists(sourceRuntimePath, 'development/project/desktop-runtime.json')
  assertExists(sourceWorkspacePath, 'development/project/pnpm-workspace.yaml')
  assertExists(sourceModulesDir, 'development/project/node_modules')

  const dest = layout.runtimeProject
  assertPathWithinRoot(layout.root, dest, 'dir')
  rmSync(dest, { recursive: true, force: true })
  ensureDirWithinRoot(layout.root, dest, 0o700)

  copyFileNoFollow(layout.root, sourceRuntimePath, join(dest, 'desktop-runtime.json'))
  copyFileNoFollow(layout.root, sourceWorkspacePath, join(dest, 'pnpm-workspace.yaml'))

  const sourceManifest = readJson(sourceManifestPath)
  const pluginManifest = readJson(join(pluginDir, 'package.json'))
  const baseBundles = Array.isArray(sourceManifest.dsh?.profile?.bundles)
    ? sourceManifest.dsh.profile.bundles
    : ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']
  const bundles = baseBundles.includes(PLUGIN_PACKAGE_NAME)
    ? [...baseBundles]
    : [...baseBundles, PLUGIN_PACKAGE_NAME]

  const runtimeManifest = {
    ...sourceManifest,
    dependencies: {
      ...(sourceManifest.dependencies ?? {}),
      [PLUGIN_PACKAGE_NAME]: pluginManifest.version ?? '0.2.2-rc.3',
    },
    dsh: {
      ...(sourceManifest.dsh ?? {}),
      profile: {
        ...(sourceManifest.dsh?.profile ?? {}),
        bundles,
      },
    },
  }
  writeJsonNoFollow(layout.root, join(dest, 'package.json'), runtimeManifest)

  // 自建一层链接目录，绝不改动 DSH 原 development/project/node_modules
  const destModules = join(dest, 'node_modules')
  ensureDirWithinRoot(layout.root, destModules, 0o700)
  for (const entry of readdirSync(sourceModulesDir, { withFileTypes: true })) {
    if (entry.name === '.bin') continue
    const srcEntry = join(sourceModulesDir, entry.name)
    if (entry.name.startsWith('@') && (entry.isDirectory() || entry.isSymbolicLink())) {
      const scopeDest = join(destModules, entry.name)
      ensureDirWithinRoot(layout.root, scopeDest, 0o700)
      for (const scoped of readdirSync(srcEntry, { withFileTypes: true })) {
        if (!scoped.isDirectory() && !scoped.isSymbolicLink()) continue
        linkEntry(layout.root, join(srcEntry, scoped.name), join(scopeDest, scoped.name))
      }
      continue
    }
    if (entry.isDirectory() || entry.isSymbolicLink()) {
      linkEntry(layout.root, srcEntry, join(destModules, entry.name))
    }
  }
  linkEntry(layout.root, pluginDir, join(destModules, '@superagent', 'core'))
}

function prepareDesktopProfile(layout, pluginDir) {
  const profileDir = layout.profileDir
  ensureDirWithinRoot(layout.root, profileDir, 0o700)

  const manifest = {
    name: 'dsh-profile-desktop',
    private: true,
    dependencies: {
      [PLUGIN_PACKAGE_NAME]: `link:${pluginDir}`,
    },
    dsh: {
      profile: {
        bundles: [
          '@deepseek-ai/dsh-base',
          '@deepseek-ai/dsh-web-app',
          PLUGIN_PACKAGE_NAME,
        ],
      },
    },
  }
  writeJsonNoFollow(layout.root, join(profileDir, 'package.json'), manifest)

  writeFileNoFollow(
    layout.root,
    join(profileDir, 'pnpm-workspace.yaml'),
    'packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n',
    0o600,
  )

  // 通过公开 profile patch 关闭统计与遥测，不改动 DSH 核心也不动旧用户配置
  writeFileNoFollow(
    layout.root,
    join(profileDir, 'cordis.patch.yml'),
    [
      '# 本地临时桌面版：通过公开 profile 配置关闭统计与遥测',
      '- id: product-analytics',
      '  config:',
      '    enabled: false',
      '- id: desktop-product-telemetry',
      '  disabled: true',
      '',
    ].join('\n'),
    0o600,
  )

  linkEntry(layout.root, pluginDir, join(profileDir, 'node_modules', '@superagent', 'core'))
}

function prepareBootstrap(layout) {
  const dir = layout.bootstrapDir
  assertPathWithinRoot(layout.root, dir, 'dir')
  rmSync(dir, { recursive: true, force: true })
  ensureDirWithinRoot(layout.root, dir, 0o700)

  writeJsonNoFollow(layout.root, join(dir, 'package.json'), {
    name: 'superagent-local-shell',
    private: true,
    type: 'module',
    main: 'index.mjs',
  })

  // 只存非敏感本地路径配置，绝不写入任何 process.env 或凭据
  writeJsonNoFollow(layout.root, join(dir, 'config.json'), {
    appName: APP_DISPLAY_NAME,
    dshDesktopDir: layout.dshDesktopDir,
    userDataDir: layout.userData,
    logsDir: layout.logs,
    bundleId: layout.bundleId,
    keychainService: layout.keychainService,
    keychainTestRef: layout.keychainTestRef,
  })

  const bootstrapCode = `import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { app } from 'electron'

const here = dirname(fileURLToPath(import.meta.url))
const config = JSON.parse(readFileSync(join(here, 'config.json'), 'utf8'))

// 在加载 DSH built main.js 前设置应用名、userData 与独立 logs 路径
app.setName(config.appName)
app.setPath('userData', config.userDataDir)
app.setAppLogsPath(config.logsDir)

// 原 main.js 顶层会无参调用 app.setAppLogsPath()，包装拦截防止重置到 ~/Library/Logs/超级玩家
const originalSetAppLogsPath = app.setAppLogsPath.bind(app)
const pinnedSetAppLogsPath = (_path) => originalSetAppLogsPath(config.logsDir)
try {
  Object.defineProperty(app, 'setAppLogsPath', {
    value: pinnedSetAppLogsPath,
    configurable: true,
    writable: true,
  })
} catch {
  app.setAppLogsPath = pinnedSetAppLogsPath
}

// 守卫默认协议注册，防止任何路径将本临时壳抢注为 dsh:// 默认处理器
const originalSetDefaultProtocol = app.setAsDefaultProtocolClient.bind(app)
const guardedSetDefaultProtocol = (scheme, ...args) => {
  if (scheme === 'dsh') return false
  return originalSetDefaultProtocol(scheme, ...args)
}
try {
  Object.defineProperty(app, 'setAsDefaultProtocolClient', {
    value: guardedSetDefaultProtocol,
    configurable: true,
    writable: true,
  })
} catch {
  app.setAsDefaultProtocolClient = guardedSetDefaultProtocol
}

// 指向只读的 DSH apps/desktop，确保 node-bin/pnpm/renderer/preload 资源正常寻址，同时保持 app.isPackaged === false
app.setAppPath(config.dshDesktopDir)

const rewriteTitle = (title) => {
  if (typeof title !== 'string' || title.trim() === '') return config.appName
  if (title.includes('DeepSeek Harness')) {
    return title.replaceAll('DeepSeek Harness', config.appName)
  }
  return title
}

app.on('browser-window-created', (_event, win) => {
  if (!win.isDestroyed()) {
    const initial = win.getTitle()
    if (!initial || initial === 'Electron' || initial.includes('DeepSeek Harness')) {
      win.setTitle(config.appName)
    }
  }
  win.on('page-title-updated', (event, title) => {
    const next = rewriteTitle(title)
    if (next !== title) {
      event.preventDefault()
      if (!win.isDestroyed()) win.setTitle(next)
    }
  })
})

await import(pathToFileURL(join(config.dshDesktopDir, 'lib', 'main.js')).href)
`
  writeFileNoFollow(layout.root, join(dir, 'index.mjs'), bootstrapCode, 0o600)
}

function buildIcnsFromPng(sourcePng, outputIcns, workRoot) {
  const tempWork = join(workRoot, '.icon-build')
  const iconsetDir = join(tempWork, 'superagent.iconset')
  assertPathWithinRoot(workRoot, tempWork, 'dir')
  assertPathWithinRoot(workRoot, outputIcns, 'file')
  rmSync(tempWork, { recursive: true, force: true })
  ensureDirWithinRoot(workRoot, iconsetDir, 0o700)

  try {
    const squarePng = join(tempWork, 'square.png')
    const widthOut = execFileSync('/usr/bin/sips', ['-g', 'pixelWidth', sourcePng], { encoding: 'utf8' })
    const heightOut = execFileSync('/usr/bin/sips', ['-g', 'pixelHeight', sourcePng], { encoding: 'utf8' })
    const w = Number(/pixelWidth:\s*(\d+)/.exec(widthOut)?.[1] ?? 129)
    const h = Number(/pixelHeight:\s*(\d+)/.exec(heightOut)?.[1] ?? 129)
    const edge = Math.max(w, h)
    execFileSync('/usr/bin/sips', ['-p', String(edge), String(edge), sourcePng, '--out', squarePng], { stdio: 'pipe' })

    const sizes = [
      ['icon_16x16.png', 16],
      ['icon_16x16@2x.png', 32],
      ['icon_32x32.png', 32],
      ['icon_32x32@2x.png', 64],
      ['icon_128x128.png', 128],
      ['icon_128x128@2x.png', 256],
      ['icon_256x256.png', 256],
      ['icon_256x256@2x.png', 512],
      ['icon_512x512.png', 512],
      ['icon_512x512@2x.png', 1024],
    ]
    for (const [name, size] of sizes) {
      execFileSync(
        '/usr/bin/sips',
        ['-z', String(size), String(size), squarePng, '--out', join(iconsetDir, name)],
        { stdio: 'pipe' },
      )
    }
    execFileSync('/usr/bin/iconutil', ['-c', 'icns', iconsetDir, '-o', outputIcns], { stdio: 'pipe' })
  } finally {
    rmSync(tempWork, { recursive: true, force: true })
  }
}

function buildLauncherScript(layout, ports) {
  const launchEnv = desktopLaunchEnvironment({ ...layout, hostInspectPort: ports.hostInspectPort }, {})
  const args = [
    join(layout.bundle, 'Contents', 'MacOS', 'Electron'),
    `--inspect=127.0.0.1:${String(ports.mainInspectPort)}`,
    `--remote-debugging-port=${String(ports.rendererDebugPort)}`,
    `--user-data-dir=${layout.userData}`,
    layout.bootstrapDir,
  ]

  // 运行时按白名单从调用方环境中仅挑选非敏感系统变量，再经 /usr/bin/env -i 清空其余全部环境变量，
  // 从而同时兼容 Finder 与命令行启动，且绝不将生成期宿主 process.env 序列化进产物。
  const runtimeAllowlistLines = PARENT_ENV_ALLOWLIST.map((key) => {
    if (key === 'PATH') {
      return 'if [ -n "${PATH+x}" ]; then set -- "$@" "PATH=${PATH}"; else set -- "$@" "PATH=/usr/bin:/bin:/usr/sbin:/sbin"; fi'
    }
    return `if [ -n "\${${key}+x}" ]; then set -- "$@" "${key}=\${${key}}"; fi`
  })

  const explicitPairs = Object.entries(launchEnv).map(([key, value]) => `${key}=${value}`)
  const trailingItems = [...explicitPairs, ...args].map(shellQuote)

  return [
    '#!/bin/sh',
    'set -eu',
    '_orig_argc=$#',
    ...runtimeAllowlistLines,
    'set -- "$@" \\',
    ...trailingItems.map((item, idx) => (idx < trailingItems.length - 1 ? `  ${item} \\` : `  ${item}`)),
    '_i=0',
    'while [ "$_i" -lt "$_orig_argc" ]; do',
    '  set -- "$@" "$1"',
    '  shift',
    '  _i=$((_i + 1))',
    'done',
    `cd ${shellQuote(layout.dshDesktopDir)}`,
    'exec /usr/bin/env -i "$@"',
    '',
  ].join('\n')
}

function writeLocalReadme(layout, ports, pluginDir) {
  const content = [
    '超级玩家 · 本地临时桌面版（开发验证壳，非可分发安装包）',
    '============================================================',
    `临时根目录 (root): ${layout.root}`,
    `应用包 (bundle): ${layout.bundle}`,
    `Bundle ID: ${layout.bundleId}`,
    `启动可执行文件: ${layout.executable}`,
    `只读 DSH 仓库: ${layout.dshRoot}`,
    `插件包来源: ${pluginDir}`,
    `独立 Runtime Project: ${layout.runtimeProject}`,
    `独立 DSH_HOME: ${layout.home}`,
    `独立 DSH_AGENTS_HOME: ${layout.agentsHome}`,
    `独立 userData: ${layout.userData}`,
    `独立积木库 (SUPERAGENT_DSH_BLOCKS_HOME): ${layout.blocksHome}`,
    `独立默认工作区 (SUPERAGENT_PROJECT): ${layout.project}`,
    `独立日志目录: ${layout.logs}`,
    `${layout.selfUse ? '自用版正式' : '测试专用'}钥匙串服务名 (SUPERAGENT_KEYCHAIN_SERVICE): ${layout.keychainService}`,
    `钥匙串条目 Ref (SUPERAGENT_KEYCHAIN_TEST_REF): ${layout.keychainTestRef || '（自用版不设，用正式默认名）'}`,
    `调试端口: main=${ports.mainInspectPort}, renderer=${ports.rendererDebugPort}, host=${ports.hostInspectPort}`,
    '',
    '启动方式（任选其一）：',
    `  ${shellQuote(layout.executable)}`,
    `  open -n ${shellQuote(layout.bundle)}`,
    '',
    '说明：',
    '1. 本产物是依赖本机 dsh-base 与 super-agent 仓库的本地临时开发壳，未安装到 /Applications，也不是可分发安装包。',
    '2. 打开后直接进入超级玩家首次配置，不弹 DSH 欢迎页（第 16 项补丁，SUPERAGENT_DESKTOP=1）。',
    layout.selfUse
      ? '3. 自用版：Key 存正式钥匙串 com.aisoup.superagent（含从旧版复制来的 legacy:* 模型 Key）；不读写旧版钥匙串条目和旧版积木库。不会注册 dsh:// 默认协议，不会开启自动更新。'
      : '3. 测试版：只用测试专用钥匙串条目，不读写真实 Keychain 或旧版积木库。不会注册 dsh:// 默认协议，不会开启自动更新。',
    layout.selfUse ? '4. 自用版放在长期位置，不要随手删除（里面有新版的设置和对话）。' : `4. 清理本临时产物可执行: rm -rf ${shellQuote(layout.root)}`,
    '',
  ].join('\n')
  writeFileNoFollow(layout.root, layout.readmePath, content, 0o600)
}

/**
 * 在安全的临时根目录下制作可本地运行的临时桌面版“超级玩家.app”，返回产物与启动命令信息（不自动打开）。
 */
export async function prepareLocalDesktop(options = {}) {
  const layout = desktopLayout(options)
  const repoRoot = resolve(options.repoRoot ?? DEFAULT_REPO_ROOT)
  const localPluginDir = resolve(options.pluginDir ?? DEFAULT_PLUGIN_CORE_DIR)
  const brandPng = join(repoRoot, 'docs', '借鉴DSH', '页面对齐', 'aisoup-碗-反白.png')

  // 先检查必需只读依赖是否存在
  assertExists(join(layout.dshDesktopDir, 'lib', 'main.js'), 'DSH desktop lib/main.js')
  assertExists(join(layout.dshRoot, 'apps', 'desktop-host', 'lib', 'index.js'), 'DSH desktop-host lib/index.js')
  assertExists(layout.pnpmEntry, 'DSH desktop pnpm.mjs')
  assertExists(layout.primaryRuntimeDir, 'DSH primary-runtime')
  assertExists(brandPng, '品牌原图 aisoup-碗-反白.png')

  const electronBinaryLink = join(layout.dshDesktopDir, 'node_modules', 'electron', 'dist', 'Electron.app')
  assertExists(electronBinaryLink, 'Electron.app')
  const sourceElectronApp = realpathSync(electronBinaryLink)
  const pluginDir = selectPluginPackageDir(layout.dshRoot, localPluginDir)

  // 分配不会与旧 App 冲突的本地空闲端口
  const usedPorts = new Set()
  const ports = {
    mainInspectPort: await allocateFreePort(layout.mainInspectPort, usedPorts),
    rendererDebugPort: await allocateFreePort(layout.rendererDebugPort, usedPorts),
    hostInspectPort: await allocateFreePort(layout.hostInspectPort, usedPorts),
  }

  // 在任何写入前再完整校验一遍目标布局无符号链接逃逸，然后写入自家非敏感标记
  assertLayoutPathsSafe(layout)
  ensureDirWithinRoot(layout.root, layout.root, 0o700)
  writeJsonNoFollow(layout.root, join(layout.root, OWN_MARKER_FILENAME), {
    owner: OWN_MARKER_TYPE,
    bundleId: layout.bundleId,
    keychainService: layout.keychainService,
    keychainTestRef: layout.keychainTestRef,
  })

  for (const dir of [layout.home, layout.agentsHome, layout.workspace, layout.userData, layout.blocksHome, layout.logs]) {
    ensureDirWithinRoot(layout.root, dir, 0o700)
  }

  prepareRuntimeProject(layout, pluginDir)
  prepareDesktopProfile(layout, pluginDir)
  prepareBootstrap(layout)

  // 复制 Electron.app 到临时 bundle 并替换名称、Bundle ID、协议、图标、启动脚本与 ad-hoc 签名
  assertPathWithinRoot(layout.root, layout.bundle, 'dir')
  rmSync(layout.bundle, { recursive: true, force: true })
  execFileSync('/usr/bin/ditto', [sourceElectronApp, layout.bundle], { stdio: 'pipe' })

  const icnsDest = join(layout.bundle, 'Contents', 'Resources', 'electron.icns')
  const plistPath = join(layout.bundle, 'Contents', 'Info.plist')
  assertPathWithinRoot(layout.root, icnsDest, 'file')
  assertPathWithinRoot(layout.root, plistPath, 'file')
  assertPathWithinRoot(layout.root, layout.executable, 'file')

  buildIcnsFromPng(brandPng, icnsDest, layout.root)

  const plistUpdates = {
    CFBundleIdentifier: layout.bundleId,
    CFBundleName: APP_DISPLAY_NAME,
    CFBundleDisplayName: APP_DISPLAY_NAME,
    CFBundleExecutable: LAUNCHER_EXECUTABLE_NAME,
    CFBundleIconFile: 'electron.icns',
    CFBundleURLTypes: [
      {
        CFBundleURLName: 'SuperAgent Local',
        CFBundleURLSchemes: [`superagent-local-${layout.rootHash}`],
        CFBundleTypeRole: 'Viewer',
      },
    ],
  }
  for (const [key, value] of Object.entries(plistUpdates)) {
    execFileSync('/usr/bin/plutil', ['-replace', key, '-json', JSON.stringify(value), plistPath], { stdio: 'pipe' })
  }

  writeFileNoFollow(layout.root, layout.executable, buildLauncherScript(layout, ports), 0o755)
  writeLocalReadme(layout, ports, pluginDir)

  execFileSync('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', layout.bundle], { stdio: 'pipe' })

  return {
    layout: { ...layout, ...ports },
    pluginDir,
    launchCommands: {
      executable: shellQuote(layout.executable),
      open: `open -n ${shellQuote(layout.bundle)}`,
    },
  }
}

function parseCliArgs(argv) {
  const options = {}
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--help' || arg === '-h') {
      return { help: true }
    }
    if (arg === '--self-use') {
      options.selfUse = true
      continue
    }
    if (arg === '--root' || arg === '--dsh-root' || arg === '--project') {
      const value = argv[i + 1]
      if (value === undefined || value.startsWith('--')) {
        throw new Error(`local-desktop: 参数 ${arg} 缺少目录值`)
      }
      i += 1
      if (arg === '--root') options.root = value
      else if (arg === '--dsh-root') options.dshRoot = value
      else if (arg === '--project') options.project = value
      continue
    }
    throw new Error(`local-desktop: 不支持的参数 ${arg}`)
  }
  return { help: false, options }
}

function isMainModule() {
  const entry = process.argv[1]
  if (!entry) return false
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === realpathSync(entry)
  } catch {
    return false
  }
}

if (isMainModule()) {
  ;(async () => {
    const parsed = parseCliArgs(process.argv.slice(2))
    if (parsed.help) {
      process.stdout.write(
        [
          '用法: node dsh-plugin/core/scripts/local-desktop.mjs [--self-use | --root <tmpDir>] [--dsh-root <dir>] [--project <dir>]',
          '  --self-use  自用版：放 ~/Library/Application Support/超级玩家，用正式钥匙串名字（第 17 项）',
          '',
          '说明:',
          '  --root      临时根目录（必须位于系统 tmpdir 或 /private/tmp 下；省略时自动新建 mkdtemp 目录）',
          '  --dsh-root  只读 DSH 仓库目录（默认 ../dsh-base）',
          '  --project   SUPERAGENT_PROJECT 初始项目路径（默认 <root>/home/workspaces）',
          '',
        ].join('\n'),
      )
      return
    }
    const result = await prepareLocalDesktop(parsed.options)
    process.stdout.write(
      [
        `临时桌面版“超级玩家”已就绪（本地开发壳，非可分发安装包，未自动启动）:`,
        `  bundle:            ${result.layout.bundle}`,
        `  bundleId:          ${result.layout.bundleId}`,
        `  root:              ${result.layout.root}`,
        `  home (DSH_HOME):   ${result.layout.home}`,
        `  userData:          ${result.layout.userData}`,
        `  blocksHome:        ${result.layout.blocksHome}`,
        `  logs:              ${result.layout.logs}`,
        `  keychainService:   ${result.layout.keychainService}`,
        `  keychainTestRef:   ${result.layout.keychainTestRef}`,
        `  readme:            ${result.layout.readmePath}`,
        `  启动命令 (CLI):     ${result.launchCommands.executable}`,
        `  启动命令 (macOS):   ${result.launchCommands.open}`,
        '',
      ].join('\n'),
    )
  })().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  })
}
