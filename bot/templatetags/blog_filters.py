from django import template
import re
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter(name='render_links')
def render_links(value):
    # Regex to find image links (jpg, jpeg, png, gif, webp)
    image_pattern = r'(https?://\S+\.(?:jpg|jpeg|png|gif|webp))'
    
    # Replacement HTML (Tailwind styling included for rounded corners)
    image_replacement = r'<img src="\1" class="w-full h-auto rounded-lg mt-3 mb-2 shadow-sm" loading="lazy" />'
    
    # Replace links with img tags
    new_value = re.sub(image_pattern, image_replacement, value, flags=re.IGNORECASE)
    
    # Baaki bache hue normal links ko clickable banayein
    # (Optional: Agar aap chahein to normal links ko bhi <a> tag de sakte hain)
    
    return mark_safe(new_value)

@register.filter(name='render_tags')
def render_tags(value):
    # Find #words
    tag_pattern = r'(#\w+)'
    # Replace with Link
    # Note: URL hardcoded for simplicity, ideal is to use {% url %} logic inside view
    new_value = re.sub(tag_pattern, r'<a href="/tag/\1" class="text-blue-500 hover:underline">\1</a>', value)
    
    # Fix: Remove the /tag/# part -> /tag/word
    new_value = new_value.replace('href="/tag/#', 'href="/tag/')
    
    return mark_safe(new_value)
