const express = require('express');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');
const multer = require('multer');

const app = express();
const port = 3000;

app.use(express.json());

const dataDir = process.env.WHATSAPP_DATA_DIR || __dirname;

const uploadDir = path.join(dataDir, 'uploads');
if (!fs.existsSync(uploadDir)) {
    fs.mkdirSync(uploadDir, { recursive: true });
}
const upload = multer({ dest: uploadDir });

let client;
let isReady = false;
let currentQR = null;
let isShuttingDown = false;

process.on('uncaughtException', (err) => {
    const errStr = err ? err.toString() : '';
    if (isShuttingDown) {
        console.log('Ignored uncaught error during shutdown:', err.message);
    } else if (errStr.includes('Execution context was destroyed') || errStr.includes('Target closed')) {
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
    } else if (reasonStr.includes('Execution context was destroyed') || reasonStr.includes('Target closed')) {
        console.log('Ignored benign Puppeteer rejection:', reasonStr);
    } else {
        console.error('Unhandled Rejection at:', promise, 'reason:', reason);
    }
});

async function startClient() {
    let options = {
        authStrategy: new LocalAuth({
            dataPath: path.join(dataDir, '.wwebjs_auth')
        }),
        webVersionCache: {
            type: 'local',
            path: path.join(dataDir, '.wwebjs_cache')
        },
        puppeteer: {
            headless: true,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu'
            ]
        }
    };

    console.log('Starting client with strict local whatsapp-web.js...');
    client = new Client(options);

    client.on('authenticated', () => {
        console.log('WhatsApp Client is authenticated!');
    });

    client.on('qr', (qr) => {
        console.log('QR Code received. Scan with your phone:');
        currentQR = qr;
        qrcode.generate(qr, { small: true });
    });

    client.on('ready', async () => {
        console.log('WhatsApp Client is ready! Running health check...');
        try {
            await client.getState();
            
            // Explicitly validate internal store structures to catch protocol mismatches 
            // that getState() misses when the protocol changes silently.
            await client.pupPage.evaluate(() => {
                const WAWebCollections = window.require("WAWebCollections");
                if (!WAWebCollections || !WAWebCollections.Chat) {
                    throw new Error("WAWebCollections.Chat is missing");
                }
                const models = WAWebCollections.Chat.getModelsArray();
                if (models.length === 0) {
                    throw new Error("Protocol mismatch: 0 chats found (likely structural change)");
                }
            });

            console.log('Health check passed. Bridge is fully active.');
            currentQR = null;
            isReady = true;
        } catch (err) {
            console.error('Health check failed. Protocol mismatch detected:', err.message);
            console.error('Waiting for whatsapp-web.js patch update.');
            isReady = true; // Stay "ready" so the API endpoints can return the 500 protocol mismatch error explicitly
        }
    });

    client.on('message', async msg => {
        let senderName = msg._data?.notifyName || msg.author || 'Unknown';
        try {
            const chat = await msg.getChat();
            if (chat && !chat.isGroup) {
                senderName = chat.name || senderName;
            } else if (chat && chat.isGroup && msg.author) {
                const contact = await client.getContactById(msg.author);
                if (contact) {
                    senderName = contact.name || contact.pushname || senderName;
                }
            }
        } catch (e) {
            console.error('Failed to resolve chat for MSG_RECEIVED:', e);
        }
        
        let resolvedChatId = msg.from;
        console.log(`[MSG_RECEIVED] ${resolvedChatId} ${senderName}`);
        
        if(msg.hasMedia && msg.type === 'ptt') {
            // It's a voice message
        }
    });

    client.initialize();
}

startClient();

// --- Endpoints ---

app.get('/status', (req, res) => {
    res.json({ ready: isReady, qr: currentQR });
});

