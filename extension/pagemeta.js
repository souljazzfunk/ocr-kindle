// Book title and a record of the reader's page structure, shared by content.js and pagemeta.test.js.
// The full-book reader on read.amazon.com has neither .book-title nor the text layer the sample reader has,
// and its structure can only be read inside Laiken's signed-in Brave, so a capture records it in the ZIP.

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
    a11y: sample('[class*="a11y" i], [aria-live], [role="document"], [role="main"]'),
    body: describe(doc.body, 0),
  };
}

if (typeof module !== 'undefined') module.exports = { pickTitle, probePage };
