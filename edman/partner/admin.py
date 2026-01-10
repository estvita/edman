from django.contrib import admin
from .models import App, PartnerAccount, PartnerLead

@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("name", "auth_url", "leads_url")
    search_fields = ("name",)

@admin.register(PartnerAccount)
class PartnerAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "app", "login", "is_active", "created_at")
    list_filter = ("is_active", "app", "user")
    search_fields = ("name", "login", "user__username", "user__email")

@admin.register(PartnerLead)
class PartnerLeadAdmin(admin.ModelAdmin):
    list_display = (
        "external_id", 
        "first_name", 
        "last_name", 
        "phone", 
        "status", 
        "target_city", 
        "lead_created_at",
        "account"
    )
    list_filter = ("status", "target_city", "account", "created_at" if hasattr(PartnerLead, 'created_at') else "lead_created_at")
    search_fields = ("external_id", "first_name", "last_name", "phone", "status")
    date_hierarchy = "lead_created_at"
    list_per_page = 50
