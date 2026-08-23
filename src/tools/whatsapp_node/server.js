const express = require('express');
const puppeteer = require('puppeteer');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');
const multer = require('multer');

const app = express();
const port = parseInt(process.env.WHATSAPP_PORT || process.env.PORT, 10) || 3000;

app.use(express.json({ limit: '50mb' }));

const dataDir = process.env.WHATSAPP_DATA_DIR || __dirname;
const sessionDir = path.join(dataDir, '.wpp_session');
const uploadDir = path.join(dataDir, 'uploads');
const customWaJsPath = path.join(dataDir, 'wppconnect-wa.js');

if (!fs.existsSync(uploadDir)) {
    fs.mkdirSync(uploadDir, { recursive: true });
}
if (!fs.existsSync(sessionDir)) {
    fs.mkdirSync(sessionDir, { recursive: true });
}

const upload = multer({ dest: uploadDir });

let browser = null;
let page = null;
let isReady = false;
let currentQR = null;
let isShuttingDown = false;

process.on('uncaughtException', (err) => {
    const errStr = err ? err.toString() : '';
    if (isShuttingDown) {
        console.log('Ignored uncaught error during shutdown:', err.message);
    } else if (errStr.includes('Execution context was destroyed') || errStr.includes('Target closed') || errStr.includes('Session closed')) {
        console.log('Ignored benign Puppeteer exception:', errStr);
    } else {
        console.error('Uncaught Exception:', err);
        process.exit(1);
    }
});

process.on('unhandledRejection', (reason, promise) => {
    const reasonStr = reason ? reason.toString() : '';
    if (isShuttingDown) {
        console.log('Ignored unhandled rejection during shutdown');
    } else if (reasonStr.includes('Execution context was destroyed') || reasonStr.includes('Target closed') || reasonStr.includes('Session closed')) {
        console.log('Ignored benign Puppeteer rejection:', reasonStr);
    } else {
        console.error('Unhandled Rejection at:', promise, 'reason:', reason);
    }
});

function getWaJsSource() {
    if (fs.existsSync(customWaJsPath)) {
        try {
            console.log(`Using custom hot-patched WA-JS from ${customWaJsPath}`);
            return fs.readFileSync(customWaJsPath, 'utf8');
        } catch (e) {
            console.error(`Failed to read custom WA-JS at ${customWaJsPath}, falling back to bundled:`, e);
        }
    }

    try {
        const pkgPath = require.resolve('@wppconnect/wa-js/package.json');
        const distFile = path.join(path.dirname(pkgPath), 'dist', 'wppconnect-wa.js');
        if (fs.existsSync(distFile)) {
            return fs.readFileSync(distFile, 'utf8');
        }
    } catch (e) {}

    try {
        const mainPath = require.resolve('@wppconnect/wa-js');
        const distFile = path.join(path.dirname(mainPath), 'wppconnect-wa.js');
        if (fs.existsSync(distFile)) {
            return fs.readFileSync(distFile, 'utf8');
        }
    } catch (e) {}

    const localFallback = path.join(__dirname, 'node_modules', '@wppconnect', 'wa-js', 'dist', 'wppconnect-wa.js');
    if (fs.existsSync(localFallback)) {
        return fs.readFileSync(localFallback, 'utf8');
    }

    throw new Error('Could not locate wppconnect-wa.js distribution bundle.');
}

async function injectWaJs() {
    if (!page) return false;
    try {
        const waJsCode = getWaJsSource();
        await page.evaluate(waJsCode);
        await page.waitForFunction(() => typeof window.WPP !== 'undefined' && window.WPP.isReady, { timeout: 15000 });
        console.log('WA-JS successfully injected and ready.');
        return true;
    } catch (e) {
        console.error('WA-JS injection waiting/failed:', e.message);
        return false;
    }
}

