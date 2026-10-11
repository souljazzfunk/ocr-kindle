const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const manifest = require('./manifest.json');

test('content scripts load the shared rules before content.js', () => {
  assert.deepStrictEqual(manifest.content_scripts[0].js, ['pagemeta.js', 'policy.js', 'content.js']);
});

test('the content scripts share one scope, so no two of them declare the same top-level name', () => {
  // Chrome runs them as separate scripts in one world: a name declared twice throws and the later script never runs.
  const context = vm.createContext({ setTimeout, chrome: { runtime: { sendMessage: async () => ({ none: true }) } } });
  for (const file of manifest.content_scripts[0].js) {
    assert.doesNotThrow(() => new vm.Script(fs.readFileSync(path.join(__dirname, file), 'utf8'), { filename: file })
      .runInContext(context, { timeout: 100 }), file);
  }
});

test('the worker reaches only the local channel and saves nothing through downloads', () => {
  assert.deepStrictEqual(manifest.host_permissions, ['http://127.0.0.1/*']);
  assert.ok(manifest.permissions.includes('alarms') && !manifest.permissions.includes('downloads'));
});
