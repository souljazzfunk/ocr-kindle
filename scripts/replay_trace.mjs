// Feeds the observations of a capture's trace.jsonl through extension/policy.js nextAction, in order, and prints the
// action it returns for each, one per line. test_cases.py compares them with the actions the trace recorded.
//
//     node scripts/replay_trace.mjs <case or pages folder>/trace.jsonl
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const { initialState, nextAction } = createRequire(import.meta.url)('../extension/policy.js');

let state = initialState();
for (const line of readFileSync(process.argv[2], 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const result = nextAction(state, JSON.parse(line).observation);
  state = result.state;
  console.log(result.action);
}
