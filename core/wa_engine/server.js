import { createServer } from 'node:http';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import pino from 'pino';
import makeWASocket, {
  useMultiFileAuthState,
  makeCacheableSignalKeyStore,
  fetchLatestBaileysVersion,
  Browsers,
  DisconnectReason,
  proto,
  getBinaryNodeChild,
  getBinaryNodeChildren,
  jidNormalizedUser
} from '@whiskeysockets/baileys';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const SESSION_DIR = process.env.WA_SESSION_DIR || path.join(os.homedir(), '.wa_session_data');
if (!fs.existsSync(SESSION_DIR)) {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
}

let sock = null;
let authState = null;
let saveCredsFunc = null;
let connectionStatus = 'DISCONNECTED';
const jobs = new Map();

const delay = (baseMs, jitterMs = 300) => new Promise(r => setTimeout(r, baseMs + Math.random() * jitterMs));

const withTimeout = (promise, ms = 800, fallback = undefined) => {
  return Promise.race([
    promise,
    new Promise(resolve => setTimeout(() => resolve(fallback), ms))
  ]);
};

const getWaWebVersion = async () => {
  try {
    const res = await fetch('https://web.whatsapp.com/check-update?version=1&platform=web');
    const json = await res.json();
    if (json && json.currentVersion) {
      const v = json.currentVersion.split('.').map(Number);
      if (v.length === 3) return { version: v };
    }
  } catch (e) {}
  return await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1043857760] }));
};

const shouldSyncHistoryMessageFilter = (msg) => {
  return msg.syncType !== proto.HistorySync.HistorySyncType.FULL;
};

// Initialize socket only when account is FULLY registered
async function initWASocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  authState = state;
  saveCredsFunc = saveCreds;

  if (!state.creds.registered) {
    connectionStatus = 'DISCONNECTED';
    console.log('[WA-ENGINE] Account is not registered yet. Ready for pairing request...');
    return null;
  }

  if (sock && (connectionStatus === 'CONNECTED' || connectionStatus === 'CONNECTING')) {
    return sock;
  }

  const logger = pino({ level: 'silent' });
  const { version } = await getWaWebVersion();

  sock = makeWASocket({
    version,
    logger,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys)
    },
    printQRInTerminal: false,
    browser: Browsers.macOS('Chrome'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    shouldSyncHistoryMessage: shouldSyncHistoryMessageFilter,
    fireInitQueries: false,
    connectTimeoutMs: 30000,
    keepAliveIntervalMs: 25000,
    getMessage: async () => undefined
  });

  sock.ev.process(async (events) => {
    if (events['creds.update']) {
      try {
        if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });
        await saveCreds();
      } catch (e) {}
    }

    if (events['connection.update']) {
      const update = events['connection.update'];
      const { connection, lastDisconnect } = update;

      if (connection === 'connecting') {
        connectionStatus = 'CONNECTING';
        console.log('[WA-ENGINE] Connecting to WhatsApp...');
      }

      if (connection === 'open') {
        connectionStatus = 'CONNECTED';
        console.log(`[WA-ENGINE] Connected successfully as: ${sock.user?.id || 'Unknown'}`);
      }

      if (connection === 'close') {
        connectionStatus = 'DISCONNECTED';
        const statusCode = lastDisconnect?.error?.output?.statusCode || lastDisconnect?.error?.statusCode;
        const isLoggedOut = statusCode === DisconnectReason.loggedOut;

        console.log(`[WA-ENGINE] Connection closed. Reason code: ${statusCode}`);

        if (isLoggedOut) {
          console.log('[WA-ENGINE] Session invalid/logged out. Resetting state & preparing for new pairing...');
          try {
            fs.rmSync(SESSION_DIR, { recursive: true, force: true });
            fs.mkdirSync(SESSION_DIR, { recursive: true });
          } catch (e) {}
          sock = null;
        } else if (state.creds.registered) {
          console.log('[WA-ENGINE] Registered account connection drop, reconnecting in 3s...');
          setTimeout(() => {
            initWASocket().catch(err => console.error('[WA-ENGINE] Reconnect failed:', err));
          }, 3000);
        }
      }
    }
  });

  return sock;
}

