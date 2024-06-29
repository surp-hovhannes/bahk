from django.contrib import admin
from markdownx.admin import MarkdownxModelAdmin
from .models import Changelog

admin.site.register(Changelog, MarkdownxModelAdmin)

# Register your models here.