function ensureExecutablePermissions(targetDir) {
    if (!targetDir || !fs.existsSync(targetDir)) return;
    const helperNames = new Set([
        'chrome',
        'chrome_crashpad_handler',
        'crashpad_handler',
        'chrome_sandbox',
        'chrome-wrapper',
        'chromedriver',
        'chrome_management_service',
        'interactive_ui_tests',
        'xdg-mime',
        'xdg-settings'
    ]);

    const fixDir = (dir, depth = 0) => {
        if (depth > 5) return;
        try {
            const entries = fs.readdirSync(dir, { withFileTypes: true });
            for (const entry of entries) {
                const full = path.join(dir, entry.name);
                if (entry.isDirectory()) {
                    fixDir(full, depth + 1);
                } else if (entry.isFile()) {
                    if (helperNames.has(entry.name) || entry.name.startsWith('chrome') || entry.name.toLowerCase().includes('crashpad')) {
                        try {
                            fs.chmodSync(full, 0o755);
                        } catch (e) {}
                    }
                }
            }
        } catch (e) {}
    };

    fixDir(targetDir);
}

function findChromeExecutable() {
    const candidateDirs = [
        process.env.PUPPETEER_CACHE_DIR,
        path.join(process.env.HOME || '', '.cache', 'puppeteer'),
        '/app/share/puppeteer',
        '/usr/bin',
        '/usr/local/bin'
    ].filter(Boolean);

    let candidates = [];
    const searchDir = (current, depth = 0) => {
        if (depth > 6) return;
        try {
            const entries = fs.readdirSync(current, { withFileTypes: true });
            for (const entry of entries) {
                const full = path.join(current, entry.name);
                if (entry.isDirectory()) {
                    searchDir(full, depth + 1);
                } else if (entry.isFile()) {
                    if (entry.name === 'chrome' || entry.name === 'chromium' || entry.name === 'google-chrome' || entry.name === 'google-chrome-stable') {
                        try {
                            try {
                                fs.chmodSync(full, 0o755);
                            } catch (e) {}
                            fs.accessSync(full, fs.constants.X_OK);
                            candidates.push(full);
                        } catch (e) {}
                    } else if (entry.name === 'chrome_crashpad_handler' || entry.name.toLowerCase().includes('crashpad') || entry.name === 'chrome_sandbox' || entry.name === 'chrome-wrapper') {
                        try {
                            fs.chmodSync(full, 0o755);
                        } catch (e) {}
                    }
                }
            }
        } catch (e) {}
    };

    for (const dir of candidateDirs) {
        if (fs.existsSync(dir)) searchDir(dir);
    }
    if (candidates.length === 0) return null;
    candidates.sort().reverse();
    const chosen = candidates[0];
    ensureExecutablePermissions(path.dirname(chosen));
    return chosen;
}