// Generate pairing code cleanly on a single fresh socket using Browsers.macOS('Chrome')
async function generatePairingCode(rawPhone) {
  if (sock) {
    try { sock.end(undefined); } catch (e) {}
    sock = null;
  }

  try {
    fs.rmSync(SESSION_DIR, { recursive: true, force: true });
    fs.mkdirSync(SESSION_DIR, { recursive: true });
  } catch (e) {}

  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  authState = state;

  const logger = pino({ level: 'silent' });
  const { version } = await getWaWebVersion();

  sock = makeWASocket({
    version,
    logger,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys)
    },
    printQRInTerminal: false,
    browser: Browsers.macOS('Chrome'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    shouldSyncHistoryMessage: shouldSyncHistoryMessageFilter,
    fireInitQueries: false,
    getMessage: async () => undefined
  });

  return new Promise((resolve, reject) => {
    let requested = false;
    const timer = setTimeout(() => {
      reject(new Error('Timed out waiting for pairing code (25s)'));
    }, 25000);

    sock.ev.process(async (events) => {
      if (events['creds.update']) {
        try {
          if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });
          await saveCreds();
        } catch (e) {}
      }

      if (events['connection.update']) {
        const { connection, qr, lastDisconnect } = events['connection.update'];

        if (connection === 'connecting') {
          connectionStatus = 'CONNECTING';
        }

        if (connection === 'open') {
          connectionStatus = 'CONNECTED';
          console.log(`[WA-ENGINE] Connected as: ${sock.user?.id || 'Unknown'}`);
        }

        if (qr && !requested) {
          requested = true;
          try {
            console.log(`[WA-ENGINE] Handshake ready (QR received), requesting pairing code for ${rawPhone}...`);
            const code = await sock.requestPairingCode(rawPhone);
            clearTimeout(timer);
            resolve({ registered: false, code });
          } catch (err) {
            clearTimeout(timer);
            reject(err);
          }
        }

        if (connection === 'close') {
          connectionStatus = 'DISCONNECTED';
          const statusCode = lastDisconnect?.error?.output?.statusCode || lastDisconnect?.error?.statusCode;
          console.log(`[WA-ENGINE] Pairing socket closed (Status Code: ${statusCode})`);

          if (statusCode === 515 || statusCode === DisconnectReason.restartRequired) {
            console.log('[WA-ENGINE] Post-pairing restart (Status 515) received, reconnecting to finalize login...');
            setTimeout(() => {
              initWASocket().catch(err => console.error('[WA-ENGINE] Post-515 reconnect failed:', err));
            }, 1500);
          }
        }
      }
    });
  });
}

// Fast Business Profile Fetcher with 800ms cap
async function getBusinessProfileRaw(jid) {
  const normalizedJid = jidNormalizedUser(jid);
  let profile = null;
  let verified = null;

  try {
    profile = await withTimeout(sock.getBusinessProfile(normalizedJid), 800, undefined);
  } catch (e) {}

  if (profile) {
    try {
      const results = await withTimeout(sock.query({
        tag: 'iq',
        attrs: { to: 's.whatsapp.net', xmlns: 'w:biz', type: 'get' },
        content: [{ tag: 'business_profile', attrs: { v: '244' }, content: [{ tag: 'profile', attrs: { jid: normalizedJid } }] }]
      }), 800, undefined);

      if (results) {
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
      }
    } catch (e) {}
  }

  return { profile, verified };
}

