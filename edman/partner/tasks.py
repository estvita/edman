import os
import time
import pandas as pd
from celery import chain
from config import celery_app
from .models import PartnerAccount, PartnerLead
from playwright.sync_api import sync_playwright
from datetime import datetime
from django.utils.timezone import make_aware
from django.utils import timezone

def parse_date(date_str):
    if not date_str or pd.isna(date_str): return None
    try:
        # Adjust format as per CSV "2026-01-07T23:10:31"
        dt = datetime.fromisoformat(str(date_str))
        if timezone.is_naive(dt):
            return make_aware(dt)
        return dt
    except:
        return None

def extract_phone_number(page, external_id, base_url):
    """Refactored extraction logic using passed page object"""
    try:
        target_url = f"{base_url}?external_ids={external_id}"
        page.goto(target_url, timeout=60000)
        
        if "passport.yandex" in page.url:
            print(f"❌ Session expired for {external_id}!")
            return None
        
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        # Check if we are checking for robot
        if "checkboxcaptcha" in page.content().lower():
             # Basic handling if needed, or fail
             pass

        # Wait for table or empty state
        try:
            page.wait_for_selector('tr[aria-rowindex="2"]', timeout=5000)
        except:
            # Maybe no result found
            return None

        # Click the row
        page.locator('tr[aria-rowindex="2"]').click()
        # Wait for URL to change to details
        # page.wait_for_url("**/leads/*", timeout=10000) # Pattern matching might fail if URL struct changes
        # Better: wait for the side panel or details block
        time.sleep(2) # Stability
        
        # Look for the phone field label "Номер телефона"
        # The parser script used: page.locator("text=Номер телефона").first
        phone_label = page.locator("text=Номер телефона").first
        if not phone_label.is_visible():
            return None
            
        # Click eye button
        eye_button = phone_label.locator("..").locator("button").first
        if eye_button.is_visible():
            eye_button.click()
            time.sleep(1)
            
            # Extract text
            phone_container = phone_label.locator("..").locator("span").last
            phone_text = phone_container.text_content()
            if phone_text:
                return phone_text.strip()
                
        return None
        
    except Exception as e:
        print(f"❌ Error extracting phone for {external_id}: {e}")
        return None

@celery_app.task(time_limit=600, soft_time_limit=600)
def process_leads_batch(account_id, leads_batch):
    """
    Process a batch of leads (e.g. 50 items) with a single browser instance.
    leads_batch is a list of dictionaries.
    """
    # Allow DB access within Playwright's loop
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    print(f"[{account_id}] Starting batch of {len(leads_batch)} items")
    
    try:
        account = PartnerAccount.objects.get(id=account_id)
        if not account.session_data:
            print("No session data for account")
            return "No Session"

        base_url = account.app.leads_url
        if not base_url:
             raise ValueError(f"Leads URL is missing for App: {account.app.name}")

        existing_ids = set(PartnerLead.objects.filter(account=account).values_list('external_id', flat=True))
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=account.session_data)
            page = context.new_page()
            
            for row in leads_batch:
                external_id = str(row.get('external_id'))
                if not external_id: continue

                # Helper to get safely
                def get_val(col):
                    val = row.get(col)
                    return val if (val is not None and not pd.isna(val)) else ""
                
                def get_date(col):
                    return parse_date(row.get(col))

                defaults = {
                    'lead_created_at': get_date('lead_created_at'),
                    'updated_ts': get_date('updated_ts'),
                    'first_name': get_val('first_name'),
                    'last_name': get_val('last_name'),
                    'target_city': get_val('target_city'),
                    'status': get_val('status'),
                    'eats_order_number': get_val('eats_order_number'),
                    'rewarded_at': get_date('rewarded_at'),
                    'closed_reason': get_val('closed_reason'),
                    'utm_campaign': get_val('utm_campaign'),
                    'utm_content': get_val('utm_content'),
                    'utm_medium': get_val('utm_medium'),
                    'utm_source': get_val('utm_source'),
                    'utm_term': get_val('utm_term'),
                    'creator_username': get_val('creator_username'),
                    'reward': get_val('reward'),
                    'complaint_status': get_val('complaint_status'),
                }

                if external_id in existing_ids:
                    # Update
                    PartnerLead.objects.filter(account=account, external_id=external_id).update(**defaults)
                else:
                    # Create + Phone
                    phone = extract_phone_number(page, external_id, base_url)
                    PartnerLead.objects.create(
                        account=account,
                        external_id=external_id,
                        phone=phone,
                        **defaults
                    )
                    existing_ids.add(external_id)

            browser.close()
    except Exception as e:
        print(f"Batch task failed: {e}")
        raise e

@celery_app.task
def process_leads_file(account_id, file_path):
    print(f"Starting file dispatch for account_id={account_id}, file={file_path}")
    try:
        # 1. Read CSV
        try:
             df = pd.read_csv(file_path)
        except Exception as e:
             print(f"Failed to read CSV: {e}")
             return

        if 'external_id' not in df.columns:
            print("CSV missing external_id column")
            return

        # 2. Convert to list of dicts for JSON serialization
        # Important: Handle NaN values, maybe fillna("")
        # Dates are strings in CSV, so okay.
        leads_data = df.fillna("").to_dict('records')
        
        # 3. Batching
        BATCH_SIZE = 50
        batches = [leads_data[i:i + BATCH_SIZE] for i in range(0, len(leads_data), BATCH_SIZE)]
        
        print(f"Split {len(leads_data)} leads into {len(batches)} batches.")

        # 4. Cleanup file immediately (since data is now memory/serialized)
        if os.path.exists(file_path):
            os.remove(file_path)

        # 5. Chain tasks
        # chain(*tasks) executes them sequentially
        if batches:
            # Use .si() (immutable signature) to preventing passing the result of the previous task
            workflow = chain(*(process_leads_batch.si(account_id, batch) for batch in batches))
            workflow.apply_async()
            
    except Exception as e:
        print(f"Process leads file failed: {e}")
        # Ensure cleanup on error
        if os.path.exists(file_path):
            os.remove(file_path)
