from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

class App(models.Model):
    name = models.CharField(_("Name"), max_length=255)
    auth_url = models.URLField(_("Auth URL"))
    leads_url = models.URLField(_("Leads URL"))

    class Meta:
        verbose_name = _("Partner App")
        verbose_name_plural = _("Partner Apps")

    def __str__(self):
        return self.name

class PartnerAccount(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="partner_accounts")
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="accounts")
    name = models.CharField(_("Account Name"), max_length=255)
    login = models.CharField(_("Login"), max_length=255)
    # Storing session data (cookies, localStorage)
    session_data = models.JSONField(_("Session Data"), default=dict, blank=True)
    
    is_active = models.BooleanField(_("Active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Partner Account")
        verbose_name_plural = _("Partner Accounts")
        unique_together = ('user', 'login', 'app')

    def __str__(self):
        return f"{self.name}"

class PartnerLead(models.Model):
    account = models.ForeignKey(PartnerAccount, on_delete=models.CASCADE, related_name="leads")
    external_id = models.CharField(_("External ID"), max_length=255, db_index=True)
    lead_created_at = models.DateTimeField(_("Created At"), null=True, blank=True)
    updated_ts = models.DateTimeField(_("Updated TS"), null=True, blank=True)
    first_name = models.CharField(_("First Name"), max_length=255, blank=True)
    last_name = models.CharField(_("Last Name"), max_length=255, blank=True)
    target_city = models.CharField(_("City"), max_length=255, blank=True)
    status = models.CharField(_("Status"), max_length=255, blank=True)
    eats_order_number = models.CharField(_("Order Number"), max_length=255, blank=True)
    rewarded_at = models.DateTimeField(_("Rewarded At"), null=True, blank=True)
    closed_reason = models.CharField(_("Closed Reason"), max_length=255, blank=True)
    utm_campaign = models.CharField(_("UTM Campaign"), max_length=255, blank=True)
    utm_content = models.CharField(_("UTM Content"), max_length=255, blank=True)
    utm_medium = models.CharField(_("UTM Medium"), max_length=255, blank=True)
    utm_source = models.CharField(_("UTM Source"), max_length=255, blank=True)
    utm_term = models.CharField(_("UTM Term"), max_length=255, blank=True)
    creator_username = models.CharField(_("Creator"), max_length=255, blank=True)
    reward = models.CharField(_("Reward"), max_length=255, blank=True)
    complaint_status = models.CharField(_("Complaint Status"), max_length=255, blank=True)
    phone = models.CharField(_("Phone"), max_length=255, blank=True, null=True)

    class Meta:
        verbose_name = _("Partner Lead")
        verbose_name_plural = _("Partner Leads")
        unique_together = ('account', 'external_id')

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.external_id})"