// Catalog Inspector (800ms cap)
async function checkCommercialOffers(jid) {
  const normalizedJid = jidNormalizedUser(jid);
  let hasCatalog = false;
  let productsCount = 0;
  let sampleProducts = [];

  try {
    const catalog = await withTimeout(sock.getCatalog({ jid: normalizedJid, limit: 10 }), 800, undefined);
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

// Single Active WA Worker Process (Ultra-Fast Concurrent Worker)
async function processActiveNumberWorker(targetJid, rawNum) {
  try {
    const [statusResResult, bizResResult] = await Promise.allSettled([
      withTimeout(sock.fetchStatus(targetJid), 800, undefined),
      getBusinessProfileRaw(targetJid)
    ]);

    const statusRes = statusResResult.status === 'fulfilled' ? statusResResult.value : undefined;
    const bizRes = bizResResult.status === 'fulfilled' ? bizResResult.value : { profile: null, verified: null };

    let bioText = statusRes?.status || '';
    const profile = bizRes?.profile || null;
    const verified = bizRes?.verified || null;
    
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

    let hasOffers = false;
    let category = profile?.category || '';
    let description = profile?.description || '';

    if (profile) {
      const offersInfo = await checkCommercialOffers(targetJid);
      if (offersInfo.hasCatalog || offersInfo.productsCount > 0) {
        hasOffers = true;
      }
    }

    const offerKeywords = ['promo', 'diskon', 'order', 'jasa', 'jual', 'ready', 'price', 'harga', 'wa.me', 'http'];
    const combinedText = `${bioText} ${description}`.toLowerCase();
    if (offerKeywords.some(kw => combinedText.includes(kw))) {
      hasOffers = true;
    }

    return {
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
    };
  } catch (err) {
    return {
      phone: rawNum,
      jid: targetJid,
      exists: true,
      accountType: 'Personal',
      isVermet: false,
      verifiedLevel: 'none',
      verifiedName: '',
      hasOffers: false,
      category: '',
      description: '',
      bio: ''
    };
  }
}

// True Chunk-Parallel Ultra-Fast Batch Scan Runner
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
      const onWaResults = await withTimeout(sock.onWhatsApp(...chunkJids), 3000, []);
      const registeredMap = new Map();

      if (Array.isArray(onWaResults)) {
        onWaResults.forEach(item => {
          if (item && item.exists) {
            registeredMap.set(item.jid, item);
          }
        });
      }

      // Collect all tasks for the chunk and run them concurrently in PARALLEL
      const activeWorkerPromises = [];

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
        } else {
          activeWorkerPromises.push(processActiveNumberWorker(targetJid, rawNum));
        }
      }

      // Execute all active WA profiles in the chunk PARALLEL SIMULTANEOUSLY!
      if (activeWorkerPromises.length > 0) {
        const workerResults = await Promise.all(activeWorkerPromises);
        for (const res of workerResults) {
          job.done++;
          job.results.push(res);
        }
      }

      await delay(100, 100);
    } catch (err) {
      console.error(`[WA-ENGINE] Batch error at index ${i}:`, err.message);
      await delay(1000);
    }

    if ((i + CHUNK_SIZE) % REST_EVERY === 0 && i > 0) {
      console.log(`[WA-ENGINE] Circuit breaker rest window (${REST_MS / 1000}s) active...`);
      await delay(REST_MS, 3000);
    } else {
      await delay(300, 300);
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

      console.log(`[WA-ENGINE] Requesting pairing code for ${rawPhone}...`);
      const pairRes = await generatePairingCode(rawPhone);

      if (pairRes.registered) {
        return res.end(JSON.stringify({
          success: true,
          registered: true,
          message: 'Account is already paired and registered.'
        }));
      }

      const code = pairRes.code;
      const formattedCode = code.match(/.{1,4}/g)?.join('-') || code;
      console.log(`[WA-ENGINE] Pairing code generated: ${formattedCode}`);

      return res.end(JSON.stringify({
        success: true,
        pairingCode: formattedCode,
        rawCode: code,
        phone: rawPhone
      }));
    } catch (e) {
      console.error('[WA-ENGINE] Request pairing error:', e.message);
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
