/**
 * Discord Bot for Upscale Service
 */
const path = require('path');
const fs = require('fs');
require('dotenv').config({ path: path.join(__dirname, '../.env') });
const { Client, GatewayIntentBits, SlashCommandBuilder, REST, Routes, EmbedBuilder, MessageFlags, PermissionsBitField, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const axios = require('axios');
const { createClient } = require('@supabase/supabase-js');

// Config
const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID;
const API_URL = process.env.API_URL || 'http://localhost:10100';
const API_KEY = process.env.DISCORD_API_KEY;
const ADMIN_USER_ID = process.env.ADMIN_USER_ID;
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_KEY;

// Check if admin ID is set
if (!ADMIN_USER_ID) {
    console.warn('[WARNING] ADMIN_USER_ID not set in .env - Admin commands will be disabled');
}

// Admin check
function isAdmin(userId) {
    return ADMIN_USER_ID && userId === ADMIN_USER_ID;
}

// Create logs directory
const logsDir = path.join(__dirname, '..', 'logs');
if (!fs.existsSync(logsDir)) {
    fs.mkdirSync(logsDir, { recursive: true });
}

// Logger
function log(level, message, error = null) {
    const timestamp = new Date().toISOString();
    const logMessage = `${timestamp} | ${level} | ${message}`;
    
    // Console output (minimal)
    if (level === 'ERROR') {
        console.error(`[ERROR] ${message} - Check logs/bot.log for details`);
    } else if (level === 'INFO') {
        console.log(`[INFO] ${message}`);
    }
    
    // File output (full details)
    const logFile = path.join(logsDir, `bot_${new Date().toISOString().split('T')[0]}.log`);
    let fileContent = logMessage;
    if (error) {
        fileContent += `\n${error.stack || error.message || error}`;
    }
    fileContent += '\n';
    
    fs.promises.appendFile(logFile, fileContent).catch(() =>
        console.error('Failed to write to log file')
    );
}

// Colors
const COLORS = {
    primary: 0x5865F2,
    success: 0x57F287,
    error: 0xED4245,
    warning: 0xFEE75C
};

// Create Discord client
const client = new Client({
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.DirectMessages
    ]
});

// Supabase client
const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

// API helper
async function apiRequest(method, endpoint, data = null) {
    try {
        const config = {
            method,
            url: `${API_URL}${endpoint}`,
            headers: {
                'Authorization': `Bearer ${API_KEY}`,
                'Content-Type': 'application/json'
            }
        };
        
        if (data) {
            config.data = data;
        }
        
        config.timeout = 10000;
        const response = await axios(config);
        return response.data;
    } catch (error) {
        log('ERROR', `API ${method} ${endpoint} failed`, error);
        throw error;
    }
}

// Commands
const commands = [
    new SlashCommandBuilder()
        .setName('ping')
        .setDescription('Check bot latency'),
    
    new SlashCommandBuilder()
        .setName('profile')
        .setDescription('View your profile and credit stats')
        .addUserOption(option =>
            option.setName('user')
                .setDescription('User to check (Admin only)')
                .setRequired(false)
        ),
    
    new SlashCommandBuilder()
        .setName('upscale')
        .setDescription('Submit a Google Drive folder for upscaling')
        .addStringOption(option =>
            option.setName('folder_url')
                .setDescription('Google Drive folder URL')
                .setRequired(true)
        ),
    
    new SlashCommandBuilder()
        .setName('history')
        .setDescription('View your transaction history')
        .addUserOption(option =>
            option.setName('user')
                .setDescription('User to check (Admin only)')
                .setRequired(false)
        ),
    
    new SlashCommandBuilder()
        .setName('admin')
        .setDescription('[Admin] Manage user credits')
        .addStringOption(option =>
            option.setName('action')
                .setDescription('Action to perform')
                .setRequired(true)
                .addChoices(
                    { name: '➕ Add Credits', value: 'add' },
                    { name: '➖ Deduct Credits', value: 'deduct' }
                )
        )
        .addUserOption(option =>
            option.setName('user')
                .setDescription('Target user')
                .setRequired(true)
        )
        .addIntegerOption(option =>
            option.setName('amount')
                .setDescription('Number of credits')
                .setRequired(true)
        )
        .addStringOption(option =>
            option.setName('reason')
                .setDescription('Reason')
                .setRequired(false)
        )
];

