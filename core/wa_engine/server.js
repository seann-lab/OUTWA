import { createServer } from 'node:http';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import pino from 'pino';
import makeWASocket, {
  useMultiFileAuthState,
  Browsers,
  DisconnectReason,
  proto,
  getBinaryNodeChild,
  getBinaryNodeChildren,
  jidNormalizedUser
} from '@whiskeysockets/baileys';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Session dir inside project or home
const SESSION_DIR = process.env.WA_SESSION_DIR || path.join(__dirname, 'session_data');
if (!fs.existsSync(SESSION_DIR)) {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
}

let sock = null;
let authState = null;
let saveCredsFunc = null;
let connectionStatus = 'DISCONNECTED'; // DISCONNECTED, CONNECTING, CONNECTED
let pairingCodeInFlight = null;
let lastPairingError = null;

// Jobs map for async batch scanning
const jobs = new Map();

// Initialize Baileys Socket
async function initWASocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  authState = state;
  saveCredsFunc = saveCreds;

  const logger = pino({ level: 'silent' });

  sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    logger,
    browser: Browsers.ubuntu('Chrome'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    fireInitQueries: false,
    connectTimeoutMs: 30000,
    keepAliveIntervalMs: 25000,
    getMessage: async () => undefined
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect } = update;

    if (connection === 'connecting') {
      connectionStatus = 'CONNECTING';
      console.log('[WA-ENGINE] Connecting to WhatsApp...');
    }

    if (connection === 'open') {
      connectionStatus = 'CONNECTED';
      pairingCodeInFlight = null;
      lastPairingError = null;
      console.log(`[WA-ENGINE] Connected successfully as: ${sock.user?.id || 'Unknown'}`);
    }

    if (connection === 'close') {
      connectionStatus = 'DISCONNECTED';
      const statusCode = lastDisconnect?.error?.output?.statusCode || lastDisconnect?.error?.statusCode;
      const isLoggedOut = statusCode === DisconnectReason.loggedOut;

      console.log(`[WA-ENGINE] Connection closed. Reason code: ${statusCode}`);

      if (isLoggedOut) {
        console.log('[WA-ENGINE] Logged out. Clearing session directory...');
        try {
          fs.rmSync(SESSION_DIR, { recursive: true, force: true });
        } catch (e) {}
      } else {
        // Auto-reconnect backoff
        setTimeout(() => {
          initWASocket().catch(err => console.error('[WA-ENGINE] Reconnect failed:', err));
        }, 3000);
      }
    }
  });
}

// Raw w:biz IQ Query for Meta Verified (Vermet) & Business Profile
async function getBusinessProfileRaw(jid) {
  const normalizedJid = jidNormalizedUser(jid);
  let profile = null;
  let verified = null;

  try {
    profile = await sock.getBusinessProfile(normalizedJid).catch(() => undefined);
  } catch (e) {}

  try {
    const results = await sock.query({
      tag: 'iq',
      attrs: { to: 's.whatsapp.net', xmlns: 'w:biz', type: 'get' },
      content: [{ tag: 'business_profile', attrs: { v: '244' }, content: [{ tag: 'profile', attrs: { jid: normalizedJid } }] }]
    });

    const bizProfileNode = getBinaryNodeChild(results, 'business_profile');
    if (bizProfileNode) {
      const verifiedNode = getBinaryNodeChild(bizProfileNode, 'verified_name');
      if (verifiedNode) {
        const level = verifiedNode.attrs?.verified_level || 'none';
        verified = { verifiedLevel: level, verifiedName: null, issuer: null };

        const certBytes = verifiedNode.content;
        if (certBytes instanceof Uint8Array || Buffer.isBuffer(certBytes)) {
          try {
            const cert = proto.VerifiedNameCertificate.decode(certBytes);
            if (cert && cert.details) {
              const details = proto.VerifiedNameCertificate.Details.decode(cert.details);
              verified.verifiedName = details.verifiedName || null;
              verified.issuer = details.issuer || null;
            }
          } catch (err) {}
        }
      }
    }
  } catch (e) {}

  return { profile, verified };
}

