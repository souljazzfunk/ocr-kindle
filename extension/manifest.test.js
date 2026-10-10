const test = require('node:test');
const assert = require('node:assert');
const manifest = require('./manifest.json');

test('content scripts load the shared rules before content.js', () => {
  const js = manifest.content_scripts[0].js;
  assert.deepStrictEqual(js, ['autostart.js', 'pagemeta.js', 'content.js']);
});

test('the background worker can keep the one-tab lock', () => {
  assert.ok(manifest.permissions.includes('storage'));
});