// Register commands
async function registerCommands() {
    const rest = new REST({ version: '10' }).setToken(DISCORD_TOKEN);
    
    try {
        log('INFO', 'Registering slash commands...');
        
        await rest.put(
            Routes.applicationCommands(DISCORD_CLIENT_ID),
            { body: commands.map(cmd => cmd.toJSON()) }
        );
        
        log('INFO', 'Commands registered successfully');
    } catch (error) {
        log('ERROR', 'Failed to register commands', error);
    }
}

// Calculate credits based on total height
function calculateCredits(totalHeight) {
    if (totalHeight <= 200000) return 1;
    if (totalHeight <= 300000) return 2;
    if (totalHeight <= 400000) return 3;
    if (totalHeight <= 500000) return 4;
    return 5;
}

// Get estimated processing time from settings
async function getEstimatedProcessingTime(queuePosition = 0) {
    try {
        const { data: setting } = await supabase
            .from('settings')
            .select('value')
            .eq('key', 'estimated_processing_time_per_chapter_seconds')
            .maybeSingle();
        
        if (setting?.value) {
            const secondsPerChapter = parseInt(setting.value, 10);
            if (!isNaN(secondsPerChapter) && secondsPerChapter > 0) {
                // queuePosition is 1-indexed, so subtract 1 for the calculation
                // If queuePosition is 1, this request is next, so estimated time = secondsPerChapter
                const totalSeconds = queuePosition * secondsPerChapter;
                return Math.ceil(totalSeconds / 60);
            }
        }
    } catch (error) {
        log('ERROR', 'Failed to get estimated processing time', error);
    }
    return null;
}

// Realtime poller - polls Supabase every 10 seconds for completed/failed requests
const notifiedIds = new Set();
const notifiedTimestamps = new Map();

function cleanupNotifiedIds() {
    const oneHourAgo = Date.now() - 3600000;
    for (const [id, ts] of notifiedTimestamps) {
        if (ts < oneHourAgo) {
            notifiedIds.delete(id);
            notifiedTimestamps.delete(id);
        }
    }
}

async function startRealtimePoller() {
    log('INFO', 'Starting realtime polling loop (every 10s).');
    
    // Cleanup notified IDs every 10 minutes
    setInterval(cleanupNotifiedIds, 600000);
    
    setInterval(async () => {
        try {
            const { data: requests } = await supabase
                .from('requests')
                .select('id, discord_user_id, status, output_url, error_message, credits_deducted, pixel_count, channel_id, notified')
                .in('status', ['completed', 'failed'])
                .eq('notified', false);
            
            const pending = (requests ?? []).filter(req => !notifiedIds.has(req.id));
            if (pending.length > 0) {
                await Promise.allSettled(pending.map(req => notifyUser(req)));
            }
        } catch (error) {
            log('ERROR', 'Realtime poll error', error);
        }
    }, 10000);
}

