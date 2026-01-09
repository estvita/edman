import re
import uuid
import json
import redis
import requests
from celery import shared_task
from django.shortcuts import render, redirect
from django.contrib.sites.models import Site
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.http import HttpResponseBadRequest, HttpResponse

from django.contrib import messages
from .models import App, Employer, Resume, Contact
User = get_user_model()

r = redis.Redis(host='localhost', port=6379, db=0)

TOKEN_URL = "https://hh.ru/oauth/token"

@shared_task()
def send_message(url, payload, headers):
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.HTTPError as err:
        return f"HTTP error occurred: {err} Response: {err.response.text}"
    except Exception as err:
        return f"Other error occurred: {err}"


@shared_task
def refresh_hh_token(employer_id: int):
    employer = Employer.objects.filter(pk=employer_id).first()
    if not employer:
        return

    access_token = employer.access_token
    refresh_token = employer.refresh_token

    headers = {"Authorization": f"Bearer {access_token}"}
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    response = requests.post(TOKEN_URL, data=data, headers=headers)
    if response.status_code != 200:
        raise Exception(
            f"Failed to refresh token: {response.status_code} {response.text}"
        )

    tokens = response.json()
    new_access_token = tokens.get("access_token")
    new_refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in") or 0

    Employer.objects.filter(pk=employer_id).update(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
    )

    # планируем следующий запуск: время жизни + 10 секунд
    refresh_hh_token.apply_async(
        args=[employer_id],
        countdown=expires_in + 10,
    )


@shared_task()
def event_processor(data):
    subscription_id = data.get("subscription_id")
    employer = Employer.objects.filter(subscription=subscription_id).first()
    if employer:
        action_type = data.get("action_type")
        if action_type in ["NEW_RESPONSE_OR_INVITATION_VACANCY", "NEW_NEGOTIATION_VACANCY"]:
            payload = data.get("payload")
            resume_id = payload.get("resume_id")
            headers = {"Authorization": f"Bearer {employer.access_token}"}
            resume_url = f"https://api.hh.ru/resumes/{resume_id}"
            resume_data = requests.get(resume_url, headers=headers)
            resume_data.raise_for_status()
            resume_data = resume_data.json()
            last_name = resume_data.get("last_name")
            first_name = resume_data.get("first_name")
            title = resume_data.get("title")
            contact = resume_data.get("contact", [])
            middle_name = resume_data.get("middle_name")
            birth_date_value = resume_data.get("birth_date")
            area = resume_data.get("area")
            city = area.get("name") if area else None
            skill_set = resume_data.get("skill_set", [])

            # Сохраняем резюме
            resume = Resume.objects.create(
                owner=employer.owner,
                last_name=last_name,
                first_name=first_name,
                title=title,
                raw_json=resume_data
            )

            # Сохраняем контакты
            for item in contact:
                kind = item.get("kind")
                type_obj = item.get("type") or {}
                contact_type = type_obj.get("id")
                value = item.get("contact_value")
                if contact_type and value:
                    Contact.objects.create(
                        resume=resume,
                        type=contact_type,
                        value=value
                    )
                # Поиск подключенных мессенджеров 
                senders = employer.senders.all()
                if senders:
                    for sender in senders:
                        if sender.type == "waweb" and kind == "phone":
                            cleaned = re.sub(r'\D', '', value)
                            headers = {"apikey": sender.key}
                            payload = {
                                "number": cleaned,
                                "text": sender.text,
                                "linkPreview": True,
                            }

                            send_message.delay(sender.uri, payload, headers)

            bitrixes = employer.bitrixes.all()
            if bitrixes:
                for bitrix in bitrixes:
                    headers = {
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "fields": {
                            "TITLE": f"Резюме {last_name} {first_name} - {title}",
                            "NAME": first_name,
                            "LAST_NAME": last_name,
                            "SECOND_NAME": middle_name,
                            "BIRTHDATE": birth_date_value,
                            "ADDRESS_CITY": city,
                            "COMMENTS": f"Навыки: {', '.join(skill_set)}",
                            "SOURCE_ID": bitrix.source_id,
                            "ASSIGNED_BY_ID": bitrix.assign_by_id,
                            "SOURCE_DESCRIPTION": employer.user_id,
                        },
                        "params": {
                            "REGISTER_SONET_EVENT": "Y"
                        }
                    }
                    # Добавление контактов в payload
                    emails = []
                    phones = []
                    ims = []

                    for item in contact:
                        kind = item.get("kind")
                        type_obj = item.get("type") or {}
                        contact_type = type_obj.get("id")
                        value = item.get("contact_value")
                        if contact_type and value:
                            if contact_type == "email":
                                emails.append({"VALUE": value, "VALUE_TYPE": "WORK"})
                            elif contact_type == "cell":
                                phones.append({"VALUE": value, "VALUE_TYPE": "WORK"})
                            elif contact_type == "telegram":
                                ims.append({"VALUE": value, "VALUE_TYPE": "TELEGRAM"})
                            elif contact_type == "whatsapp":
                                ims.append({"VALUE": value, "VALUE_TYPE": "WHATSAPP"})

                    if emails:
                        payload["fields"]["EMAIL"] = emails
                    if phones:
                        payload["fields"]["PHONE"] = phones
                    if ims:
                        payload["fields"]["IM"] = ims

                    send_message.delay(bitrix.uri + "crm.lead.add", payload, headers)

    return data