async function ensureListenersAttached() {
    if (!page) return false;
    try {
        return await page.evaluate(async () => {
            if (typeof window.WPP === 'undefined' || !window.WPP.isReady) {
                return false;
            }

            if (!window.__wpp_listeners_attached) {
                window.__wpp_listeners_attached = true;

                window.WPP.on('conn.auth_code_change', (authCode) => {
                    if (authCode && authCode.fullCode) {
                        window.nodeOnQrCode(authCode.fullCode);
                    }
                });

                window.WPP.on('conn.authenticated', () => {
                    window.nodeOnAuthenticated();
                    window.nodeOnReady();
                });

                window.WPP.on('conn.main_ready', async () => {
                    try {
                        if (await window.WPP.conn.isAuthenticated()) {
                            window.nodeOnReady();
                        }
                    } catch (e) {}
                });

                window.WPP.on('chat.new_message', async (msg) => {
                    try {
                        if (!msg) return;

                        // 1. Filter out outgoing / sent messages
                        if (msg.fromMe || msg.isSentByMe || (msg.id && msg.id.fromMe)) {
                            return;
                        }

                        // 2. Filter out status updates / broadcasts
                        if (msg.isStatusV3 || msg.isBroadcast || (msg.chatId && (msg.chatId._serialized || msg.chatId).includes('@broadcast'))) {
                            return;
                        }

                        // 3. Determine chat identifiers
                        const rawFrom = (msg.from && (msg.from._serialized || msg.from)) || '';
                        const rawChatId = (msg.chatId && (msg.chatId._serialized || msg.chatId)) || rawFrom;
                        const rawAuthor = (msg.author && (msg.author._serialized || msg.author)) || rawFrom;
                        const isGroup = Boolean(msg.isGroupMsg || rawChatId.endsWith('@g.us'));

                        const targetId = isGroup ? rawAuthor : rawFrom;
                        let resolvedSenderId = targetId;
                        let senderName = msg.notifyName || '';

                        // 4. Resolve LID / Contact / Phone number
                        if (targetId) {
                            // Try getPnLidEntry
                            try {
                                const entry = await window.WPP.contact.getPnLidEntry(targetId);
                                if (entry) {
                                    if (entry.phoneNumber) {
                                        resolvedSenderId = entry.phoneNumber._serialized || (entry.phoneNumber.id ? entry.phoneNumber.id + '@c.us' : resolvedSenderId);
                                    }
                                    if (entry.contact) {
                                        senderName = senderName || entry.contact.name || entry.contact.pushname || entry.contact.shortName || '';
                                    }
                                }
                            } catch (e) {}

                            // Try contact.get
                            if (!senderName || resolvedSenderId.includes('@lid')) {
                                try {
                                    const contact = await window.WPP.contact.get(targetId) || (resolvedSenderId !== targetId ? await window.WPP.contact.get(resolvedSenderId) : null);
                                    if (contact) {
                                        senderName = senderName || contact.name || contact.pushname || contact.shortName || contact.formattedName || '';
                                        if (resolvedSenderId.includes('@lid')) {
                                            const cid = (contact.id && (contact.id._serialized || contact.id)) || '';
                                            if (cid.includes('@c.us')) {
                                                resolvedSenderId = cid;
                                            } else if (contact.phoneNumber) {
                                                const pn = contact.phoneNumber._serialized || (contact.phoneNumber.id ? contact.phoneNumber.id + '@c.us' : (typeof contact.phoneNumber === 'string' ? contact.phoneNumber : ''));
                                                if (pn) resolvedSenderId = pn.includes('@') ? pn : pn + '@c.us';
                                            }
                                        }
                                    }
                                } catch (e) {}
                            }

                            // Try contact list matching
                            if (!senderName || resolvedSenderId.includes('@lid')) {
                                try {
                                    const contacts = await window.WPP.contact.list();
                                    const matched = contacts.find(c => {
                                        const cid = (c.id && (c.id._serialized || c.id)) || '';
                                        const clid = (c.lid && (c.lid._serialized || c.lid)) || '';
                                        return cid === targetId || clid === targetId || (resolvedSenderId && (cid === resolvedSenderId || clid === resolvedSenderId));
                                    });
                                    if (matched) {
                                        senderName = senderName || matched.name || matched.pushname || matched.shortName || '';
                                        const cid = (matched.id && (matched.id._serialized || matched.id)) || '';
                                        if (cid.includes('@c.us')) {
                                            resolvedSenderId = cid;
                                        }
                                    }
                                } catch (e) {}
                            }

                            // If DM, try chat.get for chat name
                            if (!isGroup) {
                                try {
                                    const chat = await window.WPP.chat.get(rawChatId);
                                    if (chat) {
                                        senderName = senderName || chat.name || chat.formattedTitle || '';
                                    }
                                } catch (e) {}
                            }
                        }

                        // Final fallback for senderName
                        if (!senderName || senderName === 'Unknown') {
                            senderName = msg.notifyName || (resolvedSenderId.includes('@c.us') ? resolvedSenderId.split('@')[0] : (resolvedSenderId || 'Unknown'));
                        }

                        const finalChatId = isGroup ? rawChatId : resolvedSenderId;
                        window.nodeOnMsgReceived(finalChatId, senderName);
                    } catch (e) {
                        console.error('Error handling incoming message event:', e);
                    }
                });
            }

            const isAuthed = await window.WPP.conn.isAuthenticated();
            if (isAuthed) {
                window.nodeOnReady();
            } else {
                const authCode = await window.WPP.conn.getAuthCode();
                if (authCode && authCode.fullCode) {
                    window.nodeOnQrCode(authCode.fullCode);
                } else {
                    const domEl = document.querySelector('div[data-ref]') || document.querySelector('[data-ref]');
                    if (domEl) {
                        const ref = domEl.getAttribute('data-ref');
                        if (ref) window.nodeOnQrCode(ref);
                    }
                }
            }

            return true;
        });
    } catch (e) {
        return false;
    }
}

