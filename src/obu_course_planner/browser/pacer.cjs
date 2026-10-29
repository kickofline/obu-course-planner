'use strict';

const { performance } = require('node:perf_hooks');
const { setTimeout: sleep } = require('node:timers/promises');

class RequestPacer {
  constructor(intervalMs = 1000, clock = () => performance.now(), wait = sleep) {
    if (!Number.isFinite(intervalMs) || intervalMs < 100) throw new RangeError('Minimum request interval is 100 ms');
    this.intervalMs = intervalMs;
    this.clock = clock;
    this.wait = wait;
    this.lastDispatch = -Infinity;
    this.tail = Promise.resolve();
    this.abort = new AbortController();
  }

  run(dispatch) {
    const task = this.tail.then(async () => {
      while (!this.abort.signal.aborted) {
        const remaining = this.lastDispatch + this.intervalMs - this.clock();
        if (remaining <= 0) break;
        // Recheck the monotonic clock: timers can wake early.
        await this.wait(Math.ceil(remaining), undefined, { signal: this.abort.signal });
      }
      if (this.abort.signal.aborted) throw new Error('Browser request queue closed');
      try {
        return await dispatch();
      } finally {
        // Measure from the continuation ACK, not a reserved future slot. Slow
        // dispatches and idle time cannot accumulate credit or create a burst.
        this.lastDispatch = this.clock();
      }
    });
    this.tail = task.catch(() => {});
    return task;
  }

  cancel() {
    this.abort.abort();
  }
}

module.exports = { RequestPacer };
