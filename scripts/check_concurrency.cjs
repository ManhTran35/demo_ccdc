#!/usr/bin/env node
/* Optional developer check. Requires Playwright and SheetJS outside production.
 * Usage: NODE_PATH=/path/to/dev/node_modules node scripts/check_concurrency.cjs \
 *   --origin http://127.0.0.1:8502 --pid STREAMLIT_PID \
 *   --expected /path/to/concurrency-expected.json --output /path/to/results.json
 * Five independent contexts share a real Streamlit server and Parquet snapshot.
 * This is a local functional/load observation, not an IAP or Cloud Run proof.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const { chromium } = require('playwright');
const XLSX = require('../assets/xlsx.full.min.js');

const args = Object.fromEntries(process.argv.slice(2).reduce((out, arg, i, all) => {
  if (arg.startsWith('--')) out.push([arg.slice(2), all[i + 1]]);
  return out;
}, []));
const origin = args.origin || 'http://127.0.0.1:8502';
const expectedPath = args.expected || '/workspace/outputs/ccdc_cloud/concurrency-expected.json';
const outputPath = args.output || '/workspace/outputs/ccdc_cloud/concurrency-results.json';
const serverPid = Number(args.pid || process.env.CCDC_SERVER_PID);
const expected = JSON.parse(fs.readFileSync(expectedPath, 'utf8'));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ccdc-five-sessions-'));
const started = performance.now();
const seconds = (value) => Number((value / 1000).toFixed(4));
const comma = (value) => Number(value).toLocaleString('en-US');
const branchList = expected.branches || expected.branch_values.map(branch => ({
  branch, rows: expected.branch_counts[branch], metrics: expected.branch_metrics[branch],
}));
assert(branchList.length >= 5, 'Need five distinct branch fixtures');
const results = {
  synthetic_only: true, local_streamlit: true, origin,
  sessions: 5, rows: expected.rows || expected.row_count,
  data_date: expected.data_date, active_version: expected.version_id || expected.active_version,
  started_at_utc: new Date().toISOString(), server_pid: serverPid || null,
  visible_logical_cpus: os.availableParallelism(),
  process_memory: { sampled_peak_rss_mib: 0, kernel_peak_rss_mib: 0, samples: 0 },
  users: [],
  limitations: [
    'Five independent browser sessions on one local Streamlit process with synthetic data.',
    'Local sessions do not establish five real IAP identities, IAM permissions, or deployed access control.',
    'Host CPU/memory are not constrained to Cloud Run 1 vCPU / 2 GiB; observed RSS covers the Streamlit process only.',
    'Timing includes real local browser, Streamlit, filtering, pagination, and five concurrent filtered exports.',
  ],
};

function sampleMemory() {
  if (!serverPid) return;
  try {
    const status = fs.readFileSync(`/proc/${serverPid}/status`, 'utf8');
    const rss = Number(status.match(/^VmRSS:\s+(\d+) kB$/m)?.[1] || 0) / 1024;
    const peak = Number(status.match(/^VmHWM:\s+(\d+) kB$/m)?.[1] || 0) / 1024;
    results.process_memory.sampled_peak_rss_mib = Math.max(results.process_memory.sampled_peak_rss_mib, rss);
    results.process_memory.kernel_peak_rss_mib = Math.max(results.process_memory.kernel_peak_rss_mib, peak);
    results.process_memory.samples += 1;
  } catch (_) { /* A stopped server becomes a test failure through page checks. */ }
}

function kpiTexts(metrics) {
  return [
    comma(metrics.using_codes).replaceAll(',', '.'),
    comma(metrics.material_missing_codes).replaceAll(',', '.'),
    metrics.broken_rate == null ? '—' : Number(metrics.broken_rate).toFixed(3).replace('.', ',') + '%',
  ];
}

async function assertKpis(page, metrics) {
  const wanted = kpiTexts(metrics);
  await page.waitForFunction((values) => {
    const actual = [...document.querySelectorAll('[data-testid="stMetricValue"]')]
      .map(element => element.textContent.trim());
    return JSON.stringify(actual) === JSON.stringify(values);
  }, wanted, { timeout: 180000 });
  assert.deepEqual((await page.locator('[data-testid="stMetricValue"]').allTextContents())
    .map(value => value.trim()), wanted);
}