// Catalog & Commercial Offer Inspector
async function checkCommercialOffers(jid) {
  const normalizedJid = jidNormalizedUser(jid);
  let hasCatalog = false;
  let productsCount = 0;
  let sampleProducts = [];

  try {
    const catalog = await sock.getCatalog({ jid: normalizedJid, limit: 10 }).catch(() => undefined);
    if (catalog && catalog.products && catalog.products.length > 0) {
      hasCatalog = true;
      productsCount = catalog.products.length;
      sampleProducts = catalog.products.map(p => ({
        name: p.name || 'Unnamed',
        price: p.price || 0,
        currency: p.currency || ''
      }));
    }
  } catch (e) {}

  return { hasCatalog, productsCount, sampleProducts };
}

// Delay helper with jitter
const delay = (baseMs, jitterMs = 1500) => new Promise(r => setTimeout(r, baseMs + Math.random() * jitterMs));

// Batch Scan Runner (Anti-Banned Protected)
async function runBatchScan(jobId, numbers) {
  const job = jobs.get(jobId);
  if (!job) return;

  const CHUNK_SIZE = 25;
  const REST_EVERY = 250;
  const REST_MS = 15000;

  for (let i = 0; i < numbers.length; i += CHUNK_SIZE) {
    if (job.status === 'CANCELLED') break;

    const chunkRaw = numbers.slice(i, i + CHUNK_SIZE);
    const chunkJids = chunkRaw.map(n => n.includes('@') ? n : `${n.replace(/[^\d]/g, '')}@s.whatsapp.net`);

    try {
      // Step 1: Batch onWhatsApp check (USync burst)
      const onWaResults = await sock.onWhatsApp(...chunkJids).catch(() => []);
      const registeredMap = new Map();

      if (Array.isArray(onWaResults)) {
        onWaResults.forEach(item => {
          if (item && item.exists) {
            registeredMap.set(item.jid, item);
          }
        });
      }

      for (let j = 0; j < chunkJids.length; j++) {
        if (job.status === 'CANCELLED') break;

        const targetJid = chunkJids[j];
        const rawNum = chunkRaw[j];
        const registered = registeredMap.get(targetJid) || registeredMap.get(jidNormalizedUser(targetJid));

        if (!registered) {
          job.done++;
          job.results.push({
            phone: rawNum,
            jid: targetJid,
            exists: false,
            accountType: 'Non-WA',
            isVermet: false,
            verifiedLevel: 'none',
            verifiedName: '',
            hasOffers: false,
            category: '',
            description: '',
            bio: ''
          });
          continue;
        }

        // Step 2: Deep Inspection for Registered WA Numbers
        let bioText = '';
        try {
          const statusRes = await sock.fetchStatus(targetJid).catch(() => undefined);
          if (statusRes && statusRes.status) {
            bioText = statusRes.status;
          }
        } catch (e) {}

        const { profile, verified } = await getBusinessProfileRaw(targetJid);
        
        let accountType = 'Personal';
        let isVermet = false;
        let verifiedLevel = verified?.verifiedLevel || 'none';
        let verifiedName = verified?.verifiedName || '';

        if (profile) {
          accountType = 'Business';
        }
        if (verifiedLevel === 'blue' || verifiedLevel === 'green' || verifiedName) {
          isVermet = true;
          if (accountType === 'Personal') accountType = 'Verified Enterprise';
        }

        // Step 3: Check Commercial Offers & Catalogue
        let hasOffers = false;
        let category = profile?.category || '';
        let description = profile?.description || '';

        if (profile) {
          const offersInfo = await checkCommercialOffers(targetJid);
          if (offersInfo.hasCatalog || offersInfo.productsCount > 0) {
            hasOffers = true;
          }
        }

        // Offer keyword detection in bio / description
        const offerKeywords = ['promo', 'diskon', 'order', 'jasa', 'jual', 'ready', 'price', 'harga', 'wa.me', 'http'];
        const combinedText = `${bioText} ${description}`.toLowerCase();
        if (offerKeywords.some(kw => combinedText.includes(kw))) {
          hasOffers = true;
        }

        job.done++;
        job.results.push({
          phone: rawNum,
          jid: targetJid,
          exists: true,
          accountType,
          isVermet,
          verifiedLevel,
          verifiedName,
          hasOffers,
          category,
          description,
          bio: bioText
        });

        // Jitter delay per item
        await delay(800, 1000);
      }
    } catch (err) {
      console.error(`[WA-ENGINE] Batch error at index ${i}:`, err.message);
      await delay(10000);
    }

    // Circuit breaker rest window per 250 items
    if ((i + CHUNK_SIZE) % REST_EVERY === 0 && i > 0) {
      console.log(`[WA-ENGINE] Circuit breaker rest window (${REST_MS / 1000}s) active...`);
      await delay(REST_MS, 3000);
    } else {
      await delay(2500, 2000);
    }
  }

  job.status = 'COMPLETED';
  console.log(`[WA-ENGINE] Job ${jobId} COMPLETED. Total scanned: ${job.done}`);
}

