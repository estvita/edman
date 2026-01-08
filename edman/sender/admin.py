from django.contrib import admin

# Register your models here.
from .models import Sender, Bitrix

@admin.register(Sender)
class SenderAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "owner")

@admin.register(Bitrix)
class BitrixAdmin(admin.ModelAdmin):
    list_display = ("id", "uri", "owner")