@csrf_exempt
def event_handler(request):
    if request.method == "POST":
        data = json.loads(request.body)
        event_processor.delay(data)
        return HttpResponse("Ok")
    else:
        return redirect("auth_page")

@login_required
def auth_page(request):
    accounts = Employer.objects.filter(owner=request.user)
    return render(request, "hh_auth.html", {"accounts": accounts})

@login_required
def auth_start(request):
    if request.method == "POST":
        state = str(uuid.uuid4())
        current_site = Site.objects.get_current(request)
        app_obj = App.objects.filter(site=current_site).first()
        r.setex(f"hh_auth_state:{state}", 600, request.user.id)
        auth_url = (
            "https://hh.kz/oauth/authorize?"
            f"response_type=code&client_id={app_obj.client_id}"
            f"&state={state}"
        )
        return redirect(auth_url)
    return redirect("auth_page")


@login_required
def auth_finish(request):
    code = request.GET.get("code")
    state = request.GET.get("state")

    if not code or not state:
        return HttpResponseBadRequest("Missing code or state")

    # Проверка пользователя в state через Redis
    user_id = r.get(f"hh_auth_state:{state}")
    if not user_id:
        return HttpResponseBadRequest("Invalid or expired state")
    user = User.objects.get(id=int(user_id))

    # Найдите подходящий объект App, например, по site
    current_site = Site.objects.get_current(request)
    app_obj = App.objects.get(site=current_site)

    # Получение access_token
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": app_obj.client_id,
        "client_secret": app_obj.client_secret,
        "redirect_uri": app_obj.redirect_uri,
    }
    response = requests.post(TOKEN_URL, data=data)
    if response.status_code != 200:
        return HttpResponseBadRequest(f"Failed to get token {response.text}")
    tokens = response.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in")

    # Информация о текущем пользователе
    headers = {"Authorization": f"Bearer {access_token}"}
    user_data = requests.get("https://api.hh.ru/me", headers=headers)
    if user_data.status_code != 200:
        return HttpResponseBadRequest(f"Failed to get user {user_data.text}")
    user_data = user_data.json()
    hh_id = user_data.get("id")
    user_email = user_data.get("email")

    # Сохраняем или обновляем токены для данной пары user-app
    hh_user, created = Employer.objects.update_or_create(
        owner=user,
        app=app_obj,
        user_id=hh_id,
        defaults={
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user_email': user_email,
        }
    )
    refresh_hh_token.apply_async(
        args=[hh_user.id],
        countdown=expires_in + 10,
    )
    if not hh_user.subscription:
        subscribe_data = {
            "url": f"https://{current_site.domain}/hh/events/",
            "actions": [
                {
                    "type": "NEW_RESPONSE_OR_INVITATION_VACANCY"
                }
            ]
        }
        api_url = "https://api.hh.ru/webhook/subscriptions"
        subscribe = requests.post(api_url, json=subscribe_data, headers=headers)
        if subscribe.status_code != 201:
            return HttpResponseBadRequest(f"Failed to subscribe {subscribe.text}")
        subscribe_id = subscribe.json().get("id")
        hh_user.subscription = subscribe_id
        hh_user.save()
    messages.success(request, f"Авторизация HH завершена!")
    return redirect("auth_page")