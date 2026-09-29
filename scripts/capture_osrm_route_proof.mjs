import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const APP_URL = process.env.APP_URL || "http://127.0.0.1:8000/";
const DEBUG_PORT = 9667;
const screenshotPath = resolve("docs", "screenshots", "mvp-07-osrm-road-geometry-closeup.png");
mkdirSync(resolve("docs", "screenshots"), { recursive: true });

const browser = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
  "--disable-dev-shm-usage", `--remote-debugging-port=${DEBUG_PORT}`,
  "--remote-allow-origins=*", `--user-data-dir=${join(tmpdir(), `beeline-osrm-proof-${process.pid}`)}`,
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
let commandId = 0;
const pending = new Map();
const browserErrors = [];
socket.addEventListener("message", async event => {
  const raw = typeof event.data === "string" ? event.data : await event.data.text();
  const message = JSON.parse(raw);
  if (message.method === "Runtime.exceptionThrown") {
    browserErrors.push(message.params.exceptionDetails?.exception?.description || message.params.exceptionDetails?.text || "exception");
  }
  if (message.id && pending.has(message.id)) {
    const request = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) request.reject(new Error(message.error.message));
    else request.resolve(message.result);
  }
});
function command(method, params = {}) {
  const id = ++commandId;
  return new Promise((resolveCommand, rejectCommand) => {
    pending.set(id, { resolve: resolveCommand, reject: rejectCommand });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "evaluation failed");
  return result.result.value;
}
async function waitFor(expression, timeoutMs = 90000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await sleep(200);
  }
  throw new Error(`Timed out waiting for ${expression}`);
}

try {
  await command("Page.enable");
  await command("Runtime.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1800, height: 1200, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: APP_URL });
  await waitFor("window.__lastPlanResponse?.travel_model?.provider === 'osrm'");
  await waitFor("window.__routeGeometryDiagnostics?.some(item => item.source === 'osrm')");

  const selection = await evaluate(`(() => {
    const routes=window.__lastPlanResponse.routes.filter(route=>route.route_geometry?.features?.length);
    const route=routes[0];
    const candidates=route.route_geometry.features.map((feature,index)=>({
      index,
      distance:Number(feature.properties.distance_m),
      points:feature.geometry.coordinates.length
    })).filter(item=>item.points>2&&item.distance>=250);
    candidates.sort((a,b)=>a.distance-b.distance);
    const segment=candidates[0]||{index:0};
    const engineer=document.getElementById('map-engineer-select');
    engineer.value=route.engineer_id;
    engineer.dispatchEvent(new Event('change',{bubbles:true}));
    const layerMode=document.getElementById('map-layer-mode');
    layerMode.value='selected_route';
    layerMode.dispatchEvent(new Event('change',{bubbles:true}));
    return {engineer_id:route.engineer_id,segment_index:segment.index};
  })()`);
  await sleep(700);
  await evaluate(`window.BeelineRouteDebug.focusSegment(${JSON.stringify(selection.engineer_id)},${Number(selection.segment_index)})`);
  await sleep(1800);

  const diagnostics = await evaluate(`(() => {
    const plan=window.__lastPlanResponse;
    const route=plan.routes.find(item=>item.engineer_id===${JSON.stringify(selection.engineer_id)});
    const features=route.route_geometry.features;
    const segmentDistance=features.reduce((sum,feature)=>sum+Number(feature.properties.distance_m),0);
    const allRouteDistance=plan.routes.reduce((sum,item)=>sum+Number(item.distance_m||0),0);
    return {
      engineer_id:route.engineer_id,
      route_geometry_present:Boolean(route.route_geometry),
      feature_count:features.length,
      segments:features.map(feature=>({
        source:feature.properties.source,
        geometry_type:feature.geometry.type,
        coordinate_count:feature.geometry.coordinates.length,
        distance_m:feature.properties.distance_m,
        travel_min:feature.properties.travel_min,
        matrix_id:feature.properties.matrix_id
      })),
      segment_distance_sum_m:segmentDistance,
      route_distance_m:route.distance_m,
      route_kpi_matches:segmentDistance===Number(route.distance_m),
      plan_total_distance_m:plan.metrics.total_distance_m,
      plan_kpi_matches:allRouteDistance===Number(plan.metrics.total_distance_m),
      rendered_osrm_paths:document.querySelectorAll('.route-line--osrm').length,
      rendered_calculated_paths:document.querySelectorAll('.route-line--calculated').length,
      rendered_coordinate_counts:[...document.querySelectorAll('.route-line--osrm')].map(path=>Number(path.dataset.coordinateCount))
    };
  })()`);
  diagnostics.browserErrors = browserErrors;

  if (
    !diagnostics.route_geometry_present ||
    !diagnostics.route_kpi_matches ||
    !diagnostics.plan_kpi_matches ||
    diagnostics.rendered_osrm_paths < 1 ||
    diagnostics.rendered_calculated_paths !== 0 ||
    diagnostics.segments.some(segment => segment.source !== "osrm" || segment.geometry_type !== "LineString" || segment.coordinate_count <= 2) ||
    diagnostics.rendered_coordinate_counts.some(count => count <= 2) ||
    browserErrors.length
  ) {
    throw new Error(`OSRM browser proof failed: ${JSON.stringify(diagnostics)}`);
  }

  const image = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false, fromSurface: true });
  writeFileSync(screenshotPath, Buffer.from(image.data, "base64"));
  console.log(JSON.stringify({ selection, diagnostics, screenshotPath }, null, 2));
} finally {
  socket.close();
  browser.kill();
}
