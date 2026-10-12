// Runs in the page's own world: the scrubber is an Ionic <ion-range> whose value and events belong to the reader's
// scripts, which content.js in its isolated world cannot reach. content.js asks for the jump by postMessage.
window.addEventListener('message', (e) => {
  if (e.source !== window || e.data?.kc !== 'jump-start') return;
  const range = document.querySelector('#kr-scrubber-bar');
  if (!range) return;
  const value = Number(range.min) || 0;
  range.value = value;
  for (const type of ['ionInput', 'ionChange', 'ionKnobMoveEnd']) {
    range.dispatchEvent(new CustomEvent(type, { detail: { value }, bubbles: true }));
  }
});