// Send notification to user
async function notifyUser(req) {
    // Add to local cache immediately to prevent any re-processing
    notifiedIds.add(req.id);
    notifiedTimestamps.set(req.id, Date.now());
    
    let user;
    try {
        user = await client.users.fetch(req.discord_user_id);
    } catch (error) {
        log('warn', `Could not fetch user ${req.discord_user_id}`);
        // Still mark as notified in DB to prevent infinite loop
        try { 
            await supabase.from('requests').update({ notified: true }).eq('id', req.id); 
        } catch (e) {}
        return;
    }
    
    const credits = req.credits_deducted || (req.pixel_count ? calculateCredits(req.pixel_count) : null);
    const mention = `<@${req.discord_user_id}>`;
    
    let embed;
    if (req.status === 'completed') {
        embed = new EmbedBuilder()
            .setTitle('✅ Processing Complete!')
            .setDescription(`${mention} Your images have been upscaled successfully.`)
            .setColor(COLORS.success)
            .addFields(
                { name: '🆔 Request ID', value: `\`${req.id}\``, inline: true },
                { name: '📦 Download Link', value: `[Click here to download](${req.output_url})`, inline: false }
            );
        
        if (req.pixel_count) {
            embed.addFields({ name: '📐 Total Height', value: req.pixel_count.toLocaleString(), inline: true });
        }
        if (credits !== null && credits > 0) {
            embed.addFields({ name: '💳 Credits Deducted', value: `**${credits}** credit(s)`, inline: true });
        }
        
        embed.addFields({
            name: '⚠️ Important',
            value: 'The output file will be **deleted automatically after 20 minutes**. Please download it now.',
            inline: false,
        });
    } else {
        embed = new EmbedBuilder()
            .setTitle('❌ Processing Failed')
            .setDescription(`${mention} Unfortunately, your request could not be processed.`)
            .setColor(COLORS.error)
            .addFields(
                { name: '🆔 Request ID', value: `\`${req.id}\``, inline: false },
                { name: 'Error Details', value: `\`\`\`\n${req.error_message || 'Unknown error.'}\n\`\`\``, inline: false },
                { name: '💡 Tip', value: 'Make sure your Google Drive folder is shared correctly. If the problem persists, contact support.', inline: false }
            );
    }
    
    embed.setFooter({ text: 'UPSCALE-BOT' });
    
    // Try channel delivery first if channel_id exists, fall back to DM
    let delivered = false;
    
    if (req.channel_id) {
        try {
            const channel = await client.channels.fetch(req.channel_id);
            if (channel) {
                const botMember = channel.guild?.members.me;
                if (botMember) {
                    const permissions = channel.permissionsFor(botMember);
                    if (permissions && permissions.has(PermissionsBitField.Flags.SendMessages)) {
                        await channel.send({
                            content: mention,
                            embeds: [embed],
                            allowedMentions: { users: [req.discord_user_id] },
                        });
                        log('INFO', `Notified user ${req.discord_user_id} in channel ${req.channel_id} about request ${req.id} (${req.status})`);
                        delivered = true;
                    }
                }
            }
        } catch (error) {
            log('warn', `Cannot send to channel ${req.channel_id}, falling back to DM.`);
        }
    }
    
    if (!delivered) {
        try {
            await user.send({ embeds: [embed] });
            log('INFO', `Notified user ${req.discord_user_id} via DM about request ${req.id} (${req.status})`);
            delivered = true;
        } catch (error) {
            log('warn', `Cannot DM user ${req.discord_user_id} — DMs closed.`);
        }
    }
    
    // Mark as notified in DB to prevent re-polling
    try {
        await supabase
            .from('requests')
            .update({ notified: true })
            .eq('id', req.id);
    } catch (error) {
        // ignore
    }
}

// Bot ready
client.once('ready', async () => {
    log('INFO', `Bot logged in as ${client.user.tag}`);
    await registerCommands();
    client.user.setActivity('your manga panels 🎨', { type: 'WATCHING' });
    startRealtimePoller();
    log('INFO', 'Bot is ready!');
});

