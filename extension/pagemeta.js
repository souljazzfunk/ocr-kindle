// What content.js reads off the page (title, position, structure, notices, library entries), shared with pagemeta.test.js.
// The full-book reader on read.amazon.com has neither .book-title nor the text layer the sample reader has,
// and its structure can only be read inside Laiken's signed-in Chrome, so a capture records it (dom-probe.json).

const GENERIC_TITLES = /^(kindle|amazon|kindle cloud reader|amazon kindle|read\.amazon\.\w+)$/i;

// candidates: texts from title elements and document.title, best first.
function pickTitle(candidates, asin) {
  for (const raw of candidates) {
    const title = (raw || '').replace(/\s*[|\-–—:]\s*(Kindle|Amazon)( Cloud Reader)?\s*$/i, '').trim();
    if (title && !GENERIC_TITLES.test(title)) return title.replace(/[\\/:*?"<>|~]/g, '_').slice(0, 120);
  }
  return asin || 'kindle-book';
}

const PROBE_DEPTH = 6;
const PROBE_TEXT = 120;

// Tag, id and classes of the elements a few levels down, with how many children each has.
// Text is cut to PROBE_TEXT characters so the record shows where the text lives without copying the book.
function describe(el, depth) {
  const node = {
    tag: el.tagName.toLowerCase(),
    id: el.id || undefined,
    class: typeof el.className === 'string' && el.className ? el.className : undefined,
    aria: el.getAttribute('aria-label') || undefined,
    children: el.children.length,
  };
  if (!el.children.length && el.textContent.trim()) node.text = el.textContent.trim().slice(0, PROBE_TEXT);
  if (depth < PROBE_DEPTH) node.kids = [...el.children].slice(0, 12).map((c) => describe(c, depth + 1));
  return node;
}

function probePage(doc) {
  const sample = (selector) => [...doc.querySelectorAll(selector)].slice(0, 8).map((el) => describe(el, PROBE_DEPTH - 2));
  return {
    title: doc.title,
    url: doc.location.pathname + doc.location.search,
    counts: {
      blobImages: doc.querySelectorAll('img[src^="blob:"]').length,
      canvases: doc.querySelectorAll('canvas').length,
      iframes: doc.querySelectorAll('iframe').length,
      textLayer: doc.querySelectorAll('#kr-renderer .kg-view > .kg-a11y-abs').length,
    },
    titleCandidates: sample('[class*="title" i], [data-testid*="title" i], h1'),
    // The reader's buttons and menu items by label, so a jump to the start can be built from what the reader shows
    // instead of paging back one turn at a time (Automate the Boring Stuff paged back from 517).
    controls: [...doc.querySelectorAll('button, [role="button"], [role="menuitem"], a[aria-label]')].slice(0, 80)
      .map((el) => (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60)).filter(Boolean),
    a11y: sample('[class*="a11y" i], [aria-live], [role="document"], [role="main"]'),
    // Whether the page-turn buttons are there and usable, for a capture that stopped turning.
    turns: ['Previous page', 'Next page'].map((label) => {
      const button = doc.querySelector(`[aria-label="${label}"]`);
      return { label, present: !!button, disabled: !!button && (button.disabled === true || button.getAttribute('aria-disabled') === 'true'),
        shown: !!button && button.getBoundingClientRect().width > 0 };
    }),
    readerFooter: footerText(doc),
    footer: (doc.body.innerText.match(/(?:位置|ページ|Location|Page)[^\n]{0,40}/g) || []).slice(0, 4),
    body: describe(doc.body, 0),
  };
}

// The reader footer's own text, whatever it shows. The position comes from body.innerText, and from 2026-10-12 13:00
// JST some captures saw no position at all (The History of Jazz, So You Want to Talk About Race): this records what
// the footer held instead, so the reading can be fixed from a recording rather than a guess.
function footerText(doc) {
  const footer = doc.querySelector('.reader-footer');
  return footer ? (footer.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120) : null;
}

// The position on screen. From 2026-10-12 13:00 JST the reader hides its footer, so body.innerText no longer holds
// "Page N of M": Automate the Boring Stuff (2026-10-13) had "Page x of 552 ● 1%" in the footer's textContent while
// innerText showed no Page line (dom-probe readerFooter and footer). The footer is read first, the body as before.
function readPosition(doc) {
  return parsePosition(footerText(doc)) || parsePosition(doc.body.innerText);
}

// "Page 75 of 120" (or 位置/ページ/Location) from the reader's footer as { n, m }, or null when it shows none.
function parsePosition(text) {
  const m = (text || '').match(/(?:位置|ページ|Location|Page)\D{0,3}(\d+)\s*(?:\/|of)\s*(\d+)/);
  return m ? { n: Number(m[1]), m: Number(m[2]) } : null;
}

// Some books open in Cloud Reader only as a notice that they need the Kindle app (2026-10-11, For 91 Days In
// Tokyo), with no page to capture.
function appOnly(text) {
  return /Kindle App Is Required|can only be opened using Kindle app/i.test(text || '');
}

// Cloud Reader sends a signed-out reader to Amazon's sign-in page, which content scripts do not run on (the worker
// watches for it); a sign-in form inside the reader is caught here.
function signedOut(doc) {
  return /\/ap\/signin/.test(doc.location.pathname) || Boolean(doc.querySelector('input[type="password"]'));
}

// The library page carries its list as JSON in #itemViewResponse (read.amazon.co.jp, 2026-10-12): itemsList of
// { asin, title, authors, resourceType (EBOOK | EBOOK_SAMPLE), originType, mangaOrComicAsin, ... } and a paginationToken.
// It holds the 50 most recent books; the page loads more through its own getLibrary call, not by scrolling.
function parseLibrary(doc) {
  let data;
  try {
    data = JSON.parse(doc.querySelector('#itemViewResponse')?.textContent || 'null');
  } catch (e) {
    return { books: [], error: String(e) };
  }
  const books = (data?.itemsList || []).filter((item) => item.asin).map((item) => ({
    asin: item.asin,
    title: (item.title || '').trim(),
    authors: (item.authors || []).map((a) => a.replace(/:+$/, '').trim()),
    resourceType: item.resourceType || '',
    manga: Boolean(item.mangaOrComicAsin),
  }));
  return { books, paginationToken: data?.paginationToken ?? null };
}

if (typeof module !== 'undefined') module.exports = { pickTitle, probePage, footerText, readPosition, parsePosition, appOnly, signedOut, parseLibrary };
