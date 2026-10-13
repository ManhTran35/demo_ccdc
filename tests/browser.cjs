/* End-to-end checks. npm ci; python scripts/build_html.py; npm run test:browser */
const {chromium}=require('@playwright/test');
const XLSX=require('../assets/xlsx.full.min.js');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const http=require('node:http');

(async()=>{
  const html=path.resolve(process.argv[2]||'dist/dashboard_ccdc.html');
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ccdc-browser-'));
  const headers=['STT','Tên VTTB','Mã VTTB','ĐVT','Chủng loại','Serial','Số lượng','Đơn vị quản lý','Chi nhánh mới','Ngày đưa vào sử dụng','Tình trạng','Mã đơn vị'];
  const rows=[
    [1,'Máy in thử','0007','cái','Thiết bị','0001',1,'D1','A','20/03/2025','Đang sử dụng','001'],
    [2,'Vật tư lạ','0008','m','Vật tư','0002',2,'D2','A','','Tốt','002'],
    [3,'MÁY IN THỬ','0007','cái','Thiết bị','0001',1,'D3','B','20/03/2027','Hỏng','003'],
    [4,'Máy rà soát','0009','cái','Thiết bị','0003',1,'D4','B','31/02/2024','Đang sử dụng','004']
  ];
  function writeExcel(file,data){const wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet(data),'Sheet1');fs.writeFileSync(path.join(temp,file),Buffer.from(XLSX.write(wb,{type:'array',bookType:'xlsx'})));return path.join(temp,file);}
  const good=writeExcel('good.xlsx',[headers,...rows]);
  const badRows=JSON.parse(JSON.stringify(rows));badRows[0][6]=-1;
  const bad=writeExcel('bad.xlsx',[headers,...badRows]);
  const missing=writeExcel('missing.xlsx',[['Tên VTTB','Mã VTTB'],['a','0001']]);
  const mappingFile=writeExcel('mapping.xlsx',[
    ['Tên VTTB gốc','Loại VTTB','Nhóm lớn','Trạng thái rà soát'],
    ['Máy in thử','Máy in','CNTT','Đã phân loại theo tên'],
    [' MÁY IN THỬ ','Máy in','CNTT','Đã phân loại theo tên'],
    ['Máy rà soát','Máy tính','CNTT','Cần rà soát']
  ]);
  const server=http.createServer((req,res)=>{res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});fs.createReadStream(html).pipe(res);});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  let browser;const errors=[],network=[];let checks=0;
  const pass=label=>{checks++;console.log('PASS',label)};
  try{
    const executable=process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined);
    browser=await chromium.launch({...(executable?{executablePath:executable}:{}),headless:true,args:['--no-sandbox']});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url())&&!r.url().startsWith(origin))network.push(r.url());});
    const start=Date.now();await page.goto(origin);await page.waitForFunction(()=>window.CCDC?.ready,{timeout:60000});
    const initial=await page.evaluate(()=>({metrics:CCDC.getMetrics(CCDC.rows),report:CCDC.report}));
    assert.equal(initial.metrics.rows,91955);assert.equal(initial.metrics.using_codes,3911);assert.equal(initial.metrics.material_missing_codes,577);assert.equal(initial.metrics.device_rows,58290);assert.equal(initial.metrics.broken_rows,20);assert.equal(initial.report.joined_rows,91955);assert.equal(initial.report.review_names,12);
    pass('Actual 91,955 rows / KPI / quality parity with Python; ready '+(Date.now()-start)+' ms');
    await page.evaluate(()=>{const c=CCDC;for(const [m,index] of [[11,0],[12,1],[35,1],[36,2],[59,2],[60,3],[71,3],[72,4],[95,4],[96,5]]){const groups=['Dưới 12 tháng','12–35 tháng','36–59 tháng','60–71 tháng','72–95 tháng','Từ 96 tháng trở lên'];if(c.ageGroup(m)!==groups[index])throw Error('Age '+m);}if(c.fullMonths('2025-03-20','2026-03-19')!==11||c.fullMonths('2025-03-20','2026-03-20')!==12||c.fullMonths('2027-01-01','2026-01-01')!==null)throw Error('Full months');if(c.parseDate('31/02/2024').date||c.parseDate(0).status!=='Ngày không hợp lệ')throw Error('Invalid date');});
    pass('Browser month boundaries, future, invalid and missing date rules');
    await page.locator('#as-of').fill('2026-10-12');await page.locator('#as-of').dispatchEvent('change');assert.equal(await page.locator('#no-version').isVisible(),true);assert.equal(await page.locator('#overview-page').isVisible(),false);pass('No future snapshot before first cutoff');
    await page.locator('#reset').click();await page.locator('#branch-filter summary').click();await page.locator('#branch-filter input[value="HCM"]').check();
    const branch=await page.evaluate(()=>({rows:CCDC.filtered.length,valid:CCDC.filtered.every(r=>r['Chi nhánh']==='HCM'),managerOptions:[...document.querySelectorAll('#manager-filter .options input')].map(e=>e.value),allowed:[...new Set(CCDC.rows.filter(r=>r['Chi nhánh']==='HCM').map(r=>r['Đơn vị quản lý']))]}));assert(branch.valid);assert.equal(branch.rows,16639);assert(branch.managerOptions.every(v=>branch.allowed.includes(v)));pass('Branch filter and dependent manager options');
    await page.locator('#status-filter summary').click();await page.locator('#status-filter input[value="Hỏng"]').check();assert.equal(await page.locator('#kpi-using').innerText(),'0');assert((await page.locator('#age-chart').innerText()).includes('Không có bản ghi'));pass('Status filter updates KPI and explains empty age chart');
    await page.locator('#reset').click();await page.locator('[data-issue="Serial nghi trùng"]').click();assert.equal(await page.evaluate(()=>CCDC.filtered.length),339);pass('Quality indicator filters the same common dataset');await page.locator('#reset').click();
    await page.locator('[data-page="details"]').click();await page.locator('#next-page').click();assert.equal(await page.locator('#page-number').innerText(),'2 / 920');assert.equal(await page.locator('#details-table tbody tr').count(),100);pass('Detail pagination across full dataset');
    await page.locator('#search').fill('267961');await page.waitForFunction(()=>CCDC.state.search==='267961');assert.equal(await page.evaluate(()=>CCDC.filtered.length),521);pass('Global search matches Python 521-row fixture');
    async function download(selector,name){const p=page.waitForEvent('download',{timeout:90000});await page.locator(selector).click();const d=await p;const target=path.join(temp,name);await d.saveAs(target);return target;}
    const csv=await download('#csv-export','filtered.csv');const csvText=fs.readFileSync(csv,'utf8');assert(csvText.includes('Ngày chốt dữ liệu'));assert(csvText.includes('Ngày tính tuổi sử dụng'));assert.equal(csvText.split('\r\n').length,522);pass('CSV exports all filtered rows and both dates');
    const excel=await download('#excel-export','filtered.xlsx');let wb=XLSX.read(fs.readFileSync(excel),{type:'buffer'}),data=XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]],{header:1});assert.equal(data.length,522);assert.equal(data[1][2],'267961');assert.equal(data[1].at(-2),'2026-10-13');pass('Excel worker exports same 521 filtered rows');
    await page.locator('#reset').click();const full=await download('#excel-export','full.xlsx');wb=XLSX.read(fs.readFileSync(full),{type:'buffer'});const range=XLSX.utils.decode_range(wb.Sheets[wb.SheetNames[0]]['!ref']);assert.equal(range.e.r,91955);pass('Full 91,955-row Excel export completes');
    await page.locator('[data-page="admin"]').click();await page.locator('#role').selectOption('Người duyệt');await page.locator('[data-admin="mapping"]').click();await page.locator('#mapping-file').setInputFiles(mappingFile);await page.locator('#inspect-mapping').click();await page.locator('#save-mapping').waitFor({timeout:30000});await page.locator('#save-mapping').click();await page.waitForTimeout(100);pass('Classification import with duplicate exact reference');
    await page.locator('[data-admin="upload"]').click();await page.locator('#role').selectOption('Người nhập');await page.locator('#upload-cutoff').fill('2026-10-12');await page.locator('#upload-file').setInputFiles(good);await page.locator('#inspect-file').click();await page.locator('[data-publish]').first().waitFor({timeout:30000});assert(await page.locator('[data-publish]').first().isDisabled());assert.equal(await page.evaluate(()=>CCDC.rows.length),91955);pass('Upload previews retain current data; importer cannot publish');
    await page.locator('#role').selectOption('Người duyệt');await page.locator('[data-publish]').first().click();await page.waitForFunction(()=>CCDC.rows.length===4);const fixtureMetrics=await page.evaluate(()=>({m:CCDC.getMetrics(CCDC.rows),r:CCDC.report}));assert.equal(fixtureMetrics.m.device_rows,3);assert.equal(fixtureMetrics.m.broken_rows,1);assert.equal(fixtureMetrics.r.unclassified_names,1);assert.equal(fixtureMetrics.r.review_names,2);assert.equal(fixtureMetrics.r.duplicate_serial_rows,2);pass('Reviewer publishes full four-row snapshot without accumulation');
    await page.locator('[data-page="details"]').click();const zeroExcel=await download('#excel-export','zero.xlsx');wb=XLSX.read(fs.readFileSync(zeroExcel),{type:'buffer'});data=XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]],{header:1});assert.equal(data.length,5);assert.equal(data[1][2],'0007');assert.equal(wb.Sheets[wb.SheetNames[0]].C2.t,'s');assert.equal(data[2][9],'Chưa xác định');pass('Excel preserves leading zeros and unknown ages');
    await page.locator('[data-page="admin"]').click();await page.locator('#upload-cutoff').fill('2026-10-14');await page.locator('#upload-file').setInputFiles(bad);await page.locator('#inspect-file').click();await page.waitForFunction(()=>CCDC.versions.some(v=>v.name==='bad.xlsx'));assert(await page.locator('[data-publish]').first().isDisabled());assert.equal(await page.evaluate(()=>CCDC.rows.length),4);pass('Invalid quantity blocks publication and preserves active snapshot');
    await page.locator('#upload-file').setInputFiles(missing);await page.locator('#inspect-file').click();await page.waitForFunction(()=>CCDC.versions.some(v=>v.name==='missing.xlsx'));assert.equal(await page.evaluate(()=>CCDC.rows.length),4);assert((await page.locator('#pending-list').innerText()).includes('File thiếu cột'));pass('Missing required columns retain original and current version');
    await page.locator('[data-admin="history"]').click();const publishedId=await page.evaluate(()=>CCDC.currentVersion.id);const originalPromise=page.waitForEvent('download');await page.locator(`[data-original="${publishedId}"]`).click();const original=await originalPromise;const originalPath=path.join(temp,'original.xlsx');await original.saveAs(originalPath);assert.deepEqual(fs.readFileSync(originalPath),fs.readFileSync(good));pass('Original upload bytes preserved exactly');
    const seedId=await page.evaluate(()=>CCDC.versions.find(v=>v.name==='file ccdc.xlsx').id);await page.locator(`[data-restore="${seedId}"]`).click();await page.waitForFunction(()=>CCDC.rows.length===91955);assert.equal(await page.evaluate(()=>CCDC.versions.filter(v=>v.published_at).length),3);pass('Restoration retains history and full snapshot');
    await page.reload();await page.waitForFunction(()=>window.CCDC?.ready,{timeout:60000});assert.equal(await page.evaluate(()=>CCDC.rows.length),91955);assert.equal(await page.evaluate(()=>CCDC.versions.length),5);pass('IndexedDB versions survive reload');
    await page.locator('[data-page="admin"]').click();await page.locator('#role').selectOption('Người duyệt');await page.locator('[data-admin="history"]').click();const backup=await download('#backup-export','backup.json.gz');assert(fs.statSync(backup).size>1e6);pass('Compressed history backup includes originals');
    const isolated=await browser.newContext();const fresh=await isolated.newPage();await fresh.goto(origin);await fresh.waitForFunction(()=>window.CCDC?.ready,{timeout:60000});await fresh.locator('[data-page="admin"]').click();await fresh.locator('#role').selectOption('Người duyệt');await fresh.locator('[data-admin="history"]').click();await fresh.locator('#backup-import').setInputFiles(backup);await fresh.waitForFunction(()=>CCDC.versions.length===5,{timeout:60000});assert.equal(await fresh.evaluate(()=>CCDC.rows.length),91955);await isolated.close();pass('Backup imports into a fresh browser without accumulation');
    assert.deepEqual(errors,[]);assert.deepEqual(network,[]);pass('No JS exceptions or external network requests');
    await page.locator('[data-page="overview"]').click();await page.screenshot({path:path.join(path.dirname(html),'screenshot.png'),fullPage:true});
    console.log(`BROWSER: ${checks} check groups passed`);
  }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
