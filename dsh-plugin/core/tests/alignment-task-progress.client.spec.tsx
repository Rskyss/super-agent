// @vitest-environment jsdom
import {cleanup,render,screen,within} from '@testing-library/react'
import {afterEach,expect,it} from 'vitest'
import {parseProgressView} from '../src/progress-model.ts'
import {ProgressPanel} from '../src/client/ProgressPanel.tsx'
afterEach(cleanup)
const view=(running:boolean|undefined,code='planning')=>JSON.stringify({isRunningNow:running,workElapsed:12,card:{id:'CARD-022',title:'加问候',state:'待对齐',phase:{code,label:'等待写方案',needs_you:false}},stations:[{key:'disp',who:'Codex',model:'受控模型',state:'active',detail:'正在写方案',elapsed:12}]})
it.each([false,undefined])('实际未确认进程在运行（%s）时七步不能被phase或旧station强制成转圈',(running)=>{
 expect(parseProgressView(view(running),'CARD-022').steps.some(s=>s.state==='active')).toBe(false)
})
it('真实运行的阶段间隙仍保留一个当前步骤，等待验收时不凭历史live转圈',()=>{
 const running=JSON.parse(view(true,'running'));running.stations=[]
 expect(parseProgressView(JSON.stringify(running),'CARD-022').steps.filter(s=>s.state==='active')).toHaveLength(1)
 const waiting=JSON.parse(view(false,'review'));waiting.card.phase.needs_you=true
 expect(parseProgressView(JSON.stringify(waiting),'CARD-022').steps.some(s=>s.state==='active')).toBe(false)
})
it('右栏头按七步进度展示，不露原页签卡号与刷新说明',async()=>{
 render(<ProgressPanel card="CARD-022" view={async()=>({ok:true,value:view(false)})}/> )
 expect(await screen.findByText(/进度 · 第 \d 步 \/ 共 7 步/)).toBeTruthy()
 expect(within(screen.getByRole('list')).getAllByRole('listitem')).toHaveLength(7)
 expect(screen.queryByText('每 2 秒自动刷新')).toBeNull()
 expect(screen.queryByText('CARD-022',{exact:true})).toBeNull()
})
it('停下时复查步骤显示真实三轮次数，不用事件round重置值',()=>{
 const raw={isRunningNow:false,card:{id:'CARD-022',state:'需Claude介入',phase:{code:'blocked',needs_you:true}},stopReviews:[{round:1,found:'甲',changed:'改甲'},{round:2,found:'乙',changed:'改乙'},{round:3,found:'丙',changed:'无修改记录'}],stations:[{key:'review',who:'Codex',state:'fail',detail:'三轮没过'}]}
 expect(parseProgressView(JSON.stringify(raw),'CARD-022').steps.find(s=>s.key==='rev')?.label).toBe('复查 · 3 / 3 轮')
})
