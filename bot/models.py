from django.db import models
from decouple import config  # Admin ID check karne ke liye

class TelegramUser(models.Model):
    RANK_CHOICES = [
        ('MORTAL', 'Mortal 🦶'),
        ('QI_REFINER', 'Qi Refiner 🧘'),
        ('FOUNDATION', 'Foundation Est. 🏰'),
        ('GOLDEN_CORE', 'Golden Core 🌟'),
        ('NASCENT_SOUL', 'Nascent Soul 👻'),
        ('IMMORTAL', 'Immortal Realm 🐲'),  # Renamed
    ]

    telegram_id = models.CharField(max_length=50, unique=True)
    username = models.CharField(max_length=100, null=True, blank=True)
    first_name = models.CharField(max_length=100, null=True, blank=True)
    is_approved = models.BooleanField(default=False)
    
    # New Features
    is_vip = models.BooleanField(default=False)
    is_moderator = models.BooleanField(default=False) # Future use
    is_anonymous_mode = models.BooleanField(default=False) # Toggle ke liye
    profile_pic = models.ImageField(upload_to='avatars/', blank=True, null=True) # Telegram Pic
    
    post_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def get_rank(self):
        # 1. Check for Realm Master (Admin)
        if self.telegram_id == config('ADMIN_ID'):
            return "👑 Realm Master"
        
        # 2. Check Cultivation
        c = self.post_count
        if c < 5: return "Mortal 🦶"
        if c < 15: return "Qi Refiner 🧘"
        if c < 30: return "Foundation Est. 🏰"
        if c < 50: return "Golden Core 🌟"
        if c < 100: return "Nascent Soul 👻"
        return "Immortal Realm 🐲"

    def get_stars(self):
        if self.telegram_id == config('ADMIN_ID'): return 3 # ⭐⭐⭐
        if self.is_moderator: return 2 # ⭐⭐
        if self.is_vip: return 1 # ⭐
        return 0 # No star

    def __str__(self):
        return f"{self.first_name} ({self.username})"

class BlogPost(models.Model):
    author = models.ForeignKey(TelegramUser, on_delete=models.CASCADE)
    content = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='posts/', blank=True, null=True)
    
    # Post Specific Settings
    is_anonymous = models.BooleanField(default=False) # Agar user us waqt anon mode me tha
    
    status = models.CharField(max_length=20, default='DRAFT')
    admin_remark = models.TextField(blank=True, null=True)  # <--- Add this
    is_pinned = models.BooleanField(default=False)
    is_announcement = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # New Field for Admin Feedback