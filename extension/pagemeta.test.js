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

const { parsePosition, atStart } = require('./pagemeta.js');

test('the footer position is read as page and page count', () => {
  assert.deepStrictEqual(parsePosition('Page 75 of 120 ● 59%'), { n: 75, m: 120 });
  assert.deepStrictEqual(parsePosition('Location 12 of 4021'), { n: 12, m: 4021 });
  assert.strictEqual(parsePosition('Learning reading speed...'), null);
});

test('a capture starts only at page 1, or where paging back stops when no page is shown', () => {
  assert.strictEqual(atStart({ n: 75, m: 120 }, true), false); // AI for Educators reopened at its saved place
  assert.strictEqual(atStart({ n: 1, m: 120 }, false), true);
  assert.strictEqual(atStart(null, false), true);
  assert.strictEqual(atStart(null, true), false);
});

const { turnGuard, EXTRA_TURNS, STUCK_TURNS } = require('./pagemeta.js');

test('page turns stop past the book length, so a capture cannot run on without end', () => {
  // Automate the Boring Stuff, 552 pages: paging back from 517 then forward must stay under 552 + EXTRA_TURNS each.
  const guard = turnGuard(552);
  let stop = '';
  let turns = 0;
  while (!stop && turns < 10000) stop = guard({ n: (turns++ % 552) + 1, m: 552 });
  assert.strictEqual(turns, 552 + EXTRA_TURNS + 1);
  assert.match(stop, /more than the 602/);
});

test('a reader stuck on one page stops the turns; screens without a number do not count as stuck', () => {
  const stuck = turnGuard(552);
  const results = Array.from({ length: STUCK_TURNS + 1 }, () => stuck({ n: 517, m: 552 })); // seen once, then 30 turns that did not move
  assert.ok(results.slice(0, -1).every((r) => r === ''));
  assert.match(results.at(-1), /stayed on page 517/);
  const unnumbered = turnGuard(552);
  assert.ok(Array.from({ length: STUCK_TURNS * 2 }, () => unnumbered(null)).every((r) => r === ''));
  // A long figure spread over a few screens of one page number is fine.
  const figure = turnGuard(552);
  assert.ok([5, 5, 5, 6, 7].every((n) => figure({ n, m: 552 }) === ''));
});

test('an unknown length still has a cap', () => {
  const guard = turnGuard(undefined);
  let turns = 0;
  while (!guard(null)) turns++;
  assert.strictEqual(turns, 3000 + EXTRA_TURNS);
});