app.post('/eval', async (req, res) => {
    try {
        const code = req.body.code;
        const result = await client.pupPage.evaluate(code);
        res.json({ result });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/shutdown', async (req, res) => {
    console.log('Shutdown requested via API');
    isShuttingDown = true;
    res.json({ success: true });
    try {
        await client.destroy();
    } catch (e) {
        console.error('Error during client destroy:', e);
    }
    process.exit(0);
});

app.get('/unread', async (req, res) => {
    if (!isReady) return res.status(503).json({ error: 'Client not ready' });
    try {
        console.log('[DEBUG] /unread endpoint called - fetching natively');
        const chats = await client.getChats();
        
        let unreadMessages = [];
        for (const chat of chats) {
            if (chat.unreadCount > 0) {
                try {
                    const msgs = await chat.fetchMessages({ limit: chat.unreadCount });
                    for (const msg of msgs) {
                        unreadMessages.push(await serializeMessage(msg, chat));
                    }
                } catch (err) {
                    console.error(`Failed to fetch unread for chat ${chat.id._serialized}:`, err);
                    throw err; // Re-throw to trigger protocol mismatch error
                }
            }
        }
        
        res.json({ unread: unreadMessages });
    } catch (e) {
        console.error("Unread extraction failed natively. Protocol mismatch detected:", e);
        res.status(500).json({ error: "Protocol mismatch: Waiting for whatsapp-web.js patch update.", details: e.message });
    }
});

app.post('/mark_read', async (req, res) => {
    if (!isReady) return res.status(503).json({ error: 'Client not ready' });
    const { chatId } = req.body; 
    try {
        const chat = await client.getChatById(chatId);
        await chat.sendSeen();
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/react', async (req, res) => {
    if (!isReady) return res.status(503).json({ error: 'Client not ready' });
    const { msgId, reaction } = req.body;
    try {
        const msg = await client.getMessageById(msgId);
        if (msg) {
            await msg.react(reaction);
            res.json({ success: true });
        } else {
            res.status(404).json({ error: 'Message not found' });
        }
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/resolve', async (req, res) => {
    if (!isReady) return res.status(503).json({ error: 'Client not ready' });
    const target = req.query.target;
    if (!target) return res.status(400).json({ error: 'No target provided' });
    
    try {
        const chatId = await resolveTarget(target);
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
    if (!isReady) return res.status(503).json({ error: 'Client not ready' });
    const n = parseInt(req.query.n) || 30;
    const target = req.query.target;
    
    try {
        console.log(`[DEBUG] /recent endpoint called. target=${target}`);
        let targetChat = null;
        if (target) {
            const chatId = await resolveTarget(target);
            if (!chatId) return res.status(404).json({ error: `Target not found: ${target}` });
            targetChat = await client.getChatById(chatId);
        }
        
        let msgsToSerialize = [];
        
        if (targetChat) {
            const msgs = await targetChat.fetchMessages({ limit: n });
            for (const msg of msgs) {
                msgsToSerialize.push({ msg, chat: targetChat });
            }
        } else {
            const chats = await client.getChats();
            const topChats = chats.slice(0, 15);
            for (const chat of topChats) {
                const msgs = await chat.fetchMessages({ limit: 5 });
                for (const msg of msgs) {
                    msgsToSerialize.push({ msg, chat });
                }
            }
        }
        
        let allMsgs = [];
        for (const pair of msgsToSerialize) {
            allMsgs.push(await serializeMessage(pair.msg, pair.chat));
        }
        
        allMsgs.sort((a, b) => b.timestamp - a.timestamp);
        res.json({ messages: allMsgs.slice(0, n) });
    } catch (e) {
        console.error("Recent extraction failed natively. Protocol mismatch detected:", e);
        res.status(500).json({ error: "Protocol mismatch: Waiting for whatsapp-web.js patch update.", details: e.message });
    }
});

app.get('/recent/:chatId', async (req, res) => {
    if (!isReady) return res.status(503).json({ error: 'Client not ready' });
    const n = parseInt(req.query.n) || 30;
    try {
        const chat = await client.getChatById(req.params.chatId);
        const msgs = await chat.fetchMessages({ limit: n });
        let allMsgs = [];
        for (const msg of msgs) {
            allMsgs.push(await serializeMessage(msg, chat));
        }
        allMsgs.sort((a, b) => b.timestamp - a.timestamp);
        res.json({ messages: allMsgs });
    } catch (e) {
        console.error("Recent chatId extraction failed natively:", e);
        res.status(500).json({ error: "Protocol mismatch: Waiting for whatsapp-web.js patch update.", details: e.message });
    }
});

app.post('/send', upload.single('media'), async (req, res) => {
    if (!isReady) return res.status(503).json({ error: 'Client not ready' });
    const { target, text, reply_to } = req.body;
    
    try {
        // Resolve target
        const chatId = await resolveTarget(target);
        if (!chatId) {
            return res.status(404).json({ error: `Could not resolve target: ${target}` });
        }

        let contentToSend = text || '';
        const options = {
            linkPreview: true
        };

        // Automatically parse mentions from text
        if (text) {
            const mentionMatches = text.match(/@(\d+)/g);
            if (mentionMatches) {
                const mentionIds = mentionMatches.map(m => m.substring(1) + '@c.us');
                options.mentions = mentionIds;
            }
        }

        if (req.file) {
            // Check if it's an audio file for voice message
            const isVoice = req.body.isVoice === 'true';
            
            // To ensure WhatsApp knows what it is, we need to supply the mime type and a proper filename 
            // if the multer file lacks an extension.
            const mimeType = req.file.mimetype || 'audio/mp3';
            const filename = req.file.originalname || 'voice.mp3';
            const mediaData = fs.readFileSync(req.file.path, { encoding: 'base64' });
            
            contentToSend = new MessageMedia(mimeType, mediaData, filename);

            if (isVoice) {
                options.sendAudioAsVoice = true;
            } else if (text) {
                options.caption = text;
            }
        }

        let sent = false;
        if (reply_to) {
            try {
                const quotedMsg = await client.getMessageById(reply_to);
                if (quotedMsg) {
                    await quotedMsg.reply(contentToSend, chatId, options);
                    sent = true;
                }
            } catch (e) {
                console.error("Could not fetch quoted message for reply:", e);
                options.quotedMessageId = reply_to;
            }
        }

        if (!sent) {
            await client.sendMessage(chatId, contentToSend, options);
        }
        
        res.json({ success: true, target: chatId });
        
        if (req.file) {
            fs.unlinkSync(req.file.path); // cleanup
        }
    } catch (e) {
        res.status(500).json({ error: e.message });
        if (req.file) {
            fs.unlinkSync(req.file.path);
        }
    }
});

app.get('/media/:msgId', async (req, res) => {
    if (!isReady) return res.status(503).json({ error: 'Client not ready' });
    try {
        const msg = await client.getMessageById(req.params.msgId);
        if (msg.hasMedia) {
            const media = await msg.downloadMedia();
            const buffer = Buffer.from(media.data, 'base64');
            res.set('Content-Type', media.mimetype);
            res.send(buffer);
        } else {
            res.status(404).json({ error: 'No media' });
        }
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

async function resolveTarget(target) {
    if (target.includes('@c.us') || target.includes('@g.us')) return target;
    
    // First check existing contacts instead of chats (since getChats is broken)
    const chats = await client.getContacts();
    // 1. Exact Name
    let chat = chats.find(c => c.name === target);
    // 2. Case Insensitive
    if (!chat) chat = chats.find(c => c.name && c.name.toLowerCase() === target.toLowerCase());
    // 3. Partial Match
    if (!chat) {
        const matches = chats.filter(c => c.name && c.name.toLowerCase().includes(target.toLowerCase()));
        if (matches.length > 0) chat = matches.sort((a, b) => a.name.length - b.name.length)[0];
    }
    // 4. Robust Phone Number Matching
    if (!chat) {
        // Strip everything except digits and plus
        let cleanInput = target.replace(/[^\d+]/g, '');
        
        // Handle South African number formatting (assuming local numbers starting with 0 belong to +27)
        // If it starts with 0 and is 10 digits long, convert to 27
        if (cleanInput.startsWith('0') && cleanInput.length === 10) {
            cleanInput = '27' + cleanInput.substring(1);
        }
        
        // Strip any remaining leading '+'
        cleanInput = cleanInput.replace('+', '');
        
        // Only try to match if we have a reasonable length number to avoid false positives
        if (cleanInput.length >= 7) {
            chat = chats.find(c => {
                const chatIdUser = c.id.user;
                // Direct match on ID
                if (chatIdUser === cleanInput) return true;
                
                // Sometimes the chat ID has the country code and the input doesn't (e.g. chat is 2783... input is 83...)
                if (chatIdUser.endsWith(cleanInput)) return true;
                
                // Sometimes the input has the country code and the chat ID doesn't (rare, but just in case)
                if (cleanInput.endsWith(chatIdUser) && chatIdUser.length >= 7) return true;
                
                // For LID (Linked Device) accounts, the ID is random numbers, but the name holds the formatted phone number
                if (c.name) {
                    const cleanName = c.name.replace(/[^\d+]/g, '').replace('+', '');
                    if (cleanName === cleanInput) return true;
                    if (cleanName.endsWith(cleanInput) && cleanInput.length >= 9) return true;
                }
                
                return false;
            });
        }
    }
    
    if (chat) return chat.id._serialized;

    // If no chat found, check all contacts (pushnames)
    const contacts = await client.getContacts();
    
    // 5. Exact Pushname Match
    let contact = contacts.find(c => c.pushname === target || c.name === target);
    
    // 6. Case Insensitive Pushname
    if (!contact) contact = contacts.find(c => (c.pushname && c.pushname.toLowerCase() === target.toLowerCase()) || (c.name && c.name.toLowerCase() === target.toLowerCase()));
    
    // 7. Partial Pushname Match (Target is part of pushname, or pushname is part of target)
    if (!contact) {
        const matches = contacts.filter(c => {
            const pname = (c.pushname || '').toLowerCase();
            const cname = (c.name || '').toLowerCase();
            const t = target.toLowerCase();
            return (pname && (pname.includes(t) || t.includes(pname))) || (cname && (cname.includes(t) || t.includes(cname)));
        });
        
        if (matches.length > 0) {
            // Prefer regular c.us accounts over lid accounts if there's a duplicate
            const nonLid = matches.find(m => m.id._serialized.includes('@c.us'));
            contact = nonLid || matches[0];
        }
    }
    
    // 8. Robust Phone Number Matching against contacts
    if (!contact) {
        let cleanInput = target.replace(/[^\d+]/g, '');
        if (cleanInput.startsWith('0') && cleanInput.length === 10) {
            cleanInput = '27' + cleanInput.substring(1);
        }
        cleanInput = cleanInput.replace('+', '');
        
        if (cleanInput.length >= 7) {
            contact = contacts.find(c => {
                const contactIdUser = c.id.user;
                if (contactIdUser === cleanInput) return true;
                if (contactIdUser.endsWith(cleanInput)) return true;
                if (cleanInput.endsWith(contactIdUser) && contactIdUser.length >= 7) return true;
                return false;
            });
        }
    }
    
    return contact ? contact.id._serialized : null;
}

async function resolveLidToCus(client, lidOrOther) {
    if (!lidOrOther || !lidOrOther.includes('@lid')) return lidOrOther;
    try {
        const lidContact = await client.getContactById(lidOrOther);
        const contactName = lidContact.name || lidContact.pushname;
        if (contactName) {
            const contacts = await client.getContacts();
            const realContact = contacts.find(c => (c.name === contactName || c.pushname === contactName) && c.id._serialized.includes('@c.us'));
            if (realContact) {
                return realContact.id._serialized;
            }
        }
    } catch(e) {
        console.error("LID resolution failed", e);
    }
    return lidOrOther;
}

async function serializeMessage(msg, chat) {
    let senderName = 'Unknown';
    if (msg._data && msg._data.notifyName) {
        senderName = msg._data.notifyName;
    } else if (msg.author) {
        const contact = await client.getContactById(msg.author);
        senderName = contact.name || contact.pushname || msg.author;
    } else {
        const contact = await msg.getContact();
        senderName = contact.name || contact.pushname || msg.from;
    }

    let mediaPath = null;
    if (msg.hasMedia && (msg.type === 'ptt' || msg.type === 'audio' || msg.type === 'image' || msg.type === 'document')) {
        try {
            const media = await msg.downloadMedia();
            if (media) {
                let extension = media.mimetype.split('/')[1].split(';')[0] || 'bin';
                // Some mimetypes are like application/pdf, which gives extension 'pdf'
                // Some are like image/jpeg, which gives 'jpeg'
                const filename = `${msg.id.id}.${extension}`;
                mediaPath = path.join(__dirname, 'uploads', filename);
                fs.writeFileSync(mediaPath, media.data, 'base64');
            }
        } catch (e) {
            console.error('Failed to download media:', e);
        }
    }

    const resolvedChatId = await resolveLidToCus(client, chat.id._serialized);
    const resolvedSenderId = await resolveLidToCus(client, msg.from);
    const resolvedAuthor = await resolveLidToCus(client, msg.author);

    let authorIdToUse = chat.isGroup ? resolvedAuthor : resolvedSenderId;
    let senderNumber = null;
    if (authorIdToUse && authorIdToUse.includes('@c.us')) {
        senderNumber = authorIdToUse.split('@')[0];
    }

    return {
        id: msg.id._serialized,
        chatId: resolvedChatId,
        chatName: chat.name || chat.id.user,
        timestamp: msg.timestamp,
        sender: resolvedSenderId,
        author: resolvedAuthor,
        senderName: senderName,
        senderNumber: senderNumber,
        content: msg.body,
        fromMe: msg.fromMe,
        isGroup: chat.isGroup,
        hasMedia: msg.hasMedia,
        type: msg.type, // 'ptt' for voice messages
        mediaPath: mediaPath
    };
}

const server = app.listen(port, () => {
    console.log(`WhatsApp Bridge running on port ${port}`);
});

process.on('SIGTERM', () => {
    console.log('SIGTERM signal received: closing HTTP server');
    isShuttingDown = true;
    server.close(() => {
        client.destroy();
        process.exit(0);
    });
});
