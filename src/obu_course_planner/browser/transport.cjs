'use strict';

const readline = require('node:readline');
const puppeteer = require('puppeteer-extra');
const { RequestPacer } = require('./pacer.cjs');

puppeteer.use(require('puppeteer-extra-plugin-stealth')());

const interval = Number(process.argv[process.argv.indexOf('--interval-ms') + 1]);
if (!Number.isFinite(interval) || interval < 1000 || interval > 10000) {
  throw new Error('--interval-ms must be between 1000 and 10000');
}
if (!process.env.OBU_BROWSER_EXECUTABLE) throw new Error('OBU_BROWSER_EXECUTABLE is required');

const pacer = new RequestPacer(interval);
let browser;
let page;
let requestCount = 0;
let closing = false;
let networkError;
let closePromise;

async function deadline(operation, milliseconds, message) {
  let timer;
  try {
    return await Promise.race([
      operation,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), milliseconds); }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function start() {
  if (browser) return;
  browser = await puppeteer.launch({
    executablePath: process.env.OBU_BROWSER_EXECUTABLE,
    headless: 'shell',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
    // Restore Chrome's popup blocker: this transport drives exactly one page.
    ignoreDefaultArgs: ['--disable-popup-blocking'],
    timeout: 30000,
    protocolTimeout: 90000,
  });
  page = await browser.newPage();
  await page.setBypassServiceWorker(true);
  await page.setCacheEnabled(false);
  await page.setRequestInterception(true);
  page.on('request', request => {
    const dispatch = async () => {
      if (closing || request.isInterceptResolutionHandled()) return;
      await request.continue();
      if (/^https?:/.test(request.url())) requestCount += 1;
    };
    // Puppeteer's page interception includes redirects, resources and child frames.
    const operation = /^https?:/.test(request.url()) ? pacer.run(dispatch) : dispatch();
    operation.catch(error => {
      if (!closing) networkError = error;
      if (!request.isInterceptResolutionHandled()) request.abort().catch(() => {});
    });
  });
}

function close() {
  if (!closePromise) {
    closing = true;
    pacer.cancel();
    closePromise = (async () => {
      if (!browser) return;
      try {
        await deadline(browser.close(), 5000, 'Browser close timed out');
      } finally {
        browser.process()?.kill('SIGKILL');
      }
    })();
  }
  return closePromise;
}

async function navigate(command) {
  await start();
  const url = new URL(command.url);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Only HTTP(S) navigation is supported');
  let response;
  if (command.method === 'GET') {
    response = await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 90000 });
  } else if (command.method === 'POST') {
    const navigation = page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 90000 });
    // Attach both promises immediately, so a failed submission cannot orphan a timeout.
    [response] = await Promise.all([
      navigation,
      page.evaluate(({ action, fields }) => {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = action;
        form.acceptCharset = 'UTF-8';
        for (const [name, value] of Object.entries(fields)) {
          const input = document.createElement('input');
          input.type = 'hidden';
          input.name = name;
          input.value = value;
          form.append(input);
        }
        document.body.append(form);
        HTMLFormElement.prototype.submit.call(form);
      }, { action: url.href, fields: command.data || {} }),
    ]);
  } else {
    throw new Error(`Unsupported browser method: ${command.method}`);
  }
  const status = response?.status();
  if (!status || status < 200 || status >= 400) throw new Error(`OBU schedule returned HTTP ${status || 'no response'}`);
  if (response.headers()['cf-mitigated'] === 'challenge') throw new Error('OBU returned a browser challenge');
  await page.waitForNetworkIdle({ idleTime: 250, timeout: 90000, concurrency: 0 });
  if (networkError) throw networkError;
  if (/just a moment|attention required/i.test(await page.title())) throw new Error('OBU returned a browser challenge');
  return { ok: true, html: await page.content(), status, request_count: requestCount };
}

async function reply(value) {
  await new Promise((resolve, reject) => {
    process.stdout.write(JSON.stringify(value) + '\n', error => error ? reject(error) : resolve());
  });
}

async function main() {
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  try {
    for await (const line of lines) {
      try {
        const command = JSON.parse(line);
        if (command.method === 'CLOSE') {
          await close();
          await reply({ ok: true, request_count: requestCount });
          break;
        }
        await reply(await deadline(navigate(command), 100000, 'Browser navigation exceeded 100 seconds'));
      } catch (error) {
        await reply({ ok: false, error: error.message });
        break; // No retries or reuse of a failed browser session.
      }
    }
  } finally {
    lines.close();
    await close();
  }
}

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => { close().finally(() => process.exit(0)); });
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
