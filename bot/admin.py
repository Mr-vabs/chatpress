from django.contrib import admin
from .models import TelegramUser, BlogPost

@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'username', 'telegram_id', 'is_approved', 'created_at')
    list_filter = ('is_approved',)
    search_fields = ('first_name', 'username')
    list_editable = ('is_approved',)  # List se hi tick karne ke liye

@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ('author', 'status', 'created_at')
    list_filter = ('status',)
