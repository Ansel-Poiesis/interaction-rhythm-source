const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");

function extractFunction(name) {
  const declaration = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`);
  const match = declaration.exec(source);
  assert.ok(match, `missing function ${name}`);
  const bodyStart = source.indexOf("{", match.index);
  let depth = 0;
  let quote = "";
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let index = bodyStart; index < source.length; index += 1) {
    const char = source[index];
    const next = source[index + 1];
    if (lineComment) {
      if (char === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (char === "*" && next === "/") {
        blockComment = false;
        index += 1;
      }
      continue;
    }
    if (quote) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === quote) quote = "";
      continue;
    }
    if (char === "'" || char === '"' || char === "`") quote = char;
    else if (char === "/" && next === "/") {
      lineComment = true;
      index += 1;
    } else if (char === "/" && next === "*") {
      blockComment = true;
      index += 1;
    } else if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(match.index, index + 1);
    }
  }
  throw new Error(`unterminated function ${name}`);
}

const functionNames = [
  "isoDate",
  "todayIso",
  "shiftDateIso",
  "fetchJson",
  "dashboardCacheFresh",
  "readDashboardCache",
  "writeDashboardCache",
  "applyDashboardBundle",
  "prefetchAdjacentDate",
  "loadData",
];
const functionsSource = functionNames.map(extractFunction).join("\n");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function response(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

function createHarness(fetchImpl) {
  const timers = new Map();
  let timerId = 0;
  const calls = [];
  const renders = [];
  const offline = [];
  const context = vm.createContext({
    AbortController,
    console,
    selectedDate: "2026-11-24",
    lastDateDirection: -1,
    dataLoadSequence: 0,
    dataLoadController: null,
    dataRetryTimer: null,
    dashboardCache: new Map(),
    dashboardCacheLimit: 12,
    dashboardPrefetchController: null,
    heatRange: "week",
    heatmapDays: [],
    monthHeatmap: null,
    yearHeatmap: null,
    document: { hidden: false, getElementById: () => null },
    fetch: (url, options) => {
      calls.push({ url, options });
      return fetchImpl(url, options);
    },
    setTimeout: (callback, delay) => {
      timerId += 1;
      timers.set(timerId, { callback, delay });
      return timerId;
    },
    clearTimeout: (id) => timers.delete(id),
    setOfflineMessage: (...args) => offline.push(args),
    t: (key) => key,
    updateDateControls: () => {},
    render: (data) => renders.push(["day", data.date]),
    renderHeatmap: () => renders.push(["heatmap"]),
    renderApps: (apps) => renders.push(["apps", apps.length]),
    loadMonthHeatmap: () => {},
    loadYearHeatmap: () => {},
  });
  vm.runInContext(functionsSource, context);
  context.todayIso = () => "2026-11-24";
  return {
    context,
    calls,
    renders,
    offline,
    runTimers() {
      const pending = [...timers.values()];
      timers.clear();
      pending.forEach((timer) => timer.callback());
    },
  };
}

async function testPrimaryPanelsRenderIndependentlyAndPrefetchVisibleData() {
  const day = deferred();
  const harness = createHarness((url) => {
    if (url.startsWith("/api/day")) {
      return url.includes("date=2026-11-23") ? response({ date: "2026-11-23" }) : day.promise;
    }
    if (url.startsWith("/api/heatmap")) return response({ data: [] });
    if (url.startsWith("/api/apps")) return response({ apps: [{}, {}] });
    throw new Error(`unexpected URL ${url}`);
  });

  const loading = harness.context.loadData();
  await flush();
  assert.deepEqual(harness.renders, [["heatmap"], ["apps", 2]]);
  day.resolve({ ok: true, json: async () => ({ date: "2026-11-24" }) });
  await loading;
  assert.deepEqual(harness.renders, [["heatmap"], ["apps", 2], ["day", "2026-11-24"]]);

  harness.runTimers();
  await flush();
  assert.equal(harness.calls.length, 6);
  assert.equal(harness.calls.some((call) => call.url.startsWith("/api/agent/observations")), false);
  assert.equal(harness.calls.slice(3).every((call) => call.url.includes("2026-11-23")), true);
}

async function testCompleteFreshCacheRendersWithoutNetwork() {
  const harness = createHarness(() => {
    throw new Error("cached dashboard should not fetch");
  });
  const context = harness.context;
  context.writeDashboardCache("2026-11-24", "day", { date: "2026-11-24" });
  context.writeDashboardCache("2026-11-24", "heatmap", { data: [] });
  context.writeDashboardCache("2026-11-24", "apps", { apps: [{}, {}] });

  await context.loadData();
  assert.deepEqual(harness.calls, []);
  assert.deepEqual(harness.renders, [["day", "2026-11-24"], ["heatmap"], ["apps", 2]]);
}

async function testPartialCacheRendersWhileMissingDataRefreshes() {
  const freshDay = deferred();
  const harness = createHarness((url) => {
    if (url.startsWith("/api/day")) return freshDay.promise;
    if (url.startsWith("/api/heatmap")) return response({ data: [] });
    if (url.startsWith("/api/apps")) return response({ apps: [] });
    throw new Error(`unexpected URL ${url}`);
  });
  harness.context.writeDashboardCache("2026-11-24", "day", { date: "2026-11-24", cached: true });

  const loading = harness.context.loadData();
  await flush();
  assert.deepEqual(harness.renders, [["day", "2026-11-24"], ["heatmap"], ["apps", 0]]);

  freshDay.resolve({ ok: true, json: async () => ({ date: "2026-11-24", cached: false }) });
  await loading;
  assert.equal(harness.renders.filter((item) => item[0] === "day").length, 2);
}

async function testStaleCompleteCacheRendersWhileRefreshing() {
  const day = deferred();
  const heatmap = deferred();
  const apps = deferred();
  const harness = createHarness((url) => {
    if (url.startsWith("/api/day")) return day.promise;
    if (url.startsWith("/api/heatmap")) return heatmap.promise;
    if (url.startsWith("/api/apps")) return apps.promise;
    throw new Error(`unexpected URL ${url}`);
  });
  const context = harness.context;
  context.writeDashboardCache("2026-11-24", "day", { date: "2026-11-24", cached: true });
  context.writeDashboardCache("2026-11-24", "heatmap", { data: [] });
  context.writeDashboardCache("2026-11-24", "apps", { apps: [] });
  context.dashboardCache.get("2026-11-24").savedAt = Date.now() - 300001;

  const loading = context.loadData();
  await flush();
  assert.equal(harness.renders.length, 3);
  assert.equal(harness.renders.some((item) => item[0] === "day" && item[1] === "2026-11-24"), true);
  assert.equal(harness.renders.some((item) => item[0] === "heatmap"), true);
  assert.equal(harness.renders.some((item) => item[0] === "apps"), true);
  assert.equal(harness.calls.length, 3);

  day.resolve({ ok: true, json: async () => ({ date: "2026-11-24", cached: false }) });
  heatmap.resolve({ ok: true, json: async () => ({ data: [] }) });
  apps.resolve({ ok: true, json: async () => ({ apps: [] }) });
  await loading;
  assert.equal(harness.renders.filter((item) => item[0] === "day").length, 2);
}

async function testForcedRefreshBypassesCompleteCacheFastPath() {
  const harness = createHarness((url) => {
    if (url.startsWith("/api/day")) return response({ date: "2026-11-24" });
    if (url.startsWith("/api/heatmap")) return response({ data: [] });
    if (url.startsWith("/api/apps")) return response({ apps: [] });
    throw new Error(`unexpected URL ${url}`);
  });
  const context = harness.context;
  context.writeDashboardCache("2026-11-24", "day", { date: "2026-11-24" });
  context.writeDashboardCache("2026-11-24", "heatmap", { data: [] });
  context.writeDashboardCache("2026-11-24", "apps", { apps: [] });

  await context.loadData({ force: true });
  assert.equal(harness.calls.length, 3);
  assert.equal(harness.renders.filter((item) => item[0] === "day").length, 2);
}

async function testNewDateAbortsAndRejectsStalePrimaryData() {
  const oldDay = deferred();
  const oldHeatmap = deferred();
  const oldApps = deferred();
  const harness = createHarness((url) => {
    if (url.includes("2026-11-24")) {
      if (url.startsWith("/api/day")) return oldDay.promise;
      if (url.startsWith("/api/heatmap")) return oldHeatmap.promise;
      if (url.startsWith("/api/apps")) return oldApps.promise;
    }
    if (url.startsWith("/api/day")) return response({ date: "2026-11-23" });
    if (url.startsWith("/api/heatmap")) return response({ data: [] });
    if (url.startsWith("/api/apps")) return response({ apps: [] });
    throw new Error(`unexpected URL ${url}`);
  });

  const first = harness.context.loadData();
  await flush();
  const firstSignals = harness.calls.map((call) => call.options.signal);
  harness.context.selectedDate = "2026-11-23";
  await harness.context.loadData();
  assert.equal(firstSignals.every((signal) => signal.aborted), true);

  oldDay.resolve({ ok: true, json: async () => ({ date: "2026-11-24" }) });
  oldHeatmap.resolve({ ok: true, json: async () => ({ data: [] }) });
  oldApps.resolve({ ok: true, json: async () => ({ apps: [] }) });
  await first;
  assert.deepEqual(harness.renders, [["day", "2026-11-23"], ["heatmap"], ["apps", 0]]);
}

async function testTotalFailureSchedulesBoundedRetry() {
  const harness = createHarness(() => Promise.reject(new Error("offline")));
  await harness.context.loadData();
  assert.equal(harness.calls.length, 3);
  assert.equal(harness.offline.at(-1)[0], true);
  assert.ok(harness.context.dataRetryTimer);
}

async function testNewPagingAbortsPreviousPrefetch() {
  const harness = createHarness((url) => {
    if (url.startsWith("/api/day")) return response({ date: url.includes("2026-11-23") ? "2026-11-23" : "2026-11-24" });
    if (url.startsWith("/api/heatmap")) return response({ data: [] });
    if (url.startsWith("/api/apps")) return response({ apps: [] });
    throw new Error(`unexpected URL ${url}`);
  });

  await harness.context.loadData();
  const prefetchSignal = harness.context.dashboardPrefetchController.signal;
  harness.context.selectedDate = "2026-11-23";
  await harness.context.loadData();
  assert.equal(prefetchSignal.aborted, true);
  harness.runTimers();
  await flush();
  assert.equal(harness.calls.length, 9);
  assert.equal(harness.calls.slice(6).every((call) => call.url.includes("2026-11-22")), true);
}

async function testCacheFreshnessAndEvictionRules() {
  const harness = createHarness(() => {
    throw new Error("cache test should not fetch");
  });
  const context = harness.context;
  for (let index = 0; index < 13; index += 1) {
    context.writeDashboardCache(`2026-11-${String(index + 1).padStart(2, "0")}`, "day", { date: "2026-11-01" });
  }
  assert.equal(context.dashboardCache.size, 12);
  assert.equal(context.dashboardCache.has("2026-11-01"), false);

  const entry = { savedAt: Date.now() - 16000 };
  assert.equal(context.dashboardCacheFresh(entry, "2026-11-24"), false);
  entry.savedAt = Date.now() - 14000;
  assert.equal(context.dashboardCacheFresh(entry, "2026-11-24"), true);
  entry.savedAt = Date.now() - 240000;
  assert.equal(context.dashboardCacheFresh(entry, "2026-11-23"), true);
  entry.savedAt = Date.now() - 360000;
  assert.equal(context.dashboardCacheFresh(entry, "2026-11-23"), false);
}

async function main() {
  const tests = [
    testPrimaryPanelsRenderIndependentlyAndPrefetchVisibleData,
    testCompleteFreshCacheRendersWithoutNetwork,
    testPartialCacheRendersWhileMissingDataRefreshes,
    testStaleCompleteCacheRendersWhileRefreshing,
    testForcedRefreshBypassesCompleteCacheFastPath,
    testNewDateAbortsAndRejectsStalePrimaryData,
    testTotalFailureSchedulesBoundedRetry,
    testNewPagingAbortsPreviousPrefetch,
    testCacheFreshnessAndEvictionRules,
  ];
  for (const test of tests) await test();
  console.log(`FRONTEND_LOADING_OK (${tests.length} regression scenarios)`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
