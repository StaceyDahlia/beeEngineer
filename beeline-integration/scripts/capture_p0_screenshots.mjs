import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const chrome = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const appUrl = "http://127.0.0.1:8000/";
const port = 9444;
const outputDir = resolve("docs", "screenshots");
mkdirSync(outputDir, { recursive: true });
const browser = spawn(chrome,["--headless=new","--disable-gpu","--no-sandbox","--no-first-run","--remote-allow-origins=*",`--remote-debugging-port=${port}`,`--user-data-dir=${join(tmpdir(),`beeline-p0-${process.pid}`)}`,"--window-size=1800,1200",appUrl],{stdio:["ignore","ignore","pipe"],windowsHide:true});
const sleep=ms=>new Promise(resolveSleep=>setTimeout(resolveSleep,ms));
async function endpoint(){for(let i=0;i<100;i+=1){try{const list=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();const page=list.find(x=>x.type==="page"&&x.url.includes("127.0.0.1:8000"));if(page)return page;}catch(_){}await sleep(100);}throw new Error("Chrome DevTools endpoint unavailable");}
const page=await endpoint(),socket=new WebSocket(page.webSocketDebuggerUrl);await new Promise((ok,fail)=>{socket.addEventListener("open",ok,{once:true});socket.addEventListener("error",fail,{once:true});});
let seq=0;const pending=new Map(),network=[],consoleErrors=[];
socket.addEventListener("message",async event=>{const raw=typeof event.data==="string"?event.data:await event.data.text();const msg=JSON.parse(raw);if(msg.method==="Network.responseReceived"&&msg.params.response.url.includes("photon.komoot.io"))network.push({url:msg.params.response.url,status:msg.params.response.status});if(msg.method==="Runtime.consoleAPICalled"&&["error","warning"].includes(msg.params.type))consoleErrors.push(msg.params.args.map(x=>x.value||x.description||"").join(" "));if(!msg.id||!pending.has(msg.id))return;const entry=pending.get(msg.id);pending.delete(msg.id);msg.error?entry.reject(new Error(msg.error.message)):entry.resolve(msg.result);});
function command(method,params={}){const id=++seq;return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const result=await command("Runtime.evaluate",{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;}
async function waitFor(expression,timeout=35000){const start=Date.now();while(Date.now()-start<timeout){if(await evaluate(`Boolean(${expression})`))return;await sleep(150);}throw new Error(`Timeout: ${expression}`);}
async function shot(name){const result=await command("Page.captureScreenshot",{format:"png",captureBeyondViewport:false,fromSurface:true});writeFileSync(join(outputDir,name),Buffer.from(result.data,"base64"));}

try{
  await command("Page.enable");await command("Runtime.enable");await command("Network.enable");
  await command("Emulation.setDeviceMetricsOverride",{width:1800,height:1200,deviceScaleFactor:1,mobile:false});
  await command("Page.navigate",{url:appUrl});await waitFor("document.getElementById('express-button')");
  await evaluate("localStorage.clear();location.reload();true");await waitFor("document.querySelector('#routing-status')&&!document.querySelector('#routing-status').classList.contains('loading')");
  const oldRequestStatus=await evaluate("fetch('https://photon.komoot.io/api/?q=Moscow&limit=1&lang=ru').then(response=>response.status).catch(()=>0)");

  await evaluate(`(()=>{document.getElementById('express-button').click();document.getElementById('express-name').value='Аварийное восстановление';document.getElementById('express-address').value='Москва, Тверская улица, 7';document.getElementById('express-address').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('geocode-button').click();return true;})()`);
  await waitFor("document.querySelector('[data-geocode-index]')||document.getElementById('geocode-message').textContent.includes('Не удалось')",25000);
  const geocodeSucceeded=await evaluate("Boolean(document.querySelector('[data-geocode-index]'))");
  if(!geocodeSucceeded)throw new Error("Forward geocoder did not return a candidate");
  await evaluate("document.querySelector('[data-geocode-index]').click();true");await waitFor("document.getElementById('confirm-point-button')");await evaluate("document.getElementById('confirm-point-button').click();true");await waitFor("document.getElementById('express-form').dataset.pointConfirmed==='true'");await sleep(350);await shot("p0-01-address-selected.png");

  await evaluate("document.querySelector('[data-close=\"express-modal\"]')?.click();document.getElementById('express-button').click();document.getElementById('express-name').value='Ручная точка';document.getElementById('map-pick-button').click();true");await waitFor("document.body.classList.contains('map-pick-mode')");
  await command("Network.setBlockedURLs",{urls:["*photon.komoot.io/reverse*"]});
  await evaluate(`(()=>{const map=document.getElementById('map');const r=map.getBoundingClientRect();map.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:r.left+r.width*.47,clientY:r.top+r.height*.53}));return true;})()`);
  await waitFor("document.getElementById('confirm-point-button')",10000);await sleep(700);await shot("p0-02-manual-map-point.png");await evaluate("document.getElementById('confirm-point-button').click();true");await waitFor("document.getElementById('express-form').dataset.pointConfirmed==='true'");const manualConfirmed=await evaluate("({confirmed:document.getElementById('express-form').dataset.pointConfirmed,coords:[document.getElementById('express-form').dataset.geocodedLat,document.getElementById('express-form').dataset.geocodedLon],submitEnabled:!document.getElementById('express-submit').disabled})");await command("Network.setBlockedURLs",{urls:[]});
  await evaluate("document.querySelector('[data-close=\"express-modal\"]')?.click();document.getElementById('demo-emergency-button').click();true");await waitFor("document.getElementById('apply-preview')");await evaluate("document.getElementById('apply-preview').click();true");await waitFor("document.querySelector('#compare .comparison-table')",35000);
  await evaluate("document.getElementById('compare').scrollIntoView({block:'center'});true");await sleep(400);await shot("p0-03-comparison-dark.png");
  await evaluate("document.getElementById('theme-toggle').click();document.getElementById('compare').scrollIntoView({block:'center'});true");await sleep(350);await shot("p0-04-comparison-light.png");
  const checks=await evaluate(`({comparisonRows:document.querySelectorAll('#compare .comparison-table tbody tr').length,theme:document.documentElement.getAttribute('data-theme'),tableOverflow:getComputedStyle(document.querySelector('.comparison-table-scroll')).overflowX})`);
  console.log(JSON.stringify({oldRequestStatus,network,manualConfirmed,consoleErrors,checks},null,2));
}catch(error){console.error(JSON.stringify({error:String(error),network,consoleErrors},null,2));throw error;}finally{socket.close();browser.kill();}
