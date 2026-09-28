import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const EDGE = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const APP_URL = "http://127.0.0.1:8000/";
const DEBUG_PORT = 9333;
const outputDir = resolve("docs", "screenshots");
mkdirSync(outputDir, { recursive: true });

const browser = spawn(
  EDGE,
  [
    "--headless=new",
    "--disable-gpu",
    "--disable-gpu-sandbox",
    "--disable-software-rasterizer",
    "--no-sandbox",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${DEBUG_PORT}`,
    "--remote-allow-origins=*",
    `--user-data-dir=${join(tmpdir(), `beeline-ux-${process.pid}`)}`,
    "--window-size=1800,1200",
    APP_URL,
  ],
  { stdio: ["ignore", "ignore", "pipe"], windowsHide: true }
);
browser.stderr.on("data", (chunk) => process.stderr.write(chunk));

const sleep = (ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms));

async function waitForEndpoint() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
      const targets = await response.json();
      const page = targets.find((target) => target.type === "page" && target.url?.includes("127.0.0.1:8000"))
        || targets.find((target) => target.type === "page");
      if (page?.webSocketDebuggerUrl) return page;
    } catch (_) {
      // Browser is still starting.
    }
    await sleep(100);
  }
  throw new Error("Edge DevTools endpoint did not become ready");
}

const page = await waitForEndpoint();
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolveOpen, rejectOpen) => {
  socket.addEventListener("open", resolveOpen, { once: true });
  socket.addEventListener("error", rejectOpen, { once: true });
});

let sequence = 0;
const pending = new Map();
socket.addEventListener("message", async (event) => {
  const raw = typeof event.data === "string"
    ? event.data
    : typeof event.data?.text === "function"
      ? await event.data.text()
      : Buffer.from(event.data).toString("utf8");
  const message = JSON.parse(raw);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve: resolveCommand, reject: rejectCommand } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) rejectCommand(new Error(message.error.message));
  else resolveCommand(message.result);
});
socket.addEventListener("close", (event) => {
  console.error(`DevTools socket closed (${event.code}): ${event.reason || "no reason"}`);
  for (const { reject: rejectCommand } of pending.values()) rejectCommand(new Error("DevTools socket closed"));
  pending.clear();
});

function command(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolveCommand, rejectCommand) => {
    pending.set(id, { resolve: resolveCommand, reject: rejectCommand });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const result = await command("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Page evaluation failed");
  return result.result.value;
}

async function waitFor(expression, timeoutMs = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await sleep(120);
  }
  throw new Error(`Timed out waiting for: ${expression}`);
}

async function screenshot(filename) {
  const result = await command("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
    fromSurface: true,
  });
  writeFileSync(join(outputDir, filename), Buffer.from(result.data, "base64"));
}

try {
  await command("Page.enable");
  await command("Runtime.enable");
  await command("Emulation.setDeviceMetricsOverride", {
    width: 1800,
    height: 1200,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await command("Page.navigate", { url: APP_URL });
  await waitFor("location.origin === 'http://127.0.0.1:8000'");
  await evaluate("localStorage.clear(); location.reload(); true");
  await waitFor("document.querySelectorAll('#summary .metric').length === 4");
  await waitFor("document.querySelector('#routing-status') && !document.querySelector('#routing-status').classList.contains('loading')", 30000);
  await sleep(500);

  const overview = await evaluate(`({
    kpis:[...document.querySelectorAll('#summary .metric .v')].map(node=>node.textContent.trim()),
    status:document.querySelector('.plan-status-line')?.textContent.trim(),
    controls:['region-select','theme-toggle','baseline-button','optimize-button','map-engineer-select','map-layer-mode','map-fit-button','demo-emergency-button'].every(id=>Boolean(document.getElementById(id)))
  })`);
  await evaluate("scrollTo(0,0); true");
  await screenshot("01-overview.png");

  const interactionChecks = { filterCounts: {}, modals: {} };
  for (const filter of ["urgent", "unassigned", "changed", "all"]) {
    await evaluate(`document.querySelector('[data-filter="${filter}"]').click(); true`);
    interactionChecks.filterCounts[filter] = await evaluate("document.querySelectorAll('#jobs-list .job').length");
  }
  for (const [buttonId, modalId] of [["cancel-scenario-button", "cancel-modal"], ["unavailable-scenario-button", "unavailable-modal"], ["urgent-scenario-button", "express-modal"], ["express-button", "express-modal"]]) {
    await evaluate(`document.getElementById('${buttonId}').click(); true`);
    await waitFor(`document.getElementById('${modalId}').classList.contains('open')`);
    interactionChecks.modals[buttonId] = true;
    await evaluate(`document.querySelector('[data-close="${modalId}"]').click(); true`);
  }

  await evaluate("document.getElementById('baseline-button').click(); true");
  await waitFor("document.querySelector('#source-mode')?.textContent.includes('baseline_greedy') && !document.querySelector('#routing-status').classList.contains('loading')", 30000);
  interactionChecks.baseline = true;
  await evaluate("document.getElementById('optimize-button').click(); true");
  await waitFor("document.querySelector('#source-mode')?.textContent.includes('ortools_vrptw') && !document.querySelector('#routing-status').classList.contains('loading')", 30000);
  interactionChecks.optimize = true;

  for (const region of ["southeast", "east"]) {
    await evaluate(`(() => {const select=document.getElementById('region-select');select.value='${region}';select.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`);
    await waitFor(`document.getElementById('region-select').value === '${region}' && document.querySelector('#source-mode')?.textContent.includes('ortools_vrptw') && !document.querySelector('#routing-status').classList.contains('loading')`, 30000);
  }
  interactionChecks.region = true;
  await sleep(2600);

  const clusterClicked = await evaluate(`(() => {
    const cluster=document.querySelector('.marker-cluster');
    if(!cluster)return false;
    cluster.click();return true;
  })()`);
  if (clusterClicked) await sleep(600);

  let markerClicked = await evaluate(`(() => {
    const marker=[...document.querySelectorAll('.leaflet-marker-icon')].find(node=>node.querySelector('.map-marker'));
    if(!marker)return false;
    marker.click();return true;
  })()`);
  if (!markerClicked) {
    await evaluate("document.querySelector('[data-select-job]')?.click(); true");
    markerClicked = false;
  }
  await waitFor("document.querySelector('.job.selected') && document.querySelector('#dispatcher-detail .explain-item')");
  await sleep(1000);
  const selectedJob = await evaluate(`({
    markerClicked:${JSON.stringify(markerClicked)},
    selectedCards:document.querySelectorAll('.job.selected').length,
    selectedMarkers:document.querySelectorAll('.map-marker.selected').length,
    explanation:document.querySelector('#explanation-text')?.textContent.trim(),
    technicalDetails:Boolean(document.querySelector('.technical-details'))
  })`);
  await screenshot("02-selected-job.png");

  await evaluate("document.getElementById('engineers-tab').click(); true");
  await waitFor("document.querySelector('[data-focus-engineer-card]')");
  await evaluate("document.querySelector('[data-focus-engineer-card]').click(); true");
  await waitFor("document.querySelector('[data-focus-engineer-card].selected') && document.getElementById('map-engineer-select').value !== 'all'");
  await evaluate("document.getElementById('view-schedule-tab').click(); true");
  await waitFor("document.querySelector('[data-focus-timeline-engineer]')");
  await evaluate("document.querySelector('[data-focus-timeline-engineer]').click(); document.getElementById('view-map-tab').click(); true");
  await sleep(350);
  const routeSelection = await evaluate(`({
    engineer:document.getElementById('map-engineer-select').value,
    selectedCards:document.querySelectorAll('[data-focus-engineer-card].selected').length,
    routeOpacities:[...document.querySelectorAll('.leaflet-overlay-pane path')].map(path=>path.getAttribute('stroke-opacity')).filter(Boolean)
  })`);
  await screenshot("03-selected-route.png");

  await evaluate(`(() => {
    const select=document.getElementById('map-engineer-select');
    const option=[...select.options].find(item=>item.value!=='all'&&item.value!==select.value);
    if(!option)return false;
    select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));return true;
  })()`);
  await waitFor("document.getElementById('map-engineer-select').value !== 'all'");
  interactionChecks.mapEngineer = true;

  await evaluate(`(() => {
    const select=document.getElementById('map-layer-mode');
    select.value='selected_route';select.dispatchEvent(new Event('change',{bubbles:true}));
    return true;
  })()`);
  await waitFor("document.getElementById('map-layer-mode').value === 'selected_route'");
  await evaluate(`(() => {
    const select=document.getElementById('map-layer-mode');
    select.value='all';select.dispatchEvent(new Event('change',{bubbles:true}));
    document.getElementById('theme-toggle').click();document.getElementById('theme-toggle').click();
    document.getElementById('jobs-tab').click();
    return true;
  })()`);

  await evaluate("document.getElementById('demo-emergency-button').click(); true");
  await waitFor("document.getElementById('apply-preview')", 35000);
  await evaluate("document.getElementById('scenario-preview').scrollIntoView({block:'start'}); true");
  await sleep(350);
  const preview = await evaluate(`({
    heading:document.querySelector('#scenario-preview .preview-heading')?.textContent.trim(),
    emergency:document.querySelector('#scenario-preview .scenario-event')?.textContent.trim(),
    changes:document.querySelectorAll('#scenario-preview .change-item').length,
    canApply:Boolean(document.getElementById('apply-preview')),
    canDiscard:Boolean(document.getElementById('discard-preview'))
  })`);
  await screenshot("04-emergency-preview.png");

  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await evaluate("scrollTo(0,0); true");
  await sleep(250);
  const narrow = await evaluate(`({
    viewport:window.innerWidth,
    pageWidth:document.documentElement.scrollWidth,
    bodyOverflow:document.documentElement.scrollWidth>window.innerWidth
  })`);

  console.log(JSON.stringify({ overview, interactionChecks, clusterClicked, selectedJob, routeSelection, preview, narrow }, null, 2));
} finally {
  socket.close();
  browser.kill();
}