async function assertCount(page, rows) {
  await page.getByText(new RegExp(`${comma(rows)} / ${comma(results.rows)} bản ghi`))
    .waitFor({ timeout: 180000 });
}

async function assertSnapshot(page) {
  const [year, month, day] = expected.data_date.split('-');
  await page.getByText(new RegExp(`Dữ liệu chốt ngày ${day}/${month}/${year}`)).waitFor();
}

async function assertHealthy(page) {
  assert.equal(await page.locator('[data-testid="stException"]').count(), 0, 'Streamlit exception');
  assert.equal(await page.getByText(/Không thể hoàn tất thao tác dữ liệu/).count(), 0, 'App failed');
}

function inspectExport(file, branch, rows, kind) {
  const workbook = kind === 'csv'
    ? XLSX.read(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''), { type: 'string', raw: true })
    : XLSX.read(fs.readFileSync(file), { type: 'buffer', raw: true, cellDates: false });
  const sheet = workbook.Sheets[workbook.SheetNames[0]];
  const matrix = XLSX.utils.sheet_to_json(sheet, { header: 1, raw: true, defval: '' });
  const header = matrix.shift();
  assert.equal(matrix.length, rows, `${kind} contains every filtered row, not one page`);
  const branchColumn = header.indexOf('Chi nhánh');
  const dateColumn = header.indexOf('Ngày chốt dữ liệu');
  const codeColumn = header.indexOf('Mã VTTB');
  const serialColumn = header.indexOf('Serial');
  assert(branchColumn >= 0 && dateColumn >= 0 && codeColumn >= 0 && serialColumn >= 0,
    `Missing export columns: ${JSON.stringify(header)}`);
  for (const row of matrix) {
    assert.equal(row[branchColumn], branch, `${kind} branch isolation`);
    assert.equal(row[dateColumn], expected.data_date.split('-').reverse().join('/'), `${kind} immutable snapshot date`);
    if (kind === 'xlsx') {
      assert.equal(typeof row[codeColumn], 'string', 'Excel codes retain string type');
      assert.equal(typeof row[serialColumn], 'string', 'Excel serials retain string type');
    }
  }
  return { rows: matrix.length, bytes: fs.statSync(file).size };
}

