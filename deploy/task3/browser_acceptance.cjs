// REAL TLS browser probe. No mock routes, no paid requests, no stored credentials.
// Usage: TASK3_ACCEPT_ORIGIN=https://... TASK3_ACCEPT_DATASET=up_... \
// TASK3_ACCEPT_USERNAME=... TASK3_ACCEPT_PASSWORD=... node browser_acceptance.cjs
// Requires Playwright supplied by the operator. stdout contains timings/IDs only.
const {chromium}=require('playwright');
const kinds=['buyer_suppliers','buyer_bidders','supplier_peers','common_buyers','common_projects'];
async function main(){
  const origin=new URL(process.env.TASK3_ACCEPT_ORIGIN||'');
  if(origin.protocol!=='https:' || origin.username || origin.password || origin.pathname!=='/' || origin.search || origin.hash)throw Error('Public HTTPS origin required');
  const dataset=process.env.TASK3_ACCEPT_DATASET;
  if(!/^up_[a-f0-9]{32}$/.test(dataset||''))throw Error('Owned uploaded acceptance dataset required');
  const browser=await chromium.launch({headless:true,...(process.env.TASK3_ACCEPT_BROWSER_CHANNEL?{channel:process.env.TASK3_ACCEPT_BROWSER_CHANNEL}:{})});
  try{
    const context=await browser.newContext({ignoreHTTPSErrors:false,viewport:{width:1440,height:1000}});
    const page=await context.newPage(),errors=[],samples=[];
    page.on('pageerror',()=>errors.push('page_runtime_error'));
    await page.goto(origin.origin+'/login');
    await page.locator('input[autocomplete="username"]').fill(process.env.TASK3_ACCEPT_USERNAME||'');
    await page.locator('input[autocomplete="current-password"]').fill(process.env.TASK3_ACCEPT_PASSWORD||'');
    await page.getByRole('button',{name:'登录',exact:true}).click();
    await page.waitForURL(url=>url.pathname==='/task3');
    const cookies=await context.cookies();
    if(!cookies.some(cookie=>cookie.name==='t3_session' && cookie.httpOnly && cookie.secure && cookie.sameSite==='Strict'))throw Error('Secure session not established');
    // First navigation is cold; repeat route navigation is warm. These are not
    // three simultaneous-user or memory/cgroup acceptance by themselves.
    for(const phase of ['cold','hot']){
      await page.goto(origin.origin+'/task2?dataset_id='+dataset);
      await page.locator('.query-form .actor').waitFor();
      for(const [index,kind] of kinds.entries()){
        await page.getByRole('tab').nth(index).click();
        await page.locator('.query-form .actor').click();
        const options=page.locator('.el-select-dropdown:visible .el-select-dropdown__item');
        await options.first().waitFor();
        const needed=kind.startsWith('common')?2:1;
        if(await options.count()<needed)throw Error('Acceptance fixture lacks required query organizations');
        for(let n=0;n<needed;n++)await options.nth(n).click();
        await page.keyboard.press('Escape');
        const count=await page.evaluate(()=>(window.__t3RenderSamples||[]).length);
        await page.getByRole('button',{name:'查询',exact:true}).click();
        await page.waitForFunction(n=>(window.__t3RenderSamples||[]).length>n,count,{timeout:30000});
        const sample=await page.evaluate(()=>window.__t3RenderSamples.at(-1));
        if(sample.dataset_id!==dataset || sample.query_type!==kind)throw Error('Stale/cross-dataset render sample');
        samples.push({...sample,phase});
      }
    }
    if(errors.length)throw Error('Browser runtime errors');
    const averages=Object.fromEntries(kinds.map(kind=>[kind,samples.filter(s=>s.query_type===kind).reduce((sum,s)=>sum+s.elapsed_ms,0)/2]));
    const passed=Object.values(averages).every(value=>value<5000);
    process.stdout.write(JSON.stringify({kind:'real_tls_browser_query_probe',passed,query_samples:samples,averages,
      gates:{tls:true,browser_queries:passed},three_users_processing_verified:false,linux_resource_verified:false,new_model_calls:0})+'\n');
    if(!passed)process.exitCode=1;
  }finally{await browser.close()}
}
main().catch(()=>{process.stderr.write('Task3 browser acceptance failed; inspect private browser/service logs. No credentials printed.\n');process.exitCode=1});
