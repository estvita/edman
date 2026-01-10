import json
import logging
from django.views.generic import ListView, FormView, DetailView
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.urls import reverse_lazy
from django.core.files.storage import default_storage

from .models import PartnerAccount, App, PartnerLead
from .services import AuthSession, get_auth_status, submit_auth_otp, get_auth_result
from .forms import LeadUploadForm
from .tasks import process_leads_file

logger = logging.getLogger(__name__)

@method_decorator(ensure_csrf_cookie, name='dispatch')
class AccountListView(LoginRequiredMixin, ListView):
    model = PartnerAccount
    template_name = "partner/account_list.html"
    context_object_name = "accounts"

    def get_queryset(self):
        return PartnerAccount.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['apps'] = App.objects.all()
        return context

@method_decorator(ensure_csrf_cookie, name='dispatch')
class StartAuthView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.body)
            app_id = data.get('app_id')
            login = data.get('login')
            password = data.get('password')
            
            try:
                app = App.objects.get(id=app_id)
            except App.DoesNotExist:
                return JsonResponse({'error': 'App not found'}, status=404)
            
            # Start auth session
            session = AuthSession(app.auth_url, login, password)
            session_id = session.start()
            
            return JsonResponse({'session_id': session_id, 'status': 'initiated'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

class CheckAuthStatusView(LoginRequiredMixin, View):
    def get(self, request, session_id):
        status_data = get_auth_status(session_id)
        if not status_data:
             return JsonResponse({'status': 'UNKNOWN'}, status=404)
        
        return JsonResponse(status_data)

class SubmitOtpView(LoginRequiredMixin, View):
    def post(self, request):
        data = json.loads(request.body)
        session_id = data.get('session_id')
        code = data.get('code')
        submit_auth_otp(session_id, code)
        return JsonResponse({'status': 'submitted'})

class SaveAccountView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.body)
            session_id = data.get('session_id')
            app_id = data.get('app_id')
            login = data.get('login')
            name = data.get('name', login) # Default name to login
            
            logger.info(f"Attempting to save account. Session: {session_id}, Login: {login}")
            
            # Verify Session
            status = get_auth_status(session_id)
            if not status or status['status'] != AuthSession.STATUS_SUCCESS:
                logger.error(f"Save failed: Auth status is {status.get('status') if status else 'None'}")
                return JsonResponse({'error': f"Auth not complete. Status: {status.get('status') if status else 'None'}"}, status=404)
                
            session_data = get_auth_result(session_id)
            if not session_data:
                 logger.error("Save failed: No session data in cache")
                 return JsonResponse({'error': 'No session data found in cache'}, status=400)

            try:
                app = App.objects.get(id=app_id)
                
                # Check for existing account to update or create
                account, created = PartnerAccount.objects.update_or_create(
                    user=request.user,
                    app=app,
                    login=login,
                    defaults={
                        'name': name,
                        'session_data': session_data,
                        'is_active': True
                    }
                )
                logger.info(f"Account saved successfully. ID: {account.id}")
                return JsonResponse({'status': 'saved', 'id': account.id})
            except Exception as e:
                logger.error(f"Database error during save: {e}")
                return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
             logger.error(f"Unexpected error in SaveAccountView: {e}")
             return JsonResponse({'error': str(e)}, status=500)

class LeadUploadView(LoginRequiredMixin, FormView):
    template_name = "partner/lead_upload.html"
    form_class = LeadUploadForm
    success_url = reverse_lazy("partner:lead_upload")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        account = form.cleaned_data['account']
        file = form.cleaned_data['file']
        
        # Save file to distinct path
        file_path = default_storage.save(f"uploads/leads_{account.id}_{file.name}", file)
        
        # Get absolute path for Task
        full_path = default_storage.path(file_path)
        
        process_leads_file.delay(account.id, full_path)
        
        return self.render_to_response(self.get_context_data(form=form, val_success=True))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if kwargs.get('val_success'):
             context['success_message'] = "File uploaded and processing started."
        return context

from django.views.generic import DetailView
from .models import PartnerLead

class LeadListView(LoginRequiredMixin, ListView):
    model = PartnerLead
    template_name = "partner/lead_list.html"
    context_object_name = "leads"
    paginate_by = 50

    def get_queryset(self):
        # Base QuerySet limited to user
        qs = PartnerLead.objects.filter(account__user=self.request.user).select_related('account')
        
        # Filtering
        city = self.request.GET.get('city')
        status = self.request.GET.get('status')
        account_id = self.request.GET.get('account')
        search_id = self.request.GET.get('id')
        phone = self.request.GET.get('phone')
        creator = self.request.GET.get('creator')

        if city:
            qs = qs.filter(target_city=city)
        if status:
            qs = qs.filter(status=status)
        if account_id:
            qs = qs.filter(account_id=account_id)
        if search_id:
            qs = qs.filter(external_id__icontains=search_id)
        if phone:
            qs = qs.filter(phone__icontains=phone)
        if creator:
            qs = qs.filter(creator_username=creator)
            
        return qs.order_by('-lead_created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_leads = PartnerLead.objects.filter(account__user=self.request.user)
        
        # Get unique values for filters
        context['cities'] = user_leads.values_list('target_city', flat=True).distinct().order_by('target_city')
        context['statuses'] = user_leads.values_list('status', flat=True).distinct().order_by('status')
        context['creators'] = user_leads.values_list('creator_username', flat=True).distinct().order_by('creator_username')
        context['accounts'] = PartnerAccount.objects.filter(user=self.request.user)
        
        # Current filters state to keep in form
        context['current_city'] = self.request.GET.get('city', '')
        context['current_status'] = self.request.GET.get('status', '')
        context['current_creator'] = self.request.GET.get('creator', '')
        context['current_account'] = int(self.request.GET.get('account', 0)) if self.request.GET.get('account') else ''
        context['current_id'] = self.request.GET.get('id', '')
        context['current_phone'] = self.request.GET.get('phone', '')
        
        return context

class LeadDetailView(LoginRequiredMixin, DetailView):
    model = PartnerLead
    template_name = "partner/lead_detail.html"
    context_object_name = "lead"

    def get_queryset(self):
        # Ensure user can only view their own leads
        return PartnerLead.objects.filter(account__user=self.request.user)
