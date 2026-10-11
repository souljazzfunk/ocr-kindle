const test = require('node:test');
const assert = require('node:assert');
const { pickTitle, parsePosition, appOnly, parseLibraryGuess } = require('./pagemeta.js');

test('the title element wins when it has text', () => {
  assert.strictEqual(pickTitle(['How to Write a Lot', 'Kindle'], 'B07HNMJV57'), 'How to Write a Lot');
});

test('a missing title element falls back to the page title without the app name', () => {
  assert.strictEqual(pickTitle([undefined, 'How to Write a Lot - Kindle'], 'B07HNMJV57'), 'How to Write a Lot');
  assert.strictEqual(pickTitle(['', 'Amazon Kindle'], 'B07HNMJV57'), 'B07HNMJV57');
});

test('characters a file name cannot hold are replaced', () => {
  assert.strictEqual(pickTitle(['Zero to Sold: How to Start/Run a Business'], ''), 'Zero to Sold_ How to Start_Run a Business');
});

test('nothing usable gives the old default name', () => {
  assert.strictEqual(pickTitle([], ''), 'kindle-book');
});

test('the footer position is read as page and page count', () => {
  assert.deepStrictEqual(parsePosition('Page 75 of 120 ● 59%'), { n: 75, m: 120 });
  assert.deepStrictEqual(parsePosition('Location 12 of 4021'), { n: 12, m: 4021 });
  assert.strictEqual(parsePosition('Learning reading speed...'), null);
});

test('a book Cloud Reader can only send to the Kindle app is recognised', () => {
  assert.ok(appOnly('Kindle App Is Required\nThe book you’re trying to read can only be opened using Kindle app.\nBack to Library'));
  assert.ok(!appOnly('Page 1 of 120'));
});

function element(attributes, text = '', alt = null) {
  return {
    getAttribute: (name) => attributes[name] ?? null,
    textContent: text,
    querySelector: () => (alt === null ? null : { getAttribute: () => alt }),
  };
}

test('the library guess takes each ASIN once, with the best title it carries', () => {
  const doc = { querySelectorAll: () => [
    element({ id: 'cover-B07HNMJV57' }, '', 'How to Write a Lot'),
    element({ 'data-asin': 'B07HNMJV57' }, 'ignored, already titled'),
    element({ href: '/?asin=B0ZEROSOLD' }),
    element({ href: '/?asin=B0ZEROSOLD', 'aria-label': 'Zero to Sold' }),
    element({ id: 'library-header' }, 'Library'),
  ] };
  assert.deepStrictEqual(parseLibraryGuess(doc), [
    { asin: 'B07HNMJV57', title: 'How to Write a Lot' },
    { asin: 'B0ZEROSOLD', title: 'Zero to Sold' },
  ]);
});
