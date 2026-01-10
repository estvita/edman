from django.urls import path
from .views import (
    AccountListView, 
    StartAuthView, 
    CheckAuthStatusView, 
    SubmitOtpView, 
    SaveAccountView,
    LeadUploadView,
    LeadListView,
    LeadDetailView
)

app_name = "partner"

urlpatterns = [
    path("", AccountListView.as_view(), name="list"),
    path("auth/start/", StartAuthView.as_view(), name="auth_start"),
    path("auth/status/<str:session_id>/", CheckAuthStatusView.as_view(), name="auth_status"),
    path("auth/otp/", SubmitOtpView.as_view(), name="auth_otp"),
    path("auth/save/", SaveAccountView.as_view(), name="auth_save"),
    path("leads/upload/", LeadUploadView.as_view(), name="lead_upload"),
    path("leads/", LeadListView.as_view(), name="lead_list"),
    path("leads/<int:pk>/", LeadDetailView.as_view(), name="lead_detail"),
]
