// @vitest-environment jsdom
/// <reference types="vite/client" />
import { expect,it } from 'vitest'
const modules=import.meta.glob('../src/client/revision-images.ts',{eager:true}) as Record<string,Record<string,any>>
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII='
function encode(files:File[]){const fn=modules['../src/client/revision-images.ts']?.encodeRevisionImages;expect(fn,'缺少从原生草稿File读取原图的转换器').toBeTypeOf('function');return fn(files)}
it('读浏览器File原字节，不上传用户文件名、不压缩原图',async()=>{
 const bytes=Uint8Array.from(atob(png),c=>c.charCodeAt(0))
 expect(await encode([new File([bytes],'../../用户原名.png',{type:'image/png'})])).toEqual([{mediaType:'image/png',data:png}])
})
it.each(['image/gif','image/svg+xml','text/plain'])('不接收%s，不能降级只发文字',async type=>{
 await expect(encode([new File(['not-png'],'x',{type})])).rejects.toThrow(/PNG|JPG|截图|图片/)
})
it('单图超过10MiB立即拒绝，保留给调用者处理草稿',async()=>{
 await expect(encode([new File([new Uint8Array(10*1024*1024+1)],'big.png',{type:'image/png'})])).rejects.toThrow(/10|大小|大/)
})
