import requests
import time
from django.core.management.base import BaseCommand
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.models import TelegramUser, BlogPost
from asgiref.sync import sync_to_async
from decouple import config
import os
import threading
from django.core.files.base import ContentFile
from http.server import HTTPServer, BaseHTTPRequestHandler

# GLOBAL STATE (Memory storage for Editing/Remarking steps)
USER_STATE = {}

class Command(BaseCommand):
    help = 'Runs the Telegram Bot'

    def handle(self, *args, **kwargs):
        # --- DUMMY SERVER (Keep Render Awake) ---
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
        # ----------------------------------------
        
        # 2. WATCHDOG (Monitor Website & Notify Admin) - NEW FEATURES 🚨
        def start_watchdog():
            admin_id = str(config('ADMIN_ID'))
            website_url = "https://chatpress-web.onrender.com" # <--- Confirm this URL
            bot_token = config('TELEGRAM_TOKEN')
            
            print("🐶 Watchdog started...")
            
            while True:
                time.sleep(300) # Wait 5 minutes
                try:
                    response = requests.get(website_url, timeout=30)
                    if response.status_code != 200:
                        raise Exception(f"Status Code: {response.status_code}")
                except Exception as e:
                    # Alert Admin via Telegram API (Direct call to avoid async complexity here)
                    alert_msg = f"🚨 <b>ALERT: Website is DOWN!</b>\n\nError: {str(e)}\n\nCheck Render Dashboard immediately."
                    requests.get(f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={admin_id}&text={alert_msg}&parse_mode=HTML")

        threading.Thread(target=start_watchdog, daemon=True).start()
        # -----------------------------------------------------------

        token = config('TELEGRAM_TOKEN')
        application = ApplicationBuilder().token(token).build()

        # --- Handlers ---
        # Public Commands
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CommandHandler('help', self.start))
        application.add_handler(CommandHandler('rules', self.rules))
        application.add_handler(CommandHandler('anon', self.toggle_anon))
        
        # User Dashboard Commands
        application.add_handler(CommandHandler('drafts', self.my_drafts))  # Pending/Drafts
        application.add_handler(CommandHandler('myposts', self.my_published)) # Published (New)

        # Admin Commands
        application.add_handler(CommandHandler('pending', self.admin_pending))
        application.add_handler(CommandHandler('users', self.admin_users_list)) # New
        application.add_handler(CommandHandler('broadcast', self.admin_broadcast)) # New
        application.add_handler(CommandHandler('notify', self.admin_notify_user)) # New
        
        # Core Handlers
        application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, self.handle_message))
        application.add_handler(CallbackQueryHandler(self.handle_button))

        self.stdout.write(self.style.SUCCESS('Bot started polling...'))
        application.run_polling()

    # ==========================
    # 1. HELPERS & RULES
    # ==========================
    def get_rules_text(self):
        return (
            "<b>📜 Posting Guidelines:</b>\n"
            "1. Be polite & respectful.\n"
            "2. No spam.\n"
            "3. Use #tags.\n\n"
            "<b>🌟 Ideal Post:</b>\n"
            "<i>Trip to mountains! #travel\nhttps://img.url/example.jpg</i>"
        )

    async def rules(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self.get_rules_text(), parse_mode='HTML')

    # ==========================
    # 2. START & MENU
    # ==========================
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        admin_id = str(config('ADMIN_ID'))
        
        # Website Deep Link Check
        if context.args and context.args[0] == 'web_post':
            await update.message.reply_text("👋 <b>Welcome from the Web Realm!</b>\nSend your text/photo to create a post.", parse_mode='HTML')

        # User Create
        tg_user, created = await sync_to_async(TelegramUser.objects.get_or_create)(
            telegram_id=user.id,
            defaults={'username': user.username, 'first_name': user.first_name}
        )
        
        # Avatar Download
        try:
            user_photos = await user.get_profile_photos(limit=1)
            if user_photos and user_photos.total_count > 0:
                photo_file = await user_photos.photos[0][-1].get_file()
                file_byte_array = await photo_file.download_as_bytearray()
                def save_avatar():
                    tg_user.profile_pic.save(f"{user.id}_avatar.jpg", ContentFile(file_byte_array), save=True)
                await sync_to_async(save_avatar)()
        except: pass

        # Notify Admin on New Join
        if created:
            admin_text = f"🚨 <b>New User!</b>\nName: {user.first_name}\nID: {user.id}"
            keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"userapprove_{tg_user.id}"),
                         InlineKeyboardButton("❌ Block", callback_data=f"userblock_{tg_user.id}")]]
            try:
                await context.bot.send_message(chat_id=admin_id, text=admin_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            except: pass

        status = "Approved ✅" if tg_user.is_approved else "Pending ⏳"
        
        menu = f"👋 <b>Welcome, {user.first_name}!</b>\nStatus: {status}\n\n"
        menu += (
            "<b>🛠 User Options:</b>\n"
            "/drafts - Drafts & Pending\n"
            "/myposts - Published Posts (Req Delete)\n"
            "/anon - Toggle Anonymous\n"
            "/rules - Guidelines"
        )

        if str(user.id) == admin_id:
            menu += (
                "\n\n<b>👮‍♂️ Admin Panel:</b>\n"
                "/pending - Approvals\n"
                "/users - All Users & Posts\n"
                "/broadcast [msg] - Send to All\n"
                "/notify [id] [msg] - DM User"
            )

        await update.message.reply_text(menu, parse_mode='HTML', disable_web_page_preview=True)

    # ==========================
    # 3. USER COMMANDS
    # ==========================
    async def toggle_anon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            tg_user.is_anonymous_mode = not tg_user.is_anonymous_mode
            await sync_to_async(tg_user.save)()
            state = "👻 ON" if tg_user.is_anonymous_mode else "👤 OFF"
            await update.message.reply_text(f"Anonymous Mode: {state}")
        except: pass

    async def my_drafts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            drafts = await sync_to_async(list)(BlogPost.objects.filter(author=tg_user, status__in=['DRAFT', 'PENDING', 'REJECTED']).order_by('-created_at'))
        except: return

        if not drafts: await update.message.reply_text("📭 No drafts/pending posts.")
        else: await update.message.reply_text("📂 <b>Your Drafts:</b>", parse_mode='HTML')

        for post in drafts:
            if post.status == 'DRAFT': icon = "📝 Draft"
            elif post.status == 'PENDING': icon = "⏳ Pending"
            else: icon = "❌ Rejected"
            
            keyboard = []
            if post.status in ['DRAFT', 'REJECTED']:
                keyboard = [[InlineKeyboardButton("✏️ Edit", callback_data=f"edituser_{post.id}"),
                             InlineKeyboardButton("🗑️ Delete", callback_data=f"discard_{post.id}")]]
            
            remark = f"\n👮 <b>Remark:</b> {post.admin_remark}" if post.admin_remark else ""
            await update.message.reply_text(f"{icon} <b>ID: {post.id}</b>\nPreview: {post.content[:50]}...{remark}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    # --- NEW: User Published Posts (Feedback 1 & 2) ---
    async def my_published(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            posts = await sync_to_async(list)(BlogPost.objects.filter(author=tg_user, status='PUBLISHED').order_by('-created_at'))
        except: return

        if not posts:
            await update.message.reply_text("📭 You haven't published anything yet.")
            return

        await update.message.reply_text(f"🌟 <b>Your Published Scrolls ({len(posts)}):</b>", parse_mode='HTML')
        for post in posts:
            keyboard = [[InlineKeyboardButton("🗑️ Request Deletion", callback_data=f"reqdel_{post.id}")]]
            await update.message.reply_text(
                f"✅ <b>ID: {post.id}</b>\n{post.content[:100]}...\n<i>(Live on Website)</i>",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
            )

    # ==========================
    # 4. ADMIN COMMANDS
    # ==========================
    async def admin_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        admin_id = str(config('ADMIN_ID'))
        if str(user.id) != admin_id: return

        pending = await sync_to_async(list)(BlogPost.objects.filter(status='PENDING').select_related('author').order_by('created_at'))
        if not pending: await update.message.reply_text("✅ No pending approvals.")
        else: await update.message.reply_text(f"🚨 <b>Pending: {len(pending)}</b>", parse_mode='HTML')

        for post in pending:
            kb = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{post.id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"reject_{post.id}")],
                [InlineKeyboardButton("✏️ Edit", callback_data=f"adminedit_{post.id}"),
                 InlineKeyboardButton("↩️ Return", callback_data=f"remark_{post.id}")]
            ]
            await update.message.reply_text(f"👤 {post.author.first_name}\n\n{post.content}", reply_markup=InlineKeyboardMarkup(kb))

    # --- NEW: List All Users (Feedback 4) ---
    async def admin_users_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if str(user.id) != str(config('ADMIN_ID')): return

        users = await sync_to_async(list)(TelegramUser.objects.all().order_by('-id'))
        await update.message.reply_text(f"👥 <b>Total Users: {len(users)}</b>", parse_mode='HTML')

        for u in users:
            status = "✅" if u.is_approved else "⏳"
            kb = [[InlineKeyboardButton("📜 View Posts", callback_data=f"viewuser_{u.id}"),
                   InlineKeyboardButton("🗣️ Message", callback_data=f"msguser_{u.id}")]]
            await update.message.reply_text(
                f"{status} <b>{u.first_name}</b> (@{u.username})\nID: <code>{u.telegram_id}</code>\nRank: {u.get_rank()}",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML'
            )

    # --- NEW: Broadcast (Feedback 5) ---
    async def admin_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if str(user.id) != str(config('ADMIN_ID')): return

        msg = " ".join(context.args)
        if not msg:
            await update.message.reply_text("⚠️ Usage: /broadcast Your Message Here")
            return

        users = await sync_to_async(list)(TelegramUser.objects.all())
        count = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u.telegram_id, text=f"📢 <b>Announcement:</b>\n\n{msg}", parse_mode='HTML')
                count += 1
            except: pass
        await update.message.reply_text(f"✅ Broadcast sent to {count} users.")

    # --- NEW: Notify Specific User (Feedback 5) ---
    async def admin_notify_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if str(user.id) != str(config('ADMIN_ID')): return

        try:
            target_id = context.args[0]
            msg = " ".join(context.args[1:])
            await context.bot.send_message(chat_id=target_id, text=f"🔔 <b>Admin Message:</b>\n\n{msg}", parse_mode='HTML')
            await update.message.reply_text("✅ Message sent.")
        except:
            await update.message.reply_text("⚠️ Usage: /notify [user_id] [message]")

    # ==========================
    # 5. HANDLERS
    # ==========================
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        text = update.message.text or update.message.caption or ""
        
        # State Handling (Edit/Remark/Notify)
        if user.id in USER_STATE:
            state = USER_STATE[user.id]
            action = state['action']
            target_id = state['target_id']
            
            if action == 'ADD_REMARK':
                try:
                    post = await sync_to_async(BlogPost.objects.select_related('author').get)(id=target_id)
                    post.admin_remark = text
                    post.status = 'DRAFT'
                    await sync_to_async(post.save)()
                    await update.message.reply_text("✅ Post returned.")
                    await context.bot.send_message(post.author.telegram_id, f"↩️ <b>Post Returned:</b>\n{text}", parse_mode='HTML')
                except: pass

            elif action == 'ADMIN_EDIT':
                try:
                    post = await sync_to_async(BlogPost.objects.get)(id=target_id)
                    post.content = text
                    await sync_to_async(post.save)()
                    await update.message.reply_text("✅ Updated.")
                except: pass

            elif action == 'USER_EDIT':
                try:
                    post = await sync_to_async(BlogPost.objects.get)(id=target_id)
                    post.content = text
                    post.admin_remark = None
                    await sync_to_async(post.save)()
                    await update.message.reply_text("✅ Draft updated.")
                    kb = [[InlineKeyboardButton("🚀 Send", callback_data=f"send_{post.id}")]]
                    await update.message.reply_text(f"Preview:\n{post.content[:100]}...", reply_markup=InlineKeyboardMarkup(kb))
                except: pass
            
            # --- NEW: Admin replying to user directly via button ---
            elif action == 'DM_USER':
                try:
                    await context.bot.send_message(chat_id=target_id, text=f"🔔 <b>Admin Message:</b>\n\n{text}", parse_mode='HTML')
                    await update.message.reply_text("✅ Message sent.")
                except: await update.message.reply_text("❌ Failed to send.")

            del USER_STATE[user.id]
            return

        # New Post Creation Logic
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
        except:
            await update.message.reply_text("/start first.")
            return

        if not tg_user.is_approved:
            await update.message.reply_text("🚫 Not approved yet.")
            return
            
        if not text:
            await update.message.reply_text("Send text or photo.")
            return

        new_post = await sync_to_async(BlogPost.objects.create)(
            author=tg_user, content=text, image=None, status='DRAFT', is_anonymous=tg_user.is_anonymous_mode
        )
        
        # Auto Reset Anon
        if tg_user.is_anonymous_mode:
            tg_user.is_anonymous_mode = False
            await sync_to_async(tg_user.save)()

        kb = [[InlineKeyboardButton("🚀 Send", callback_data=f"send_{new_post.id}"),
               InlineKeyboardButton("🗑️ Discard", callback_data=f"discard_{new_post.id}")]]
        await update.message.reply_text(f"📝 <b>Draft:</b>\n{text[:100]}...", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data.split('_')
        action = data[0]
        target_id = data[1]
        admin_id = str(config('ADMIN_ID'))
        user_id = query.from_user.id

        # --- User/Admin Interactions ---
        if action == "userapprove":
            try:
                u = await sync_to_async(TelegramUser.objects.get)(id=target_id)
                u.is_approved = True
                await sync_to_async(u.save)()
                await query.edit_message_text(f"✅ Approved {u.first_name}")
                await context.bot.send_message(u.telegram_id, "🎉 You are approved!")
            except: pass
            return

        if action == "userblock":
            await query.edit_message_text("🚫 Blocked")
            return

        # --- NEW: View User Posts (Admin) ---
        if action == "viewuser":
            if str(user_id) != admin_id: return
            try:
                target_user = await sync_to_async(TelegramUser.objects.get)(id=target_id)
                posts = await sync_to_async(list)(BlogPost.objects.filter(author=target_user).order_by('-created_at')[:10]) # Last 10
                
                if not posts: await query.edit_message_text(f"📭 {target_user.first_name} has no posts.")
                else:
                    await context.bot.send_message(chat_id=admin_id, text=f"📜 <b>History: {target_user.first_name}</b>", parse_mode='HTML')
                    for p in posts:
                        # Admin Delete Button logic here
                        kb = [[InlineKeyboardButton("❌ Admin Delete", callback_data=f"admindel_{p.id}")]]
                        status_icon = "✅" if p.status == 'PUBLISHED' else "📝"
                        await context.bot.send_message(chat_id=admin_id, text=f"{status_icon} [{p.status}] ID: {p.id}\n{p.content[:50]}...", reply_markup=InlineKeyboardMarkup(kb))
            except: pass
            return

        # --- NEW: DM User Button ---
        if action == "msguser":
            if str(user_id) != admin_id: return
            # Target ID here is Database ID, need Telegram ID
            try:
                u = await sync_to_async(TelegramUser.objects.get)(id=target_id)
                USER_STATE[user_id] = {'action': 'DM_USER', 'target_id': u.telegram_id}
                await query.edit_message_text(f"✍️ Type message for {u.first_name}:")
            except: pass
            return

        # --- Post Actions ---
        try:
            post = await sync_to_async(BlogPost.objects.select_related('author').get)(id=target_id)
        except:
            await query.edit_message_text("❌ Not found.")
            return

        # --- NEW: User Requests Deletion ---
        if action == "reqdel":
            # Security Check: Compare as Strings to avoid Type Mismatch
            if str(post.author.telegram_id) != str(user_id):
                await query.answer("⛔ You can only delete your own posts!", show_alert=True)
                return

            await query.edit_message_text("✅ Deletion requested. Admin notified.")
            
            # Notify Admin
            admin_kb = [[InlineKeyboardButton("🗑️ Confirm Delete", callback_data=f"admindel_{post.id}")]]
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🗑️ <b>Delete Request!</b>\n\n👤 User: {post.author.first_name}\n🆔 Post ID: {post.id}\n\n📄 Content:\n{post.content[:100]}...",
                    reply_markup=InlineKeyboardMarkup(admin_kb), 
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"Admin notify failed: {e}")
            return


        # --- NEW: Admin Force Delete ---
        elif action == "admindel":
            if str(user_id) != admin_id: return
            await sync_to_async(post.delete)()
            await query.edit_message_text("🗑️ Post Deleted by Admin.")

        elif action == "discard":
            await sync_to_async(post.delete)()
            await query.edit_message_text("🗑️ Discarded.")

        elif action == "send":
            post.status = 'PENDING'
            await sync_to_async(post.save)()
            await query.edit_message_text("✅ Sent to Admin!")
            kb = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{post.id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"reject_{post.id}")],
                [InlineKeyboardButton("✏️ Edit", callback_data=f"adminedit_{post.id}"),
                 InlineKeyboardButton("↩️ Remark", callback_data=f"remark_{post.id}")]
            ]
            await context.bot.send_message(chat_id=admin_id, text=f"🚨 <b>New Post:</b>\n{post.author.first_name}\n\n{post.content}", reply_markup=InlineKeyboardMarkup(kb))

        elif action == "edituser":
            USER_STATE[user_id] = {'action': 'USER_EDIT', 'target_id': post.id}
            await context.bot.send_message(chat_id=user_id, text=f"📝 Send new text for Post {post.id}:")

        elif action == "approve":
            post.status = 'PUBLISHED'
            post.admin_remark = None
            if "#pinned" in post.content.lower(): post.is_pinned = True
            if "#announce" in post.content.lower(): post.is_announcement = True
            post.author.post_count += 1
            await sync_to_async(post.author.save)()
            await sync_to_async(post.save)()
            await query.edit_message_text(f"✅ Published {post.id}")
            await context.bot.send_message(post.author.telegram_id, f"🎉 <b>Published!</b>\nRank: {await sync_to_async(post.author.get_rank)()}", parse_mode='HTML')

        elif action == "reject":
            post.status = 'REJECTED'
            await sync_to_async(post.save)()
            await query.edit_message_text(f"❌ Rejected {post.id}")

        elif action == "remark":
            USER_STATE[user_id] = {'action': 'ADD_REMARK', 'target_id': post.id}
            await query.edit_message_text("💬 Enter remark:")

        elif action == "adminedit":
            USER_STATE[user_id] = {'action': 'ADMIN_EDIT', 'target_id': post.id}
            await query.edit_message_text("✏️ Enter new text:")
