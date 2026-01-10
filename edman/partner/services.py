import time
import uuid
import logging
import threading
import os
from django.conf import settings
from django.core.cache import cache
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

class AuthSession:
    STATUS_INIT = 'INIT'
    STATUS_RUNNING = 'RUNNING'
    STATUS_OTP_REQUIRED = 'OTP_REQUIRED'
    STATUS_SUCCESS = 'SUCCESS'
    STATUS_FAILED = 'FAILED'
    
    CACHE_KEY_PREFIX = "partner_auth_session_"
    OTP_KEY_PREFIX = "partner_auth_otp_"
    RESULT_KEY_PREFIX = "partner_auth_result_"

    def __init__(self, auth_url, login, password):
        self.session_id = str(uuid.uuid4())
        self.auth_url = auth_url
        self.login = login
        self.password = password
        self._thread = None
        # Initialize logs
        cache.set(f"partner_auth_logs_{self.session_id}", [], timeout=600)
        
        # Ensure debug directory exists
        try:
            os.makedirs("debug_dumps", exist_ok=True)
        except Exception:
            pass
    
    def _dump_page(self, page, name):
        """Save page content for debugging"""
        if not getattr(settings, 'PARTNER_AUTH_DEBUG_DUMPS', False):
            return

        try:
            timestamp = int(time.time())
            filename = f"debug_dumps/{timestamp}_{self.session_id[:5]}_{name}.html"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(page.content())
            self._log(f"Saved debug dump: {filename}")
        except Exception as e:
            self._log(f"Failed to save dump {name}: {e}")

    def _log(self, message):
        """Append a log message to the session logs"""
        key = f"partner_auth_logs_{self.session_id}"
        logs = cache.get(key, [])
        timestamp = time.strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        logs.append(log_entry)
        # Keep last 50 lines
        if len(logs) > 50:
            logs = logs[-50:]
        cache.set(key, logs, timeout=600)
        # Also update status message for simple display
        self._set_status(self._get_current_status(), message)

    def _get_current_status(self):
        data = cache.get(f"{self.CACHE_KEY_PREFIX}{self.session_id}")
        return data['status'] if data else self.STATUS_INIT

    def start(self):
        self._set_status(self.STATUS_INIT, "Initializing...")
        self._thread = threading.Thread(target=self._run_auth_process)
        self._thread.daemon = True
        self._thread.start()
        return self.session_id

    def _set_status(self, status, message=None):
        cache.set(f"{self.CACHE_KEY_PREFIX}{self.session_id}", {
            'status': status,
            'message': message
        }, timeout=600)

    def _save_result(self, session_data):
        cache.set(f"{self.RESULT_KEY_PREFIX}{self.session_id}", session_data, timeout=600)

    def _wait_for_otp(self):
        # Wait up to 2 minutes for OTP
        for _ in range(120):
            otp = cache.get(f"{self.OTP_KEY_PREFIX}{self.session_id}")
            if otp:
                return otp
            time.sleep(1)
        return None

    def _run_auth_process(self):
        self._set_status(self.STATUS_RUNNING, "Starting browser...")
        playwright = None
        browser = None
        try:
            show_browser = getattr(settings, 'PARTNER_AUTH_SHOW_BROWSER', False)
            headless_mode = not show_browser
            self._log(f"Launching browser (Headless: {headless_mode})...")
            playwright = sync_playwright().start()
            
            # Add arguments to reduce bot detection
            browser = playwright.chromium.launch(
                headless=headless_mode, 
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    '--window-position=0,0',
                    '--ignore-certificate-errors',
                    '--ignore-ssl-errors',
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                ]
            )
            
            # Create context with a realistic User Agent
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                viewport={'width': 1366, 'height': 768},
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False,
                locale='en-US',
                timezone_id='America/New_York'
            )
            
            # Inject script to hide webdriver property
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            page = context.new_page()

            self._log(f"Navigating to {self.auth_url}...")
            page.goto(self.auth_url)
            
            # Wait for content to load
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
                self._log("Page loaded (networkidle).")
            except Exception:
                self._log("Page load timeout (networkidle), continuing anyway around...")

            self._dump_page(page, "01_initial_load")

            # Check for Captcha (SmartCaptcha / Checkbox)
            if "SmartCaptcha" in page.content() or "checkbox-captcha" in page.content() or "robot" in page.title().lower():
                self._log("Captcha detected!")
                self._set_status(self.STATUS_RUNNING, "Solving Captcha...")
                self._dump_page(page, "01_captcha_found")
                
                try:
                    # Specific handling for the SmartCaptcha button found in logs
                    # Ideally we want to click the 'I'm not a robot' checkbox area
                    self._log("Attempting to click captcha...")
                    
                    # Give it a moment to render fully
                    time.sleep(2)
                    
                    # Try to click the submit button directly as it has the event listener
                    button = page.locator('#js-button, .CheckboxCaptcha-Button').first
                    
                    if button.is_visible():
                         self._log("Found captcha button. Clicking...")
                         try:
                             button.hover()
                             time.sleep(0.2)
                         except:
                             pass
                         button.click(force=True)
                         time.sleep(1)
                    else:
                        # Fallback to the checkbox visual element
                        self._log("Button not visible, trying checkbox div...")
                        page.locator('.CheckboxCaptcha-Checkbox').first.click(force=True)

                except Exception as e:
                    self._log(f"Error interacting with captcha: {e}")
                
                self._log("Waiting for Captcha resolution...")
                
                # Loop to check if we passed it or need to retry
                for attempt in range(10):
                    if page.locator('input[name="login"]').is_visible() or "passport" in page.url:
                        self._log("Captcha cleared! Login input found.")
                        self._dump_page(page, "01_captcha_cleared")
                        break
                    
                    # Check if the captcha is still there and unchecked
                    if attempt % 3 == 0 and attempt > 0:
                        self._log("Still on captcha page. Retrying click...")
                        try:
                            # Try executing JS click which is more reliable for hidden inputs
                            page.evaluate("document.getElementById('js-button').click()")
                        except:
                            page.locator('.CheckboxCaptcha-Checkbox').first.click(force=True)
                    
                    time.sleep(2)
                
                # Check outcome
                if not page.locator('input[name="login"]').is_visible():
                     self._log("Could not clear captcha automatically. Pending manual solution...")
                     try:
                         page.wait_for_selector('input[name="login"]', state='visible', timeout=20000)
                     except:
                         pass

            # Identification generic logic (tuned for target Partner)
            # Step 1: Login
            self._set_status(self.STATUS_RUNNING, "Entering login...")
            login_input = None
            
            self._log("Inspecting page for login inputs...")
            # Wait a bit for JS to render forms
            time.sleep(2)
            self._dump_page(page, "02_before_login_search")
            
            # Debug: Log all inputs found
            try:
                inputs = page.locator('input').all()
                self._log(f"Found {len(inputs)} input elements.")
                for idx, inp in enumerate(inputs):
                    try:
                        attrs = page.evaluate('(el) => { return {name: el.name, id: el.id, type: el.type, placeholder: el.placeholder} }', inp)
                        self._log(f"Input #{idx}: {attrs}")
                    except:
                        pass
            except Exception as e:
                self._log(f"Error inspecting inputs: {e}")

            # Try to find the login input with multiple selectors
            selectors = [
                'input[name="login"]',
                'input#passp-field-login',
                'input[type="email"]',
                'input[type="text"]',
                'input[type="tel"]',
                'input[autocomplete="username"]'
            ]

            # Auto-detect toggles between Phone/Email and handle "More" button for alternative auth
            try:
                is_phone = self.login.replace('+','').replace('-','').strip().isdigit()

                # Special case: If we are not a phone login, but the page forces Phone input (common in Yandex SplitAddUser)
                if not is_phone:
                    # Check if 'More' button is visible which hides 'Log in with username'
                    more_btn = page.locator('button[data-testid="split-add-user-more-button"]')
                    if more_btn.count() > 0 and more_btn.first.is_visible():
                         self._log("Found 'More' button. Checking if we need to switch to username/email flow...")
                         # If we only see a phone input, or if there is no explicit email toggle
                         phone_input_visible = page.locator('input[type="tel"]').count() > 0 and page.locator('input[type="tel"]').first.is_visible()
                         email_input_visible = page.locator('input[name="login"]').count() > 0 and page.locator('input[name="login"]').first.is_visible()
                         
                         if phone_input_visible and not email_input_visible:
                             self._log("Only phone input visible, but login is not a phone. Clicking 'More'...")
                             more_btn.first.click()
                             time.sleep(1)
                             
                             # Look for "Log in with username" option
                             # Usually it has text "Log in with username" or similar, or data-testid="auth-via-login"
                             # Try multiple strategies to find the menu item
                             menu_item = page.locator('button[data-testid="auth-via-login"]').or_(
                                         page.locator('li[data-testid="auth-via-login"]')).or_(
                                         page.get_by_text("Log in with username", exact=False)).or_(
                                         page.get_by_text("Войти по логину", exact=False)).or_(
                                         page.get_by_text("Log in with email", exact=False))
                             
                             if menu_item.count() > 0 and menu_item.first.is_visible():
                                 self._log("Found 'Log in with username' menu item. Clicking...")
                                 menu_item.first.click()
                                 time.sleep(1)
                             else:
                                 self._log("Could not find 'Log in with username' item in menu.")


                # Look for radio/toggle with value 'EMAIL' or 'PHONE'
                # Also try specific test ids
                email_toggle = page.locator('input[type="radio"][value="EMAIL"]').or_(page.locator('input[data-testid="add-user-email-option"]'))
                phone_toggle = page.locator('input[type="radio"][value="PHONE"]').or_(page.locator('input[data-testid="add-user-phone-option"]'))
                
                
                if is_phone:
                    if phone_toggle.count() > 0:
                        self._log("Found Phone toggle. Activating...")
                        # Click the label or the input
                        try:
                            # Try clicking the parent label to be safe (often the input is hidden)
                            phone_toggle.first.locator('xpath=..').click(force=True)
                        except:
                            phone_toggle.first.click(force=True)
                        time.sleep(1)
                else:
                    if email_toggle.count() > 0:
                        self._log("Found Email toggle. Activating...")
                        try:
                            email_toggle.first.locator('xpath=..').click(force=True)
                        except:
                            email_toggle.first.click(force=True)
                        time.sleep(1)
            except Exception as e:
                self._log(f"Toggle detection error: {e}")
            
            for selector in selectors:
                if page.locator(selector).is_visible():
                    login_input = page.locator(selector)
                    self._log(f"Found login input via: {selector}")
                    break
            
            # If still not found, try searching by placeholder text
            if not login_input:
                self._log("Trying to find by placeholder...")
                try:
                    candidates = page.get_by_placeholder("Phone", exact=False)
                    if candidates.count() > 0 and candidates.first.is_visible():
                        login_input = candidates.first
                        self._log("Found by placeholder 'Phone'")
                except:
                    pass

            if login_input and login_input.is_visible():
                self._log(f"Filling login: {self.login}")
                login_input.fill(self.login)
                self._log("Pressing Enter...")
                page.keyboard.press("Enter")
            else:
                 debug_content = page.content()[:200]
                 self._log(f"CRITICAL: Login input not found. Content start: {debug_content}")
                 self._dump_page(page, "99_login_not_found")
                 raise Exception("Could not find login input field")

            self._log("Waiting for transition...")
            time.sleep(3) # Wait for transition
            self._dump_page(page, "03_after_login_submit")

            # Check if password field appeared
            self._set_status(self.STATUS_RUNNING, "Entering password...")
            
            # Wait for password input
            password_input = None
            pwd_selectors = ['input[name="passwd"]', 'input#passp-field-passwd', 'input[type="password"]']
            
            self._log("Looking for password field...")
            # Try to wait for one of them to appear
            for i in range(10):  # Increased from 5 to 10
                # Check for "Log in with your password" button (Force Password Flow)
                try:
                    pwd_btn = page.locator("button[data-testid='password-btn']")
                    if pwd_btn.is_visible():
                        self._log("Found 'Log in with your password' button. Clicking to force password flow...")
                        pwd_btn.click()
                        time.sleep(2)
                except:
                    pass

                for selector in pwd_selectors:
                    if page.locator(selector).is_visible():
                        password_input = page.locator(selector)
                        self._log(f"Found password input via: {selector}")
                        break
                if password_input:
                    break
                time.sleep(1)
            
            if password_input:
                self._log("Filling password...")
                password_input.fill(self.password)
                page.keyboard.press("Enter")
            else:
                 self._log("Password field not found. Checking if OTP is required immediately or if already logged in.")

            time.sleep(3)
            
            self._log("Checking final state...")
            
            # Loop to check state
            success = False
            for i in range(20): # Increased iterations to 20 (approx 40s) for slow SMS arrival/UI transitions
                try:
                    url = page.url
                    title = page.title()
                    content = page.content()
                except Exception as nav_err:
                    self._log(f"Navigation/Loading in progress... ({str(nav_err)})")
                    time.sleep(2)
                    continue
                
                # Check errors first
                if "Incorrect password" in content or "Неверный пароль" in content:
                     self._log("ERROR: Yandex reported 'Incorrect password'.")
                     self._set_status(self.STATUS_FAILED, "Incorrect Password")
                     # We explicitly DO NOT return here immediately to allow manual correction if user is watching
                     # But we should probably pause longer
                     time.sleep(5)
                
                self._log(f"Check {i}: URL={url}, Title={title}")

                # Check for success (redirect to partner app)
                if "partners-app" in url or "partner" in title.lower():
                    # Success
                    self._log("Success detected!")
                    self._set_status(self.STATUS_RUNNING, "Saving session...")
                    storage_state = context.storage_state()
                    self._save_result(storage_state)
                    # Small wait to ensure storage is flushed
                    time.sleep(1)
                    self._set_status(self.STATUS_SUCCESS, "Authentication successful")
                    return
                
                # Check if we landed on generic Yandex ID page (Logged in successfully but not redirected)
                if "id.yandex." in url and "auth" not in url:
                     self._log(f"Landed on Yandex ID profile. Authenticated! Redirecting to {self.auth_url}...")
                     try:
                         page.goto(self.auth_url)
                         time.sleep(3)
                         continue
                     except Exception as e:
                         self._log(f"Redirect failed: {e}")
                
                # Check for phone confirmation challenge specifically
                if "challenges/phone-confirmation" in url:
                     self._log("Phone confirmation challenge detected.")
                     # Check if we need to click a button to send SMS
                     try:
                         # Look for specific phone confirmation button
                         confirm_btn_next = page.locator('button[data-testid="challenges-phone-confirmation-next"]').first
                         if confirm_btn_next.is_visible():
                             self._log("Found 'challenges-phone-confirmation-next' button. Clicking...")
                             confirm_btn_next.click()
                             time.sleep(2)
                             continue

                         # Look for common 'Confirm' or 'Send' buttons (fallback)
                         confirm_btn = page.locator('button[type="submit"], button[data-testid="submit-button"]').first
                         if confirm_btn.is_visible():
                             btn_text = confirm_btn.text_content().lower()
                             if "sms" in btn_text or "code" in btn_text or "get" in btn_text or "код" in btn_text or "смс" in btn_text or "confirm" in btn_text or "подтвердить" in btn_text:
                                 self._log(f"Found confirmation button '{btn_text}'. Clicking...")
                                 confirm_btn.click()
                                 # Wait for input to appear
                                 time.sleep(2)
                                 continue
                     except Exception as e:
                         self._log(f"Error checking confirm button: {e}")

                # Check for "WebauthnRegStart" (Skip face/fingerprint login)
                if "WebauthnRegStart" in content or "Want to log in with face or fingerprint?" in content:
                     self._log("Detected Webauthn/Biometric promo page. Skipping...")
                     try:
                         # Click "Remind me later" button
                         skip_btn = page.locator('button[data-testid="webauthn-reg-later-button"]').first
                         if skip_btn.is_visible():
                             self._log("Found 'Remind me later' button. Clicking...")
                             skip_btn.click()
                             time.sleep(2)
                             continue
                     except Exception as e:
                         self._log(f"Error skipping Webauthn promo: {e}")

                # Check for OTP Input
                # We need to distinguish between "Button to send SMS" and "Input for SMS"
                # The dump showed buttons with text "Log in with SMS code", triggering false positive.
                
                otp_input_visible = False
                otp_input = None
                
                # Specific selectors for the code input field
                code_selectors = [
                    'input[data-testid="code-field-segment"]', # Segmented input (modern yandex)
                    'input[name="code"]', 
                    'input[type="tel"]',
                    'input[id="passp-field-phoneCode"]',
                    'input[autocomplete="one-time-code"]'
                ]
                
                for sel in code_selectors:
                    # Check count first to avoid strict mode errors for is_visible()
                    if page.locator(sel).count() > 0:
                        # If multiple elements (like segments), check if the first one is visible
                        if page.locator(sel).first.is_visible():
                            otp_input_visible = True
                            otp_input = page.locator(sel)
                            break
                
                if otp_input_visible:
                     # Identify input field for code
                     self._dump_page(page, f"05_otp_needed_{i}")
                     self._set_status(self.STATUS_OTP_REQUIRED, "Enter SMS/Code")
                     
                     code = self._wait_for_otp()
                     if not code:
                         # If timed out waiting for code from system, assume user might have entered it manually?
                         # Or just loop again to see if state changed
                         self._log("No code provided by system yet...")
                     else:
                         self._set_status(self.STATUS_RUNNING, "Submitting code...")
                         self._log(f"Submitting code: {code}")
                         
                         try:
                             # Try filling generic code inputs
                             segmented = page.locator('input[data-testid="code-field-segment"]')
                             if segmented.count() > 0:
                                 self._log("Detected segmented code input. Typing...")
                                 segmented.first.click()
                                 page.keyboard.type(code)
                             elif otp_input:
                                 otp_input.first.fill(code)
                                 # Sometimes enter is needed
                                 page.keyboard.press("Enter")
                            
                             # IMPORTANT: Wait for navigation after entering OTP to avoid "Execution context destroyed" error
                             # in the next loop iteration.
                             self._log("Code entered. Waiting for redirect...")
                             time.sleep(3)
                             try:
                                 page.wait_for_load_state('networkidle', timeout=5000)
                             except:
                                 pass
                                 
                         except Exception as e:
                             self._log(f"Error filling OTP: {e}")
                             # If error, maybe manual entry worked? Continue loop
                
                elif "code" in content.lower() or "sms" in content.lower():
                    # Should we click "Log in via SMS" if password failed?
                    # Only if we are stuck on password error page
                    if "Incorrect password" in content and "Log in with SMS code" in content:
                         sms_btn = page.locator('button[data-testid="auth-by-sms-button"]')
                         if sms_btn.is_visible():
                             self._log("Incorrect password detected. Clicking 'Log in with SMS code' fallback...")
                             sms_btn.click()
                             time.sleep(2)
                             continue



                time.sleep(2)
            
            # If we fall through here, auth failed or timed out
            self._log("Process finished without clear success. Dumping state...")
            self._dump_page(page, "99_final_fail")
            # Keep browser open for a bit to let user see
            time.sleep(10)
            
        except Exception as e:
            self._log(f"Error during auth process: {e}")
            import traceback
            logger.error(traceback.format_exc())
            if getattr(settings, 'PARTNER_AUTH_SHOW_BROWSER', False):
                self._log("DEBUG: Waiting 60s before closing browser...")
                time.sleep(60)
            self._set_status(self.STATUS_FAILED, f"Error: {str(e)}")
        finally:
            if browser:
                try:
                    browser.close()
                except:
                    pass
            if playwright:
                try:
                    playwright.stop()
                except:
                    pass
            # Ensure we don't leave it in RUNNING state, but don't overwrite SUCCESS
            current = self._get_current_status()
            if current == self.STATUS_RUNNING:
                 self._set_status(self.STATUS_FAILED, "Process terminated unexpectedly")

def get_auth_status(session_id):
    data = cache.get(f"{AuthSession.CACHE_KEY_PREFIX}{session_id}")
    if data:
        # Include logs
        logs = cache.get(f"partner_auth_logs_{session_id}", [])
        data['logs'] = logs
    return data

def submit_auth_otp(session_id, code):
    cache.set(f"{AuthSession.OTP_KEY_PREFIX}{session_id}", code, timeout=300)

def get_auth_result(session_id):
    return cache.get(f"{AuthSession.RESULT_KEY_PREFIX}{session_id}")
