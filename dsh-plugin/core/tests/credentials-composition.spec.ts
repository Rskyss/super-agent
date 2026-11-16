import {readFileSync} from 'node:fs'
import {expect,it} from 'vitest'
import {createRequire} from 'node:module'
// 通过实际加载器依赖的 YAML 版本解析，不给产品新增依赖。
const {parse}=createRequire(new URL('../../../../packages/credentials/credentials-local/package.json',import.meta.url))('yaml')
import {applyEntryPatches} from '@deepseek-ai/cordis-plugin-include'
it('实际覆盖规则下只挂载新版凭据工厂，原文件提供方被关闭',()=>{const patches=parse(readFileSync(new URL('../cordis.patch.yml',import.meta.url),'utf8'),{customTags:[{tag:'tag:yaml.org,2002:js',resolve:(s:string)=>s}]});const tree=applyEntryPatches([{id:'credentials',name:'@deepseek-ai/dsh-credentials-local'}],patches,()=>{});const active=tree.filter(e=>!e.disabled && [ '@deepseek-ai/dsh-credentials-local','@superagent/core/credentials'].includes(e.name as string));expect(active.map(e=>e.name)).toEqual(['@superagent/core/credentials'])})
