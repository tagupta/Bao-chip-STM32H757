import puppeteer from 'puppeteer-core';
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const BRAVE = '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser';
const FFMPEG = process.env.FFMPEG;
const mode = process.argv[2] || 'stills';        // stills | video
const FPS = 30;

const browser = await puppeteer.launch({
  executablePath: BRAVE, headless: 'new',
  args: ['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist','--hide-scrollbars'],
  defaultViewport: { width:1280, height:720, deviceScaleFactor:1 },
});
const page = await browser.newPage();
page.on('pageerror', e => console.log('[pageerror]', e.message));
await page.goto('http://localhost:8765/index.html', { waitUntil:'load' });
await page.waitForFunction('window.__ready === true', { timeout: 60000 });
const total = await page.evaluate('window.__TOTAL');

if (mode === 'stills') {
  // Times are "<beatId>:<seconds into beat>", e.g. p2:10
  fs.mkdirSync('stills', { recursive:true });
  for (const tok of (process.argv[3] || 'i2:8').split(',')) {
    const [id, off] = tok.split(':');
    const t = await page.evaluate((id, off) => window.BEATS.find(b => b.id === id).t0 + off, id, Number(off));
    await page.evaluate(t => window.renderAt(t), t);
    await page.screenshot({ path:`stills/${id}_${off}.png` });
    console.log('still', tok, t.toFixed(1));
  }
} else {
  const ff = spawn(FFMPEG, ['-y','-f','image2pipe','-framerate',String(FPS),'-c:v','mjpeg','-i','-',
    '-i','narration.wav','-map','0:v','-map','1:a',
    '-vf','scale=out_range=tv,format=yuv420p','-c:v','libx264','-crf','18','-preset','medium',
    '-c:a','aac','-b:a','160k','-shortest','-movflags','+faststart','kakute_dabao_360.mp4'], { stdio:['pipe','ignore','inherit'] });
  const n = Math.floor(total * FPS);
  for (let i = 0; i < n; i++) {
    await page.evaluate(t => window.renderAt(t), i / FPS);
    const buf = await page.screenshot({ type:'jpeg', quality:93 });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (i % 300 === 0) console.log(`frame ${i}/${n}`);
  }
  ff.stdin.end();
  await new Promise(r => ff.on('close', r));
  console.log('done');
}
await browser.close();
