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
    body: describe(doc.body, 0),
  };
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

const ASIN = /\b(B0[0-9A-Z]{8})\b/;

// Books on the library page as { asin, title }. Its structure has not been recorded yet, so this reads every element
// that carries an ASIN in an attribute, and the library event also sends probePage's record to fix it from.
function parseLibraryGuess(doc) {
  const books = new Map();
  for (const el of doc.querySelectorAll('[data-asin], [id*="B0"], a[href*="asin="]')) {
    const asin = ['data-asin', 'id', 'href'].map((name) => ASIN.exec(el.getAttribute(name) || '')?.[1]).find(Boolean);
    if (!asin) continue;
    const image = el.querySelector('img[alt]');
    const title = [el.getAttribute('aria-label'), el.getAttribute('title'), image?.getAttribute('alt'), el.textContent]
      .map((text) => (text || '').trim()).find(Boolean) || '';
    if (!books.has(asin) || (!books.get(asin).title && title)) books.set(asin, { asin, title: title.slice(0, 200) });
  }
  return [...books.values()];
}

if (typeof module !== 'undefined') module.exports = { pickTitle, probePage, parsePosition, appOnly, signedOut, parseLibraryGuess };
