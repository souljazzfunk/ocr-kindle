const test = require('node:test');
const assert = require('node:assert');
const { pollStep, showIfIdle, handOut, isSample, browserName, readerUrl, RELOAD_GAP_MS } = require('./job.js');

const job = { id: 'j1', kind: 'capture', asin: 'B07HNMJV57', domain: 'read.amazon.com', recapture: false };
const running = { job, tabId: 7, windowId: 3, handed: true };

test('an idle worker on another version than the checkout reloads itself, at most once a minute', () => {
  assert.strictEqual(pollStep(null, { version: '2.1', job: null }, '2.0', RELOAD_GAP_MS, 0), 'reload');
  assert.strictEqual(pollStep(null, { version: '2.1', job }, '2.0', RELOAD_GAP_MS, 0), 'reload'); // before taking the job
  assert.strictEqual(pollStep(null, { version: '2.1', job: null }, '2.0', RELOAD_GAP_MS + 5, RELOAD_GAP_MS), 'none');
});

test('a worker mid-job does not reload, and keeps its job while the channel still offers it', () => {
  assert.strictEqual(pollStep(running, { version: '2.1', job }, '2.0', RELOAD_GAP_MS, 0), 'none');
});

test('an offered job is opened by an idle worker on the same version', () => {
  assert.strictEqual(pollStep(null, { version: '2.0', job }, '2.0', 0, 0), 'open');
  assert.strictEqual(pollStep(null, { version: '2.0', job: null }, '2.0', 0, 0), 'none');
});

test('a job the channel dropped or replaced ends; an unreachable channel changes nothing', () => {
  assert.strictEqual(pollStep(running, { version: '2.0', job: null }, '2.0', 0, 0), 'end');
  assert.strictEqual(pollStep(running, { version: '2.0', job: { ...job, id: 'j2' } }, '2.0', 0, 0), 'end');
  assert.strictEqual(pollStep(running, null, '2.0', 0, 0), 'none');
});

test('the worker opens the book or the library on the job domain', () => {
  assert.strictEqual(readerUrl(job), 'https://read.amazon.com/?asin=B07HNMJV57');
  assert.strictEqual(readerUrl({ kind: 'library', domain: 'read.amazon.co.jp' }), 'https://read.amazon.co.jp/kindle-library');
});

test('a job tab that reloads before sending a page gets its job again; after a page it gets none', () => {
  const opened = { job, tabId: 7, windowId: 3, handed: false };
  assert.strictEqual(handOut(opened, 7), job);
  assert.strictEqual(handOut({ ...opened, handed: true }, 7), job); // read.amazon.co.jp redirected to the sample
  assert.strictEqual(handOut({ ...opened, handed: true, started: true }, 7), null);
  assert.strictEqual(handOut(opened, 8), null);
  assert.strictEqual(handOut(null, 7), null);
});

test('the sample reader is told apart from the book', () => {
  assert.strictEqual(isSample('https://read.amazon.co.jp/sample/B07G4SH6BV'), true);
  assert.strictEqual(isSample('https://read.amazon.co.jp/kindle-library/sample/B07G4SH6BV?ref_=kfw_samples_to_litb'), true);
  assert.strictEqual(isSample('https://read.amazon.com/?asin=B07G4SH6BV'), false);
});

test('the worker names its browser so that only the chosen one gets jobs', () => {
  assert.strictEqual(browserName({ userAgentData: { brands: [{ brand: 'Google Chrome' }, { brand: 'Chromium' }] } }), 'chrome');
  assert.strictEqual(browserName({ userAgentData: { brands: [{ brand: 'Brave' }, { brand: 'Chromium' }] } }), 'brave');
  assert.strictEqual(browserName({ brave: {}, userAgentData: { brands: [{ brand: 'Chromium' }] } }), 'brave');
});

test('a hidden reader window comes to the front only while nobody is using the Mac', async () => {
  const shown = [];
  const chrome = (state) => ({ idle: { queryState: async () => state },
    windows: { update: async (id, info) => { shown.push([id, info]); } } });
  assert.strictEqual(await showIfIdle(chrome('idle'), 3, true), true);
  assert.strictEqual(await showIfIdle(chrome('active'), 3, true), false);
  assert.strictEqual(await showIfIdle(chrome('locked'), 3, true), false);
  assert.strictEqual(await showIfIdle(chrome('idle'), 3, false), false);
  assert.deepStrictEqual(shown, [[3, { focused: true }]]);
});
