'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { RequestPacer } = require('../../src/obu_course_planner/browser/pacer.cjs');

function clockedPacer() {
  let now = 0;
  const pacer = new RequestPacer(1000, () => now, async ms => {
    // Deliberately wake early; the queue must recheck instead of granting early.
    now += Math.max(1, Math.floor(ms / 2));
  });
  return { pacer, advance: ms => { now += ms; }, now: () => now };
}

test('concurrent resources, redirect hops and post-idle bursts share one interval', async () => {
  const { pacer, advance, now } = clockedPacer();
  const sent = [];
  const send = name => pacer.run(async () => {
    sent.push([name, now()]);
    if (name === 'document') advance(250); // Delayed CDP acknowledgment.
  });
  await Promise.all([send('document'), send('css'), send('redirect')]);
  advance(10000);
  await Promise.all([send('post'), send('image'), send('iframe')]);
  assert.deepEqual(sent.map(([name]) => name), ['document', 'css', 'redirect', 'post', 'image', 'iframe']);
  assert.deepEqual(sent.map(([, at]) => at), [0, 1250, 2250, 12250, 13250, 14250]);
});

test('failed continuation cannot jam the queue or let the next request burst', async () => {
  const { pacer, now } = clockedPacer();
  const failed = pacer.run(async () => { throw new Error('target closed'); });
  const next = pacer.run(async () => now());
  await assert.rejects(failed, /target closed/);
  assert.equal(await next, 1000);
});

test('shutdown cancels pending dispatches rather than releasing them later', async () => {
  const pacer = new RequestPacer();
  await pacer.run(async () => {});
  let dispatched = false;
  const queued = pacer.run(async () => { dispatched = true; });
  pacer.cancel();
  await assert.rejects(queued);
  assert.equal(dispatched, false);
});