async function startClient() {
    console.log('Starting WhatsApp Client with WPPConnect (WA-JS)...');

    const puppeteerArgs = [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--no-first-run',
        '--no-zygote',
        '--disable-gpu',
        '--disable-web-security',
        '--aggressive-cache-discard',
        '--disable-features=IsolateOrigins,site-per-process'
    ];

    if (process.env.PUPPETEER_CACHE_DIR) {
        ensureExecutablePermissions(process.env.PUPPETEER_CACHE_DIR);
    }

    const exe = findChromeExecutable();
    if (exe) {
        console.log(`Using discovered Chrome executable: ${exe}`);
        ensureExecutablePermissions(path.dirname(exe));
    }

    // Clean any orphaned Chromium profile locks from previous runs
    for (const lockFileName of ['SingletonLock', 'SingletonCookie', 'SingletonSocket']) {
        const lockPath = path.join(sessionDir, lockFileName);
        if (fs.existsSync(lockPath)) {
            try {
                fs.unlinkSync(lockPath);
            } catch (e) {}
        }
    }

    try {
        browser = await puppeteer.launch({
            executablePath: exe || undefined,
            headless: true,
            userDataDir: sessionDir,
            args: puppeteerArgs
        });

        const pages = await browser.pages();
        page = pages.length > 0 ? pages[0] : await browser.newPage();

        await page.setUserAgent('Mozilla/5.0 (X-UA-Compatible; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36');
        await page.setBypassCSP(true);

        // Expose Node bridge callbacks to the browser context
        await page.exposeFunction('nodeOnQrCode', (qr) => {
            if (qr && qr !== currentQR) {
                currentQR = qr;
                console.log('QR Code received. Scan with your phone:');
                qrcode.generate(qr, { small: true });
            }
        });

        await page.exposeFunction('nodeOnAuthenticated', () => {
            console.log('WhatsApp Client is authenticated!');
        });

        await page.exposeFunction('nodeOnReady', () => {
            console.log('WhatsApp Client is ready! Bridge is fully active.');
            currentQR = null;
            isReady = true;
        });

        await page.exposeFunction('nodeOnMsgReceived', (chatId, senderName) => {
            console.log(`[MSG_RECEIVED] ${chatId} ${senderName}`);
        });

        // Pre-evaluate WA-JS on every page reload
        const initialWaJsCode = getWaJsSource();
        await page.evaluateOnNewDocument(initialWaJsCode);

        console.log('Navigating to https://web.whatsapp.com ...');
        await page.goto('https://web.whatsapp.com', {
            waitUntil: 'domcontentloaded',
            timeout: 60000
        });

        // Continuous QR code, authentication, and listener attachment polling interval
        const checkInterval = setInterval(async () => {
            if (isShuttingDown || !page) {
                return;
            }
            try {
                await ensureListenersAttached();

                const state = await page.evaluate(async () => {
                    // 1. Check if WPP ready / authenticated
                    if (typeof window.WPP !== 'undefined' && window.WPP.conn) {
                        const isAuthed = await window.WPP.conn.isAuthenticated();
                        if (isAuthed) {
                            return { ready: true, qr: null };
                        }
                        const code = await window.WPP.conn.getAuthCode();
                        if (code && code.fullCode) {
                            return { ready: false, qr: code.fullCode };
                        }
                    }

                    // 2. Check DOM data-ref fallback
                    const domEl = document.querySelector('div[data-ref]') || document.querySelector('[data-ref]');
                    if (domEl) {
                        const ref = domEl.getAttribute('data-ref');
                        if (ref) return { ready: false, qr: ref };
                    }

                    return { ready: false, qr: null };
                });

                if (state) {
                    if (state.ready) {
                        isReady = true;
                        currentQR = null;
                    } else if (state.qr && state.qr !== currentQR) {
                        currentQR = state.qr;
                        isReady = false;
                        console.log('QR Code received. Scan with your phone:');
                        qrcode.generate(state.qr, { small: true });
                    }
                }
            } catch (e) {}
        }, 2000);

        // Loop to ensure WA-JS is injected and event listeners are active on startup
        let injected = false;
        for (let i = 0; i < 30; i++) {
            if (isShuttingDown) break;
            try {
                injected = await ensureListenersAttached();
                if (injected) {
                    break;
                }
            } catch (e) {
                // Page might still be loading Webpack scripts
            }
            await new Promise(r => setTimeout(r, 2000));
        }

        if (!injected) {
            console.log('Attempting direct WA-JS evaluation injection...');
            await injectWaJs();
            await ensureListenersAttached();
        }

    } catch (err) {
        console.error('Failed to initialize WhatsApp browser client:', err);
    }
}

