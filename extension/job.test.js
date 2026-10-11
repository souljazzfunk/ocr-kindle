const test = require('node:test');
const assert = require('node:assert');
const { pollStep, readerUrl, RELOAD_GAP_MS } = require('./job.js');

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
