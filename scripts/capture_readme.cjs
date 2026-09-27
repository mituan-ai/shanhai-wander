// Uses a local demonstration account and real pages, without changing their content.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.BASE_URL||'http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:960},deviceScaleFactor:1});
 const out=path.resolve('assets/readme');
 fs.mkdirSync(out,{recursive:true});
 fs.mkdirSync('test-results',{recursive:true});
 const username='漫游旅人_'+Date.now().toString(36).slice(-4);
 page.on('dialog',d=>d.accept());
 try {
  await page.goto(base+'/routes/');
  await page.locator('.route-card img').evaluateAll(images=>images.forEach(image=>image.loading='eager'));
  await page.waitForFunction(()=>[...document.querySelectorAll('.route-card img')].every(image=>image.complete&&image.naturalWidth>0));
  await page.setViewportSize({width:1280,height:1060});
  await page.evaluate(()=>window.scrollTo(0,280));
  await page.screenshot({path:path.join(out,'route-library.png')});
  await page.setViewportSize({width:1280,height:960});
  await page.goto(base+'/accounts/register/');
  const password='Wander!'+crypto.randomUUID();
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password1]').fill(password);
  await page.locator('[name=password2]').fill(password);
  await page.getByRole('button',{name:'创建账号，出发 →'}).click();
  await page.waitForURL('**/my-trips/');
  fs.writeFileSync('test-results/readme-account.json',JSON.stringify({username}));
  await page.goto(base+'/routes/?q='+encodeURIComponent('皖南'));
  await page.locator('.route-title').first().click();
  await page.getByRole('link',{name:'用这条路线开始规划 ↗'}).click();
  await page.locator('#trip-title').fill('皖南三日 · 山水慢游');
  await page.locator('#trip-description').fill('把周末交给山水。第一天看青龙湾，第二天慢慢走六道湾，最后留一段时间，在水边喝杯茶。');
  await page.locator('#trip-start-date').fill('2026-10-02');
  await page.locator('#trip-mode').selectOption('ev');
  await page.locator('#trip-budget').fill('1800');
  async function stop(kind,name,lng,lat,note){
   await page.locator('#add-stop').click();await page.locator(`[data-kind=${kind}]`).click();
   if(!await page.locator('#manual-place-form [name=name]').isVisible())await page.locator('.manual-place summary').click();
   await page.locator('#manual-place-form [name=name]').fill(name);
   await page.locator('#manual-place-form [name=lng]').fill(String(lng));await page.locator('#manual-place-form [name=lat]').fill(String(lat));
   await page.locator('#manual-place-form [name=note]').fill(note);await page.getByRole('button',{name:'添加到当天行程 ＋'}).click();
  }
  await stop('hotel','青龙湾附近 · 住宿待定',118.555,30.485,'在青龙湾附近选一间喜欢的民宿，预订前确认入住条件。');
  await page.locator('[data-day="2"]').click();
  await stop('charging','泾县附近 · 补能待确认',118.41,30.69,'出发前通过高德确认充电站、接口与可用桩。');
  await page.locator('[data-day="1"]').click();
  await page.locator('#save-trip').click();await page.getByText('已保存到账号',{exact:true}).waitFor();
  await page.locator('#calculate-trip').click();await page.getByText('当前结果为直线估算，详细说明见地图下方。',{exact:true}).waitFor();
  await page.locator('#toast').waitFor({state:'hidden'});
  await page.locator('.planner-shell').screenshot({path:path.join(out,'trip-planner.png')});
  await page.locator('#view-trip').click();await page.getByRole('heading',{name:'皖南三日 · 山水慢游',exact:true}).waitFor();
  await page.locator('.trip-detail-page').screenshot({path:path.join(out,'saved-trip.png')});
  console.log(JSON.stringify({username,files:['route-library.png','trip-planner.png','saved-trip.png']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
