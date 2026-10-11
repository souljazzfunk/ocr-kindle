const PAGE_WAIT_MS = 3000;
const NEXT_ATTEMPTS = 3;
const FIRST_PAGE_WAIT_MS = 60000;
const FALLBACK_SAVE_MS = 60000;
// Heading-only pages keep part of their text in the image (e.g. layer "1" for a page reading "幻想1 アメリカ…"),
// so short layers are left for img2txt.py to OCR.
const MIN_TEXT_CHARS = 40;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function pageImage() {
  return [...document.querySelectorAll('img[src^="blob:"]')]
    .find((img) => img.getBoundingClientRect().width > 200);
}

function position() {
  const pos = parsePosition(document.body.innerText);
  return pos ? `${pos.n}/${pos.m}` : '';
}

let stage = '';
function beat(now = stage) {
  stage = now;
  document.title = now ? heartbeat(document.title, now, position(), document.visibilityState) : withoutHeartbeat(document.title);
}
// Fired even while the tab is hidden, so the title says so when the timers have slowed.
document.addEventListener('visibilitychange', () => { if (stage) beat(); });

function bookTitle(asin) {
  // .fixed-book-title and .top-chrome__book-title are the full-book reader's (dom-probe.json, AI for Educators).
  const element = document.querySelector('.book-title, .fixed-book-title, .top-chrome__book-title, [data-testid*="book-title" i], [class*="BookTitle"]');
  return pickTitle([element?.textContent, withoutHeartbeat(document.title)], asin);
}

// Cloud Reader overlays the page image with a screen-reader text layer of <p> and <h1>-<h6> elements.
function pageMarkdown() {
  const layer = document.querySelector('#kr-renderer .kg-view > .kg-a11y-abs');
  if (!layer) return '';
  return [...layer.children].map((el) => {
    const level = /^H([1-6])$/.exec(el.tagName)?.[1];
    return level ? `${'#'.repeat(level)} ${el.textContent.trim()}` : el.textContent;
  }).join('\n\n').replace(/^\n+|\s+$/g, ''); // trim() would also drop a leading 　 indent
}

// The chevron ignores element.click(); it reacts to the full pointer sequence.
function press(label) {
  const button = document.querySelector(`[aria-label="${label}"]`);
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    const Event = type.startsWith('pointer') ? PointerEvent : MouseEvent;
    button?.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, view: window }));
  }
}

async function toPng(img) {
  await img.decode();
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext('2d').drawImage(img, 0, 0);
  return canvas.toDataURL('image/png');
}

async function waitForPage(prevSrc) {
  for (let t = 0; t < PAGE_WAIT_MS; t += 100) {
    const img = pageImage();
    if (img && img.src !== prevSrc) return img;
    await sleep(100);
  }
  return null;
}

// Cloud Reader occasionally ignores a turn request, so retry before concluding the book has ended.
async function turnPage(prevSrc, label = 'Next page') {
  for (let i = 0; i < NEXT_ATTEMPTS; i++) {
    press(label);
    const img = await waitForPage(prevSrc);
    if (img) return img;
  }
  return null;
}

const MENU_WAIT_MS = 5000;
let startedBy = '';

function activate(el) {
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    const Event = type.startsWith('pointer') ? PointerEvent : MouseEvent;
    el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, view: window }));
  }
}

const label = (el) => (el.getAttribute('aria-label') || el.textContent || '').trim();

// Opens the reader's contents and picks the entry nearest the start (pagemeta.js). Returns the new page image, or
// null when there is no contents control or the jump did not change the page; goToStart then pages back as before.
async function jumpToContentsStart() {
  const img = pageImage();
  const controls = [...document.querySelectorAll('button, [role="button"], [role="tab"], a[aria-label]')];
  const opener = controls[contentsControl(controls.map(label))];
  if (!img || !opener) return null;
  activate(opener);
  for (let t = 0; t < MENU_WAIT_MS; t += 250) {
    await sleep(250);
    const panel = document.querySelector('[role="dialog"], [role="navigation"], [role="menu"], [class*="toc" i], [class*="contents" i]');
    const entries = panel ? [...panel.querySelectorAll('a, button, [role="link"], [role="menuitem"], [role="treeitem"], li')] : [];
    const entry = entries[startEntry(entries.map(label))];
    if (entry) {
      activate(entry);
      return waitForPage(img.src);
    }
  }
  return null;
}

