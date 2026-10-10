importScripts('autostart.js', 'download.js');

// active lives in session storage, so a browser restart (which ends any capture) clears it.
async function loadState() {
  const { active = null } = await chrome.storage.session.get('active');
  const { saved = [] } = await chrome.storage.local.get('saved');
  return { active, saved };
}

async function storeState({ active, saved }) {
  await chrome.storage.session.set({ active });
  await chrome.storage.local.set({ saved });
}

// Claims from two tabs can arrive together; handle them one at a time.
let queue = Promise.resolve();
function update(change) {
  const next = queue.then(async () => {
    const result = change(await loadState());
    await storeState(result.state ?? result);
    return result;
  });
  queue = next.catch(() => {});
  return next;
}

chrome.action.onClicked.addListener((tab) => chrome.tabs.sendMessage(tab.id, 'toggle'));

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tabId = sender.tab.id;
  if (msg.badge) chrome.action.setBadgeText({ tabId, text: msg.badge });
  if (msg.claim) {
    update((state) => claimCapture(state, { tabId, ...msg.claim })).then(({ ok, reason }) => reply({ ok, reason }));
    return true;
  }
  if (msg.finish) {
    update((state) => finishCapture(state, { tabId, ...msg.finish }));
    const close = tabToClose({ tabId, ...msg.finish });
    if (close !== null) chrome.tabs.remove(close);
  }
  if (msg.download) {
    save(msg.download).then(() => reply({ ok: true }), (e) => reply({ ok: false, reason: String(e) }));
    return true;
  }
});

// Resolves once the download is over, so the tab keeps its blob URL alive until then.
// Downloads that end are noted from before the download starts, so a quick finish is not missed.
async function save({ url, filename }) {
  const ended = new Map();
  let id;
  let finish;
  const over = new Promise((resolve) => { finish = resolve; });
  const done = (delta) => {
    const state = delta.state?.current;
    if (state === 'complete' || state === 'interrupted') ended.set(delta.id, state);
    if (ended.has(id)) finish(ended.get(id));
  };
  chrome.downloads.onChanged.addListener(done);
  try {
    id = await chrome.downloads.download(downloadOptions(url, filename));
    if (ended.has(id)) finish(ended.get(id));
    const state = await over;
    if (state !== 'complete') throw new Error(`download ${id} was ${state}`);
  } finally {
    chrome.downloads.onChanged.removeListener(done);
  }
}

// A closed or reloaded tab has lost its capture, so it no longer holds the lock.
const release = (tabId) => update((state) => finishCapture(state, { tabId, asin: '', pages: 0 }));
chrome.tabs.onRemoved.addListener(release);
chrome.tabs.onUpdated.addListener((tabId, info) => { if (info.status === 'loading') release(tabId); });