// JSON request helper
function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (e) {
        reject(e);
      }
    });
  });
}

// HTTP REST Server Initialization
const PORT = process.env.WA_ENGINE_PORT || 12711;

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  res.setHeader('Content-Type', 'application/json');

  if (url.pathname === '/health') {
    return res.end(JSON.stringify({
      status: 'OK',
      connection: connectionStatus,
      registered: authState?.creds?.registered || false,
      user: sock?.user?.id || null
    }));
  }

  if (url.pathname === '/request-pairing' && req.method === 'POST') {
    try {
      const body = await readJsonBody(req);
      const rawPhone = body.phone ? String(body.phone).replace(/[^\d]/g, '') : '';

      if (!rawPhone) {
        res.statusCode = 400;
        return res.end(JSON.stringify({ success: false, error: 'Phone number is required' }));
      }

      if (!sock) {
        await initWASocket();
      }

      if (authState?.creds?.registered) {
        return res.end(JSON.stringify({
          success: true,
          registered: true,
          message: 'Account is already paired and registered.'
        }));
      }

      pairingCodeInFlight = await sock.requestPairingCode(rawPhone);
      const formattedCode = pairingCodeInFlight.match(/.{1,4}/g)?.join('-') || pairingCodeInFlight;

      return res.end(JSON.stringify({
        success: true,
        pairingCode: formattedCode,
        rawCode: pairingCodeInFlight,
        phone: rawPhone
      }));
    } catch (e) {
      res.statusCode = 500;
      return res.end(JSON.stringify({ success: false, error: e.message }));
    }
  }

  if (url.pathname === '/scan' && req.method === 'POST') {
    try {
      const body = await readJsonBody(req);
      const numbers = body.numbers || [];
      const jobId = body.jobId || `job_${Date.now()}`;

      if (!numbers.length) {
        res.statusCode = 400;
        return res.end(JSON.stringify({ success: false, error: 'No numbers provided' }));
      }

      if (!sock || connectionStatus !== 'CONNECTED') {
        res.statusCode = 400;
        return res.end(JSON.stringify({ success: false, error: 'WA Engine is not connected to WhatsApp' }));
      }

      const job = {
        jobId,
        status: 'RUNNING',
        total: numbers.length,
        done: 0,
        results: [],
        createdAt: Date.now()
      };

      jobs.set(jobId, job);
      runBatchScan(jobId, numbers).catch(err => console.error(`[WA-ENGINE] Job ${jobId} failed:`, err));

      return res.end(JSON.stringify({ success: true, jobId, total: numbers.length }));
    } catch (e) {
      res.statusCode = 500;
      return res.end(JSON.stringify({ success: false, error: e.message }));
    }
  }

  if (url.pathname === '/job' && req.method === 'GET') {
    const jobId = url.searchParams.get('id');
    if (!jobId || !jobs.has(jobId)) {
      res.statusCode = 404;
      return res.end(JSON.stringify({ success: false, error: 'Job not found' }));
    }
    return res.end(JSON.stringify({ success: true, job: jobs.get(jobId) }));
  }

  if (url.pathname === '/cancel-job' && req.method === 'POST') {
    try {
      const body = await readJsonBody(req);
      const jobId = body.jobId;
      if (jobId && jobs.has(jobId)) {
        jobs.get(jobId).status = 'CANCELLED';
        return res.end(JSON.stringify({ success: true, message: `Job ${jobId} cancelled` }));
      }
      res.statusCode = 404;
      return res.end(JSON.stringify({ success: false, error: 'Job not found' }));
    } catch (e) {
      res.statusCode = 500;
      return res.end(JSON.stringify({ success: false, error: e.message }));
    }
  }

  res.statusCode = 404;
  res.end(JSON.stringify({ success: false, error: 'Endpoint not found' }));
});

// Start Server & Init WASocket
server.listen(PORT, '127.0.0.1', async () => {
  console.log(`[WA-ENGINE] Server listening on http://127.0.0.1:${PORT}`);
  await initWASocket().catch(err => console.error('[WA-ENGINE] Initial socket error:', err));
});