// Moves the reader's progress slider to its start: a range input takes its minimum value, an ARIA slider the Home
// key. Returns the new page image, or null when there is no slider or the page did not change.
async function jumpBySlider() {
  const img = pageImage();
  const slider = document.querySelector('input[type="range"], [role="slider"]');
  if (!img || !slider) return null;
  if (slider instanceof HTMLInputElement) {
    slider.value = slider.min || '0';
    for (const type of ['input', 'change']) slider.dispatchEvent(new Event(type, { bubbles: true }));
  } else {
    slider.focus();
    for (const type of ['keydown', 'keyup']) {
      slider.dispatchEvent(new KeyboardEvent(type, { key: 'Home', code: 'Home', keyCode: 36, bubbles: true }));
    }
  }
  return waitForPage(img.src);
}

// The reader reopens a book where it was last read (AI for Educators opened at page 75 of 120), so an automatic
// capture goes back to the start first. Laiken, 2026-10-11: use the reader's own tools, not one turn per page. So the
// progress slider goes to its start, else the contents' first entry, and only what is left (a contents' first entry
// can sit a few pages after page 1) is paged back. Returns '' once there, or why it could not get there.
async function goToStart(isRunning) {
  const tried = [];
  for (const [name, jump] of [['slider', jumpBySlider], ['contents', jumpToContentsStart]]) {
    if (parsePosition(document.body.innerText)?.n === 1) break;
    beat(name);
    const jumped = await jump().catch(() => null);
    tried.push(`${name} ${jumped ? `to ${position() || 'an unknown page'}` : 'not used'}`);
  }
  startedBy = tried.join('; ') || 'opened at page 1';
  let img = pageImage();
  const guard = turnGuard(parsePosition(document.body.innerText)?.m);
  while (img && isRunning()) {
    const pos = parsePosition(document.body.innerText);
    beat('back');
    if (pos?.n === 1) return '';
    // turnPage only returns once the screen has changed, so every turn here moved.
    const stop = guard(pos, true);
    if (stop) return `paging back to page 1 stopped: ${stop}`;
    const back = await turnPage(img.src, 'Previous page');
    if (!back) break;
    img = back;
  }
  if (!isRunning()) return 'stopped by a click on the icon';
  return atStart(parsePosition(document.body.innerText), false) ? '' : `could not page back to page 1 (still at ${position() || 'an unknown page'})`;
}

async function captureBook(save, isRunning) {
  let img = await waitForPage(null);
  let previous = '';
  let count = 0;
  const positions = {};
  const guard = turnGuard(parsePosition(document.body.innerText)?.m);
  let stopped = '';
  while (img && isRunning()) {
    const data = await toPng(img);
    beat('page');
    stopped = guard(parsePosition(document.body.innerText), data !== previous);
    if (stopped) break;
    if (data !== previous) {
      count++;
      // Recorded on the first page, where the reader has finished loading, so selectors can be fixed from the ZIP.
      if (count === 1) {
        let probe;
        try { probe = probePage(document); } catch (e) { probe = { error: String(e) }; }
        save('dom-probe.json', new TextEncoder().encode(JSON.stringify(probe, null, 1)), position());
      }
      const name = `page_${String(count).padStart(4, '0')}`;
      positions[name] = position();
      save(`${name}.png`, Uint8Array.from(atob(data.split(',')[1]), (c) => c.charCodeAt(0)), positions[name]);
      const text = pageMarkdown();
      if (text.length >= MIN_TEXT_CHARS) save(`${name}.txt`, new TextEncoder().encode(text), positions[name]);
      previous = data;
    }
    img = await turnPage(img.src);
  }
  // Page N of M for every saved page, so check_book.py can tell a capture that missed the start or the end.
  if (count) save('positions.json', new TextEncoder().encode(JSON.stringify(positions, null, 1)), '');
  // Which extension made this ZIP, so capture_book.py can tell a Brave that was not reloaded after a change,
  // and why the capture ended early when a turn cap stopped it (check_book.py's capture_stop).
  if (count) save('capture.json', new TextEncoder().encode(JSON.stringify({ extension_version: chrome.runtime.getManifest().version, stopped, start: startedBy })), '');
  return count;
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  for (let k = 0; k < 8; k++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) crc = CRC_TABLE[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

// Uncompressed ZIP (PNG is already compressed). One archive means one save prompt per book.
function zip(files) {
  const parts = [];
  const central = [];
  let offset = 0;
  for (const { name, bytes } of files) {
    const nameBytes = new TextEncoder().encode(name);
    const header = (signature, extra) => {
      const h = new DataView(new ArrayBuffer(extra ? 46 : 30));
      let i = 0;
      const u16 = (v) => { h.setUint16(i, v, true); i += 2; };
      const u32 = (v) => { h.setUint32(i, v, true); i += 4; };
      u32(signature);
      if (extra) u16(20);
      u16(20); u16(0x0800); u16(0); u32(0); u32(crc32(bytes)); u32(bytes.length); u32(bytes.length); u16(nameBytes.length); u16(0);
      if (extra) { u16(0); u16(0); u16(0); u32(0); u32(offset); }
      return new Uint8Array(h.buffer);
    };
    const local = header(0x04034b50, false);
    parts.push(local, nameBytes, bytes);
    central.push(header(0x02014b50, true), nameBytes);
    offset += local.length + nameBytes.length + bytes.length;
  }
  const centralSize = central.reduce((n, p) => n + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, files.length, true);
  end.setUint16(10, files.length, true);
  end.setUint32(12, centralSize, true);
  end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(end.buffer)], { type: 'application/zip' });
}

