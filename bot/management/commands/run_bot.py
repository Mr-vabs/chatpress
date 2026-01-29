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
        # --- NEW: Dummy Web Server to keep Render Awake ---
        class SimpleHTTP(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'I am alive! Bot is running.')

        def start_dummy_server():
            # Render PORT environment variable deta hai, use wo chahiye
            port = int(os.environ.get("PORT", 10000))
            server = HTTPServer(('0.0.0.0', port), SimpleHTTP)
            print(f"🌍 Dummy server running on port {port}")
            server.serve_forever()

        # Server ko alag thread mein start karo taaki bot block na ho
        threading.Thread(target=start_dummy_server, daemon=True).start()
        # --------------------------------------------------

        token = config('TELEGRAM_TOKEN')
        application = ApplicationBuilder().token(token).build()

        # --- Handlers ---
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CommandHandler('help', self.start))
        application.add_handler(CommandHandler('anon', self.toggle_anon))
        application.add_handler(CommandHandler('drafts', self.my_drafts))
        application.add_handler(CommandHandler('pending', self.admin_pending))
        application.add_handler(CommandHandler('rules', self.rules))
        
        # Message Handler (Handles Text & Photos)
        application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, self.handle_message))
        
        # Button Handler
        application.add_handler(CallbackQueryHandler(self.handle_button))

        self.stdout.write(self.style.SUCCESS('Bot started polling...'))
        application.run_polling()

    # ==========================
    # 1. COMMANDS & HELPERS
    # ==========================

    def get_rules_text(self):
        return (
            "<b>📜 Posting Guidelines:</b>\n"
            "1. Be polite & respectful.\n"
            "2. No spam or 4k+ characters.\n"
            "3. Use #tags for categories.\n\n"
            "<b>🌟 Ideal Post Example:</b>\n"
            "<i>Just visited the Himalayas! The view was insane. #travel #nature\n\n"
            "https://example.com/mountain.jpg</i>\n\n"
            "(Note: Paste image links for now)"
        )

    async def rules(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self.get_rules_text(), parse_mode='HTML')

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        admin_id = str(config('ADMIN_ID'))
        
        # User Get/Create
        tg_user, created = await sync_to_async(TelegramUser.objects.get_or_create)(
            telegram_id=user.id,
            defaults={'username': user.username, 'first_name': user.first_name}
        )
        
        # Profile Pic Download
        try:
            user_photos = await user.get_profile_photos(limit=1)
            if user_photos and user_photos.total_count > 0:
                photo_file = await user_photos.photos[0][-1].get_file()
                file_byte_array = await photo_file.download_as_bytearray()
                def save_avatar():
                    tg_user.profile_pic.save(f"{user.id}_avatar.jpg", ContentFile(file_byte_array), save=True)
                await sync_to_async(save_avatar)()
        except: pass

        # --- Notify Admin on New Registration ---
        if created:
            admin_text = (
                f"🚨 <b>New User Registration!</b>\n\n"
                f"👤 Name: {user.first_name}\n"
                f"🆔 ID: {user.id}\n"
                f"🔗 Username: @{user.username}"
            )
            keyboard = [
                [InlineKeyboardButton("✅ Approve User", callback_data=f"userapprove_{tg_user.id}"),
                 InlineKeyboardButton("❌ Block User", callback_data=f"userblock_{tg_user.id}")]
            ]
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"Could not notify admin: {e}")

        status = "Approved ✅" if tg_user.is_approved else "Pending ⏳ (Wait for Admin)"
        
        # --- ROLE BASED MENU ---
        menu_text = f"👋 <b>Welcome, {user.first_name}!</b>\nStatus: {status}\n\n"
        
        menu_text += (
            "<b>🛠 User Options:</b>\n"
            "/drafts - View Dashboard (Edit/Delete)\n"
            "/anon - Toggle Anonymous (Next post only)\n"
            "/rules - See Posting Rules & Examples\n"
        )

        if str(user.id) == admin_id:
            menu_text += "\n<b>👮‍♂️ Admin Panel:</b>\n/pending - View Pending Approvals\n"

        await update.message.reply_text(menu_text, parse_mode='HTML', disable_web_page_preview=True)

    async def toggle_anon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            
            tg_user.is_anonymous_mode = not tg_user.is_anonymous_mode
            await sync_to_async(tg_user.save)()
            
            if tg_user.is_anonymous_mode:
                state = "👻 ON (For NEXT Post Only)"
                msg = "Your next post will be hidden. It will auto-reset to OFF afterwards."
            else:
                state = "👤 OFF (Visible)"
                msg = "You are visible again."

            await update.message.reply_text(f"<b>Anonymous Mode: {state}</b>\n{msg}", parse_mode='HTML')
        except:
            await update.message.reply_text("Please /start first.")

    async def my_drafts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            drafts = await sync_to_async(list)(BlogPost.objects.filter(
                author=tg_user, 
                status__in=['DRAFT', 'PENDING', 'REJECTED']
            ).order_by('-created_at'))
        except:
            await update.message.reply_text("Register first with /start")
            return

        if not drafts:
            await update.message.reply_text("📭 No active drafts found.")
            return

        await update.message.reply_text("📂 <b>Your Dashboard:</b>", parse_mode='HTML')
        
        for post in drafts:
            if post.status == 'DRAFT':
                icon, note = "📝 Draft", "<i>(Action required)</i>"
            elif post.status == 'PENDING':
                icon, note = "⏳ Pending", "<i>(Waiting for Admin)</i>"
            else:
                icon, note = "❌ Rejected", ""

            remark_text = f"\n👮 <b>Admin Remark:</b> {post.admin_remark}" if post.admin_remark else ""
            
            keyboard = []
            if post.status in ['DRAFT', 'REJECTED']:
                keyboard = [
                    [InlineKeyboardButton("✏️ Edit & Resubmit", callback_data=f"edituser_{post.id}")],
                    [InlineKeyboardButton("🗑️ Discard / Delete", callback_data=f"discard_{post.id}")]
                ]
            
            msg = (f"{icon} <b>Post ID: {post.id}</b>\n"
                   f"Preview: {post.content[:50]}...\n"
                   f"{remark_text}\n{note}")
            
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    async def admin_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        admin_id = str(config('ADMIN_ID'))

        if str(user.id) != admin_id:
            await update.message.reply_text("⛔ Authorized Personnel Only.")
            return

        pending_posts = await sync_to_async(list)(BlogPost.objects.filter(
            status='PENDING'
        ).select_related('author').order_by('created_at'))

        if not pending_posts:
            await update.message.reply_text("✅ No pending approvals.")
            return

        await update.message.reply_text(f"🚨 <b>Pending Approvals: {len(pending_posts)}</b>", parse_mode='HTML')

        for post in pending_posts:
            admin_keyboard = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{post.id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"reject_{post.id}")],
                [InlineKeyboardButton("✏️ Edit", callback_data=f"adminedit_{post.id}"),
                 InlineKeyboardButton("↩️ Return w/ Remark", callback_data=f"remark_{post.id}")]
            ]
            
            await update.message.reply_text(
                f"👤 <b>Author:</b> {post.author.first_name} ({post.author.get_rank()})\n"
                f"📄 <b>Content:</b>\n{post.content}",
                reply_markup=InlineKeyboardMarkup(admin_keyboard),
                parse_mode='HTML'
            )

    # ==========================
    # 2. MESSAGE HANDLER (Logic Core)
    # ==========================
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        text = update.message.text or update.message.caption or ""
        
        # --- A. STATE HANDLING (Editing/Remarking) ---
        if user.id in USER_STATE:
            state = USER_STATE[user.id]
            action = state['action']
            post_id = state['post_id']
            
            try:
                post = await sync_to_async(BlogPost.objects.select_related('author').get)(id=post_id)
            except BlogPost.DoesNotExist:
                del USER_STATE[user.id]
                await update.message.reply_text("❌ Post not found. State cleared.")
                return

            if action == 'ADD_REMARK':
                post.admin_remark = text
                post.status = 'DRAFT'
                await sync_to_async(post.save)()
                await update.message.reply_text(f"✅ Remark saved. Post {post_id} returned.")
                try:
                    await context.bot.send_message(
                        chat_id=post.author.telegram_id,
                        text=f"↩️ <b>Post Returned!</b>\n\n👮 <b>Admin Remark:</b> {text}\n\n👉 Use /drafts to Edit & Fix.",
                        parse_mode='HTML'
                    )
                except Exception as e:
                    print(f"Notify failed: {e}")

            elif action == 'ADMIN_EDIT':
                post.content = text
                await sync_to_async(post.save)()
                await update.message.reply_text(f"✅ Post {post_id} updated.")
                admin_keyboard = [[
                    InlineKeyboardButton("✅ Approve Now", callback_data=f"approve_{post.id}"),
                    InlineKeyboardButton("✏️ Edit Again", callback_data=f"adminedit_{post.id}")
                ]]
                await update.message.reply_text(f"Updated Content:\n{post.content}", reply_markup=InlineKeyboardMarkup(admin_keyboard))

            elif action == 'USER_EDIT':
                post.content = text
                post.admin_remark = None 
                await sync_to_async(post.save)()
                await update.message.reply_text("✅ Draft Updated.")
                keyboard = [[InlineKeyboardButton("🚀 Send to Admin", callback_data=f"send_{post.id}")]]
                await update.message.reply_text(f"Draft Preview:\n{post.content}", reply_markup=InlineKeyboardMarkup(keyboard))

            del USER_STATE[user.id]
            return

        # --- B. NEW POST CREATION ---
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
        except TelegramUser.DoesNotExist:
            await update.message.reply_text("Please /start first.")
            return

        if not tg_user.is_approved:
            await update.message.reply_text("🚫 You are not approved to post yet. Wait for Admin.")
            return
            
        if len(text) > 3000:
            await update.message.reply_text(f"⚠️ Too Long! ({len(text)}/3000 chars).")
            return
        
        if not text and not update.message.photo:
            await update.message.reply_text("Please send text or a photo with caption.")
            return

        new_post = await sync_to_async(BlogPost.objects.create)(
            author=tg_user, 
            content=text, 
            image=None, 
            status='DRAFT',
            is_anonymous=tg_user.is_anonymous_mode
        )

        # --- AUTO RESET ANON MODE ---
        anon_msg = ""
        if tg_user.is_anonymous_mode:
            tg_user.is_anonymous_mode = False
            await sync_to_async(tg_user.save)()
            anon_msg = "\n(👻 Anon Mode used & reset to OFF)"
        
        keyboard = [[InlineKeyboardButton("🚀 Send to Admin", callback_data=f"send_{new_post.id}"),
                     InlineKeyboardButton("🗑️ Discard", callback_data=f"discard_{new_post.id}")]]
        
        await update.message.reply_text(
            f"<b>📝 Draft Created:</b>{anon_msg}\n{text[:100]}...", 
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )

    # ==========================
    # 3. BUTTON HANDLER
    # ==========================

    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data.split('_')
        action = data[0]
        target_id = data[1]
        
        admin_id = str(config('ADMIN_ID'))
        user_id = query.from_user.id

        # --- A. USER APPROVAL LOGIC ---
        if action == "userapprove":
            try:
                user_obj = await sync_to_async(TelegramUser.objects.get)(id=target_id)
                user_obj.is_approved = True
                await sync_to_async(user_obj.save)()
                
                await query.edit_message_text(f"✅ User {user_obj.first_name} Approved!")
                await context.bot.send_message(
                    chat_id=user_obj.telegram_id,
                    text="🎉 <b>You are Approved!</b>\n\nYou can now start posting.",
                    parse_mode='HTML'
                )
            except:
                await query.edit_message_text("❌ User not found.")
            return

        elif action == "userblock":
            await query.edit_message_text(f"🚫 User Request Blocked.")
            return

        # --- B. POST LOGIC ---
        # Helper to get post
        def get_post():
            return BlogPost.objects.select_related('author').get(id=target_id)

        try:
            post = await sync_to_async(get_post)()
        except BlogPost.DoesNotExist:
            await query.edit_message_text("❌ Post not found.")
            return

        if action == "discard":
            await sync_to_async(post.delete)()
            await query.edit_message_text("🗑️ Draft discarded.")

        elif action == "send":
            post.status = 'PENDING'
            await sync_to_async(post.save)()
            await query.edit_message_text(f"✅ Sent to Admin! (ID: {post.id})")

            # Admin Notification
            admin_keyboard = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{post.id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"reject_{post.id}")],
                [InlineKeyboardButton("✏️ Edit", callback_data=f"adminedit_{post.id}"),
                 InlineKeyboardButton("↩️ Return w/ Remark", callback_data=f"remark_{post.id}")]
            ]
            
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🚨 <b>New Submission!</b>\nFrom: {post.author.first_name}\n\n{post.content}",
                reply_markup=InlineKeyboardMarkup(admin_keyboard),
                parse_mode='HTML'
            )

        elif action == "edituser":
            USER_STATE[user_id] = {'action': 'USER_EDIT', 'post_id': post.id}
            await context.bot.send_message(
                chat_id=user_id, 
                text=f"📝 <b>Editing Post {post.id}:</b>\n\n👇 Please send the NEW text now."
            )

        elif action == "approve":
            post.status = 'PUBLISHED'
            post.admin_remark = None 
            
            if "#pinned" in post.content.lower(): post.is_pinned = True
            if "#announce" in post.content.lower(): post.is_announcement = True
            
            post.author.post_count += 1
            await sync_to_async(post.author.save)()
            await sync_to_async(post.save)()
            
            await query.edit_message_text(f"✅ Published Post {post.id}")
            
            current_rank = await sync_to_async(post.author.get_rank)()
            await context.bot.send_message(
                chat_id=post.author.telegram_id,
                text=f"🎉 <b>Published!</b>\nYour current Cultivation: <b>{current_rank}</b>",
                parse_mode='HTML'
            )

        elif action == "reject":
            post.status = 'REJECTED'
            await sync_to_async(post.save)()
            await query.edit_message_text(f"❌ Rejected Post {post.id}")

        elif action == "remark":
            USER_STATE[user_id] = {'action': 'ADD_REMARK', 'post_id': post.id}
            await query.edit_message_text(f"💬 <b>Returning Post {post.id}...</b>\n\n👇 Type the reason/remark now:")

        elif action == "adminedit":
            USER_STATE[user_id] = {'action': 'ADMIN_EDIT', 'post_id': post.id}
            await query.edit_message_text(f"✏️ <b>Editing Post {post.id}...</b>\n\n👇 Send the corrected text now:")
