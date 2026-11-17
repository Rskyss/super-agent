// 第七批 7-4：输入框“+”旁边写“附件 / 截图”（对照 01）。样子靠样式表，这里查规则在、只作用在我们包住的输入条里。
import {readFileSync} from 'node:fs'
import {dirname,join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {expect,it} from 'vitest'
const css=readFileSync(join(dirname(fileURLToPath(import.meta.url)),'..','src','client','HomeChrome.module.css'),'utf8')
it('“+”旁边有“附件 / 截图”，只在超级玩家的输入条里生效',()=>{
  expect(css).toMatch(/\.composerWrap button\[aria-haspopup='listbox'\]::after\s*\{\s*content:\s*'附件 \/ 截图';/)
  expect(css).toMatch(/\.composerWrap button\[aria-haspopup='listbox'\]\s*\{[^}]*width:\s*auto/)
})
