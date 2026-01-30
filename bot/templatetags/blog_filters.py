import re
from django import template
from django.utils.safestring import mark_safe
from django.utils.html import escape

register = template.Library()

@register.filter(name='render_links')
def render_links(value):
    if not value:
        return ""

    # 1. Pehle pure text ko "Escape" karein (Security: Script tags hatane ke liye)
    # Taaki koi <script> hack na kar sake, lekin humare img tags safe rahein
    value = escape(value)
    
    # 🔥 FIX: Apostrophe (&#x27;) ko wapas Normal (') bana do
    value = value.replace('&#x27;', "'")

    # 2. Logic to find URLS
    url_pattern = r'(https?://[^\s]+)'
    
    def replace_logic(match):
        url = match.group(0)
        # Check extensions (Images)
        if url.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            # Return Image Tag
            return f'''
                <div class="my-1">
                    <img src="{url}" class="w-full h-auto rounded-lg shadow-sm border border-gray-200" loading="lazy" alt="Post Image">
                </div>
            '''
        else:
            # Return Normal Clickable Link
            return f'<a href="{url}" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:underline break-all">{url}</a>'

    # Regex Replacement
    return mark_safe(re.sub(url_pattern, replace_logic, value))

@register.filter(name='render_tags')
def render_tags(value):
    if not value:
        return ""
    
    # #Hashtag pattern
    tag_pattern = r'#(\w+)'
    
    # Replace with Span/Link (Filhal blue text style de rahe hain)
    # Agar aapne /tag/ view nahi banaya hai to href="#" rakhein, ya sirf span use karein
    return mark_safe(re.sub(tag_pattern, r'<span class="text-blue-500 font-medium">#\1</span>', value))
