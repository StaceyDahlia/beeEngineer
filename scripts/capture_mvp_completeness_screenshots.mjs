import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const APP_URL = "http://127.0.0.1:8010/";
const DEBUG_PORT = 9666;
const outputDir = resolve("docs", "screenshots");
mkdirSync(outputDir, { recursive: true });

const browser = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
  "--disable-dev-shm-usage", `--remote-debugging-port=${DEBUG_PORT}`,
  "--remote-allow-origins=*", `--user-data-dir=${join(tmpdir(), `beeline-mvp-${process.pid}`)}`,
  "--window-size=1800,1200", "about:blank",
], { stdio: ["ignore", "ignore", "pipe"], windowsHide: true });
browser.stderr.on("data", chunk => process.stderr.write(chunk));

const sleep = ms => new Promise(resolveSleep => setTimeout(resolveSleep, ms));
async function target() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`)).json();
      const page = targets.find(item => item.type === "page");
      if (page?.webSocketDebuggerUrl) return page;
    } catch (_) {}
    await sleep(100);
  }
  throw new Error("Chrome DevTools endpoint did not start");
}

const page = await target();
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolveOpen, rejectOpen) => {
  socket.addEventListener("open", resolveOpen, { once: true });
  socket.addEventListener("error", rejectOpen, { once: true });
});
let id = 0;
const pending = new Map();
const browserErrors = [];
socket.addEventListener("message", async event => {
  const raw = typeof event.data === "string" ? event.data : await event.data.text();
  const message = JSON.parse(raw);
  if (message.method === "Runtime.exceptionThrown") browserErrors.push(message.params.exceptionDetails?.exception?.description || message.params.exceptionDetails?.text || "exception");
  if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
    browserErrors.push(message.params.args.map(arg => arg.value || arg.description || "").join(" "));
  }
  if (!message.id || !pending.has(message.id)) return;
  const promise = pending.get(message.id); pending.delete(message.id);
  if (message.error) promise.reject(new Error(message.error.message)); else promise.resolve(message.result);
});
function command(method, params = {}) {
  const commandId = ++id;
  return new Promise((resolveCommand, rejectCommand) => {
    pending.set(commandId, { resolve: resolveCommand, reject: rejectCommand });
    socket.send(JSON.stringify({ id: commandId, method, params }));
  });
}
async function evaluate(expression) {
  const result = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "evaluation failed");
  return result.result.value;
}
async function waitFor(expression, timeoutMs = 120000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await sleep(200);
  }
  throw new Error(`Timed out waiting for ${expression}`);
}
async function screenshot(name) {
  const result = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false, fromSurface: true });
  writeFileSync(join(outputDir, name), Buffer.from(result.data, "base64"));
}

const importedPayload = {
  region: "Демо-импорт Москва",
  planning_date: "2026-08-17",
  jobs: [
    { id: "IMP-1", type: "Подключение", skill: "connect", address: "Москва, Тверская улица, 1", coords: [55.757, 37.615], window_start: "15:00", window_end: "17:00", duration_min: 30, required_vehicle: "Автомобиль", required_equipment: { router: 1 } },
    { id: "IMP-2", type: "Локальная работа", skill: "local", address: "Москва, улица Покровка, 10", coords: [55.76, 37.64], window_start: "10:00", window_end: "16:00", duration_min: 30, required_equipment: { fiber_splicer: 1 } },
  ],
  engineers: [
    { id: "ENG-1", name: "Бригада Импорт", skills: ["connect", "local"], vehicle: "Автомобиль", shift_start: "08:00", shift_end: "18:00", start_point: [55.75, 37.61], equipment: { router: 1 } },
  ],
};

try {
  await command("Page.enable");
  await command("Runtime.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1800, height: 1200, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: APP_URL });
  await waitFor("document.querySelector('#summary .metric') && !document.querySelector('#routing-status').classList.contains('loading')", 30000);
  await waitFor("document.querySelector('#routing-status').textContent.includes('OSRM')");
  await sleep(700);
  await evaluate("scrollTo(0,0); true");
  await screenshot("mvp-01-osrm-road-plan.png");

  await evaluate(`(() => { const select=document.getElementById('map-engineer-select'); const option=[...select.options].find(item=>item.value!=='all'); select.value=option.value; select.dispatchEvent(new Event('change',{bubbles:true})); return true; })()`);
  await waitFor("document.getElementById('map-engineer-select').value !== 'all'");
  await sleep(400);
  await screenshot("mvp-02-osrm-selected-route.png");

  await evaluate(`(() => {
    document.getElementById('import-button').click();
    const file=new File([${JSON.stringify(JSON.stringify(importedPayload))}], 'mvp-demo.json', {type:'application/json'});
    const transfer=new DataTransfer(); transfer.items.add(file);
    document.getElementById('import-file').files=transfer.files;
    document.getElementById('import-form').requestSubmit();
    return true;
  })()`);
  await waitFor("document.getElementById('source-mode').textContent.includes('mvp-demo.json') && !document.querySelector('#routing-status').classList.contains('loading')");
  await sleep(600);
  await evaluate("scrollTo(0,0); true");
  await screenshot("mvp-03-imported-plan.png");

  await evaluate("document.querySelector('[data-filter=\"unassigned\"]').click(); true");
  await waitFor("document.querySelector('#jobs-list [data-select-job]')");
  await evaluate("document.querySelector('#jobs-list [data-select-job]').click(); true");
  await waitFor("document.getElementById('explanation-text').textContent.includes('оборудован')");
  await sleep(300);
  await screenshot("mvp-04-hard-constraints.png");

  await evaluate("document.querySelector('[data-filter=\"all\"]').click(); document.getElementById('normal-scenario-button').click(); true");
  await waitFor("document.getElementById('normal-modal').classList.contains('open')");
  await evaluate("document.getElementById('normal-form').requestSubmit(); true");
  await waitFor("document.getElementById('apply-preview')");
  await evaluate("document.getElementById('scenario-preview').scrollIntoView({block:'start'}); true");
  await sleep(400);
  await screenshot("mvp-05-normal-job-preview.png");

  const previewText = await evaluate("document.getElementById('scenario-preview').innerText");
  if (!previewText.toUpperCase().includes("НОВЫХ НАЗНАЧЕНИЙ") || !previewText.includes("2 назначено")) throw new Error("Normal job was not assigned in preview");
  await evaluate("document.getElementById('apply-preview').click(); true");
  await waitFor("document.getElementById('compare').textContent.includes('Изменения в оставшейся части смены')");
  await evaluate("document.querySelector('.plans-section').scrollIntoView({block:'start'}); true");
  await sleep(400);
  await screenshot("mvp-06-same-horizon-comparison.png");

  const result = await evaluate(`({
    routing: document.getElementById('routing-status').textContent.trim(),
    source: document.getElementById('source-mode').textContent.trim(),
    assigned: document.querySelector('#summary .metric .v')?.textContent.trim(),
    comparison: document.getElementById('compare').textContent.trim(),
    roadFeatures: document.querySelectorAll('.leaflet-overlay-pane path').length
  })`);
  console.log(JSON.stringify({ result, browserErrors }, null, 2));
} catch (error) {
  const diagnostic = await evaluate(`({
    readyState: document.readyState,
    title: document.title,
    routing: document.getElementById('routing-status')?.textContent,
    notice: document.querySelector('.notice')?.textContent,
    bodyText: document.body?.innerText?.slice(0, 500)
  })`).catch(diagnosticError => ({ diagnosticError: diagnosticError.message }));
  console.error(JSON.stringify({ diagnostic, browserErrors }, null, 2));
  throw error;
} finally {
  socket.close();
  browser.kill();
}
