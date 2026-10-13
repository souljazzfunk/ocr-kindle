const test = require('node:test');
const assert = require('node:assert');
const { pickTitle, footerText, readPosition, parsePosition, appOnly, parseLibrary } = require('./pagemeta.js');

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

test('the library list is read from the page JSON, authors without their trailing colon', () => {
  const json = JSON.stringify({ libraryType: 'BOOKS', sortType: 'RECENCY', paginationToken: '50', itemsList: [
    { asin: 'B07G4SH6BV', title: '英語教育幻想 (ちくま新書) ', authors: ['久保田竜子:'], resourceType: 'EBOOK', originType: 'PURCHASE', mangaOrComicAsin: false },
    { asin: 'B081F1JL1X', title: '自動車の社会的費用', authors: [], resourceType: 'EBOOK_SAMPLE', originType: 'PURCHASE', mangaOrComicAsin: false },
    { title: 'no asin' },
  ] });
  const doc = { querySelector: (selector) => (selector === '#itemViewResponse' ? { textContent: json } : null) };
  assert.deepStrictEqual(parseLibrary(doc), { paginationToken: '50', books: [
    { asin: 'B07G4SH6BV', title: '英語教育幻想 (ちくま新書)', authors: ['久保田竜子'], resourceType: 'EBOOK', manga: false },
    { asin: 'B081F1JL1X', title: '自動車の社会的費用', authors: [], resourceType: 'EBOOK_SAMPLE', manga: false },
  ] });
  assert.deepStrictEqual(parseLibrary({ querySelector: () => null }), { books: [], paginationToken: null });
});

test('the position is read from the footer the reader hides, then from the page text', () => {
  // 2026-10-13: Automate the Boring Stuff's footer held "Page x of 552 ● 1%" while body.innerText had no Page line.
  const doc = (footer, body) => ({ body: { innerText: body },
    querySelector: (selector) => (selector === '.reader-footer' && footer !== null ? { textContent: footer } : null) });
  assert.deepStrictEqual(readPosition(doc('Page 12 of 552 ● 3%', 'Chapter 1')), { n: 12, m: 552 });
  assert.strictEqual(readPosition(doc('Page x of 552 ● 1%', 'Introduction')), null);
  assert.deepStrictEqual(readPosition(doc(null, 'Location 70 of 552')), { n: 70, m: 552 });
  assert.deepStrictEqual(readPosition(doc('', 'Page 9 of 10')), { n: 9, m: 10 });
});

test('the reader footer is recorded as it reads, so a screen without a position shows what it held instead', () => {
  // 2026-10-13: The History of Jazz and So You Want to Talk About Race saw no position on any screen.
  const doc = (text) => ({ querySelector: (selector) => (selector === '.reader-footer' && text !== null ? { textContent: text } : null) });
  assert.strictEqual(footerText(doc('\n  12 mins left in chapter \n ● 3%\n')), '12 mins left in chapter ● 3%');
  assert.strictEqual(footerText(doc('')), '');
  assert.strictEqual(footerText(doc(null)), null);
});
