importScripts('job.js');

// The channel run by capture_book.py (channel.py, KC_PORT there). Only this worker talks to it.
const CHANNEL = 'http://127.0.0.1:8765';
const FAST_MS = 2000; // polling while a job runs, and for a minute after, when the next book usually follows
const AFTER_JOB_MS = 60000;
const VERSION = chrome.runtime.getManifest().version;

// active lives in session storage, so a worker woken again mid-job still knows its tab and window.
const loadActive = async () => (await chrome.storage.session.get('active')).active ?? null;
const storeActive = (active) => chrome.storage.session.set({ active });
let opening = false;
let ticking = false;
let fastUntil = 0;

async function post(path, init) {
  const response = await fetch(`${CHANNEL}${path}`, { method: 'POST', ...init });
  if (!response.ok) throw new Error(`${path} answered ${response.status}: ${await response.text()}`);
}

async function endJob(active) {
  await storeActive(null);
  fastUntil = Date.now() + AFTER_JOB_MS;
  if (active) await chrome.windows.remove(active.windowId).catch(() => {});
}

async function openJob(job) {
  opening = true;
  try {
    // A normal window of its own, not minimized and not a background tab, so the reader renders and its timers run.
    const win = await chrome.windows.create({ url: readerUrl(job), focused: false, state: 'normal' });
    await storeActive({ job, tabId: win.tabs[0].id, windowId: win.id, handed: false });
  } finally {
    opening = false;
  }
}

async function poll() {
  let reply = null;
  try {
    reply = await (await fetch(`${CHANNEL}/job?v=${encodeURIComponent(VERSION)}`)).json();
  } catch (e) {
    // No channel: nothing is being captured right now.
  }
  const active = await loadActive();
  const { lastReload = 0 } = await chrome.storage.local.get('lastReload');
  const step = pollStep(active, reply, VERSION, Date.now(), lastReload);
  if (step === 'reload') {
    await chrome.storage.local.set({ lastReload: Date.now() });
    chrome.runtime.reload();
  } else if (step === 'open') {
    await openJob(reply.job);
  } else if (step === 'end') {
    await endJob(active); // the channel dropped it (stalled, or restarted); a new job opens on the next poll
  }
}

async function tick() {
  if (ticking) return;
  ticking = true;
  try {
    for (;;) {
      await poll();
      if (!(await loadActive()) && Date.now() >= fastUntil) break;
      await new Promise((r) => setTimeout(r, FAST_MS));
    }
  } finally {
    ticking = false;
  }
}

async function forward(message, active) {
  if (message.kc === 'page') {
    if (!active.started) await storeActive({ ...active, started: true });
    const png = await (await fetch(message.png)).blob();
    const text = btoa(Array.from(new TextEncoder().encode(message.text || ''), (b) => String.fromCharCode(b)).join(''));
    await post(`/page?job=${message.job}&n=${message.n}`, { body: png, headers: {
      'Content-Type': 'image/png', 'X-Position': message.position, 'X-Text': text, 'X-Sha': message.sha } });
    return;
  }
  await post('/event', { body: JSON.stringify(message.event), headers: { 'Content-Type': 'application/json' } });
  if (['done', 'refused', 'library'].includes(message.event.type)) await endJob(active);
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  (async () => {
    const active = await loadActive();
    const mine = active && active.tabId === sender.tab?.id;
    if (message.kc === 'job') {
      if (opening) return null;
      const job = handOut(active, sender.tab?.id);
      if (!job) return { none: true };
      await storeActive({ ...active, handed: true });
      return job;
    }
    if (!mine) return { ok: false, reason: 'not the job tab' };
    try {
      await forward(message, active);
      return { ok: true };
    } catch (e) {
      await endJob(active);
      return { ok: false, reason: String(e) };
    }
  })().then(reply);
  return true;
});

async function abandon(tabId, reason) {
  const active = await loadActive();
  if (!active || active.tabId !== tabId) return;
  await post('/event', { body: JSON.stringify({ job: active.job.id, type: 'refused', reason }),
    headers: { 'Content-Type': 'application/json' } }).catch(() => {});
  await endJob(active);
}

chrome.tabs.onRemoved.addListener((tabId) => abandon(tabId, 'the reader tab was closed'));
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url && /\/ap\/signin/.test(info.url)) abandon(tabId, 'signed out of Amazon: the reader went to the sign-in page');
  else if (info.url && isSample(info.url)) abandon(tabId, `sample: the reader showed the sample, not the purchased book (${new URL(info.url).hostname})`);
});

chrome.alarms.onAlarm.addListener(tick);
chrome.alarms.create('poll', { periodInMinutes: 0.5 });
tick();
