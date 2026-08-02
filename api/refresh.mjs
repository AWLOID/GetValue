import chromium from '@sparticuz/chromium'
import puppeteer from 'puppeteer-core'
import fs from 'node:fs/promises'
import path from 'node:path'

const BASE_URL = 'https://supremevalues.com'
const CATEGORIES = ['uniques','ancients','vintages','chromas','godlies','legendaries','rares','uncommons','commons','pets']
const LABELS = Object.fromEntries(CATEGORIES.map(x => [x, x[0].toUpperCase() + x.slice(1)]))
const CACHE_KEY = 'mm2-values:latest'

function send(res, status, payload) {
  res.statusCode = status
  res.setHeader('content-type','application/json; charset=utf-8')
  res.setHeader('cache-control','no-store')
  res.setHeader('x-content-type-options','nosniff')
  res.end(JSON.stringify(payload))
}

function redisConfig() {
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN
  return url && token ? {url:url.replace(/\/$/,''), token} : null
}

async function redis(command) {
  const config = redisConfig()
  if (!config) throw new Error('Persistent cache is not configured')
  const response = await fetch(config.url, {
    method:'POST', headers:{authorization:`Bearer ${config.token}`,'content-type':'application/json'},
    body:JSON.stringify(command), signal:AbortSignal.timeout(12000),
  })
  if (!response.ok) throw new Error(`Redis HTTP ${response.status}`)
  const payload = await response.json()
  if (payload.error) throw new Error(payload.error)
  return payload.result
}

async function readLatest() {
  try {
    const raw = await redis(['GET', CACHE_KEY])
    if (raw) return JSON.parse(raw)
  } catch {}
  const seedPath = path.join(process.cwd(), 'data', 'seed.json')
  return JSON.parse(await fs.readFile(seedPath, 'utf8'))
}

async function writeLatest(data) {
  await redis(['SET', CACHE_KEY, JSON.stringify(data)])
}

function valueNumber(raw) {
  if (/\bT\d\b/i.test(raw) || /^x/i.test(raw)) return null
  const match = raw.match(/([\d,.]+)\s*([kmb])?/i)
  if (!match) return null
  const multipliers = {k:1_000,m:1_000_000,b:1_000_000_000}
  return Math.trunc(Number(match[1].replaceAll(',','')) * (multipliers[(match[2] || '').toLowerCase()] || 1))
}

function groupPrevious(data) {
  const grouped = Object.fromEntries(CATEGORIES.map(x => [x, []]))
  const reverse = Object.fromEntries(Object.entries(LABELS).map(([k,v]) => [v.toLowerCase(),k]))
  for (const item of data?.items || []) {
    const category = reverse[String(item.category || '').toLowerCase()]
    if (category) grouped[category].push(item)
  }
  return grouped
}

async function scrapeCategory(browser, category) {
  const page = await browser.newPage()
  try {
    await page.setViewport({width:1280,height:720,deviceScaleFactor:1})
    await page.setUserAgent('MM2ValuesCache/3.0 (+public read-only value index; hourly refresh)')
    await page.setExtraHTTPHeaders({'accept-language':'en-US,en;q=0.8'})
    await page.setRequestInterception(true)
    page.on('request', request => {
      const blocked = ['image','media','font'].includes(request.resourceType())
      if (blocked) request.abort(); else request.continue()
    })
    const response = await page.goto(`${BASE_URL}/mm2/${category}`, {waitUntil:'domcontentloaded',timeout:35000})
    if (!response || response.status() >= 400) throw new Error(`source HTTP ${response?.status() || 0}`)
    await page.waitForSelector('.itemcolumn', {timeout:15000})
    const result = await page.evaluate(() => {
      const text = document.body?.innerText || ''
      const blocked = /incapsula incident|request unsuccessful|access denied|verify you are human/i.test(text)
      const items = [...document.querySelectorAll('.itemcolumn')].map(card => ({
        name: card.querySelector('.itemhead')?.textContent?.trim() || '',
        value: card.querySelector('.itemvalue')?.textContent?.trim() || '',
      })).filter(item => item.name && item.value && item.value.toUpperCase() !== 'N/A')
      return {blocked, items}
    })
    if (result.blocked) throw new Error('source returned a protection/interstitial page')
    if (!result.items.length) throw new Error('no valued items found')
    const type = category === 'pets' ? 'pet' : 'weapon'
    return result.items.map(item => ({...item,valueNumber:valueNumber(item.value),type,category:LABELS[category]}))
  } finally {
    await page.close().catch(() => {})
  }
}

async function scrapeAll(previous) {
  const executablePath = await chromium.executablePath()
  const browser = await puppeteer.launch({
    args:[...chromium.args,'--disable-dev-shm-usage'],
    defaultViewport:{width:1280,height:720,deviceScaleFactor:1},
    executablePath,
    headless:'shell',
  })
  const prior = groupPrevious(previous)
  const results = {}
  const errors = {}
  try {
    // Small batches keep the run fast without opening a burst of ten simultaneous pages.
    for (let index = 0; index < CATEGORIES.length; index += 2) {
      const batch = CATEGORIES.slice(index,index + 2)
      await Promise.all(batch.map(async category => {
        try { results[category] = await scrapeCategory(browser,category) }
        catch (error) {
          errors[category] = error instanceof Error ? error.message : String(error)
          if (prior[category]?.length) results[category] = prior[category]
        }
      }))
    }
  } finally {
    await browser.close().catch(() => {})
  }
  const missing = CATEGORIES.filter(category => !results[category]?.length)
  if (missing.length) throw new Error(`No usable data for: ${missing.join(', ')}`)
  const items = CATEGORIES.flatMap(category => results[category]).sort((a,b) =>
    a.type.localeCompare(b.type) || (b.valueNumber ?? -1) - (a.valueNumber ?? -1) || a.name.localeCompare(b.name)
  )
  return {updatedAt:new Date().toISOString(),source:BASE_URL,renderer:'chromium',partial:Object.keys(errors).length > 0,errors,items}
}

export default async function handler(request, response) {
  if (request.method !== 'GET') return send(response,405,{ok:false,error:'Method not allowed'})
  const secret = process.env.CRON_SECRET
  const authorization = request.headers.authorization || ''
  if (!secret || authorization !== `Bearer ${secret}`) return send(response,401,{ok:false,error:'Unauthorized'})
  let previous
  try {
    previous = await readLatest()
    const data = await scrapeAll(previous)
    await writeLatest(data)
    return send(response,200,{ok:true,renderer:'chromium',updatedAt:data.updatedAt,count:data.items.length,weapons:data.items.filter(x=>x.type==='weapon').length,pets:data.items.filter(x=>x.type==='pet').length,partial:data.partial,errors:data.errors})
  } catch (error) {
    return send(response,502,{ok:false,error:error instanceof Error ? error.message : String(error),fallbackCount:previous?.items?.length || 0,updatedAt:previous?.updatedAt || null})
  }
}