// The worker saves the ZIP with no dialog (download.js). Should that fail, the link click still saves it,
// so a capture is never lost.
async function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  let result;
  try {
    result = await chrome.runtime.sendMessage({ download: { url, filename } });
  } catch (e) {
    result = { ok: false, reason: String(e) };
  }
  if (result?.ok) {
    URL.revokeObjectURL(url);
    return 'complete';
  }
  console.log(`[kindle-capture] saving through the page instead, the worker could not: ${result?.reason}`);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  await sleep(FALLBACK_SAVE_MS); // the tab must stay open while the browser reads the blob
  URL.revokeObjectURL(url);
  return 'fallback';
}

let running = false;

function asinFromUrl() {
  return new URL(location.href).searchParams.get('asin') || '';
}

// An automatic capture that will not run says why in <ASIN>.skip.json in Downloads and closes its tab, so
// capture_book.py stops waiting at once instead of for an hour.
async function refuse(asin, auto, reason) {
  running = false;
  chrome.runtime.sendMessage({ badge: 'skip' });
  console.log(`[kindle-capture] not starting: ${reason}`);
  if (!auto) return;
  const notice = { asin, reason, position: position(), extension_version: chrome.runtime.getManifest().version };
  const saved = await download(new Blob([JSON.stringify(notice, null, 1)], { type: 'application/json' }), `${asin}.skip.json`);
  chrome.runtime.sendMessage({ finish: { asin, pages: 0, auto, download: saved } });
}

async function startCapture(asin, auto, again = false) {
  running = true; // set before the claim returns, so a second click stops rather than starts again
  const { ok, reason } = await chrome.runtime.sendMessage({ claim: { asin, auto, again } });
  if (!ok) return refuse(asin, auto, reason);
  // Opened from a URL, the reader may still be loading its first page.
  for (let t = 0; auto && !pageImage() && t < FIRST_PAGE_WAIT_MS; t += 500) {
    if (appOnly(document.body.innerText)) return refuse(asin, auto, `${APP_ONLY}: Cloud Reader cannot open this book`);
    await sleep(500);
  }
  if (auto) {
    chrome.runtime.sendMessage({ badge: 'back' });
    const problem = await goToStart(() => running);
    if (problem) return refuse(asin, auto, problem);
  }
  const files = [];
  const save = (name, bytes, pos) => {
    files.push({ name, bytes });
    chrome.runtime.sendMessage({ badge: pos.split('/')[0] || '…' });
  };
  const count = await captureBook(save, () => running);
  running = false;
  beat('saving');
  const textPages = files.filter((f) => f.name.endsWith('.txt')).length;
  console.log(`[kindle-capture] title "${bookTitle(asin)}", text layer on ${textPages} of ${count} pages`);
  const saved = count ? await download(zip(files), `${bookTitle(asin)}.zip`) : 'none';
  console.log(`[kindle-capture] saved ${count} pages`);
  beat('');
  chrome.runtime.sendMessage({ badge: 'done' });
  // Sent last: for an automatic capture the worker closes this tab on it.
  chrome.runtime.sendMessage({ finish: { asin, pages: count, auto, download: saved } });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg !== 'toggle') return;
  if (running) running = false;
  else startCapture(asinFromUrl(), false);
});

// Drop the marker before anything else, so a reload or a restored tab does not start again.
const autoStart = readAutoStart(location.href);
if (autoStart) {
  history.replaceState(history.state, '', autoStart.cleanUrl);
  startCapture(autoStart.asin, true, autoStart.again);
}
