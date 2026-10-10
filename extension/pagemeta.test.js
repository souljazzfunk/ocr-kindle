const test = require('node:test');
const assert = require('node:assert');
const { pickTitle } = require('./pagemeta.js');

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
