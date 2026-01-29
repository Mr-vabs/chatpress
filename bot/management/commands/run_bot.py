from django.core.management.base import BaseCommand
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.models import TelegramUser, BlogPost
from asgiref.sync import sync_to_async
from decouple import config
import os
from django.core.files.base import ContentFile

# GLOBAL STATE (Memory storage for Editing/Remarking steps)
USER_STATE = {}

class Command(BaseCommand):
    help = 'Runs the Telegram Bot'

    def handle(self, *args, **kwargs):
        token = config('TELEGRAM_TOKEN')
        application = ApplicationBuilder().token(token).build()

        # --- Handlers ---
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CommandHandler('anon', self.toggle_anon))
        application.add_handler(CommandHandler('drafts', self.my_drafts))
        application.add_handler(CommandHandler('pending', self.admin_pending))
        application.add_handler(CommandHandler('rules', self.rules))
        application.add_handler(CommandHandler('help', self.start)) # Help bhi start jaisa dikhe

        
        # Message Handler (Handles Text & Photos)
        application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, self.handle_message))
        
        # Button Handler
        application.add_handler(CallbackQueryHandler(self.handle_button))

        self.stdout.write(self.style.SUCCESS('Bot started polling...'))
        application.run_polling()

    # --- HELPER: Rules Text (Common logic) ---
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

    # --- COMMAND: /rules ---
    async def rules(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self.get_rules_text(), parse_mode='HTML')

    # --- COMMAND: /start (Updated with Role-Based Menu) ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        admin_id = str(config('ADMIN_ID'))
        
        # User Get/Create
        tg_user, created = await sync_to_async(TelegramUser.objects.get_or_create)(
            telegram_id=user.id,
            defaults={'username': user.username, 'first_name': user.first_name}
        )
        
        # Profile Pic Download (Same as before...)
        try:
            user_photos = await user.get_profile_photos(limit=1)
            if user_photos and user_photos.total_count > 0:
                photo_file = await user_photos.photos[0][-1].get_file()
                file_byte_array = await photo_file.download_as_bytearray()
                def save_avatar():
                    tg_user.profile_pic.save(f"{user.id}_avatar.jpg", ContentFile(file_byte_array), save=True)
                await sync_to_async(save_avatar)()
        except: pass

        status = "Approved ✅" if tg_user.is_approved else "Pending ⏳"
        
        # --- ROLE BASED MENU ---
        menu_text = f"👋 <b>Welcome, {user.first_name}!</b>\nStatus: {status}\n\n"
        
        # 1. Common Commands
        menu_text += (
            "<b>🛠 User Options:</b>\n"
            "/drafts - View Dashboard (Edit/Delete)\n"
            "/anon - Toggle Anonymous (Next post only)\n"
            "/rules - See Posting Rules & Examples\n"
        )

        # 2. Admin Only Commands
        if str(user.id) == admin_id:
            menu_text += (
                "\n<b>👮‍♂️ Admin Panel:</b>\n"
                "/pending - View Pending Approvals\n"
            )

        menu_text += f"\n{self.get_rules_text()}"

        await update.message.reply_text(menu_text, parse_mode='HTML', disable_web_page_preview=True)

    # ==========================
    # 1. COMMANDS
    # ==========================
    
    async def toggle_anon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
            
            # Logic: Agar ON hai to OFF karo, OFF hai to ON karo
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
            
            # --- NEW BUTTON LOGIC ---
            keyboard = []
            if post.status in ['DRAFT', 'REJECTED']:
                keyboard = [
                    [InlineKeyboardButton("✏️ Edit & Resubmit", callback_data=f"edituser_{post.id}")],
                    [InlineKeyboardButton("🗑️ Discard / Delete", callback_data=f"discard_{post.id}")] # <--- Added This
                ]
            
            msg = (f"{icon} <b>Post ID: {post.id}</b>\n"
                   f"Preview: {post.content[:50]}...\n"
                   f"{remark_text}\n{note}")
            
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')


    # ==========================
    # 2. MESSAGE HANDLER (Logic Core)
    # ==========================
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        # Get Text (from text msg or photo caption)
        text = update.message.text or update.message.caption or ""
        
        # --- A. STATE HANDLING (Editing/Remarking) ---
        if user.id in USER_STATE:
            state = USER_STATE[user.id]
            action = state['action']
            post_id = state['post_id']
            
            try:
                # IMPORTANT: select_related('author') prevents crash when notifying user
                post = await sync_to_async(BlogPost.objects.select_related('author').get)(id=post_id)
            except BlogPost.DoesNotExist:
                del USER_STATE[user.id]
                await update.message.reply_text("❌ Post not found. State cleared.")
                return

            # Scenario 1: Admin Adding Remark
            if action == 'ADD_REMARK':
                post.admin_remark = text
                post.status = 'DRAFT' # Send back to user as draft
                await sync_to_async(post.save)()
                
                await update.message.reply_text(f"✅ Remark saved. Post {post_id} returned to user.")
                
                # Notify User safely
                try:
                    await context.bot.send_message(
                        chat_id=post.author.telegram_id,
                        text=f"↩️ <b>Post Returned!</b>\n\n👮 <b>Admin Remark:</b> {text}\n\n👉 Use /drafts to Edit & Fix.",
                        parse_mode='HTML'
                    )
                except Exception as e:
                    await update.message.reply_text(f"⚠️ Saved, but user notification failed: {e}")

            # Scenario 2: Admin Editing Content
            elif action == 'ADMIN_EDIT':
                post.content = text
                await sync_to_async(post.save)()
                await update.message.reply_text(f"✅ Post {post_id} updated.")
                
                # Show Admin Panel Again
                admin_keyboard = [[
                    InlineKeyboardButton("✅ Approve Now", callback_data=f"approve_{post.id}"),
                    InlineKeyboardButton("✏️ Edit Again", callback_data=f"adminedit_{post.id}")
                ]]
                await update.message.reply_text(f"Updated Content:\n{post.content}", reply_markup=InlineKeyboardMarkup(admin_keyboard))

            # Scenario 3: User Editing Draft
            elif action == 'USER_EDIT':
                post.content = text
                # Clear remark since user has edited it
                post.admin_remark = None 
                
                # Update photo logic (Optional: Currently keeping text focus)
                if update.message.photo:
                     pass 
                
                await sync_to_async(post.save)()
                await update.message.reply_text("✅ Draft Updated.")
                
                # Show Send Button
                keyboard = [[InlineKeyboardButton("🚀 Send to Admin", callback_data=f"send_{post.id}")]]
                await update.message.reply_text(f"Draft Preview:\n{post.content}", reply_markup=InlineKeyboardMarkup(keyboard))

            # Clear State
            del USER_STATE[user.id]
            return

        # --- B. NEW POST CREATION ---
        
        # 1. Validation
        try:
            tg_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
        except TelegramUser.DoesNotExist:
            await update.message.reply_text("Please /start first.")
            return

        if not tg_user.is_approved:
            await update.message.reply_text("🚫 You are not approved to post yet.")
            return
            
        CHAR_LIMIT = 3000
        if len(text) > CHAR_LIMIT:
            await update.message.reply_text(f"⚠️ Too Long! ({len(text)}/{CHAR_LIMIT} chars).")
            return
        
        if not text and not update.message.photo:
            await update.message.reply_text("Please send text or a photo with caption.")
            return

        # 2. Photo Handling
        photo_file = None
        # Image download logic can go here if needed later

        # 3. Create Draft
                # ... (Upar ka code same rahega) ...

        # 3. Create Draft
        new_post = await sync_to_async(BlogPost.objects.create)(
            author=tg_user, 
            content=text, 
            image=photo_file, 
            status='DRAFT',
            is_anonymous=tg_user.is_anonymous_mode
        )

        # --- NEW: AUTO RESET ANON MODE ---
        if tg_user.is_anonymous_mode:
            tg_user.is_anonymous_mode = False
            await sync_to_async(tg_user.save)()
            anon_msg = "\n(👻 Anon Mode used & reset to OFF)"
        else:
            anon_msg = ""
        # ---------------------------------

        keyboard = [[InlineKeyboardButton("🚀 Send to Admin", callback_data=f"send_{new_post.id}"),
                     InlineKeyboardButton("🗑️ Discard", callback_data=f"discard_{new_post.id}")]]
        
        await update.message.reply_text(
            f"<b>📝 Draft Created:</b>{anon_msg}\n{text[:100]}...", 
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )

    # --- NEW ADMIN COMMAND ---
    async def admin_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        admin_id = config('ADMIN_ID')

        # Security Check: Only Admin can use this
        if str(user.id) != str(admin_id):
            await update.message.reply_text("⛔ Authorized Personnel Only.")
            return

        # Fetch Pending Posts
        pending_posts = await sync_to_async(list)(BlogPost.objects.filter(
            status='PENDING'
        ).select_related('author').order_by('created_at'))

        if not pending_posts:
            await update.message.reply_text("✅ No pending approvals. Good job!")
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
    # 3. BUTTON HANDLER
    # ==========================

    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data.split('_')
        action = data[0]
        post_id = data[1]
        
        admin_id = config('ADMIN_ID')
        user_id = query.from_user.id

        # Helper to get post
        def get_post():
            return BlogPost.objects.select_related('author').get(id=post_id)

        try:
            post = await sync_to_async(get_post)()
        except BlogPost.DoesNotExist:
            await query.edit_message_text("❌ Post not found.")
            return

        # --- USER ACTIONS ---
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
            # User wants to edit draft
            USER_STATE[user_id] = {'action': 'USER_EDIT', 'post_id': post.id}
            await context.bot.send_message(
                chat_id=user_id, 
                text=f"📝 <b>Editing Post {post.id}:</b>\n\n👇 Please send the NEW text now."
            )

        # --- ADMIN ACTIONS ---
        elif action == "approve":
            post.status = 'PUBLISHED'
            post.admin_remark = None 
            
            # Tag & Rank Logic
            if post.content:
                if "#pinned" in post.content.lower(): post.is_pinned = True
                if "#announce" in post.content.lower(): post.is_announcement = True
            
            post.author.post_count += 1
            await sync_to_async(post.author.save)()
            await sync_to_async(post.save)()
            
            await query.edit_message_text(f"✅ Published Post {post.id}")
            
            # Notify User
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
            # Admin adding remark
            USER_STATE[user_id] = {'action': 'ADD_REMARK', 'post_id': post.id}
            await query.edit_message_text(f"💬 <b>Returning Post {post.id}...</b>\n\n👇 Type the reason/remark now:")

        elif action == "adminedit":
            # Admin editing content
            USER_STATE[user_id] = {'action': 'ADMIN_EDIT', 'post_id': post.id}
            await query.edit_message_text(f"✏️ <b>Editing Post {post.id}...</b>\n\n👇 Send the corrected text now:")
