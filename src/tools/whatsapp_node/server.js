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
let isInitializing = false;
let lastInitError = null;
let checkInterval = null;
let retryTimeout = null;

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
                if (entry.name.startsWith('.chrome-staging')) continue;
                const full = path.join(dir, entry.name);
                if (entry.isDirectory()) {
                    fixDir(full, depth + 1);
                } else if (entry.isFile()) {
                    const isHelper = (
                        helperNames.has(entry.name) ||
                        entry.name.startsWith('chrome') ||
                        entry.name.toLowerCase().includes('crashpad') ||
                        entry.name.endsWith('.so') ||
                        entry.name.includes('.so.')
                    );
                    if (isHelper) {
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
    for (const explicitEnv of [process.env.CHROME_EXECUTABLE_PATH, process.env.PUPPETEER_EXECUTABLE_PATH]) {
        if (explicitEnv && fs.existsSync(explicitEnv)) {
            try {
                ensureExecutablePermissions(path.dirname(explicitEnv));
                fs.accessSync(explicitEnv, fs.constants.X_OK);
                return explicitEnv;
            } catch (e) {}
        }
    }

    const candidateDirs = [
        process.env.PUPPETEER_CACHE_DIR,
        process.env.XDG_CACHE_HOME ? path.join(process.env.XDG_CACHE_HOME, 'puppeteer') : null,
        path.join(process.env.HOME || '', '.cache', 'puppeteer'),
        '/app/share/puppeteer',
        '/usr/bin',
        '/usr/local/bin'
    ].filter(Boolean);

    const searchDir = (current, bucket, depth = 0) => {
        if (depth > 6) return;
        try {
            const entries = fs.readdirSync(current, { withFileTypes: true });
            for (const entry of entries) {
                if (entry.name.startsWith('.chrome-staging')) continue;
                const full = path.join(current, entry.name);
                if (entry.isDirectory()) {
                    searchDir(full, bucket, depth + 1);
                } else if (entry.isFile() || entry.isSymbolicLink()) {
                    if (entry.name === 'chrome' || entry.name === 'chromium' || entry.name === 'google-chrome' || entry.name === 'google-chrome-stable') {
                        try {
                            try {
                                fs.chmodSync(full, 0o755);
                            } catch (e) {}
                            fs.accessSync(full, fs.constants.X_OK);
                            bucket.push(full);
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
        if (!fs.existsSync(dir)) continue;
        const dirCandidates = [];
        searchDir(dir, dirCandidates);
        if (dirCandidates.length > 0) {
            dirCandidates.sort().reverse();
            const chosen = dirCandidates[0];
            ensureExecutablePermissions(path.dirname(chosen));
            return chosen;
        }
    }
    return null;
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

                        // Group name (groups only)
                        let groupName = '';
                        if (isGroup) {
                            try {
                                const chat = await window.WPP.chat.get(rawChatId);
                                groupName = (chat && (chat.name || chat.formattedTitle)) || '';
                            } catch (e) {}
                        }

                        // Mention / reply-to-me detection
                        let mentioned = false;
                        try {
                            const ser = (w) => (w && (w._serialized || w)) || '';
                            const myIds = new Set();
                            try { myIds.add(ser(window.WPP.conn.getMyUserId())); } catch (e) {}
                            try { if (window.WPP.conn.getMyUserLid) myIds.add(ser(window.WPP.conn.getMyUserLid())); } catch (e) {}
                            myIds.delete('');
                            const mentions = (msg.mentionedJidList || []).map(ser);
                            const quoted = ser(msg.quotedParticipant);
                            mentioned = mentions.some(j => myIds.has(j)) || (quoted !== '' && myIds.has(quoted));
                        } catch (e) {}

                        const finalChatId = isGroup ? rawChatId : resolvedSenderId;
                        const payload = JSON.stringify({
                            chatId: finalChatId,
                            senderName: senderName || '',
                            isGroup: Boolean(isGroup),
                            groupName: groupName || '',
                            mentioned: Boolean(mentioned)
                        });
                        window.nodeOnMsgReceived(payload);
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

function scheduleClientRetry(delayMs = 5000) {
    if (isShuttingDown || retryTimeout) return;
    console.log(`Scheduling WhatsApp browser client retry in ${Math.round(delayMs / 1000)}s...`);
    retryTimeout = setTimeout(() => {
        retryTimeout = null;
        if (!isShuttingDown) {
            startClient();
        }
    }, delayMs);
}

async function startClient() {
    if (isShuttingDown || isInitializing) return;
    isInitializing = true;
    if (retryTimeout) {
        clearTimeout(retryTimeout);
        retryTimeout = null;
    }
    if (checkInterval) {
        clearInterval(checkInterval);
        checkInterval = null;
    }

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

    // Close any previous partial browser instance before cleaning profile locks
    if (browser) {
        try {
            await browser.close();
        } catch (e) {}
        browser = null;
        page = null;
    }

    // Clean any orphaned Chromium profile locks from previous runs.
    // Note: On Linux, SingletonLock/SingletonCookie/SingletonSocket are dangling symlinks
    // for which fs.existsSync() returns false! Remove them unconditionally.
    for (const lockFileName of ['SingletonLock', 'SingletonCookie', 'SingletonSocket']) {
        const lockPath = path.join(sessionDir, lockFileName);
        try {
            fs.rmSync(lockPath, { force: true });
        } catch (e) {
            try {
                fs.unlinkSync(lockPath);
            } catch (e2) {}
        }
    }

    try {
        const launchedBrowser = await puppeteer.launch({
            executablePath: exe || undefined,
            headless: true,
            userDataDir: sessionDir,
            args: puppeteerArgs
        });
        browser = launchedBrowser;
        lastInitError = null;

        launchedBrowser.on('disconnected', () => {
            if (isShuttingDown || browser !== launchedBrowser) return;
            console.error('WhatsApp headless browser disconnected unexpectedly. Recovering...');
            browser = null;
            page = null;
            isReady = false;
            currentQR = null;
            if (checkInterval) {
                clearInterval(checkInterval);
                checkInterval = null;
            }
            scheduleClientRetry(5000);
        });

        const pages = await browser.pages();
        page = pages.length > 0 ? pages[0] : await browser.newPage();

        await page.setUserAgent('Mozilla/5.0 (X-UA-Compatible; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36');
        await page.setBypassCSP(true);

        let lastProgressTime = Date.now();

        // Expose Node bridge callbacks to the browser context
        await page.exposeFunction('nodeOnQrCode', (qr) => {
            if (qr) {
                lastProgressTime = Date.now();
            }
            if (qr && qr !== currentQR) {
                currentQR = qr;
                console.log('QR Code received. Scan with your phone:');
                qrcode.generate(qr, { small: true });
            }
        });

        await page.exposeFunction('nodeOnAuthenticated', () => {
            lastProgressTime = Date.now();
            console.log('WhatsApp Client is authenticated!');
        });

        await page.exposeFunction('nodeOnReady', () => {
            lastProgressTime = Date.now();
            if (!isReady) {
                console.log('WhatsApp Client is ready! Bridge is fully active.');
            }
            currentQR = null;
            isReady = true;
        });

        await page.exposeFunction('nodeOnMsgReceived', (payload) => {
            console.log(`[MSG_RECEIVED_JSON] ${payload}`);
        });

        // Pre-evaluate WA-JS on every page reload
        const initialWaJsCode = getWaJsSource();
        await page.evaluateOnNewDocument(initialWaJsCode);

        console.log('Navigating to https://web.whatsapp.com ...');
        await page.goto('https://web.whatsapp.com', {
            waitUntil: 'domcontentloaded',
            timeout: 60000
        });
        lastProgressTime = Date.now();

        // Continuous QR code, authentication, and listener attachment polling interval
        checkInterval = setInterval(async () => {
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
                        if (typeof window.WPP.conn.refreshQR === 'function') {
                            try {
                                await window.WPP.conn.refreshQR();
                            } catch (e) {}
                        }
                    }

                    // 2. Check DOM data-ref fallback
                    const domEl = document.querySelector('div[data-ref]') || document.querySelector('[data-ref]');
                    if (domEl) {
                        const ref = domEl.getAttribute('data-ref');
                        if (ref) return { ready: false, qr: ref };
                    }

                    // 3. Click QR reload button if QR code expired on page
                    const reloadBtn = document.querySelector('[data-testid="refresh-large"]') ||
                        Array.from(document.querySelectorAll('button, div[role="button"]')).find(
                            el => el.textContent && el.textContent.toLowerCase().includes('reload')
                        );
                    if (reloadBtn && typeof reloadBtn.click === 'function') {
                        try {
                            reloadBtn.click();
                        } catch (e) {}
                    }

                    return { ready: false, qr: null };
                });

                if (state) {
                    if (state.ready) {
                        lastProgressTime = Date.now();
                        if (!isReady) {
                            console.log('WhatsApp Client is ready! Bridge is fully active.');
                        }
                        isReady = true;
                        currentQR = null;
                    } else if (state.qr) {
                        lastProgressTime = Date.now();
                        if (state.qr !== currentQR) {
                            currentQR = state.qr;
                            isReady = false;
                            console.log('QR Code received. Scan with your phone:');
                            qrcode.generate(state.qr, { small: true });
                        }
                    } else if (!isReady && !currentQR && (Date.now() - lastProgressTime > 60000)) {
                        lastProgressTime = Date.now();
                        console.log('WhatsApp Web produced neither session nor QR for 60s; reloading page...');
                        await page.goto('https://web.whatsapp.com', {
                            waitUntil: 'domcontentloaded',
                            timeout: 60000
                        });
                        await injectWaJs();
                        await ensureListenersAttached();
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

        if (!injected && !isShuttingDown) {
            console.log('Attempting direct WA-JS evaluation injection...');
            await injectWaJs();
            await ensureListenersAttached();
        }

    } catch (err) {
        lastInitError = err && err.message ? err.message : String(err);
        console.error('Failed to initialize WhatsApp browser client:', err);
        isReady = false;
        currentQR = null;
        if (checkInterval) {
            clearInterval(checkInterval);
            checkInterval = null;
        }
        if (browser) {
            try {
                await browser.close();
            } catch (e) {}
            browser = null;
            page = null;
        }
        scheduleClientRetry(5000);
    } finally {
        isInitializing = false;
    }
}

startClient();

// --- Helper Functions in Node ---

async function forceShutdown() {
    console.log('Initiating shutdown...');
    isShuttingDown = true;
    if (retryTimeout) {
        clearTimeout(retryTimeout);
        retryTimeout = null;
    }
    if (checkInterval) {
        clearInterval(checkInterval);
        checkInterval = null;
    }
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
    const isLocation = msgType === 'location' || msgType === 'live_location' || (typeof msg.lat === 'number' && typeof msg.lng === 'number');
    const isContact = msgType === 'vcard' || msgType === 'multi_vcard' || msgType === 'contact';

    let locationData = null;
    let contactData = null;
    let content = '';

    if (isLocation) {
        msgType = msg.isLive ? 'live_location' : 'location';
        locationData = {
            lat: typeof msg.lat === 'number' ? msg.lat : (msg.lat ? parseFloat(msg.lat) : null),
            lng: typeof msg.lng === 'number' ? msg.lng : (msg.lng ? parseFloat(msg.lng) : null),
            name: msg.loc || '',
            address: msg.address || '',
            comment: msg.comment || msg.caption || '',
            url: msg.clientUrl || msg.url || '',
            isLive: Boolean(msg.isLive),
            accuracy: msg.accuracy,
            speed: msg.speed,
            degrees: msg.degrees,
            shareDuration: msg.shareDuration
        };

        const details = [];
        if (locationData.lat !== null && locationData.lng !== null) {
            details.push('Latitude: ' + locationData.lat, 'Longitude: ' + locationData.lng);
        }
        if (locationData.name) {
            details.push('Place: ' + locationData.name);
        }
        if (locationData.address) {
            details.push('Address: ' + locationData.address);
        }
        if (locationData.lat !== null && locationData.lng !== null) {
            details.push('Maps: https://maps.google.com/?q=' + locationData.lat + ',' + locationData.lng);
        }
        if (locationData.url) {
            details.push('URL: ' + locationData.url);
        }

        const tag = locationData.isLive ? 'Live Location' : 'Location Pin';
        const locHeader = details.length > 0 ? '[' + tag + ': ' + details.join(' | ') + ']' : '[' + tag + ']';
        const userText = locationData.comment;
        content = userText ? (locHeader + ' ' + userText) : locHeader;
    } else if (isContact) {
        function parseVCardString(vcardStr, fallbackName) {
            if (!vcardStr || typeof vcardStr !== 'string') {
                return fallbackName ? { name: fallbackName, phone: null, phones: [], whatsappId: null, org: null, title: null, email: null, url: null, vcard: null } : null;
            }
            const lines = vcardStr.replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n').replace(/\\n[ \\t]/g, '').split('\\n');
            let name = fallbackName || '';
            let phones = [];
            let whatsappId = null;
            let org = '';
            let title = '';
            let email = '';
            let url = '';

            for (const rawLine of lines) {
                const line = rawLine.trim();
                if (!line) continue;
                const colonIdx = line.indexOf(':');
                if (colonIdx === -1) continue;
                const keyPart = line.slice(0, colonIdx);
                const valPart = line.slice(colonIdx + 1).trim();
                const keyUpper = keyPart.toUpperCase();

                if (keyUpper === 'FN' || keyUpper.startsWith('FN;') || keyUpper.startsWith('FN:')) {
                    if (valPart) name = valPart;
                } else if ((keyUpper === 'N' || keyUpper.startsWith('N;')) && !name) {
                    const parts = valPart.split(';').filter(Boolean);
                    if (parts.length >= 2) name = parts[1] + ' ' + parts[0];
                    else if (parts.length === 1) name = parts[0];
                } else if (keyUpper === 'TEL' || keyUpper.startsWith('TEL;') || keyUpper.startsWith('TEL:')) {
                    if (valPart) phones.push(valPart);
                    const waidMatch = keyPart.match(/waid=(\\d+)/i);
                    if (waidMatch && !whatsappId) {
                        whatsappId = waidMatch[1] + '@c.us';
                    }
                } else if (keyUpper === 'ORG' || keyUpper.startsWith('ORG;')) {
                    org = valPart.replace(/;/g, ' ').trim();
                } else if (keyUpper === 'TITLE' || keyUpper.startsWith('TITLE;')) {
                    title = valPart;
                } else if (keyUpper === 'EMAIL' || keyUpper.startsWith('EMAIL;')) {
                    email = valPart;
                } else if (keyUpper === 'URL' || keyUpper.startsWith('URL;')) {
                    url = valPart;
                }
            }

            const primaryPhone = phones[0] || null;
            if (!whatsappId && primaryPhone) {
                const cleanDigits = primaryPhone.replace(/[^\\d]/g, '');
                if (cleanDigits.length >= 7) {
                    whatsappId = cleanDigits + '@c.us';
                }
            }

            return {
                name: name || 'Unknown Contact',
                phone: primaryPhone,
                phones: phones,
                whatsappId: whatsappId,
                org: org || null,
                title: title || null,
                email: email || null,
                url: url || null,
                vcard: vcardStr
            };
        }

        let parsedContacts = [];
        if (msg.vcardList && Array.isArray(msg.vcardList) && msg.vcardList.length > 0) {
            for (const item of msg.vcardList) {
                const vcardStr = (item && typeof item === 'object') ? (item.vcard || '') : (typeof item === 'string' ? item : '');
                const fallbackName = (item && typeof item === 'object') ? (item.displayName || item.name || '') : '';
                const parsed = parseVCardString(vcardStr, fallbackName);
                if (parsed) parsedContacts.push(parsed);
            }
        } else if (msg.body && typeof msg.body === 'string' && msg.body.includes('BEGIN:VCARD')) {
            const chunks = msg.body.split(/(?=BEGIN:VCARD)/i).filter(c => c.trim().length > 0);
            for (const chunk of chunks) {
                const parsed = parseVCardString(chunk, msg.vcardFormattedName);
                if (parsed) parsedContacts.push(parsed);
            }
        } else if (msg.vcardFormattedName) {
            parsedContacts.push({
                name: msg.vcardFormattedName,
                phone: null,
                phones: [],
                whatsappId: null,
                org: null,
                title: null,
                email: null,
                url: null,
                vcard: null
            });
        }

        contactData = parsedContacts;
        if (contactData.length === 1) {
            const c = contactData[0];
            const details = [];
            if (c.name) details.push('Name: ' + c.name);
            if (c.phone) details.push('Phone: ' + c.phone);
            if (c.whatsappId) details.push('WhatsApp ID: ' + c.whatsappId);
            if (c.org) details.push('Org: ' + c.org);
            if (c.title) details.push('Title: ' + c.title);
            if (c.email) details.push('Email: ' + c.email);
            if (c.url) details.push('URL: ' + c.url);
            const cardHeader = details.length > 0 ? '[Contact Card: ' + details.join(' | ') + ']' : '[Contact Card]';
            const userText = msg.comment || msg.caption || '';
            content = userText ? (cardHeader + ' ' + userText) : cardHeader;
        } else if (contactData.length > 1) {
            const summaries = contactData.map((c, i) => {
                const parts = [];
                if (c.name) parts.push('Name: ' + c.name);
                if (c.phone) parts.push('Phone: ' + c.phone);
                if (c.whatsappId) parts.push('WhatsApp ID: ' + c.whatsappId);
                if (c.org) parts.push('Org: ' + c.org);
                return (i + 1) + '. ' + parts.join(' | ');
            });
            const cardHeader = '[Contact Cards (' + contactData.length + '): ' + summaries.join(' ; ') + ']';
            const userText = msg.comment || msg.caption || '';
            content = userText ? (cardHeader + ' ' + userText) : cardHeader;
        } else {
            content = '[Contact Card]';
        }
    } else {
        if (msg.isMedia) {
            if (!msgType || msgType === 'chat') msgType = 'document';
        }
        content = msg.body || msg.caption || '';
    }

    const hasMedia = !isLocation && !isContact && Boolean(msg.hasMedia || msg.isMedia || msgType === 'ptt' || msgType === 'audio' || msgType === 'image' || msgType === 'video' || msgType === 'document');

    return {
        id: msgIdStr,
        chatId: chatIdStr,
        chatName: (chat && (chat.name || (chat.id && chat.id.user))) || '',
        timestamp: msg.t || msg.timestamp || Math.floor(Date.now() / 1000),
        sender: fromStr,
        author: authorStr,
        senderName: senderName,
        senderNumber: senderNumber,
        content: content,
        fromMe: Boolean(msg.fromMe || msg.id && msg.id.fromMe),
        isGroup: isGroup,
        hasMedia: hasMedia,
        type: msgType,
        location: locationData,
        contacts: contactData
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

        let mimeType = 'application/octet-stream';
        let rawBase64 = base64Data;

        const commaIdx = base64Data.indexOf(',');
        if (base64Data.startsWith('data:') && commaIdx !== -1) {
            const meta = base64Data.slice(5, commaIdx);
            mimeType = meta.split(';')[0].trim() || 'application/octet-stream';
            rawBase64 = base64Data.slice(commaIdx + 1).trim();
        } else {
            const matches = base64Data.match(/^data:([^;,]+)(?:;[^,]*)?;base64,(.+)$/s);
            if (matches) {
                mimeType = matches[1].trim();
                rawBase64 = matches[2].trim();
            }
        }

        let extension = 'bin';
        if (mimeType.includes('/')) {
            extension = mimeType.split('/')[1].split(';')[0].trim();
        }
        if (msgType === 'ptt' || msgType === 'audio' || mimeType.startsWith('audio/')) {
            if (extension === 'bin' || extension === 'octet-stream') {
                extension = 'ogg';
            }
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
    res.json({
        ready: isReady,
        qr: currentQR,
        browserAttached: Boolean(browser && page),
        initializing: isInitializing,
        error: lastInitError
    });
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

app.post('/send_location', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    const { target, lat, lng, name, address, url, reply_to } = req.body;

    if (!target) {
        return res.status(400).json({ error: 'Missing required field: target' });
    }
    const parsedLat = typeof lat === 'string' ? parseFloat(lat) : Number(lat);
    const parsedLng = typeof lng === 'string' ? parseFloat(lng) : Number(lng);
    if (isNaN(parsedLat) || isNaN(parsedLng)) {
        return res.status(400).json({ error: 'Invalid or missing coordinates (lat, lng)' });
    }

    try {
        const resolvedChatId = await page.evaluate(async (t, resolveHelper) => {
            eval(resolveHelper);
            return await resolveTargetInBrowser(t);
        }, target, browserResolveTarget);

        if (!resolvedChatId) {
            return res.status(404).json({ error: `Could not resolve target: ${target}` });
        }

        let sendOptions = {};
        if (reply_to) {
            sendOptions.quotedMsg = reply_to;
        }

        const locationOptions = {
            lat: parsedLat,
            lng: parsedLng,
            name: name || undefined,
            address: address || undefined,
            url: url || undefined,
            ...sendOptions
        };

        await page.evaluate(async (cid, opts) => {
            await window.WPP.chat.sendLocationMessage(cid, opts);
        }, resolvedChatId, locationOptions);

        res.json({ success: true, target: resolvedChatId });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/send_contact', async (req, res) => {
    if (!isReady || !page) return res.status(503).json({ error: 'Client not ready' });
    const { target, name, phone, organization, email, vcard, contacts, reply_to } = req.body;

    if (!target) {
        return res.status(400).json({ error: 'Missing required field: target' });
    }

    if (!name && !phone && !vcard && (!contacts || (Array.isArray(contacts) && contacts.length === 0))) {
        return res.status(400).json({ error: 'Missing contact information (name, phone, vcard, or contacts)' });
    }

    try {
        const resolvedChatId = await page.evaluate(async (t, resolveHelper) => {
            eval(resolveHelper);
            return await resolveTargetInBrowser(t);
        }, target, browserResolveTarget);

        if (!resolvedChatId) {
            return res.status(404).json({ error: `Could not resolve target: ${target}` });
        }

        let sendOptions = {};
        if (reply_to) {
            sendOptions.quotedMsg = reply_to;
        }

        const contactPayload = {
            name: name || undefined,
            phone: phone || undefined,
            organization: organization || undefined,
            email: email || undefined,
            vcard: vcard || undefined,
            contacts: contacts || undefined
        };

        await page.evaluate(async (cid, data, opts) => {
            function buildVCard(cName, cPhone, cOrg, cEmail) {
                let cleanPhone = (cPhone || '').replace(/[^\d]/g, '');
                let lines = [
                    'BEGIN:VCARD',
                    'VERSION:3.0',
                    'FN:' + (cName || cleanPhone || 'Contact'),
                    'N:' + (cName || cleanPhone || 'Contact') + ';;;;'
                ];
                if (cleanPhone) {
                    lines.push('TEL;type=CELL;type=VOICE;waid=' + cleanPhone + ':+' + cleanPhone);
                }
                if (cOrg) {
                    lines.push('ORG:' + cOrg);
                }
                if (cEmail) {
                    lines.push('EMAIL;type=INTERNET:' + cEmail);
                }
                lines.push('END:VCARD');
                return lines.join('\n');
            }

            // Case 1: Multi-contact array
            if (data.contacts && Array.isArray(data.contacts) && data.contacts.length > 0) {
                const list = [];
                for (const item of data.contacts) {
                    if (item.vcard) {
                        list.push({
                            vcard: item.vcard,
                            displayName: item.name || item.displayName || 'Contact'
                        });
                    } else {
                        const iName = item.name || item.displayName || 'Contact';
                        const iPhone = item.phone || item.id || '';
                        const vcardText = buildVCard(iName, iPhone, item.organization || item.org, item.email);
                        list.push({
                            vcard: vcardText,
                            displayName: iName
                        });
                    }
                }
                if (list.length === 1) {
                    return await window.WPP.chat.sendRawMessage(cid, {
                        type: 'vcard',
                        body: list[0].vcard,
                        vcardFormattedName: list[0].displayName
                    }, opts);
                } else {
                    return await window.WPP.chat.sendRawMessage(cid, {
                        type: 'multi_vcard',
                        vcardList: list
                    }, opts);
                }
            }

            // Case 2: Raw vCard string provided
            if (data.vcard && typeof data.vcard === 'string') {
                let vcardName = data.name;
                if (!vcardName) {
                    const fnMatch = data.vcard.match(/FN(?:;[^:]*)?:([^\r\n]+)/i);
                    vcardName = fnMatch ? fnMatch[1].trim() : 'Contact';
                }
                return await window.WPP.chat.sendRawMessage(cid, {
                    type: 'vcard',
                    body: data.vcard,
                    vcardFormattedName: vcardName
                }, opts);
            }

            // Case 3: Single contact (with name, phone, optional org/email)
            let resolvedPhone = data.phone;
            let resolvedName = data.name;

            // If phone is missing, try looking up contact by name
            if (!resolvedPhone && resolvedName) {
                try {
                    const contactsList = await window.WPP.contact.list();
                    const lower = resolvedName.toLowerCase();
                    const matched = contactsList.find(c => (c.name && c.name.toLowerCase() === lower) || (c.pushname && c.pushname.toLowerCase() === lower));
                    if (matched) {
                        resolvedPhone = (matched.id && (matched.id._serialized || matched.id)) || '';
                        resolvedName = resolvedName || matched.name || matched.pushname;
                    }
                } catch (e) {}
            }

            if (!resolvedPhone && !resolvedName) {
                throw new Error('Could not resolve contact details to send');
            }

            // If organization or email is present, send rich custom vCard
            if (data.organization || data.email) {
                const vcardText = buildVCard(resolvedName, resolvedPhone, data.organization, data.email);
                return await window.WPP.chat.sendRawMessage(cid, {
                    type: 'vcard',
                    body: vcardText,
                    vcardFormattedName: resolvedName || 'Contact'
                }, opts);
            }

            // Standard contact card via WPP.chat.sendVCardContactMessage
            let wid = resolvedPhone;
            if (wid && !wid.includes('@')) {
                const cleanDigits = wid.replace(/[^\d]/g, '');
                wid = cleanDigits + '@c.us';
            }
            if (!wid) {
                const vcardText = buildVCard(resolvedName, '', '', '');
                return await window.WPP.chat.sendRawMessage(cid, {
                    type: 'vcard',
                    body: vcardText,
                    vcardFormattedName: resolvedName || 'Contact'
                }, opts);
            }

            return await window.WPP.chat.sendVCardContactMessage(cid, {
                id: wid,
                name: resolvedName || wid.split('@')[0]
            }, opts);
        }, resolvedChatId, contactPayload, sendOptions);

        res.json({ success: true, target: resolvedChatId });
    } catch (e) {
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

        let mimeType = 'application/octet-stream';
        let rawBase64 = base64Data;

        const commaIdx = base64Data.indexOf(',');
        if (base64Data.startsWith('data:') && commaIdx !== -1) {
            const meta = base64Data.slice(5, commaIdx);
            mimeType = meta.split(';')[0].trim() || 'application/octet-stream';
            rawBase64 = base64Data.slice(commaIdx + 1).trim();
        } else {
            const matches = base64Data.match(/^data:([^;,]+)(?:;[^,]*)?;base64,(.+)$/s);
            if (matches) {
                mimeType = matches[1].trim();
                rawBase64 = matches[2].trim();
            }
        }
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
