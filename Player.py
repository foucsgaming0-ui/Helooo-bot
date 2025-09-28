# ==============================================================================
# ==== SECTION 1: IMPORTS & CONFIGURATION ====
# ==============================================================================

import json
import logging
import re
import os
from pathlib import Path
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)

# --- CONFIGURATION ---
BOT_TOKEN = "8496998289:AAFnSSu9BLa3KQugvSHyFMVOHtscwd64tI4"
CHANNEL_ID = -1002936212397  # Aapke songs ka channel ID
ADMIN_IDS = [5168899073]      # Aapki Telegram User ID

# --- FILE PATHS ---
DB_FILE = "songs.json"
USERS_FILE = "users.json"
MISSING_FILE = "missing_songs.json"
CONFIG_FILE = "config.json"

# --- BOT SETTINGS ---
FREE_POINTS_ON_START = 10
SONG_PRICE_POINTS = 1
DAILY_POINTS_GIFT = 1   # Roz milne wale free points
PAYMENT_OPTIONS = {
    2: 10,
    5: 20,
    10: 35,
    20: 60
}

# --- CONVERSATION HANDLER STATES ---
WAITING_QR_PHOTO = 1
WAITING_BROADCAST_CONTENT = 2

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==============================================================================
# ==== SECTION 2: HELPER & DATABASE FUNCTIONS ====
# ==============================================================================

