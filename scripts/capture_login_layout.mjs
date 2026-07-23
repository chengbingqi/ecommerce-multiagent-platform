import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const url = process.argv[2] ?? "https://localhost:8443/workbench";
const outputDir = resolve(process.argv[3] ?? "reports/login-layout");
const port = 19229;
const profile = await mkdtemp(join(tmpdir(), "stage7-login-chrome-"));
await mkdir(outputDir, { recursive: true });

const chrome = spawn(
  "/usr/bin/google-chrome",
  [
    "--headless=new",
    "--disable-gpu",
    "--ignore-certificate-errors",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "about:blank",
  ],
  { stdio: "ignore" },
);
const chromeExited = new Promise((complete) => chrome.once("exit", complete));

const sleep = (milliseconds) => new Promise((done) => setTimeout(done, milliseconds));

async function pageTarget() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) =>
        response.json(),
      );
      const page = targets.find((target) => target.type === "page");
      if (page) return page;
    } catch {
      // Chrome is still starting.
    }
    await sleep(100);
  }
  throw new Error("Chrome DevTools endpoint did not become ready");
}

function connect(webSocketDebuggerUrl) {
  const socket = new WebSocket(webSocketDebuggerUrl);
  const pending = new Map();
  let nextId = 1;
  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (!payload.id || !pending.has(payload.id)) return;
    const { resolve: complete, reject } = pending.get(payload.id);
    pending.delete(payload.id);
    if (payload.error) reject(new Error(payload.error.message));
    else complete(payload.result);
  };
  const opened = new Promise((complete, reject) => {
    socket.onopen = complete;
    socket.onerror = () => reject(new Error("Chrome DevTools WebSocket failed"));
  });
  return {
    opened,
    close: () => socket.close(),
    send(method, params = {}) {
      const id = nextId;
      nextId += 1;
      return new Promise((complete, reject) => {
        pending.set(id, { resolve: complete, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
  };
}

const viewports = [
  { width: 1920, height: 1080, mobile: false },
  { width: 1366, height: 768, mobile: false },
  { width: 390, height: 844, mobile: true },
];

try {
  const target = await pageTarget();
  const cdp = connect(target.webSocketDebuggerUrl);
  await cdp.opened;
  await cdp.send("Page.enable");
  const results = [];
  for (const viewport of viewports) {
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor: 1,
      mobile: viewport.mobile,
    });
    await cdp.send("Page.navigate", { url });
    await sleep(1200);
    const evaluated = await cdp.send("Runtime.evaluate", {
      returnByValue: true,
      expression: `(() => {
        const card = document.querySelector('.auth-card');
        const titleParts = [...document.querySelectorAll('.auth-title span')];
        const rect = (element) => {
          const value = element?.getBoundingClientRect();
          return value ? { x: value.x, y: value.y, width: value.width, height: value.height, right: value.right, bottom: value.bottom } : null;
        };
        return {
          innerWidth,
          innerHeight,
          scrollWidth: document.documentElement.scrollWidth,
          scrollHeight: document.documentElement.scrollHeight,
          card: rect(card),
          titleParts: titleParts.map(rect),
          loginVisible: Boolean(document.querySelector('.auth-submit')),
        };
      })()`,
    });
    const screenshot = await cdp.send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
    });
    const stem = `login-${viewport.width}x${viewport.height}`;
    await writeFile(join(outputDir, `${stem}.png`), Buffer.from(screenshot.data, "base64"));
    results.push({ viewport, ...evaluated.result.value });
  }
  await writeFile(join(outputDir, "layout-metrics.json"), `${JSON.stringify(results, null, 2)}\n`);
  cdp.close();
  console.log(JSON.stringify(results));
} finally {
  chrome.kill("SIGTERM");
  await Promise.race([chromeExited, sleep(2000)]);
  await rm(profile, { recursive: true, force: true });
}
