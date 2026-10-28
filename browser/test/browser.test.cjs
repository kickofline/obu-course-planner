'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { once } = require('node:events');
const { spawn } = require('node:child_process');
const path = require('node:path');
const readline = require('node:readline');
const { performance } = require('node:perf_hooks');

test('native JavaScript navigation can finish an intermediate 403 without unpaced requests or resubmission', {
  skip: !process.env.OBU_BROWSER_EXECUTABLE,
  timeout: 30000,
}, async t => {
  const requests = [];
  const posts = [];
  const server = http.createServer((request, response) => {
    requests.push({ at: performance.now(), method: request.method, url: request.url });
    response.setHeader('Cache-Control', 'no-store');
    const send = (status, body, type = 'text/html') => {
      response.writeHead(status, { 'Content-Type': type });
      response.end(body);
    };
    if (request.url === '/start') {
      response.setHeader('cf-mitigated', 'challenge');
      return send(403, '<title>Just a moment...</title><script src="/navigate.js"></script>');
    }
    if (request.url === '/navigate.js') {
      return send(200, 'setTimeout(() => location.replace("/CLASSSCHED.php"), 100);', 'application/javascript');
    }
    if (request.url === '/style.css') return send(200, 'table { color: black; }', 'text/css');
    if (request.url === '/frame') return send(200, '<p>child frame</p>');
    if (request.url !== '/CLASSSCHED.php') return send(404, 'missing');
    let body = '';
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      if (request.method === 'POST') {
        if (!request.headers.cookie?.includes('session=kept')) return send(403, 'lost session');
        posts.push(Object.fromEntries(new URLSearchParams(body)));
      }
      response.setHeader('Set-Cookie', 'session=kept; Path=/');
      send(200, `<link rel="stylesheet" href="/style.css"><iframe src="http://localhost:${server.address().port}/frame"></iframe>` +
        '<form action="CLASSSCHED.php"><input name="SEM_filt" value="227"></form><table class="mainlist"></table>');
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const helper = path.resolve(__dirname, '../../src/obu_course_planner/browser/transport.cjs');
  const child = spawn(process.execPath, [helper, '--interval-ms', '1000'], {
    env: { ...process.env, NODE_PATH: process.env.NODE_PATH || path.resolve(__dirname, '../node_modules') },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let errors = '';
  child.stderr.on('data', bytes => { errors += bytes; });
  const lines = readline.createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  t.after(async () => {
    child.kill();
    server.closeAllConnections();
    server.close();
  });
  const command = async value => {
    child.stdin.write(JSON.stringify(value) + '\n');
    const line = await lines.next();
    assert.equal(line.done, false, errors);
    const reply = JSON.parse(line.value);
    assert.equal(reply.ok, true, reply.error || errors);
    return reply;
  };
  const base = `http://127.0.0.1:${server.address().port}`;
  const opened = await command({ method: 'GET', url: base + '/start' });
  assert.equal(opened.status, 200); // The initial response was 403, not the final document.
  const posted = await command({ method: 'POST', url: base + '/CLASSSCHED.php', data: { SEM_filt: '227', submit: 'filter', blank: '' } });
  assert.equal(posted.status, 200);
  const closed = await command({ method: 'CLOSE' });
  assert.equal(closed.request_count, requests.length);
  assert.deepEqual(posts, [{ SEM_filt: '227', submit: 'filter', blank: '' }]);
  assert.equal(requests.filter(request => request.url === '/start').length, 1);
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
  assert.ok(requests.some(request => request.url === '/frame'));
  for (let index = 1; index < requests.length; index++) {
    assert.ok(requests[index].at - requests[index - 1].at >= 990, 'browser requests escaped the shared one-second queue');
  }
});