def load_db(filename):
    """JSON database file ko load karta hai."""
    try:
        with open(filename, "r", encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.error(f"JSON file {filename} mein error hai.")
        return {}

def save_db(data, filename):
    """Data ko JSON database file mein save karta hai."""
    with open(filename, "w", encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- LOAD DATABASES & CONFIG ---
USERS_DATA = load_db(USERS_FILE)
MISSING_DB = load_db(MISSING_FILE)
BOT_CONFIG = load_db(CONFIG_FILE)
SONG_DB = load_db(DB_FILE)
if not isinstance(SONG_DB, list):
    SONG_DB = []

if 'users' not in USERS_DATA: USERS_DATA['users'] = {}
if 'username_map' not in USERS_DATA: USERS_DATA['username_map'] = {}
USERS_DB = USERS_DATA['users']
USERNAME_MAP = USERS_DATA['username_map']

def escape_markdown(text):
    """Telegram Markdown ke special characters se bachata hai."""
    if not isinstance(text, str): text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(['\\' + char if char in escape_chars else char for char in text])


# ==============================================================================
# ==== SECTION 3: ADVANCED SONG PARSING & SEARCH LOGIC ====
# ==============================================================================

def determine_artist_and_song(part1, part2):
    """Kaun sa part artist hai aur kaun sa song, yeh pata lagata hai."""
    part1_lower, part2_lower = part1.lower(), part2.lower()
    feat_keywords = ['ft.', 'ft', 'feat.', 'feat', 'featuring', 'with', 'vs', 'x']
    if any(k in part1_lower for k in feat_keywords) and not any(k in part2_lower for k in feat_keywords): return part1, part2
    if any(k in part2_lower for k in feat_keywords) and not any(k in part1_lower for k in feat_keywords): return part2, part1
    if len(part1) > len(part2) * 1.5: return part1, part2
    if len(part2) > len(part1) * 1.5: return part2, part1
    return part2, part1 # Default: Artist - Song format

def parse_song_info(filename, file_size_bytes=0):
    """Filename se song ki jaankari (artist, title, size) nikalta hai."""
    file_path = Path(filename)
    file_format = file_path.suffix.lower().replace('.', '') if file_path.suffix else 'unknown'
    size_mb = round(file_size_bytes / (1024 * 1024), 2) if file_size_bytes > 0 else 0

    clean_name = re.sub(r'\.(mp3|m4a|wav|flac|aac|ogg)$', '', filename, flags=re.IGNORECASE)
    clean_name = os.path.basename(clean_name)
    clean_name = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', clean_name).strip()
    clean_name = re.sub(r'^\d+\s*[-._]\s*', '', clean_name).strip()
    clean_name = re.sub(r'^\d+\s+', '', clean_name).strip()
    clean_name = re.sub(r'\s{2,}', ' ', clean_name).strip()

    separators = [' - ', ' – ', ' — ', ' | ', ' by ']
    for sep in separators:
        if sep in clean_name:
            parts = clean_name.split(sep, 1)
            if len(parts) == 2:
                part1, part2 = parts[0].strip(), parts[1].strip()
                if not part1 or not part2: continue
                song_title, artist = determine_artist_and_song(part1, part2)
                return {"song_title": song_title, "artist": artist, "format": file_format, "size_mb": size_mb, "original_filename": os.path.basename(filename)}
    
    return {"song_title": clean_name if clean_name else "Unknown Song", "artist": "Unknown Artist", "format": file_format, "size_mb": size_mb, "original_filename": os.path.basename(filename)}

def fuzzy_search(query, song_data):
    """Song ke title aur artist mein search karta hai."""
    query = query.lower().strip()
    song_name = song_data.get('song_title', '').lower()
    artist_name = song_data.get('artist', '').lower()

    if query in song_name or query in artist_name: return True
    
    query_words = set(re.sub(r'[^\w\s]', '', query).split())
    all_song_words = set(re.sub(r'[^\w\s]', '', song_name).split()).union(set(re.sub(r'[^\w\s]', '', artist_name).split()))
    
    if not query_words: return False
    return len(query_words.intersection(all_song_words)) / len(query_words) >= 0.6


# ==============================================================================
# ==== SECTION 4: CORE BOT HANDLERS & USER DATA ====
# ==============================================================================

def get_user_data(user_id, username=None):
    """User ka data laata hai ya naya banata hai."""
    user_id = str(user_id)
    if user_id not in USERS_DB:
        USERS_DB[user_id] = {
            'points': FREE_POINTS_ON_START, 'join_date': datetime.now().isoformat(),
            'username': username, 'total_downloaded': 0, 'total_purchased': 0, 'total_spent': 0.0,
        }
        if username: USERNAME_MAP[username.lower()] = user_id
        save_db(USERS_DATA, USERS_FILE)
    return USERS_DB[user_id]

def update_user_data(user_id, **kwargs):
    """User ke data ko update karta hai."""
    user_id = str(user_id)
    user_data = get_user_data(user_id)
    user_data.update(kwargs)
    if 'username' in kwargs and kwargs['username']: USERNAME_MAP[kwargs['username'].lower()] = user_id
    save_db(USERS_DATA, USERS_FILE)

async def save_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Channel mein upload kiye gaye gaano ko database mein save karta hai."""
    global SONG_DB
    if not (update.channel_post and update.channel_post.chat.id == CHANNEL_ID): return

    message = update.channel_post
    audio = message.audio or message.document
    if not audio or not audio.file_name: return

    filename, file_size, msg_id = audio.file_name, audio.file_size or 0, message.message_id
    song_info = parse_song_info(filename, file_size)
    song_info['message_id'] = msg_id

    existing_index = next((i for i, song in enumerate(SONG_DB) if song.get('message_id') == msg_id), -1)
    
    if existing_index != -1: 
        SONG_DB[existing_index] = song_info
        logger.info(f"🔄 Updated: {song_info['artist']} - {song_info['song_title']}")
    else: 
        SONG_DB.append(song_info)
        logger.info(f"✅ Saved: {song_info['artist']} - {song_info['song_title']}")
    
    save_db(SONG_DB, DB_FILE)

def add_missing_song(user_id, song_name):
    """User ki song request ko missing list mein add karta hai."""
    if 'requests' not in MISSING_DB: MISSING_DB['requests'] = []
    MISSING_DB['requests'].append({ 'user_id': user_id, 'song_name': song_name, 'request_date': datetime.now().isoformat() })
    save_db(MISSING_DB, MISSING_FILE)


# ==============================================================================
# ==== SECTION 5: USER COMMAND HANDLERS ====
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start command ko handle karta hai."""
    user = update.effective_user
    user_data = get_user_data(user.id, username=user.username)
    update_user_data(user.id, username=user.username, first_name=user.first_name)
    
    command_guide = (
        "**Here are the commands you can use:**\n"
        "• `/search <song name>` - To find any song.\n"
        "• `/balance` - To see how many points you have.\n"
        "• `/buypoint` - To buy more points.\n"
        "• `/submit <UTR> <amount>` - To confirm your payment.\n"
        "• `/daily` - To get your daily free points.\n"
        "• `/request <song name>` - To request a song.\n"
        "• `/help` - To see this guide again."
    )
    if 'join_date' in user_data and user_data.get('total_downloaded', 0) == 0:
        welcome_message = (f"🎵 **Welcome, {user.first_name}!**\n\n🎁 You've received **{FREE_POINTS_ON_START} free points**.\n💡 *1 point = 1 song download.*\n\n{command_guide}")
    else:
        welcome_message = (f"🎵 **Welcome back, {user.first_name}!**\n\n💎 You have **{user_data['points']} points**.\n\n{command_guide}")
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help command ko handle karta hai."""
    await start_command(update, context) 

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/balance command ko handle karta hai."""
    user_data = get_user_data(update.effective_user.id)
    balance_message = (
        f"💎 **Your Account Balance**\n\n"
        f"🎵 **Available Points:** {user_data.get('points', 0)}\n"
        f"📥 **Songs Downloaded:** {user_data.get('total_downloaded', 0)}\n\n"
        f"Need more? Use `/buypoint` to recharge."
    )
    await update.message.reply_text(balance_message, parse_mode='Markdown')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/search command ko handle karta hai."""
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if user_data['points'] < SONG_PRICE_POINTS:
        await update.message.reply_text(f"⚠️ **Out of Points!**\n\nYou need at least {SONG_PRICE_POINTS} point. Use `/buypoint` to get more.", parse_mode='Markdown')
        return

    if not context.args:
        await update.message.reply_text("Please provide a song name. Example: `/search Tum Hi Ho`")
        return

    query = " ".join(context.args)
    search_msg = await update.message.reply_text(f"🔍 Searching for `{escape_markdown(query)}`...", parse_mode='Markdown')

    found_songs = [song for song in SONG_DB if fuzzy_search(query, song)]

    if found_songs:
        best_match = found_songs[0]
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Download", callback_data=f"download_{best_match['message_id']}")],
            [InlineKeyboardButton("❌ No, Wrong Song", callback_data=f"wrong_song_{query}")]
        ])
        found_message = (
            f"🎵 **Song Found!**\n\n"
            f"🎶 **Song:** {escape_markdown(best_match['song_title'])}\n"
            f"🎤 **Artist:** {escape_markdown(best_match['artist'])}\n"
            f"📁 **Size:** {best_match['size_mb']} MB\n"
            f"📄 **Format:** {best_match['format'].upper()}\n\n"
            f"💎 **Your Points:** {user_data['points']}\n"
            f"💰 **Cost:** 1 point\n\n"
            f"Is this the correct song?"
        )
        await search_msg.edit_text(found_message, reply_markup=keyboard, parse_mode='Markdown')
    else:
        add_missing_song(user_id, query)
        await search_msg.edit_text(f"😔 **Song Not Found**\n\nWe couldn't find `{escape_markdown(query)}`.", parse_mode='Markdown')

async def buypoint_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/buypoint command ko handle karta hai."""
    buttons = [[InlineKeyboardButton(f"🎵 {p} points for ₹{a}", callback_data=f"show_pay_{p}_{a}")] for p, a in PAYMENT_OPTIONS.items()]
    buy_message = "💳 **Buy Music Points**\n\nChoose a package below to see payment details."
    await update.message.reply_text(buy_message, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

async def submit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/submit command ko handle karta hai."""
    user = update.effective_user
    if len(context.args) != 2:
        await update.message.reply_text("🚫 **Invalid format!**\nUse: `/submit <UTR_ID> <amount>`\nExample: `/submit 123456789012 35`", parse_mode='Markdown')
        return

    utr_id, amount_str = context.args
    try: amount = int(amount_str)
    except ValueError:
        await update.message.reply_text("❌ The amount must be a number."); return

    points_to_give = next((p for p, a in PAYMENT_OPTIONS.items() if a == amount), None)
    if points_to_give is None:
        await update.message.reply_text(f"❌ Invalid amount. No plan found for ₹{amount}."); return

    approve_command = f"/admingive {user.id} {points_to_give}"
    reject_command = f"/admingive {user.id} reject"
    mail_command = f"/mail {user.id} "

    admin_notification = (
        f"**💰 New Payment Submission**\n\n"
        f"👤 **User:** {user.mention_markdown_v2()} (`{user.id}`)\n"
        f"🔗 **UTR/Ref ID:** `{escape_markdown(utr_id)}`\n"
        f"💵 **Amount:** ₹{amount}\n"
        f"💎 **Points:** {points_to_give}\n\n"
        f"**✅ To approve:** `{approve_command}`\n"
        f"**❌ To reject:** `{reject_command}`\n"
        f"**📨 To mail:** `{mail_command}`"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=admin_notification, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send payment notification to admin {admin_id}: {e}")

    await update.message.reply_text("✅ **Submission Received!**\n\nYour payment is under review.", parse_mode='Markdown')

async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ko daily free points deta hai."""
    user_id = str(update.effective_user.id)
    user_data = get_user_data(user_id)
    now = datetime.now()
    
    last_claim_str = user_data.get('last_daily_claim')
    if last_claim_str:
        last_claim_date = datetime.fromisoformat(last_claim_str)
        if now - last_claim_date < timedelta(hours=24):
            time_left = timedelta(hours=24) - (now - last_claim_date)
            hours, rem = divmod(int(time_left.total_seconds()), 3600)
            minutes, _ = divmod(rem, 60)
            await update.message.reply_text(f"🚫 **Already Claimed!** 🚫\n\nAap aaj ke free points le chuke hain.\n\n⏳ Please try again in **{hours} hours and {minutes} minutes**.", parse_mode='Markdown')
            return
            
    user_data['points'] = user_data.get('points', 0) + DAILY_POINTS_GIFT
    user_data['last_daily_claim'] = now.isoformat()
    update_user_data(user_id, **user_data)
    
    await update.message.reply_text(f"🎁 **Daily Gift Claimed!** 🎁\n\n🎉 Congratulations! Aapko **{DAILY_POINTS_GIFT}** free points mile hain.\n\n💎 **New Balance:** `{user_data['points']}` points.", parse_mode='Markdown')

async def request_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ko song request karne ki anumati deta hai."""
    if not context.args:
        await update.message.reply_text("Please provide a song name.\n**Usage:** `/request <song name>`")
        return
    song_name = " ".join(context.args)
    add_missing_song(update.effective_user.id, song_name)
    await update.message.reply_text(f"✅ **Request Logged!**\n\nThank you for requesting '{escape_markdown(song_name)}'.", parse_mode='Markdown')

# ==============================================================================
# ==== SECTION 6: ADMIN & CONVERSATION HANDLERS ====
# ==============================================================================

def is_admin(user_id):
    """Check karta hai ki user admin hai ya nahi."""
    return user_id in ADMIN_IDS

async def setqr_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/setqr command ka conversation shuru karta hai."""
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("Please send the new QR code photo.\nSend /cancel to abort.")
    return WAITING_QR_PHOTO

async def receive_qr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Naya QR code receive karke save karta hai."""
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    photo_file = update.message.photo[-1]
    BOT_CONFIG['qr_photo_file_id'] = photo_file.file_id
    save_db(BOT_CONFIG, CONFIG_FILE)
    await update.message.reply_photo(photo=photo_file.file_id, caption="✅ **Success!** New QR code saved.")
    return ConversationHandler.END

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/broadcast command ka conversation shuru karta hai."""
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("**Broadcast Mode**\n\nPlease send the message (text or photo with caption) to broadcast.\nSend /cancel to abort.")
    return WAITING_BROADCAST_CONTENT

async def receive_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Broadcast message ko sabhi users ko bhejta hai."""
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("⏳ **Broadcast initiated...** Please wait.")
    
    photo, caption, text = None, None, None
    if update.message.photo:
        photo = update.message.photo[-1].file_id
        caption = update.message.caption or ""
    elif update.message.text:
        text = update.message.text
    else:
        await update.message.reply_text("Unsupported type."); return ConversationHandler.END

    success, failed = 0, 0
    for user_id in list(USERS_DB.keys()):
        try:
            if photo: await context.bot.send_photo(chat_id=user_id, photo=photo, caption=caption, parse_mode='Markdown')
            else: await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
            success += 1
        except Exception as e:
            failed += 1; logger.error(f"Broadcast failed for user {user_id}: {e}")
    
    await update.message.reply_text(f"✅ **Broadcast Complete!**\n\nSent: {success} | Failed: {failed}")
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Chalu conversation ko cancel karta hai."""
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

async def admingive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ko points deta hai ya payment reject karta hai."""
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: `/admingive <user_id> <points_or_reject>`", parse_mode='Markdown'); return
    try:
        target_user_id = int(context.args[0])
        action = context.args[1]
        try:
            points_to_add = int(action)
            user_data = get_user_data(target_user_id)
            user_data['points'] += points_to_add
            user_data['total_purchased'] = user_data.get('total_purchased', 0) + points_to_add
            amount_paid = next((a for p, a in PAYMENT_OPTIONS.items() if p == points_to_add), 0)
            user_data['total_spent'] = user_data.get('total_spent', 0.0) + amount_paid
            update_user_data(target_user_id, **user_data)
            await update.message.reply_text(f"✅ Success! Gave {points_to_add} points to user `{target_user_id}`.")
            await context.bot.send_message(chat_id=target_user_id, text=f"🎉 **Payment Approved!**\n\n💎 You have received **{points_to_add} points**.", parse_mode='Markdown')
        except ValueError:
            if action.lower() == 'reject':
                await context.bot.send_message(chat_id=target_user_id, text="❌ **Payment Not Approved**\n\nYour recent payment could not be verified.", parse_mode='Markdown')
                await update.message.reply_text(f"✅ Rejection message sent to user `{target_user_id}`.")
            else:
                await update.message.reply_text("❌ Invalid action. Use points (e.g., 20) or 'reject'.")
    except ValueError: await update.message.reply_text("Invalid User ID.")
    except Exception as e: await update.message.reply_text(f"An error occurred: {e}")

async def mail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin ko user ko custom message bhejne ki anumati deta hai."""
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("<b>Usage:</b> /mail [user_id] [message]", parse_mode='HTML'); return
    try:
        user_id = int(context.args[0])
        message_text = " ".join(context.args[1:])
        final_message = f"📨 **A message from the Admin:**\n\n{message_text}"
        await context.bot.send_message(chat_id=user_id, text=final_message, parse_mode='Markdown')
        await update.message.reply_text(f"✅ Message sent to user `{user_id}`.")
    except ValueError: await update.message.reply_text("❌ Invalid User ID.")
    except Exception as e: await update.message.reply_text(f"❌ Could not send message. Error: {e}")

async def setupi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Payment ke liye UPI ID set karta hai."""
    if not is_admin(update.effective_user.id): return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: `/setupi <your_upi_id>`", parse_mode='Markdown'); return
    BOT_CONFIG['upi_id'] = context.args[0]
    save_db(BOT_CONFIG, CONFIG_FILE)
    await update.message.reply_text(f"✅ UPI ID updated to: `{BOT_CONFIG['upi_id']}`", parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot ke statistics dikhata hai."""
    if not is_admin(update.effective_user.id): return
    total_spent = sum(u.get('total_spent', 0.0) for u in USERS_DB.values())
    stats_text = (f"📈 **Bot Stats**\n👥 Users: {len(USERS_DB)}\n🎵 Songs: {len(SONG_DB)}\n💰 Revenue: ₹{total_spent}")
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def missing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sabhi pending song requests dikhata hai."""
    if not is_admin(update.effective_user.id): return
    requests = MISSING_DB.get('requests', [])
    if not requests:
        await update.message.reply_text("✅ No pending song requests."); return
    song_counts = {}
    for req in requests: song_counts[req['song_name']] = song_counts.get(req['song_name'], 0) + 1
    sorted_reqs = sorted(song_counts.items(), key=lambda item: item[1], reverse=True)
    message = "📝 **Pending Song Requests:**\n\n"
    for song, count in sorted_reqs: message += f"• `{escape_markdown(song)}` (x{count})\n"
    await update.message.reply_text(message, parse_mode='Markdown')

async def clearmissing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Missing songs ki list ko saaf karta hai."""
    if not is_admin(update.effective_user.id): return
    MISSING_DB['requests'] = []
    save_db(MISSING_DB, MISSING_FILE)
    await update.message.reply_text("✅ Success! Missing songs list cleared.")
    

async def notify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Notifies users that a requested song has been added and clears the request."""
    if not is_admin(update.effective_user.id): return

    if not context.args:
        await update.message.reply_text(
            "Please provide the song name you have added.\n"
            "**Usage:** `/notify <song_name>`"
        , parse_mode='Markdown')
        return

    song_name_to_notify = " ".join(context.args)
    song_name_lower = song_name_to_notify.lower()
    
    all_requests = MISSING_DB.get('requests', [])
    if not all_requests:
        await update.message.reply_text("The request list is already empty.")
        return

    users_to_notify = []
    remaining_requests = []

    # Separate the requests for the notified song from the rest
    for request in all_requests:
        if request.get('song_name', '').lower() == song_name_lower:
            users_to_notify.append(request['user_id'])
        else:
            remaining_requests.append(request)

    if not users_to_notify:
        await update.message.reply_text(f"No pending requests found for '{escape_markdown(song_name_to_notify)}'.", parse_mode='Markdown')
        return

    # Use set() to avoid sending multiple notifications to the same user
    unique_user_ids = set(users_to_notify)
    success_count = 0
    
    notification_message = (
        f"🎉 **Good News!**\n\n"
        f"The song you requested, **{escape_markdown(song_name_to_notify)}**, is now available.\n\n"
        f"Use `/search {escape_markdown(song_name_to_notify)}` to download it!"
    )

    # Send notification to each user
    for user_id in unique_user_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=notification_message, parse_mode='Markdown')
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} for song '{song_name_to_notify}': {e}")
    
    # Update the missing songs list by removing the fulfilled requests
    MISSING_DB['requests'] = remaining_requests
    save_db(MISSING_DB, MISSING_FILE)

    await update.message.reply_text(
        f"✅ **Notification Sent!**\n\n"
        f"Message sent to **{success_count} user(s)** for the song '{escape_markdown(song_name_to_notify)}'.\n"
        f"The request has been cleared from the list."
    , parse_mode='Markdown')


# ==============================================================================
# ==== SECTION 7: MAIN CALLBACK HANDLER ====
# ==============================================================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sabhi inline buttons ke press ko handle karta hai."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("download_"):
        user_data = get_user_data(user_id)
        if user_data['points'] < SONG_PRICE_POINTS:
            await query.edit_message_text("⚠️ You don't have enough points!", parse_mode='Markdown'); return
        msg_id = int(data.split('_')[1])
        try:
            await query.edit_message_text("⏳ Preparing your song...", parse_mode='Markdown')
            await context.bot.copy_message(chat_id=user_id, from_chat_id=CHANNEL_ID, message_id=msg_id)
            user_data['points'] -= SONG_PRICE_POINTS
            user_data['total_downloaded'] += 1
            update_user_data(user_id, **user_data)
            await query.edit_message_text(f"✅ **Download complete!**\nYour new balance: **{user_data['points']}** points.", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error sending song {msg_id} to user {user_id}: {e}")
            await query.edit_message_text("❌ An error occurred. Please try again.", parse_mode='Markdown')

    elif data.startswith("wrong_song_"):
        add_missing_song(user_id, data.replace("wrong_song_", "", 1))
        await query.edit_message_text("Thanks for the feedback!", parse_mode='Markdown')

    elif data.startswith("show_pay_"):
        parts = data.split('_')
        points, amount = int(parts[2]), int(parts[3])
        upi_id = BOT_CONFIG.get("upi_id")
        qr_photo_id = BOT_CONFIG.get("qr_photo_file_id")

        if not upi_id or not qr_photo_id:
            await query.edit_message_text("❌ **Payment System Offline!**\nAdmin needs to set UPI ID and QR code.", parse_mode='Markdown'); return

        caption = (f"✅ **Step 1: Pay**\nPay **₹{amount}** for **{points} points**.\n\n`{escape_markdown(upi_id)}`\n\n---\n✅ **Step 2: Submit**\nUse this command after paying:\n`/submit <UTR_ID> {amount}`")
        try:
            await query.edit_message_text("✅ Sending payment details...", parse_mode='Markdown')
            await context.bot.send_photo(chat_id=user_id, photo=qr_photo_id, caption=caption, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send payment photo to user {user_id}: {e}")
            await query.edit_message_text(f"❌ **Error!**\nCould not send QR Code. Pay directly to:\n`{escape_markdown(upi_id)}`", parse_mode='Markdown')


# ==============================================================================
# ==== SECTION 8: MAIN FUNCTION ====
# ==============================================================================
def main():
    """Starts the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandlers
    setqr_conv = ConversationHandler(
        entry_points=[CommandHandler("setqr", setqr_start)],
        states={ WAITING_QR_PHOTO: [MessageHandler(filters.PHOTO, receive_qr_photo)] },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            WAITING_BROADCAST_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_broadcast_content), MessageHandler(filters.PHOTO, receive_broadcast_content)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(setqr_conv)
    application.add_handler(broadcast_conv)
    
    # User Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("buypoint", buypoint_command))
    application.add_handler(CommandHandler("submit", submit_command))
    application.add_handler(CommandHandler("daily", daily_command))
    application.add_handler(CommandHandler("request", request_command))

    # Admin Commands
    application.add_handler(CommandHandler("admingive", admingive_command))
    application.add_handler(CommandHandler("setupi", setupi_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("mail", mail_command))
    application.add_handler(CommandHandler("missing", missing_command))
    application.add_handler(CommandHandler("clearmissing", clearmissing_command))
    application.add_handler(CommandHandler("notify", notify_command)) # <-- YEH NAYA COMMAND

    # Core Handlers
    application.add_handler(MessageHandler(filters.Chat(CHANNEL_ID) & (filters.AUDIO | filters.Document.ALL), save_song))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    logger.info("Bot started successfully!")
    application.run_polling()


if __name__ == "__main__":
    main()

