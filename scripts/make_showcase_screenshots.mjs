/**
 * Regenerate assets/showcase/*.jpg -- the six REAL screenshots of the shared
 * live demo account that /for-firms/'s product showcase (and the homepage's
 * roster teaser) display. Not mockups: the page copy promises "the same one
 * you land on if you click 'Live Demo'", so these must be captured from the
 * live site, signed in exactly the way a visitor is.
 *
 * Originally captured by hand on 2026-08-10 (#323). Re-captured 2026-09-02
 * after the demo firm rename ("DeadlineRadar Demo Firm" -> "Deadline-Radar
 * Demo Firm", D1) because the shipped images still showed the pre-rename
 * wordmark and firm name. No generator existed before; this is it.
 *
 * Usage (from the deadlineradar root, worker/ deps installed):
 *   node scripts/make_showcase_screenshots.mjs            # writes assets/showcase/
 *   node scripts/make_showcase_screenshots.mjs --out DIR  # write to DIR instead, to eyeball first
 * then `python generate.py` mirrors assets/showcase/ into docs/showcase/.
 * Either form spends one real demo login (see the rate limit below) -- the
 * only thing --out changes is where the files land.
 *
 * Drives the locally installed Chrome (puppeteer-core, a worker/ devDependency
 * -- it downloads nothing) headless, laid out at 2560x1270 and downscaled by
 * Chrome to the showcase's 1568x778. One demo login per run: /firm/demo-login is rate-limited 3/10min per
 * IP and 10/10min globally, so every view is captured inside a single session.
 * The mobility shot submits one "just checking" (unsaved) California -> Texas
 * tax check, the same real result the original showed.
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(new URL("../worker/package.json", import.meta.url));
const puppeteer = require("puppeteer-core");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const SITE = process.env.SHOWCASE_BASE_URL || "https://deadline-radar.com";
// The originals were laid out at a 2560x1270 CSS-px window and downscaled to
// 1568x778 (ratio 0.6125): the showcase frame is 1568 wide on the page, and
// the zoomed-out layout is what lets a whole dashboard view fit one frame.
// A fractional deviceScaleFactor makes Chrome do the same downscale itself,
// so the screenshot comes out at exactly OUTPUT_W x OUTPUT_H.
const OUTPUT_W = 1568;
const OUTPUT_H = 778;
const LAYOUT_W = 2560;
const LAYOUT_H = 1270;
const VIEWPORT = { width: LAYOUT_W, height: LAYOUT_H, deviceScaleFactor: OUTPUT_W / LAYOUT_W };
const JPEG_QUALITY = 82;

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);

// The sticky site header is ~60 CSS px tall (nav.mainnav in generate.py);
// anything scrolled to sit above HEADER_CLEAR slides under it.
const HEADER_CLEAR = 68;

// Dashboard views, in sidebar order; `view` is the sidebar tab's data-view.
// `waitFor` is content that must have rendered before the shot; `anchor` is
// scrolled to just below the sticky header (default: top of page).
const DASHBOARD_SHOTS = [
  { file: "roster.jpg", view: "roster", waitFor: "#dr-stat-row .dr-stat-card" },
  { file: "calendar.jpg", view: "calendar" },
  { file: "map.jpg", view: "map" },
  { file: "cpe-hours.jpg", view: "cpe" },
  // The caption promises "compliance summary AND audit trail": with the
  // summary table's rows wrapping their full emails, the trail's rows fall
  // below the fold from the top of the page, so start the frame at the
  // report's own heading instead.
  {
    file: "reports.jpg", view: "reports",
    waitFor: "#dr-audit-trail-body tbody tr",
    anchor: "#dr-view-reports .dr-report-toolbar", anchorOffset: 20,
  },
];
// Both attestation boxes ticked, as the original was: they are the firm's
// own inputs ("we can't verify either of these"), and a CA CPA in good
// standing doing tax work in TX is the CLEAR result the caption describes.
const MOBILITY_SHOT = {
  file: "mobility.jpg", home: "california", target: "texas", service: "tax",
  checkboxes: ["#dr-mob-standing", "#dr-mob-equiv"],
};

function parseArgs(argv) {
  const out = { outDir: path.join(ROOT, "assets", "showcase") };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--out" && argv[i + 1]) out.outDir = path.resolve(argv[++i]);
  }
  return out;
}

function findChrome() {
  const hit = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!hit) throw new Error(`No Chrome found; set CHROME_PATH. Tried: ${CHROME_CANDIDATES.join(", ")}`);
  return hit;
}

async function settle(page, ms = 900) {
  // Let the view's fetches land, then give layout/transitions a beat.
  await page.waitForNetworkIdle({ idleTime: ms, timeout: 20_000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 250));
}

// The one-click prompts that greet a fresh demo session, each dismissed the
// way a visitor dismisses it -- through its own button, never by hiding the
// element -- so a shot is exactly the page state after those clicks (the
// tour's skip also clears its target highlight, which hiding the card would
// leave behind). The demo firm never persists a dismissal (every dismiss
// endpoint is a server-side no-op for demo_locked, and the cookie notice
// lives in this session's localStorage), so nothing here touches what the
// next visitor sees, and each can come back on a later fresh session --
// hence re-checked before every shot. The demo banner and the assistant
// launcher are deliberately KEPT: both are real product UI a visitor sees,
// and the showcase caption points at the banner.
const DISMISS_BUTTONS = [
  "#dr-cookie-notice-dismiss",   // cookie notice
  "#dr-product-tour-skip-btn",   // product tour (auto-starts for demo)
  "#dr-onboarding-dismiss-btn",  // "Getting started" checklist (always pending for demo)
  "#dr-nps-dismiss-btn",         // NPS ask ("Not now")
];

async function dismissPrompts(page) {
  for (const sel of DISMISS_BUTTONS) {
    const btn = await page.$(sel);
    if (btn && (await btn.isVisible())) await btn.click();
  }
}

async function shoot(page, outPath) {
  // Viewport-only, never captureBeyondViewport: the latter re-renders from
  // document origin, and on a scrolled page (the mobility shot) that tears
  // the sticky header/sidebar across the frame.
  await page.screenshot({
    path: outPath,
    type: "jpeg",
    quality: JPEG_QUALITY,
    captureBeyondViewport: false,
  });
  const { size } = fs.statSync(outPath);
  console.log(`wrote ${path.relative(ROOT, outPath)} (${size} bytes)`);
}

async function demoLogin(page) {
  await page.goto(`${SITE}/api/firm/demo-login`, { waitUntil: "networkidle2" });
  const btn = await page.waitForSelector('form[action="/api/firm/demo-login"] button[type="submit"]', { timeout: 15_000 });
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2", timeout: 30_000 }), btn.click()]);
  const url = page.url();
  if (!url.includes("/firm-dashboard/")) {
    const text = await page.evaluate(() => document.body.innerText.slice(0, 300));
    throw new Error(`demo login did not land on the dashboard (at ${url}): ${text}`);
  }
  // The sidebar firm name is filled from /api/firm/me -- wait for it so the
  // first shot is never the pre-fetch skeleton.
  await page.waitForFunction(
    () => /Demo Firm/.test((document.getElementById("dr-firm-name") || {}).textContent || ""),
    { timeout: 20_000 }
  );
}

async function scrollAnchorBelowHeader(page, selector, offset = 0) {
  await page.evaluate((sel, clear, off) => {
    const el = document.querySelector(sel);
    if (!el) throw new Error(`anchor not found: ${sel}`);
    const top = el.getBoundingClientRect().top + window.scrollY;
    window.scrollTo(0, Math.max(0, top - clear - off));
  }, selector, HEADER_CLEAR, offset);
  await new Promise((r) => setTimeout(r, 250));
}

async function captureDashboard(page, outDir) {
  for (const shot of DASHBOARD_SHOTS) {
    // The sidebar tab by id -- `[data-view]` alone also matches hidden
    // duplicates (mobile nav), which are not clickable.
    await page.click(`#dr-tab-${shot.view}`);
    await page.waitForSelector(`#dr-view-${shot.view}`, { visible: true, timeout: 15_000 });
    if (shot.waitFor) await page.waitForSelector(shot.waitFor, { timeout: 20_000 });
    await settle(page);
    await dismissPrompts(page);
    if (shot.anchor) await scrollAnchorBelowHeader(page, shot.anchor, shot.anchorOffset);
    else await page.evaluate(() => window.scrollTo(0, 0));
    await shoot(page, path.join(outDir, shot.file));
  }
}

async function captureMobility(page, outDir) {
  await page.goto(`${SITE}/firm-mobility/`, { waitUntil: "networkidle2" });
  await page.waitForSelector("#dr-mob-home", { visible: true, timeout: 15_000 });
  await page.select("#dr-mob-home", MOBILITY_SHOT.home);
  await page.select("#dr-mob-target", MOBILITY_SHOT.target);
  await page.select("#dr-mob-service", MOBILITY_SHOT.service);
  for (const sel of MOBILITY_SHOT.checkboxes) {
    const checked = await page.$eval(sel, (el) => el.checked);
    if (!checked) await page.click(sel);
  }
  // "Just checking -- don't save this result" is the default staff option,
  // so nothing is persisted on the shared demo account.
  await page.click('#dr-mobility-form button[type="submit"]');
  await page.waitForSelector("#dr-mobility-result:not([hidden])", { timeout: 30_000 });
  await page.waitForFunction(
    () => /California\s*(\u2192|->)\s*Texas/.test(document.getElementById("dr-mobility-result").innerText),
    { timeout: 30_000 }
  );
  await settle(page);
  await dismissPrompts(page);
  // Frame the result like the original: the "Run check" button is the first
  // thing under the sticky header (the attestation lines above it fully
  // hidden behind it, not sliced by its edge), with the "California ->
  // Texas" heading and the three result cards below.
  await scrollAnchorBelowHeader(page, '#dr-mobility-form button[type="submit"]');
  await shoot(page, path.join(outDir, MOBILITY_SHOT.file));
}

async function main() {
  const { outDir } = parseArgs(process.argv.slice(2));
  fs.mkdirSync(outDir, { recursive: true });
  const executablePath = findChrome();
  console.log(`chrome: ${executablePath}\nsite: ${SITE}\nout: ${outDir}`);
  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    args: ["--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "--lang=en-US"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport(VIEWPORT);
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    );
    await demoLogin(page);
    await captureDashboard(page, outDir);
    await captureMobility(page, outDir);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
