#!/usr/bin/env node

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('chrome-newtab-demo/cognitive-demo-fixture.js', 'utf8');
const context = { window: {} };
vm.runInNewContext(source, context, { filename: 'cognitive-demo-fixture.js' });

const fixture = context.window.MementoDemoFixture;
assert.equal(fixture.recordCount, 261);
assert.equal(fixture.themes.length, 6);
assert.equal(fixture.understandings.length, 4);
assert.equal(fixture.records.length, 6);
assert.match(fixture.window, /2026-08-01/);
assert.match(fixture.window, /2026-08-20/);
assert.ok(fixture.records.every((record) => record.time && record.source && record.title && record.theme));
assert.ok(fixture.themes.every((theme) => theme.id && theme.label && Number.isFinite(theme.evidence)));

console.log('Memento Preview fixture contract passed.');
