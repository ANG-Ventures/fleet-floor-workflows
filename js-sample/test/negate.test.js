'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { add } = require('../src/index.js');

// Second test file so the selftest's `shards: 2` job gives each shard one file.
test('add handles negatives', () => {
  assert.strictEqual(add(-2, 3), 1);
});