async function runUser(browser, fixture, index) {
  const userStarted = performance.now();
  const record = { user: index + 1, branch: fixture.branch, page_exceptions: [], timings_seconds: {} };
  const progress = phase => console.log(JSON.stringify({ user: index + 1, phase }));
  results.users.push(record);
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  page.on('pageerror', error => record.page_exceptions.push(error.message));
  let phase = performance.now();
  try {
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await assertKpis(page, expected.metrics);
    progress('initial_kpis_verified');
    await assertCount(page, results.rows);
    await assertSnapshot(page);
    record.timings_seconds.initial_load = seconds(performance.now() - phase);
    record.unfiltered_kpi = kpiTexts(expected.metrics);
    progress('loaded');

    phase = performance.now();
    // Selection changes the combobox's accessible name; retain the widget container.
    const branchInput = page.getByTestId('stSidebar').getByTestId('stMultiSelect')
      .filter({ hasText: 'Chi nhánh' }).getByRole('combobox');
    await branchInput.click();
    await page.getByRole('option', { name: fixture.branch, exact: true }).click();
    await page.keyboard.press('Escape');
    await assertCount(page, fixture.rows);
    await assertKpis(page, fixture.metrics);
    record.timings_seconds.branch_filter = seconds(performance.now() - phase);
    record.filtered_rows = fixture.rows;
    record.filtered_kpi = kpiTexts(fixture.metrics);
    progress('filtered');

    phase = performance.now();
    await page.getByTestId('stSidebar').getByText('Chi tiết dữ liệu', { exact: true }).click();
    const extras = page.getByTestId('stMultiSelect')
      .filter({ hasText: 'Thêm cột dữ liệu / giá trị gốc' }).getByRole('combobox');
    await extras.click();
    // Streamlit virtualizes long option lists: search before selecting Serial.
    await extras.fill('Serial');
    await page.getByRole('option', { name: 'Serial', exact: true }).click();
    await page.keyboard.press('Escape');
    const pager = page.getByRole('spinbutton', { name: 'Trang', exact: true });
    await pager.waitFor();
    await pager.fill('2');
    await pager.press('Enter');
    const pages = Math.ceil(fixture.rows / 100);
    await page.getByText(`Trang 2/${pages}. File tải xuống chứa toàn bộ bản ghi sau lọc, không chỉ trang này.`, { exact: true }).waitFor();
    await assertCount(page, fixture.rows);
    record.timings_seconds.details_and_pagination = seconds(performance.now() - phase);
    record.pagination = { page: 2, pages, page_size: 100 };
    progress('paginated');

    phase = performance.now();
    await page.getByRole('button', { name: 'Chuẩn bị CSV & Excel của dữ liệu sau lọc', exact: true }).click();
    await page.getByRole('button', { name: 'Tải Excel', exact: true }).waitFor();
    record.timings_seconds.prepare_filtered_exports = seconds(performance.now() - phase);
    const exports = {};
    for (const [kind, label] of [['csv', 'Tải CSV'], ['xlsx', 'Tải Excel']]) {
      phase = performance.now();
      const [download] = await Promise.all([
        page.waitForEvent('download'),
        page.getByRole('button', { name: label, exact: true }).click(),
      ]);
      assert.equal(await download.failure(), null);
      const filename = path.join(temp, `user-${index + 1}.${kind}`);
      await download.saveAs(filename);
      exports[kind] = inspectExport(filename, fixture.branch, fixture.rows, kind);
      record.timings_seconds[`download_validate_${kind}`] = seconds(performance.now() - phase);
    }
    record.exports = exports;
    progress('exported');

    // Other contexts have changed their own filters/pagination/export state meanwhile.
    // Return to overview and verify this context still owns its selected branch.
    phase = performance.now();
    await page.getByTestId('stSidebar').getByText('Tổng quan', { exact: true }).click();
    await assertKpis(page, fixture.metrics);
    await assertCount(page, fixture.rows);
    await assertSnapshot(page);
    await assertHealthy(page);
    assert.deepEqual(record.page_exceptions, []);
    record.timings_seconds.verify_session_isolation = seconds(performance.now() - phase);
    record.success = true;
  } catch (error) {
    record.success = false;
    record.error = error.stack || String(error);
    try { record.visible_error = await page.locator('[data-testid="stAlert"]').allTextContents(); } catch (_) {}
    try { record.diagnostic_text = (await page.locator('body').innerText()).slice(0, 10000); } catch (_) {}
  } finally {
    sampleMemory();
    record.timings_seconds.total = seconds(performance.now() - userStarted);
    await context.close();
  }
  return record;
}

(async () => {
  let browser;
  sampleMemory();
  const interval = setInterval(sampleMemory, 100);
  try {
    browser = await chromium.launch({
      executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });
    await Promise.all(branchList.slice(0, 5).map((branch, index) => runUser(browser, branch, index)));
    results.success = results.users.length === 5 && results.users.every(user => user.success);
  } catch (error) {
    results.success = false;
    results.error = error.stack || String(error);
  } finally {
    clearInterval(interval);
    sampleMemory();
    if (browser) await browser.close();
    fs.rmSync(temp, { recursive: true, force: true });
    results.total_seconds = seconds(performance.now() - started);
    for (const key of ['sampled_peak_rss_mib', 'kernel_peak_rss_mib']) {
      results.process_memory[key] = Number(results.process_memory[key].toFixed(2));
    }
    results.process_memory.observed_below_2gib = results.process_memory.kernel_peak_rss_mib > 0
      ? results.process_memory.kernel_peak_rss_mib < 2048 : null;
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, JSON.stringify(results, null, 2) + '\n');
    console.log(JSON.stringify({ success: results.success, users: results.users.map(u => ({ user: u.user, success: u.success, error: u.error })), process_memory: results.process_memory, total_seconds: results.total_seconds, output: outputPath }));
    process.exitCode = results.success ? 0 : 1;
  }
})();