startClient();

// --- Helper Functions in Node ---

async function forceShutdown() {
    console.log('Initiating shutdown...');
    isShuttingDown = true;
    try {
        if (browser) {
            await browser.close();
        }
    } catch (e) {
        console.error('Error during browser close:', e);
    }
    process.exit(0);
}

// In-browser target resolution helper stringified
const browserResolveTarget = `
async function resolveTargetInBrowser(target) {
    if (!target) return null;
    if (target.includes('@c.us') || target.includes('@g.us') || target.includes('@newsletter')) return target;

    // 1. Clean number matching
    let cleanInput = target.replace(/[^\\d+]/g, '');
    if (cleanInput.startsWith('0') && cleanInput.length === 10) {
        cleanInput = '27' + cleanInput.substring(1);
    }
    cleanInput = cleanInput.replace('+', '');

    // 2. Query contact list
    const contacts = await window.WPP.contact.list();
    
    // Direct phone matching
    if (cleanInput.length >= 7) {
        const byPhone = contacts.find(c => {
            const user = (c.id && c.id.user) || '';
            const pn = (c.phoneNumber && c.phoneNumber.user) || '';
            return user === cleanInput || pn === cleanInput || user.endsWith(cleanInput) || cleanInput.endsWith(user);
        });
        if (byPhone && byPhone.id) {
            return byPhone.id._serialized || byPhone.id;
        }
        
        try {
            const exists = await window.WPP.contact.queryExists(cleanInput + '@c.us');
            if (exists && exists.wid) {
                return exists.wid._serialized || exists.wid;
            }
        } catch (e) {}
    }

    // Exact name / pushname
    let match = contacts.find(c => c.name === target || c.pushname === target);
    if (!match) {
        const lower = target.toLowerCase();
        match = contacts.find(c => (c.name && c.name.toLowerCase() === lower) || (c.pushname && c.pushname.toLowerCase() === lower));
    }
    // Partial name
    if (!match) {
        const lower = target.toLowerCase();
        const matches = contacts.filter(c => (c.name && c.name.toLowerCase().includes(lower)) || (c.pushname && c.pushname.toLowerCase().includes(lower)));
        if (matches.length > 0) {
            match = matches.find(m => ((m.id && m.id._serialized) || m.id || '').includes('@c.us')) || matches[0];
        }
    }

    if (match && match.id) {
        return match.id._serialized || match.id;
    }

    // Check chat list
    const chats = await window.WPP.chat.list();
    let chatMatch = chats.find(c => c.name === target);
    if (!chatMatch) {
        const lower = target.toLowerCase();
        chatMatch = chats.find(c => c.name && c.name.toLowerCase() === lower);
    }
    if (!chatMatch) {
        const lower = target.toLowerCase();
        chatMatch = chats.find(c => c.name && c.name.toLowerCase().includes(lower));
    }
    if (chatMatch && chatMatch.id) {
        return chatMatch.id._serialized || chatMatch.id;
    }

    if (cleanInput.length >= 7) {
        return cleanInput + '@c.us';
    }

    return null;
}
`;

