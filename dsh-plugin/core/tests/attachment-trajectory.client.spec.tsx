// @vitest-environment jsdom
import { cleanup,fireEvent,render,screen } from '@testing-library/react'
import { afterEach,expect,it,vi } from 'vitest'
import { TaskTrajectory } from '../src/client/TaskTrajectory.tsx'
afterEach(cleanup)
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII='
it('追加要求行可点开原图，看完可关闭，既有原文与统计保留',async()=>{
 const name='202611161030000001.png',cardAttachment=vi.fn(async()=>({mediaType:'image/png',data:png}))
 const props:any={sessionId:'test',cardOfSession:async()=> 'CARD-001',trajectory:async()=>({rows:[{id:'a',time:'10:30',actor:'你',summary:'追加要求：看截图修改',seconds:null,usage:null,raw:'看图修改原话',attachments:[{name,mediaType:'image/png'}]}],distribution:[]}),cardAttachment}
 render(<TaskTrajectory {...props}/>);
 fireEvent.click(await screen.findByRole('button',{name:'查看截图 1'}))
 expect((await screen.findByRole('img',{name:'追加要求截图'})).getAttribute('src')).toBe('data:image/png;base64,'+png)
 expect(cardAttachment).toHaveBeenCalledWith('CARD-001',name)
 fireEvent.click(screen.getByRole('button',{name:'关闭截图'}))
 expect(screen.queryByRole('img',{name:'追加要求截图'})).toBeNull()
 expect(screen.getByRole('button',{name:'看原文'})).toBeTruthy()
})
