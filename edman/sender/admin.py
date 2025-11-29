from django.contrib import admin

# Register your models here.
from .models import Sender

@admin.register(Sender)
class SenderAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "owner")