// Handle interactions
client.on('interactionCreate', async interaction => {
    if (!interaction.isChatInputCommand()) return;
    
    const { commandName } = interaction;
    
    try {
        // Ping command
        if (commandName === 'ping') {
            const latency = Date.now() - interaction.createdTimestamp;
            const embed = new EmbedBuilder()
                .setTitle('🏓 Pong!')
                .setColor(COLORS.primary)
                .addFields({ name: 'Latency', value: `\`${latency}ms\``, inline: true })
                .setFooter({ text: 'UPSCALE-BOT' });
            
            await interaction.reply({ embeds: [embed] });
        }
        
        // Profile command
        else if (commandName === 'profile') {
            const target = interaction.options.getUser('user') || interaction.user;
            
            // Check admin permission if targeting another user
            if (target.id !== interaction.user.id && !isAdmin(interaction.user.id)) {
                await interaction.reply({ 
                    content: '❌ You can only check your own profile.', 
                    flags: MessageFlags.Ephemeral 
                });
                return;
            }
            
            await interaction.deferReply({ flags: MessageFlags.Ephemeral });
            
            const userData = await apiRequest('GET', `/user/${target.id}`);
            
            // Get request stats
            const { data: requestsData } = await supabase
                .from('requests')
                .select('id, status')
                .eq('discord_user_id', target.id);
            
            const total = requestsData?.length ?? 0;
            const completed = requestsData?.filter(r => r.status === 'completed').length ?? 0;
            const memberSince = userData.created_at?.slice(0, 10) ?? 'N/A';
            
            const embed = new EmbedBuilder()
                .setTitle(`👤 ${target.displayName}'s Profile`)
                .setThumbnail(target.displayAvatarURL())
                .setColor(COLORS.primary)
                .addFields(
                    { name: '💳 Credits', value: `**${userData.credits}**`, inline: true },
                    { name: '📊 Total Credits Used', value: `**${userData.total_credits_used}**`, inline: true },
                    { name: '\u200b', value: '\u200b', inline: true },
                    { name: '📁 Total Requests', value: `**${total}**`, inline: true },
                    { name: '✅ Completed', value: `**${completed}**`, inline: true },
                    { name: '\u200b', value: '\u200b', inline: true }
                )
                .setFooter({ text: `Member since ${memberSince} • UPSCALE-BOT` });
            
            await interaction.editReply({ embeds: [embed] });
        }
        
        // Upscale command
        else if (commandName === 'upscale') {
            const folderUrl = interaction.options.getString('folder_url');
            
            if (!folderUrl.includes('drive.google.com')) {
                await interaction.reply({ 
                    content: '❌ Please provide a valid Google Drive folder link.', 
                    flags: MessageFlags.Ephemeral 
                });
                return;
            }
            
            await interaction.deferReply();
            
            // Get user data to check credits
            const userData = await apiRequest('GET', `/user/${interaction.user.id}`);
            if (userData.credits <= 0) {
                const embed = new EmbedBuilder()
                    .setTitle('❌ Insufficient Credits')
                    .setDescription("You don't have enough credits to submit a request.\nContact an admin to add more credits.")
                    .setColor(COLORS.error);
                return interaction.editReply({ embeds: [embed] });
            }
            
            const data = await apiRequest('POST', '/upscale', {
                user_id: interaction.user.id,
                google_drive_folder_url: folderUrl,
                channel_id: interaction.channelId
            });
            
            // Get estimated processing time
            const estimatedMinutes = await getEstimatedProcessingTime(data.queue_position || 0);
            
            const embedFields = [
                { name: '🆔 Request ID', value: `\`#${data.request_id}\``, inline: true },
                { name: '📁 Folder', value: `[View Folder](${folderUrl})`, inline: true }
            ];
            
            if (estimatedMinutes !== null) {
                embedFields.push({
                    name: '⏱️ Estimated Time',
                    value: `~**${estimatedMinutes}** minute(s)`,
                    inline: false
                });
            }
            
            embedFields.push({
                name: '📬 Delivery',
                value: 'Results will be sent **in this channel** when processing completes.',
                inline: false
            });
            
            const embed = new EmbedBuilder()
                .setTitle('✅ Request Queued!')
                .setDescription('Your images have been queued for upscaling.')
                .setColor(COLORS.success)
                .addFields(embedFields)
                .setFooter({ text: 'UPSCALE-BOT' });
            
            await interaction.editReply({ embeds: [embed] });
            log('INFO', `Upscale request created by ${interaction.user.tag} (${interaction.user.id})`);
        }
        
        // History command
        else if (commandName === 'history') {
            const target = interaction.options.getUser('user') || interaction.user;
            
            // Check admin permission if targeting another user
            if (target.id !== interaction.user.id && !isAdmin(interaction.user.id)) {
                await interaction.reply({ 
                    content: '❌ You can only check your own history.', 
                    flags: MessageFlags.Ephemeral 
                });
                return;
            }
            
            await interaction.deferReply({ flags: MessageFlags.Ephemeral });
            
            const data = await apiRequest('GET', `/user/${target.id}/transactions`);
            
            if (!data || data.length === 0) {
                const embed = new EmbedBuilder()
                    .setTitle('📜 Transaction History')
                    .setDescription('No transactions yet.')
                    .setColor(COLORS.primary)
                    .setFooter({ text: 'UPSCALE-BOT' });
                await interaction.editReply({ embeds: [embed] });
                return;
            }
            
            // Split into categories
            const requests = data.filter(tx => 
                tx.description && tx.description.toLowerCase().includes('upscale request')
            );
            const credits = data.filter(tx => 
                !tx.description || !tx.description.toLowerCase().includes('upscale request')
            );
            
            // Pagination state
            const ITEMS_PER_PAGE = 7;
            let currentSection = 'requests';
            let currentPage = 0;
            
            function buildEmbed(section, page) {
                const items = section === 'requests' ? requests : credits;
                const totalPages = Math.max(1, Math.ceil(items.length / ITEMS_PER_PAGE));
                const start = page * ITEMS_PER_PAGE;
                const pageItems = items.slice(start, start + ITEMS_PER_PAGE);
                
                const sectionTitle = section === 'requests' ? '📁 Upscale Requests' : '💳 Credits (Bonuses & Deductions)';
                const embed = new EmbedBuilder()
                    .setTitle(`📜 Transaction History — ${target.displayName}`)
                    .setColor(COLORS.primary);
                
                if (pageItems.length === 0) {
                    embed.setDescription('No transactions in this section.');
                } else {
                    pageItems.forEach(tx => {
                        const emoji = tx.transaction_type === 'deduct' ? '➖' : 
                                      tx.transaction_type === 'refund' ? '🔄' : '➕';
                        const sign = tx.transaction_type !== 'deduct' ? '+' : '-';
                        embed.addFields({
                            name: `${emoji} ${tx.description || tx.transaction_type}`,
                            value: `\`${tx.balance_before}\` → \`${tx.balance_after}\` | **${sign}${tx.amount}**`,
                            inline: false
                        });
                    });
                }
                
                embed.setFooter({ 
                    text: `${section === 'requests' ? '📁 Requests' : '💳 Credits'} • Page ${page + 1}/${totalPages} • ${items.length} total • UPSCALE-BOT` 
                });
                
                return { embed, totalPages };
            }
            
            function buildButtons(section, page, totalPages) {
                const items = section === 'requests' ? requests : credits;
                const totalItems = items.length;
                
                const row1 = new ActionRowBuilder().addComponents(
                    new ButtonBuilder()
                        .setCustomId('history_requests')
                        .setLabel(`📁 Requests (${requests.length})`)
                        .setStyle(section === 'requests' ? ButtonStyle.Primary : ButtonStyle.Secondary),
                    new ButtonBuilder()
                        .setCustomId('history_credits')
                        .setLabel(`💳 Credits (${credits.length})`)
                        .setStyle(section === 'credits' ? ButtonStyle.Primary : ButtonStyle.Secondary)
                );
                
                const row2 = new ActionRowBuilder().addComponents(
                    new ButtonBuilder()
                        .setCustomId('history_prev')
                        .setLabel('◀️')
                        .setStyle(ButtonStyle.Secondary)
                        .setDisabled(page === 0),
                    new ButtonBuilder()
                        .setCustomId('history_next')
                        .setLabel('▶️')
                        .setStyle(ButtonStyle.Secondary)
                        .setDisabled(page >= totalPages - 1 || totalItems === 0)
                );
                
                return [row1, row2];
            }
            
            const { embed, totalPages } = buildEmbed(currentSection, currentPage);
            const buttons = buildButtons(currentSection, currentPage, totalPages);
            
            const reply = await interaction.editReply({ embeds: [embed], components: buttons });
            
            const collector = reply.createMessageComponentCollector({ 
                time: 300000 // 5 minutes
            });
            
            collector.on('collect', async (btnInteraction) => {
                if (btnInteraction.user.id !== interaction.user.id) {
                    await btnInteraction.reply({ content: '❌ This is not your history.', flags: MessageFlags.Ephemeral });
                    return;
                }
                
                if (btnInteraction.customId === 'history_requests') {
                    currentSection = 'requests';
                    currentPage = 0;
                } else if (btnInteraction.customId === 'history_credits') {
                    currentSection = 'credits';
                    currentPage = 0;
                } else if (btnInteraction.customId === 'history_prev') {
                    currentPage = Math.max(0, currentPage - 1);
                } else if (btnInteraction.customId === 'history_next') {
                    currentPage++;
                }
                
                const { embed: newEmbed, totalPages: newTotal } = buildEmbed(currentSection, currentPage);
                const newButtons = buildButtons(currentSection, currentPage, newTotal);
                
                await btnInteraction.update({ embeds: [newEmbed], components: newButtons });
            });
            
            collector.on('end', async () => {
                try {
                    const disabledButtons = buildButtons(currentSection, currentPage, totalPages);
                    disabledButtons.forEach(row => {
                        row.components.forEach(btn => btn.setDisabled(true));
                    });
                    await interaction.editReply({ components: disabledButtons });
                } catch (e) {}
            });
        }
        
        // Admin command
        else if (commandName === 'admin') {
            // Check admin permission
            if (!isAdmin(interaction.user.id)) {
                await interaction.reply({ 
                    content: '❌ You need admin permission to use this command.', 
                    flags: MessageFlags.Ephemeral 
                });
                return;
            }
            
            const action = interaction.options.getString('action');
            const target = interaction.options.getUser('user');
            const amount = interaction.options.getInteger('amount');
            const reason = interaction.options.getString('reason') || (action === 'add' ? 'Admin bonus' : 'Admin deduction');
            
            await interaction.deferReply({ flags: MessageFlags.Ephemeral });
            
            const endpoint = action === 'add' ? '/admin/add-credits' : '/admin/deduct-credits';
            const data = await apiRequest('POST', endpoint, {
                user_id: target.id,
                amount: amount,
                reason: reason
            });
            
            const isAdd = action === 'add';
            const embed = new EmbedBuilder()
                .setTitle(isAdd ? '✅ Credits Added' : '✅ Credits Deducted')
                .setColor(isAdd ? COLORS.success : COLORS.warning)
                .addFields(
                    { name: 'User', value: `${target}`, inline: true },
                    { name: isAdd ? 'Added' : 'Deducted', value: `**${amount}** credits`, inline: true },
                    { name: 'New Balance', value: `**${data.new_balance}** credits`, inline: true },
                    { name: 'Reason', value: reason, inline: false }
                )
                .setFooter({ text: `Action by ${interaction.user.tag} • UPSCALE-BOT` });
            
            await interaction.editReply({ embeds: [embed] });
            log('INFO', `${isAdd ? 'Added' : 'Deducted'} ${amount} credits ${isAdd ? 'to' : 'from'} ${target.tag} by ${interaction.user.tag}`);
            
            // Notify user
            try {
                const notify = new EmbedBuilder()
                    .setTitle(isAdd ? '💳 Credits Added' : '💳 Credits Deducted')
                    .setDescription(`**${amount}** credits ${isAdd ? 'added' : 'deducted'} by admin.`)
                    .setColor(isAdd ? COLORS.success : COLORS.warning)
                    .addFields(
                        { name: 'New Balance', value: `**${data.new_balance}** credits`, inline: true },
                        { name: 'Reason', value: reason, inline: true }
                    );
                
                await target.send({ embeds: [notify] });
            } catch (e) {
                // User may have DMs disabled
            }
        }
        
    } catch (error) {
        log('ERROR', `Error handling command ${commandName}`, error);
        
        const embed = new EmbedBuilder()
            .setTitle('❌ Error')
            .setDescription(error.message || 'An error occurred')
            .setColor(COLORS.error);
        
        try {
            if (interaction.deferred) {
                await interaction.editReply({ embeds: [embed] });
            } else {
            await interaction.reply({ embeds: [embed] });
            }
        } catch (e) {
            log('ERROR', 'Failed to send error message to user', e);
        }
    }
});

// Login
if (!DISCORD_TOKEN || !DISCORD_CLIENT_ID) {
    log('ERROR', 'Missing DISCORD_TOKEN or DISCORD_CLIENT_ID');
    process.exit(1);
}

log('INFO', 'Starting Discord bot...');
client.login(DISCORD_TOKEN);
