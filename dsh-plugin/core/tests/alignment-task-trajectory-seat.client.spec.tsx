// @vitest-environment jsdom
import {afterEach,expect,it} from 'vitest'
import {existsSync,readFileSync} from 'node:fs'
import {resolve} from 'node:path'
const cssPath=['src/client/TaskPageChrome.module.css','dsh-plugin/core/src/client/TaskPageChrome.module.css','packages/superagent/core/src/client/TaskPageChrome.module.css'].map(p=>resolve(process.cwd(),p)).find(existsSync)!
const cssText=readFileSync(cssPath,'utf8').replace(/:global\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g,'$1')
afterEach(()=>{document.head.querySelectorAll('[data-alignment-style]').forEach(s=>s.remove());document.body.innerHTML=''})
it('轨迹页按05t隐藏整个输入席位但保留DOM，切回对话恢复，普通会话不受影响',()=>{
 const style=document.createElement('style');style.dataset.alignmentStyle='';style.textContent=cssText;document.head.append(style)
 const root=document.createElement('div');root.className='mainRoot';root.dataset.superagentTaskActive='true'
 const content=document.createElement('div');content.dataset.phase='active'
 const trajectory=document.createElement('div');trajectory.dataset.superagentTrajectory='true'
 const seat=document.createElement('div');seat.dataset.composerSeat='';seat.innerHTML='<textarea aria-label="原草稿">保留</textarea><button>验收</button>'
 content.append(trajectory,seat);root.append(content);document.body.append(root)
 expect(getComputedStyle(seat).display).toBe('none');expect(seat.isConnected).toBe(true)
 trajectory.remove();expect(getComputedStyle(seat).display).not.toBe('none')
 content.prepend(trajectory);delete root.dataset.superagentTaskActive;expect(getComputedStyle(seat).display).not.toBe('none')
})
