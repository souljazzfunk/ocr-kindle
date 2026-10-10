// Run: node --test
const test = require('node:test');
const assert = require('node:assert');
const { readAutoStart, claimCapture, finishCapture, tabToClose } = require('./autostart.js');

const URL_WITH_MARKER = 'https://read.amazon.com/?asin=B07HNMJV57#autocapture';
const empty = { active: null, saved: [] };

test('the marker is read once and removed from the URL', () => {
  const first = readAutoStart(URL_WITH_MARKER);
  assert.deepStrictEqual(first, { cleanUrl: 'https://read.amazon.com/?asin=B07HNMJV57', asin: 'B07HNMJV57', again: false });
  // A reload or a restored tab sees the cleaned URL and must not start again.
  assert.strictEqual(readAutoStart(first.cleanUrl), null);
});

test('a second tab on the same book cannot start while the first captures', () => {
  const first = claimCapture(empty, { tabId: 1, asin: 'B07HNMJV57', auto: true });
  assert.ok(first.ok);
  const second = claimCapture(first.state, { tabId: 2, asin: 'B07HNMJV57', auto: true });
  assert.strictEqual(second.ok, false);
  const manual = claimCapture(first.state, { tabId: 2, asin: 'B0OTHER', auto: false });
  assert.strictEqual(manual.ok, false);
});

test('a saved book is not captured again automatically, only by clicking the icon', () => {
  const started = claimCapture(empty, { tabId: 1, asin: 'B07HNMJV57', auto: true }).state;
  const done = finishCapture(started, { tabId: 1, asin: 'B07HNMJV57', pages: 138 });
  assert.deepStrictEqual(done, { active: null, saved: ['B07HNMJV57'] });
  assert.strictEqual(claimCapture(done, { tabId: 3, asin: 'B07HNMJV57', auto: true }).ok, false);
  assert.ok(claimCapture(done, { tabId: 3, asin: 'B07HNMJV57', auto: false }).ok);
});

test('#recapture starts a saved book again, once, and still one tab at a time', () => {
  const again = readAutoStart('https://read.amazon.com/?asin=B07HNMJV57#recapture');
  assert.deepStrictEqual(again, { cleanUrl: 'https://read.amazon.com/?asin=B07HNMJV57', asin: 'B07HNMJV57', again: true });
  assert.strictEqual(readAutoStart(again.cleanUrl), null);
  const done = { active: null, saved: ['B07HNMJV57'] };
  const started = claimCapture(done, { tabId: 3, asin: 'B07HNMJV57', auto: true, again: true });
  assert.ok(started.ok);
  assert.strictEqual(claimCapture(started.state, { tabId: 4, asin: 'B07HNMJV57', auto: true, again: true }).ok, false);
});

test('a capture that saved nothing does not mark the book saved', () => {
  const started = claimCapture(empty, { tabId: 1, asin: 'B07HNMJV57', auto: true }).state;
  assert.deepStrictEqual(finishCapture(started, { tabId: 1, asin: 'B07HNMJV57', pages: 0 }), empty);
});

test('closing the capturing tab frees the lock; other tabs closing do not', () => {
  const started = claimCapture(empty, { tabId: 1, asin: 'B07HNMJV57', auto: true }).state;
  assert.strictEqual(finishCapture(started, { tabId: 9, asin: '', pages: 0 }), started);
  assert.ok(claimCapture(finishCapture(started, { tabId: 1, asin: '', pages: 0 }), { tabId: 2, asin: 'B0OTHER', auto: true }).ok);
});

test('auto start without an ASIN is refused, since it could not be deduplicated', () => {
  assert.strictEqual(claimCapture(empty, { tabId: 1, asin: '', auto: true }).ok, false);
});

test('an automatic capture closes its own tab once the ZIP is saved; a manual one leaves it open', () => {
  assert.strictEqual(tabToClose({ tabId: 7, auto: true, download: 'complete' }), 7);
  assert.strictEqual(tabToClose({ tabId: 7, auto: true, download: 'fallback' }), 7);
  assert.strictEqual(tabToClose({ tabId: 7, auto: false, download: 'complete' }), null); // clicked by a person
  assert.strictEqual(tabToClose({ tabId: 7, auto: true, download: 'none' }), null); // nothing saved, keep the console
  assert.strictEqual(tabToClose({ tabId: 7, auto: true, download: undefined }), null); // not final yet
});
