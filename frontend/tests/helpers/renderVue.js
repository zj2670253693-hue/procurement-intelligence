import {readFileSync} from 'node:fs'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'

export async function renderVue(url,props={},components={}){
  const {descriptor}=parse(readFileSync(url,'utf8'))
  const require=createRequire(import.meta.url)
  const compiled=compileScript(descriptor,{id:'actual-template-test',inlineTemplate:true,
    templateOptions:{ssr:true}}).content
  const code=compiled.replace(/from (['"])([^'"]+)\1/g,(whole,quote,specifier)=>
    'from '+JSON.stringify(specifier.startsWith('.')?new URL(specifier+'.js',url).href:pathToFileURL(require.resolve(specifier)).href))
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
  const app=createSSRApp(Component,props)
  for(const [name,value] of Object.entries(components))app.component(name,value)
  return renderToString(app)
}
