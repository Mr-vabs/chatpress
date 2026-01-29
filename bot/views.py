from django.shortcuts import render
from .models import BlogPost
from django.db.models import Q

def home(request):
    # Pinned posts sabse upar, fir baaki latest posts
    query = request.GET.get('q') # Search box se text
    posts = BlogPost.objects.filter(status='PUBLISHED')

    if query:
        # Search in Content, Author Name, or Rank
        posts = posts.filter(
            Q(content__icontains=query) | 
            Q(author__first_name__icontains=query) |
            Q(author__username__icontains=query)
        )

    posts = posts.order_by('-is_pinned', '-created_at')
    return render(request, 'home.html', {'posts': posts, 'query': query})

def tag_view(request, tag_name):
    # Case insensitive search for tag
    posts = BlogPost.objects.filter(
        status='PUBLISHED', 
        content__icontains=f"#{tag_name}"
    ).order_by('-created_at')
    
    return render(request, 'home.html', {'posts': posts, 'current_tag': tag_name})

