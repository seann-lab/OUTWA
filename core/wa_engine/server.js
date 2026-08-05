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

const delay = (baseMs, jitterMs = 500) => new Promise(r => setTimeout(r, baseMs + Math.random() * jitterMs));

async function initWASocket() {
  if (sock && (connectionStatus === 'CONNECTED' || connectionStatus === 'CONNECTING')) {
    return sock;
  }

  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  authState = state;
  saveCredsFunc = saveCreds;

  const logger = pino({ level: 'silent' });
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1043857760] }));

  sock = makeWASocket({
    version,
    logger,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger)
    },
    printQRInTerminal: false,
    browser: Browsers.ubuntu('Chrome'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    fireInitQueries: false,
    connectTimeoutMs: 30000,
    keepAliveIntervalMs: 25000,
    getMessage: async () => undefined
  });

  sock.ev.process(async (events) => {
    if (events['creds.update']) {
      await saveCreds();
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

        if (isLoggedOut && authState?.creds?.registered) {
          console.log('[WA-ENGINE] Logged out. Clearing session directory...');
          try {
            fs.rmSync(SESSION_DIR, { recursive: true, force: true });
          } catch (e) {}
        } else {
          // ALWAYS reconnect automatically so the socket stays listening for phone pairing verification!
          console.log('[WA-ENGINE] Connection dropped, reconnecting socket listener in 2s...');
          setTimeout(() => {
            initWASocket().catch(err => console.error('[WA-ENGINE] Reconnect failed:', err));
          }, 2000);
        }
      }
    }
  });

  return sock;
}

// Generate pairing code and keep auto-reconnecting socket listener active
async function generatePairingCode(rawPhone) {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  authState = state;

  if (state.creds.registered) {
    return { registered: true };
  }

  if (sock) {
    try { sock.end(undefined); } catch (e) {}
    sock = null;
  }

  try {
    fs.rmSync(SESSION_DIR, { recursive: true, force: true });
    fs.mkdirSync(SESSION_DIR, { recursive: true });
  } catch (e) {}

  const { state: newState, saveCreds: newSaveCreds } = await useMultiFileAuthState(SESSION_DIR);
  authState = newState;

  const logger = pino({ level: 'silent' });
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1043857760] }));

  sock = makeWASocket({
    version,
    logger,
    auth: {
      creds: newState.creds,
      keys: makeCacheableSignalKeyStore(newState.keys, logger)
    },
    printQRInTerminal: false,
    browser: Browsers.ubuntu('Chrome'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    fireInitQueries: false,
    getMessage: async () => undefined
  });

  let pairingCodePromise = new Promise((resolve, reject) => {
    let codeRequested = false;

    sock.ev.process(async (events) => {
      if (events['creds.update']) {
        await newSaveCreds();
      }

      if (events['connection.update']) {
        const update = events['connection.update'];
        const { connection, qr, lastDisconnect } = update;

        if (connection === 'connecting') connectionStatus = 'CONNECTING';
        if (connection === 'open') {
          connectionStatus = 'CONNECTED';
          console.log(`[WA-ENGINE] Successfully paired and logged in as: ${sock.user?.id || 'Unknown'}`);
        }

        if (qr && !codeRequested) {
          codeRequested = true;
          try {
            console.log(`[WA-ENGINE] Official Baileys Handshake ready (WA Web v${version.join('.')}), requesting pairing code for ${rawPhone}...`);
            const code = await sock.requestPairingCode(rawPhone);
            resolve(code);
          } catch (err) {
            reject(err);
          }
        }

        if (connection === 'close') {
          connectionStatus = 'DISCONNECTED';
          const statusCode = lastDisconnect?.error?.output?.statusCode || lastDisconnect?.error?.statusCode;
          console.log(`[WA-ENGINE] Pairing listener connection closed (Reason: ${statusCode}), auto-reconnecting socket...`);
          setTimeout(() => {
            initWASocket().catch(err => console.error('[WA-ENGINE] Reconnect during pairing failed:', err));
          }, 2000);
        }
      }
    });

    setTimeout(() => {
      reject(new Error('Pairing code timeout from WhatsApp server'));
    }, 25000);
  });

  const code = await pairingCodePromise;
  return { registered: false, code };
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

        await delay(800, 1000);
      }
    } catch (err) {
      console.error(`[WA-ENGINE] Batch error at index ${i}:`, err.message);
      await delay(10000);
    }

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
