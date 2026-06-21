const puppeteer = require("puppeteer");

const BASE = "http://localhost:3919/hyperliquid";
const WALLET = "0x0000000000000000000000000000000000000001";

(async () => {
  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });

  // Seed a wallet so the connected-state header renders.
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate((w) => localStorage.setItem("hl_wallet", w), WALLET);

  const shots = [
    ["home", BASE],
    ["indexes", BASE + "/indexes"],
    ["live", BASE + "/live"],
  ];

  for (const [name, url] of shots) {
    await page.goto(url, { waitUntil: "networkidle0", timeout: 45000 }).catch(() => {});
    // let fonts + entrance animation settle
    await new Promise((r) => setTimeout(r, 1800));
    await page.screenshot({ path: `/tmp/hl_${name}.png`, fullPage: false });
    console.log("shot:", name);
  }
  await browser.close();
  console.log("done");
})().catch((e) => { console.error(e); process.exit(1); });
