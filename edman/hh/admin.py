from django.contrib import admin

# Register your models here.
from .models import App, Employer, Resume, Contact

@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("id", "redirect_uri", "client_id")

@admin.register(Employer)
class EmployerAdmin(admin.ModelAdmin):
    list_display = ("user_id", "owner", "subscription", "app")

@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ("id", "date", "last_name", "title")
    search_fields = ("id", "last_name", "owner__email" "title")
    list_per_page = 50

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "value", "resume")
    search_fields = ("id", "value", "resume")
    list_filter = ("type",)
    list_per_page = 50