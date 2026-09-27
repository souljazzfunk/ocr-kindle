const PAGE_WAIT_MS = 3000;
const NEXT_ATTEMPTS = 3;
// Heading-only pages keep part of their text in the image (e.g. layer "1" for a page reading "幻想1 アメリカ…"),
// so short layers are left for img2txt.py to OCR.
const MIN_TEXT_CHARS = 40;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function pageImage() {
  return [...document.querySelectorAll('img[src^="blob:"]')]
    .find((img) => img.getBoundingClientRect().width > 200);
}

function position() {
  const m = document.body.innerText.match(/(?:位置|ページ|Location|Page)\D{0,3}(\d+)\s*(?:\/|of)\s*(\d+)/);
  return m ? `${m[1]}/${m[2]}` : '';
}

function bookTitle() {
  const title = document.querySelector('.book-title')?.textContent.trim() || 'kindle-book';
  return title.replace(/[\\/:*?"<>|~]/g, '_').slice(0, 120);
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
function pressNext() {
  const button = document.querySelector('[aria-label="Next page"]');
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
async function turnPage(prevSrc) {
  for (let i = 0; i < NEXT_ATTEMPTS; i++) {
    pressNext();
    const img = await waitForPage(prevSrc);
    if (img) return img;
  }
  return null;
}

async function captureBook(save, isRunning) {
  let img = await waitForPage(null);
  let previous = '';
  let count = 0;
  while (img && isRunning()) {
    const data = await toPng(img);
    if (data !== previous) {
      count++;
      const name = `page_${String(count).padStart(4, '0')}`;
      save(`${name}.png`, Uint8Array.from(atob(data.split(',')[1]), (c) => c.charCodeAt(0)), position());
      const text = pageMarkdown();
      if (text.length >= MIN_TEXT_CHARS) save(`${name}.txt`, new TextEncoder().encode(text), position());
      previous = data;
    }
    img = await turnPage(img.src);
  }
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

function download(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}

let running = false;

chrome.runtime.onMessage.addListener(async (msg) => {
  if (msg !== 'toggle') return;
  if (running) {
    running = false;
    return;
  }
  running = true;
  const files = [];
  const save = (name, bytes, pos) => {
    files.push({ name, bytes });
    chrome.runtime.sendMessage({ badge: pos.split('/')[0] || '…' });
  };
  const count = await captureBook(save, () => running);
  running = false;
  if (count) download(zip(files), `${bookTitle()}.zip`);
  chrome.runtime.sendMessage({ badge: 'done' });
  console.log(`[kindle-capture] saved ${count} pages`);
});