// Helper to serialize messages in browser context
const browserSerializeMessage = `
async function serializeMsgInBrowser(msg, chat) {
    let senderName = 'Unknown';
    if (msg.notifyName) {
        senderName = msg.notifyName;
    } else if (msg.author) {
        try {
            const contact = await window.WPP.contact.get(msg.author);
            senderName = contact.name || contact.pushname || (msg.author._serialized || msg.author);
        } catch (e) {
            senderName = msg.author._serialized || msg.author;
        }
    } else if (msg.from) {
        try {
            const contact = await window.WPP.contact.get(msg.from);
            senderName = contact.name || contact.pushname || (msg.from._serialized || msg.from);
        } catch (e) {
            senderName = msg.from._serialized || msg.from;
        }
    }

    const chatIdStr = (chat && chat.id && (chat.id._serialized || chat.id)) || (msg.chatId && (msg.chatId._serialized || msg.chatId)) || '';
    const fromStr = (msg.from && (msg.from._serialized || msg.from)) || '';
    const authorStr = (msg.author && (msg.author._serialized || msg.author)) || fromStr;
    const isGroup = chatIdStr.endsWith('@g.us');

    let senderNumber = null;
    let authorToUse = isGroup ? authorStr : fromStr;
    if (authorToUse && authorToUse.includes('@c.us')) {
        senderNumber = authorToUse.split('@')[0];
    } else if (authorToUse && authorToUse.includes('@lid')) {
        try {
            const entry = await window.WPP.contact.getPnLidEntry(authorToUse);
            if (entry && entry.phoneNumber && entry.phoneNumber.id) {
                senderNumber = entry.phoneNumber.id;
            }
        } catch (e) {}
    }

    const msgIdStr = (msg.id && (msg.id._serialized || msg.id)) || '';
    let msgType = msg.type || 'chat';
    if (msg.isMedia) {
        if (!msgType || msgType === 'chat') msgType = 'document';
    }

    return {
        id: msgIdStr,
        chatId: chatIdStr,
        chatName: (chat && (chat.name || (chat.id && chat.id.user))) || '',
        timestamp: msg.t || msg.timestamp || Math.floor(Date.now() / 1000),
        sender: fromStr,
        author: authorStr,
        senderName: senderName,
        senderNumber: senderNumber,
        content: msg.body || msg.caption || '',
        fromMe: Boolean(msg.fromMe || msg.id && msg.id.fromMe),
        isGroup: isGroup,
        hasMedia: Boolean(msg.hasMedia || msg.isMedia || msgType === 'ptt' || msgType === 'audio' || msgType === 'image' || msgType === 'video' || msgType === 'document'),
        type: msgType
    };
}
`;

async function downloadAndSaveMedia(msgId, msgType) {
    if (!page || !msgId) return null;
    try {
        const base64Data = await page.evaluate(async (id) => {
            const blob = await window.WPP.chat.downloadMedia(id);
            if (!blob) return null;
            return await window.WPP.util.blobToBase64(blob);
        }, msgId);

        if (!base64Data) return null;

        const matches = base64Data.match(/^data:([^;]+);base64,(.+)$/);
        const mimeType = matches ? matches[1] : 'application/octet-stream';
        const rawBase64 = matches ? matches[2] : base64Data;

        let extension = 'bin';
        if (mimeType.includes('/')) {
            extension = mimeType.split('/')[1].split(';')[0];
        }
        if (msgType === 'ptt') extension = 'ogg';

        const safeId = msgId.replace(/[^a-zA-Z0-9_-]/g, '_');
        const filename = `${safeId}.${extension}`;
        const filePath = path.join(uploadDir, filename);

        fs.writeFileSync(filePath, rawBase64, 'base64');
        return filePath;
    } catch (e) {
        console.error(`Failed to download media for message ${msgId}:`, e.message);
        return null;
    }
}

// --- Endpoints ---

app.get('/status', (req, res) => {
    res.json({ ready: isReady, qr: currentQR });
});

