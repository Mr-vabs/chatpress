import requests
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from asgiref.sync import sync_to_async
from decouple import config

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.error import BadRequest

from bot.models import TelegramUser, BlogPost

# --- GLOBAL STATE (For Multi-step flows like Broadcast/Edit) ---
USER_STATE = {}

class Command(BaseCommand):
    help = 'Runs the Telegram Bot'

    def handle(self, *args, **kwargs):
        # =====================================================
        # 1. DUMMY SERVER (Keeps Render Awake)
        # =====================================================
        class SimpleHTTP(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'I am alive! Bot is running.')
            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

        def start_dummy_server():
            port = int(os.environ.get("PORT", 10000))
            server = HTTPServer(('0.0.0.0', port), SimpleHTTP)
            print(f"🌍 Dummy server running on port {port}")
            server.serve_forever()

        # 🔥 CRITICAL START COMMAND
        threading.Thread(target=start_dummy_server, daemon=True).start()

        # =====================================================
        # 2. WATCHDOG (Monitors Website)
        # =====================================================
        def start_watchdog():
            admin_id = str(config('ADMIN_ID'))
            website_url = "https://chatpress-web.onrender.com" 
            bot_token = config('TELEGRAM_TOKEN')
            
            print("🐶 Watchdog started...")
            
            while True:
                time.sleep(300) # Check every 5 mins
                try:
                    response = requests.get(website_url, timeout=30)
                    if response.status_code != 200:
                        raise Exception(f"Status: {response.status_code}")
                except Exception as e:
                    # Notify Admin (No HTML parse mode to avoid errors on raw exception text)
                    try:
                        requests.get(f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={admin_id}&text=🚨 WEBSITE DOWN! Error: {str(e)}")
                    except: pass

        threading.Thread(target=start_watchdog, daemon=True).start()

        # =====================================================
        # 3. BOT APPLICATION
        # =====================================================
        token = config('TELEGRAM_TOKEN')
        application = ApplicationBuilder().token(token).build()

        # --- Handlers ---
        # Public
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CommandHandler('help', self.start))
        application.add_handler(CommandHandler('rules', self.rules))
        application.add_handler(CommandHandler('anon', self.toggle_anon))
        
        # User
        application.add_handler(CommandHandler('drafts', self.my_drafts)) 
        application.add_handler(CommandHandler('myposts', self.my_published)) 

        # Admin
        application.add_handler(CommandHandler('pending', self.admin_pending))
        application.add_handler(CommandHandler('users', self.admin_users_list))
        application.add_handler(CommandHandler('broadcast', self.admin_broadcast))
        application.add_handler(CommandHandler('notify', self.admin_notify_user))
        
        # Core
        application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, self.handle_message))
        application.add_handler(CallbackQueryHandler(self.handle_button))

        self.stdout.write(self.style.SUCCESS('Bot started polling...'))
        application.run_polling()

    # ==========================
    # COMMAND FUNCTIONS
    # ==========================

    async def rules(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        rules_text = (
            "<b>📜 Posting Guidelines:</b>\n"
            "1. Be polite & respectful.\n"
            "2. No spam.\n"
            "3. Use #tags.\n\n"
            "<b>🌟 Ideal Post:</b>\n"
            "<i>Trip to mountains! #travel\nhttps://img.url/example.jpg</i>"
        )
        await update.message.reply_text(rules_text, parse_mode='HTML')

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        admin_id = str(config('ADMIN_ID'))
        
        if context.args and context.args[0] == 'web_post':
            await update.message.reply_text("👋 <b>Welcome from the Web Realm!</b>\nSend your text/photo.", parse_mode='HTML')

        tg_user, created = await sync_to_async(TelegramUser.objects.get_or_create)(
            telegram_id=user.id,
            defaults={'username': user.username, 'first_name': user.first_name}
        )
        
        # Download Avatar
        if created:
            try:
                user_photos = await user.get_profile_photos(limit=1)
                if user_photos and user_photos.total_count > 0:
                    photo_file = await user_photos.photos[0][-1].get_file()
                    file_byte_array = await photo_file.download_as_bytearray()
                    def save_avatar():
                        tg_user.profile_pic.save(f"{user.id}_avatar.jpg", ContentFile(file_byte_array), save=True)
                    await sync_to_async(save_avatar)()
            except: pass

            # Notify Admin
            kb = [[InlineKeyboardButton("✅ Approve", callback_data=f"userapprove_{tg_user.id}"),
                   InlineKeyboardButton("❌ Block", callback_data=f"userblock_{tg_user.id}")]]
            try:
                await context.bot.send_message(chat_id=admin_id, text=f"🚨 <b>New User!</b>\nName: {user.first_name}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
            except: pass

        status = "Approved ✅" if tg_user.is_approved else "Pending ⏳"
        menu = (
            f"👋 <b>Welcome, {user.first_name}!</b>\nStatus: {status}\n\n"
            "<b>🛠 Options:</b>\n"
            "/drafts - View Drafts\n"
            "/myposts - View Published\n"
            "/anon - Toggle Anonymous\n"
            "/rules - Guidelines"
        )
        if str(user.id) == admin_id:
            menu += "\n\n<b>👮‍♂️ Admin:</b>\n/pending, /users, /broadcast, /notify"

        await update.message.reply_text(menu, parse_mode='HTML')

    async def toggle_anon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            tg_user.is_anonymous_mode = not tg_user.is_anonymous_mode
            await sync_to_async(tg_user.save)()
            state = "👻 ON" if tg_user.is_anonymous_mode else "👤 OFF"
            await update.message.reply_text(f"Anonymous Mode: {state}")
        except: pass

    # --- LIST VIEW: DRAFTS ---
    async def my_drafts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            drafts = await sync_to_async(list)(BlogPost.objects.filter(author=tg_user, status__in=['DRAFT', 'PENDING', 'REJECTED']).order_by('-created_at'))
        except: return

        if not drafts:
            await update.message.reply_text("📭 No drafts found.")
            return

        keyboard = []
        for post in drafts:
            icon = "📝" if post.status == 'DRAFT' else ("⏳" if post.status == 'PENDING' else "❌")
            # Limit button text length
            btn_text = f"{icon} {post.content[:20]}..."
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"viewpost_{post.id}")])

        await update.message.reply_text("📂 <b>Your Drafts:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    # --- LIST VIEW: PUBLISHED ---
    async def my_published(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            posts = await sync_to_async(list)(BlogPost.objects.filter(author=tg_user, status='PUBLISHED').order_by('-created_at'))
        except: return

        if not posts:
            await update.message.reply_text("📭 No published posts.")
            return

        keyboard = []
        for post in posts:
            btn_text = f"✅ {post.content[:20]}..."
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"viewpost_{post.id}")])

        await update.message.reply_text("🌟 <b>Published Scrolls:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    # --- ADMIN: PENDING LIST ---
    async def admin_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if str(user.id) != str(config('ADMIN_ID')): return

        pending = await sync_to_async(list)(BlogPost.objects.filter(status='PENDING').select_related('author').order_by('created_at'))
        if not pending: 
            await update.message.reply_text("✅ No pending approvals.")
            return

        keyboard = []
        for post in pending:
            btn_text = f"⏳ {post.author.first_name}: {post.content[:15]}..."
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"viewpost_{post.id}")])
            
        await update.message.reply_text(f"🚨 <b>Pending: {len(pending)}</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    # --- ADMIN: USER LIST ---
    async def admin_users_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if str(user.id) != str(config('ADMIN_ID')): return

        users = await sync_to_async(list)(TelegramUser.objects.all().order_by('-id'))
        
        keyboard = []
        for u in users:
            status = "✅" if u.is_approved else "⏳"
            btn_text = f"{status} {u.first_name} | {u.get_rank()}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"manageuser_{u.id}")])
            
        await update.message.reply_text(f"👥 <b>Users: {len(users)}</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    # --- ADMIN: BROADCAST (With Confirm) ---
    async def admin_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if str(user.id) != str(config('ADMIN_ID')): return

        msg = " ".join(context.args)
        if not msg:
            await update.message.reply_text("⚠️ Usage: /broadcast [Message]")
            return

        count = await sync_to_async(TelegramUser.objects.count)()
        USER_STATE[user.id] = {'action': 'CONFIRM_BROADCAST', 'msg': msg}
        
        kb = [[InlineKeyboardButton("✅ Yes, Send", callback_data="confirm_broadcast"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]]
        
        await update.message.reply_text(f"📢 <b>Confirm Broadcast?</b>\n\nMsg: {msg}\nTo: {count} Users", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    # --- ADMIN: NOTIFY (With Confirm) ---
    async def admin_notify_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if str(user.id) != str(config('ADMIN_ID')): return

        try:
            target_id = context.args[0]
            msg = " ".join(context.args[1:])
            USER_STATE[user.id] = {'action': 'CONFIRM_NOTIFY', 'target_id': target_id, 'msg': msg}
            
            kb = [[InlineKeyboardButton("✅ Send", callback_data="confirm_notify"),
                   InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]]
            await update.message.reply_text(f"🔔 <b>Confirm DM?</b>\n\nTo ID: {target_id}\nMsg: {msg}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        except:
            await update.message.reply_text("⚠️ Usage: /notify [user_id] [message]")

    # ==========================
    # MESSAGE HANDLER
    # ==========================
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        text = update.message.text or update.message.caption or ""
        
        # 1. Check User State (For Edit, Remark, DM flow)
        if user.id in USER_STATE:
            state = USER_STATE[user.id]
            action = state.get('action')
            
            if action == 'ADD_REMARK':
                target_id = state['target_id']
                try:
                    post = await sync_to_async(BlogPost.objects.select_related('author').get)(id=target_id)
                    post.admin_remark = text
                    post.status = 'DRAFT'
                    await sync_to_async(post.save)()
                    await update.message.reply_text("✅ Post returned with remark.")
                    await context.bot.send_message(post.author.telegram_id, f"↩️ <b>Post Returned:</b>\nRemark: {text}", parse_mode='HTML')
                except: pass
            
            elif action == 'ADMIN_EDIT':
                target_id = state['target_id']
                try:
                    post = await sync_to_async(BlogPost.objects.get)(id=target_id)
                    post.content = text
                    await sync_to_async(post.save)()
                    await update.message.reply_text("✅ Post updated.")
                except: pass

            elif action == 'USER_EDIT':
                target_id = state['target_id']
                try:
                    post = await sync_to_async(BlogPost.objects.get)(id=target_id)
                    post.content = text
                    post.admin_remark = None
                    await sync_to_async(post.save)()
                    await update.message.reply_text("✅ Draft updated.")
                    # Show the updated draft with Send button
                    kb = [[InlineKeyboardButton("🚀 Send", callback_data=f"send_{post.id}")]]
                    await update.message.reply_text(f"📄 <b>Preview:</b>\n{post.content[:100]}...", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
                except: pass
            
            elif action == 'DM_USER':
                target_id = state['target_id']
                try:
                    await context.bot.send_message(chat_id=target_id, text=f"🔔 <b>Admin Message:</b>\n\n{text}", parse_mode='HTML')
                    await update.message.reply_text("✅ Sent.")
                except: await update.message.reply_text("❌ Failed.")

            del USER_STATE[user.id]
            return

        # 2. New Post Creation
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
        except:
            await update.message.reply_text("/start first.")
            return

        if not tg_user.is_approved:
            await update.message.reply_text("🚫 Not approved.")
            return
            
        if not text:
            await update.message.reply_text("Send text or photo.")
            return

        new_post = await sync_to_async(BlogPost.objects.create)(
            author=tg_user, content=text, image=None, status='DRAFT', is_anonymous=tg_user.is_anonymous_mode
        )
        
        if tg_user.is_anonymous_mode:
            tg_user.is_anonymous_mode = False
            await sync_to_async(tg_user.save)()

        kb = [[InlineKeyboardButton("🚀 Send", callback_data=f"send_{new_post.id}"),
               InlineKeyboardButton("🗑️ Discard", callback_data=f"discard_{new_post.id}")]]
        await update.message.reply_text(f"📝 <b>Draft Created:</b>\n{text[:100]}...", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    # ==========================
    # CALLBACK QUERY (BUTTONS)
    # ==========================
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data.split('_')
        action = data[0]
        # Handle cases where data might not have a second part (like 'cancel_action')
        target_id = data[1] if len(data) > 1 else None
        
        admin_id = str(config('ADMIN_ID'))
        user_id = query.from_user.id

        # --- CANCEL ---
        if action == "cancel":
            if user_id in USER_STATE: del USER_STATE[user_id]
            await query.edit_message_text("❌ Cancelled.")
            return

        # --- CONFIRM ACTIONS (Broadcast/Notify) ---
        if action == "confirm":
            state = USER_STATE.get(user_id)
            if not state: 
                await query.edit_message_text("❌ Session expired.")
                return

            if target_id == "broadcast" and str(user_id) == admin_id:
                msg = state['msg']
                users = await sync_to_async(list)(TelegramUser.objects.all())
                c = 0
                for u in users:
                    try:
                        await context.bot.send_message(u.telegram_id, f"📢 <b>Announcement:</b>\n\n{msg}", parse_mode='HTML')
                        c += 1
                    except: pass
                await query.edit_message_text(f"✅ Sent to {c} users.")
            
            elif target_id == "notify" and str(user_id) == admin_id:
                tid = state['target_id']
                msg = state['msg']
                try:
                    await context.bot.send_message(tid, f"🔔 <b>Admin Message:</b>\n\n{msg}", parse_mode='HTML')
                    await query.edit_message_text("✅ Message Sent.")
                except: await query.edit_message_text("❌ Failed.")
            
            if user_id in USER_STATE: del USER_STATE[user_id]
            return

        # --- MANAGE USER (From List) ---
        if action == "manageuser":
            if str(user_id) != admin_id: return
            try:
                u = await sync_to_async(TelegramUser.objects.get)(id=target_id)
                kb = [
                    [InlineKeyboardButton("📜 View Posts", callback_data=f"viewuser_{u.id}")],
                    [InlineKeyboardButton("🗣️ Message", callback_data=f"msguser_{u.id}")],
                    [InlineKeyboardButton("✅ Approve", callback_data=f"userapprove_{u.id}"),
                     InlineKeyboardButton("🚫 Block", callback_data=f"userblock_{u.id}")]
                ]
                await query.edit_message_text(
                    f"👤 <b>Manage: {u.first_name}</b>\nStatus: {'✅ Approved' if u.is_approved else '⏳ Pending'}", 
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML'
                )
            except: pass
            return

        # --- VIEW POST (From List) ---
        if action == "viewpost":
            try:
                post = await sync_to_async(BlogPost.objects.select_related('author').get)(id=target_id)
            except:
                await query.edit_message_text("❌ Post not found.")
                return

            keyboard = []
            # Logic: If Owner viewing
            if str(post.author.telegram_id) == str(user_id):
                if post.status == 'DRAFT':
                    keyboard = [
                        [InlineKeyboardButton("🚀 Send", callback_data=f"send_{post.id}")],
                        [InlineKeyboardButton("✏️ Edit", callback_data=f"edituser_{post.id}"),
                         InlineKeyboardButton("🗑️ Delete", callback_data=f"discard_{post.id}")]
                    ]
                elif post.status == 'PENDING':
                    keyboard = [[InlineKeyboardButton("🔙 Withdraw", callback_data=f"withdraw_{post.id}")]]
                elif post.status == 'REJECTED':
                    keyboard = [[InlineKeyboardButton("✏️ Edit", callback_data=f"edituser_{post.id}"),
                                 InlineKeyboardButton("🗑️ Delete", callback_data=f"discard_{post.id}")]]
                elif post.status == 'PUBLISHED':
                    keyboard = [[InlineKeyboardButton("🗑️ Request Delete", callback_data=f"reqdel_{post.id}")]]
            
            # Logic: If Admin viewing
            elif str(user_id) == admin_id:
                if post.status == 'PENDING':
                    keyboard = [
                        [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{post.id}"),
                         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{post.id}")],
                        [InlineKeyboardButton("✏️ Edit", callback_data=f"adminedit_{post.id}"),
                         InlineKeyboardButton("↩️ Remark", callback_data=f"remark_{post.id}")]
                    ]
                else:
                    keyboard = [[InlineKeyboardButton("❌ Force Delete", callback_data=f"admindel_{post.id}")]]

            # Show Content
            remark_txt = f"\n\n👮 <b>Remark:</b> {post.admin_remark}" if post.admin_remark else ""
            await query.edit_message_text(
                f"📄 <b>Post ID: {post.id}</b>\nStatus: {post.status}\n\n{post.content}{remark_txt}",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
            )
            return

        # --- STANDARD ACTIONS ---
        
        # Get Post Object for actions below
        try:
            post = await sync_to_async(BlogPost.objects.select_related('author').get)(id=target_id)
        except: return 

        if action == "reqdel":
            if str(post.author.telegram_id) != str(user_id): return
            await query.edit_message_text("✅ Deletion requested sent to Admin.")
            
            # Admin gets 2 buttons: Delete OR Keep
            kb = [
                [InlineKeyboardButton("🗑️ Confirm Delete", callback_data=f"confirmdel_{post.id}")],
                [InlineKeyboardButton("🛡️ Deny (Keep)", callback_data=f"keep_{post.id}")]
            ]
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🗑️ <b>Delete Request!</b>\nUser: {post.author.first_name}\n\n{post.content[:100]}...",
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML'
                )
            except: pass

        elif action == "confirmdel":
            if str(user_id) != admin_id: return
            auth_id = post.author.telegram_id
            pid = post.id
            await sync_to_async(post.delete)()
            await query.edit_message_text(f"🗑️ Deleted Post {pid}.")
            try:
                await context.bot.send_message(auth_id, f"🗑️ <b>Your Post #{pid} was deleted by Admin.</b>", parse_mode='HTML')
            except: pass

        elif action == "keep":
            if str(user_id) != admin_id: return
            await query.edit_message_text("🛡️ Request Denied. Post Kept.")
            try:
                await context.bot.send_message(post.author.telegram_id, f"🛡️ <b>Deletion Denied.</b>\nAdmin decided to keep Post #{post.id}.", parse_mode='HTML')
            except: pass

        elif action == "send":
            post.status = 'PENDING'
            await sync_to_async(post.save)()
            await query.edit_message_text("✅ Sent to Admin.")
            # Notify Admin (HTML Fix)
            kb = [[InlineKeyboardButton("🔍 View", callback_data=f"viewpost_{post.id}")]]
            try:
                await context.bot.send_message(
                    admin_id, 
                    f"🚨 <b>New Post Submission!</b>\nUser: {post.author.first_name}\n\n{post.content[:50]}...", 
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML'
                )
            except: pass

        elif action == "approve":
            post.status = 'PUBLISHED'
            post.admin_remark = None
            if "#pinned" in post.content.lower(): post.is_pinned = True
            if "#announce" in post.content.lower(): post.is_announcement = True
            post.author.post_count += 1
            await sync_to_async(post.author.save)()
            await sync_to_async(post.save)()
            await query.edit_message_text(f"✅ Published {post.id}")
            try:
                await context.bot.send_message(post.author.telegram_id, f"🎉 <b>Published!</b>\nRank: {await sync_to_async(post.author.get_rank)()}", parse_mode='HTML')
            except: pass

        elif action == "reject":
            post.status = 'REJECTED'
            await sync_to_async(post.save)()
            await query.edit_message_text(f"❌ Rejected {post.id}")
            try:
                await context.bot.send_message(post.author.telegram_id, f"❌ <b>Post Rejected.</b>\nID: {post.id}\nCheck /drafts.", parse_mode='HTML')
            except: pass

        elif action == "discard" or action == "withdraw":
            await sync_to_async(post.delete)()
            await query.edit_message_text("🗑️ Deleted.")

        elif action == "admindel":
            if str(user_id) != admin_id: return
            await sync_to_async(post.delete)()
            await query.edit_message_text("🗑️ Deleted by Admin.")

        # --- STATE ACTIONS ---
        elif action == "remark":
            USER_STATE[user_id] = {'action': 'ADD_REMARK', 'target_id': post.id}
            await query.edit_message_text("💬 Enter remark:")
        elif action == "adminedit":
            USER_STATE[user_id] = {'action': 'ADMIN_EDIT', 'target_id': post.id}
            await query.edit_message_text("✏️ Enter new text:")
        elif action == "edituser":
            USER_STATE[user_id] = {'action': 'USER_EDIT', 'target_id': post.id}
            await context.bot.send_message(user_id, "📝 Send new text:")
        elif action == "msguser":
            USER_STATE[user_id] = {'action': 'DM_USER', 'target_id': post.author.telegram_id}
            await query.edit_message_text("✍️ Enter message:")

        # --- USER APPROVAL ---
        elif action == "userapprove":
            try:
                u = await sync_to_async(TelegramUser.objects.get)(id=target_id)
                u.is_approved = True
                await sync_to_async(u.save)()
                await query.edit_message_text(f"✅ Approved {u.first_name}")
                await context.bot.send_message(u.telegram_id, "🎉 <b>Approved!</b> You can post now.", parse_mode='HTML')
            except: pass