app.post('/eval', async (req, res) => {
    if (!page) return res.status(503).json({ error: 'Browser not initialized' });
    try {
        const code = req.body.code;
        const result = await page.evaluate(code);
        res.json({ result });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/update_engine', async (req, res) => {
    try {
        const success = await injectWaJs();
        res.json({ success });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/shutdown', async (req, res) => {
    console.log('Shutdown requested via API');
    res.json({ success: true });
    setTimeout(forceShutdown, 100);
});

app.get('/unread', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    try {
        console.log('[DEBUG] /unread endpoint called via WA-JS');
        const unreadData = await page.evaluate(async (resolveHelper, serializeHelper) => {
            eval(resolveHelper);
            eval(serializeHelper);

            const unreadChats = await window.WPP.chat.list({ onlyWithUnreadMessage: true });
            let unreadList = [];

            for (const chat of unreadChats) {
                const count = chat.unreadCount || 1;
                const msgs = await window.WPP.chat.getMessages(chat.id, { count: count, onlyUnread: true });
                for (const msg of msgs) {
                    unreadList.push(await serializeMsgInBrowser(msg, chat));
                }
            }
            return unreadList;
        }, browserResolveTarget, browserSerializeMessage);

        for (const msg of unreadData) {
            if (msg.hasMedia) {
                msg.mediaPath = await downloadAndSaveMedia(msg.id, msg.type);
            }
        }

        res.json({ unread: unreadData });
    } catch (e) {
        console.error('Unread extraction failed via WA-JS:', e);
        res.status(500).json({ error: 'Protocol mismatch: Waiting for WA-JS update.', details: e.message });
    }
});

app.post('/mark_read', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    const { chatId } = req.body;
    try {
        await page.evaluate(async (cid) => {
            await window.WPP.chat.markIsRead(cid);
        }, chatId);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/react', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    const { msgId, reaction } = req.body;
    try {
        await page.evaluate(async (mid, rx) => {
            await window.WPP.chat.sendReactionToMessage(mid, rx);
        }, msgId, reaction);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/resolve', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    const target = req.query.target;
    if (!target) return res.status(400).json({ error: 'No target provided' });

    try {
        const chatId = await page.evaluate(async (t, resolveHelper) => {
            eval(resolveHelper);
            return await resolveTargetInBrowser(t);
        }, target, browserResolveTarget);

        if (chatId) {
            res.json({ target: chatId });
        } else {
            res.status(404).json({ error: 'Target not found' });
        }
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/recent', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    const n = parseInt(req.query.n, 10) || 30;
    const target = req.query.target;

    try {
        console.log(`[DEBUG] /recent endpoint called. target=${target} n=${n}`);
        const messagesData = await page.evaluate(async (t, count, resolveHelper, serializeHelper) => {
            eval(resolveHelper);
            eval(serializeHelper);

            let allMsgs = [];
            if (t) {
                const chatId = await resolveTargetInBrowser(t);
                if (!chatId) return { error: `Target not found: ${t}` };
                const chat = await window.WPP.chat.get(chatId);
                const msgs = await window.WPP.chat.getMessages(chatId, { count: count });
                for (const msg of msgs) {
                    allMsgs.push(await serializeMsgInBrowser(msg, chat));
                }
            } else {
                const chats = await window.WPP.chat.list({ count: 15 });
                for (const chat of chats) {
                    const msgs = await window.WPP.chat.getMessages(chat.id, { count: 5 });
                    for (const msg of msgs) {
                        allMsgs.push(await serializeMsgInBrowser(msg, chat));
                    }
                }
            }

            allMsgs.sort((a, b) => b.timestamp - a.timestamp);
            return { messages: allMsgs.slice(0, count) };
        }, target, n, browserResolveTarget, browserSerializeMessage);

        if (messagesData.error) {
            return res.status(404).json({ error: messagesData.error });
        }

        const msgs = messagesData.messages || [];
        for (const msg of msgs) {
            if (msg.hasMedia) {
                msg.mediaPath = await downloadAndSaveMedia(msg.id, msg.type);
            }
        }

        res.json({ messages: msgs });
    } catch (e) {
        console.error('Recent extraction failed via WA-JS:', e);
        res.status(500).json({ error: 'Protocol mismatch: Waiting for WA-JS update.', details: e.message });
    }
});

app.get('/recent/:chatId', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    const n = parseInt(req.query.n, 10) || 30;
    const targetChatId = req.params.chatId;

    try {
        const messagesData = await page.evaluate(async (cid, count, serializeHelper) => {
            eval(serializeHelper);
            const chat = await window.WPP.chat.get(cid);
            const msgs = await window.WPP.chat.getMessages(cid, { count: count });
            let allMsgs = [];
            for (const msg of msgs) {
                allMsgs.push(await serializeMsgInBrowser(msg, chat));
            }
            allMsgs.sort((a, b) => b.timestamp - a.timestamp);
            return allMsgs;
        }, targetChatId, n, browserSerializeMessage);

        for (const msg of messagesData) {
            if (msg.hasMedia) {
                msg.mediaPath = await downloadAndSaveMedia(msg.id, msg.type);
            }
        }

        res.json({ messages: messagesData });
    } catch (e) {
        console.error('Recent chatId extraction failed via WA-JS:', e);
        res.status(500).json({ error: 'Protocol mismatch: Waiting for WA-JS update.', details: e.message });
    }
});

app.post('/send', upload.single('media'), async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    const { target, text, reply_to, isVoice } = req.body;

    try {
        const resolvedChatId = await page.evaluate(async (t, resolveHelper) => {
            eval(resolveHelper);
            return await resolveTargetInBrowser(t);
        }, target, browserResolveTarget);

        if (!resolvedChatId) {
            if (req.file) fs.unlinkSync(req.file.path);
            return res.status(404).json({ error: `Could not resolve target: ${target}` });
        }

        let sendOptions = {};
        if (reply_to) {
            sendOptions.quotedMsg = reply_to;
        }

        // Parse mentions
        if (text) {
            const mentionMatches = text.match(/@(\d+)/g);
            if (mentionMatches) {
                sendOptions.mentionedList = mentionMatches.map(m => m.substring(1) + '@c.us');
            }
        }

        if (req.file) {
            const mimeType = req.file.mimetype || 'application/octet-stream';
            const filename = req.file.originalname || 'file';
            const fileDataB64 = fs.readFileSync(req.file.path, { encoding: 'base64' });
            const dataUrl = `data:${mimeType};base64,${fileDataB64}`;
            const isVoiceNote = isVoice === 'true' || isVoice === true;

            await page.evaluate(async (cid, dUrl, isV, txt, fname, opts) => {
                if (isV) {
                    await window.WPP.chat.sendFileMessage(cid, dUrl, {
                        type: 'audio',
                        isPtt: true,
                        ...opts
                    });
                } else {
                    await window.WPP.chat.sendFileMessage(cid, dUrl, {
                        type: 'auto-detect',
                        filename: fname,
                        caption: txt || undefined,
                        ...opts
                    });
                }
            }, resolvedChatId, dataUrl, isVoiceNote, text, filename, sendOptions);

            fs.unlinkSync(req.file.path);
        } else {
            await page.evaluate(async (cid, txt, opts) => {
                await window.WPP.chat.sendTextMessage(cid, txt, opts);
            }, resolvedChatId, text || '', sendOptions);
        }

        res.json({ success: true, target: resolvedChatId });
    } catch (e) {
        if (req.file && fs.existsSync(req.file.path)) {
            fs.unlinkSync(req.file.path);
        }
        res.status(500).json({ error: e.message });
    }
});

app.get('/media/:msgId', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    try {
        const base64Data = await page.evaluate(async (id) => {
            const blob = await window.WPP.chat.downloadMedia(id);
            if (!blob) return null;
            return await window.WPP.util.blobToBase64(blob);
        }, req.params.msgId);

        if (!base64Data) {
            return res.status(404).json({ error: 'No media found or download failed' });
        }

        const matches = base64Data.match(/^data:([^;]+);base64,(.+)$/);
        const mimeType = matches ? matches[1] : 'application/octet-stream';
        const rawBase64 = matches ? matches[2] : base64Data;
        const buffer = Buffer.from(rawBase64, 'base64');

        res.set('Content-Type', mimeType);
        res.send(buffer);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

const server = app.listen(port, () => {
    console.log(`WhatsApp WA-JS Bridge running on port ${port}`);
});

process.on('SIGTERM', () => {
    console.log('SIGTERM signal received');
    forceShutdown();
});
