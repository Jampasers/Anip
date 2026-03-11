# Made by chaos_automation / spearofchaos
import argparse
import json
import pickle
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from time import sleep
import undetected_chromedriver as uc
import re
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    SessionNotCreatedException,
    StaleElementReferenceException,
    WebDriverException,
)
import getLoginUrl
import os
import random
import string
import requests
from datetime import datetime, timezone

DEFAULT_CHROME_MAJOR = 146
PROFILE_STATE_FILENAME = "profile_state.json"
OUTPUT_FILENAME = "output.txt"


def get_profiles_dir():
    profiles_dir = os.path.abspath("profiles")
    os.makedirs(profiles_dir, exist_ok=True)
    return profiles_dir


def get_profile_dir(email):
    return os.path.join(get_profiles_dir(), (email or "default").strip())


def get_profile_state_path(email):
    return os.path.join(get_profile_dir(email), PROFILE_STATE_FILENAME)


def load_profile_state(email):
    state_path = get_profile_state_path(email)
    if not os.path.exists(state_path):
        return {}

    try:
        with open(state_path, "r", encoding="utf-8") as state_file:
            data = json.load(state_file)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"Gagal membaca metadata profile {email}: {exc}")
        return {}


def save_profile_state(email, provider, status="ready", login_details=None):
    state_path = get_profile_state_path(email)
    state = load_profile_state(email)
    state.update(
        {
            "email": email,
            "provider": provider,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if login_details:
        state["last_login_details"] = {
            "mac": login_details.get("mac", ""),
            "rid": login_details.get("rid", ""),
            "wk": login_details.get("wk", ""),
            "requested_name": login_details.get("requested_name", ""),
            "country": login_details.get("country", ""),
            "hash": login_details.get("hash", ""),
            "hash2": login_details.get("hash2", ""),
            "meta": login_details.get("meta", ""),
            "ltoken": login_details.get("ltoken", ""),
            "lua_ready_line": login_details.get("lua_ready_line", ""),
        }

    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, ensure_ascii=True, indent=2)


def profile_has_browser_data(email):
    default_profile_dir = os.path.join(get_profile_dir(email), "Default")
    if not os.path.isdir(default_profile_dir):
        return False

    marker_paths = [
        os.path.join(default_profile_dir, "Preferences"),
        os.path.join(default_profile_dir, "History"),
        os.path.join(default_profile_dir, "Login Data"),
        os.path.join(default_profile_dir, "Web Data"),
        os.path.join(default_profile_dir, "Network", "Cookies"),
    ]
    if any(os.path.exists(path) for path in marker_paths):
        return True

    try:
        next(os.scandir(default_profile_dir))
        return True
    except StopIteration:
        return False


def has_saved_profile(email, provider=None):
    state = load_profile_state(email)
    if provider and state.get("provider") and state.get("provider") != provider:
        return False
    return state.get("status") == "ready" or profile_has_browser_data(email)

# Generate random text for creating GID name
def generate_random_text(length=10):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def normalize_ltoken(token):
    if not token:
        return ""
    try:
        return json.loads(f'"{token}"')
    except json.JSONDecodeError:
        return token.replace("\\/", "/")


def build_lua_ready_output(email, token, login_details):
    login_details = login_details or {}
    mac = login_details.get("mac", "")
    rid = login_details.get("rid", "")
    wk = login_details.get("wk", "")
    normalized_token = normalize_ltoken(token)
    return f"{email}|{mac}:{rid}:{wk}:{normalized_token}"


def save_output_line(email, token, login_details):
    output_line = build_lua_ready_output(email, token, login_details)
    with open(OUTPUT_FILENAME, "a", encoding="utf-8") as output_file:
        output_file.write(output_line + "\n")
    print(f"Saved output ready for login ltoken.lua: {output_line}")
    return output_line

# Setup Chrome options
def setup_chrome_options(proxy, load_capsolver=True, email="default"):
    CAPSOLVER_EXTENSION_PATH = os.path.abspath("Capsolver")
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--profile-directory=Default")
    
    if load_capsolver and os.path.isdir(CAPSOLVER_EXTENSION_PATH):
        chrome_options.add_argument(f"--load-extension={CAPSOLVER_EXTENSION_PATH}")
    chrome_options.add_argument("--lang=en-EN")
    if proxy:
        chrome_options.add_argument(f'--proxy-server={proxy}')
    
    # Save Chrome Profile based on Email
    user_data_dir = get_profile_dir(email)
    chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
    
    return chrome_options

# Initialize WebDriver
def init_driver(proxy, chrome_major=DEFAULT_CHROME_MAJOR, load_capsolver=True, email="default"):
    try:
        print(f"Starting Chrome with pinned major version {chrome_major} for {email}")
        driver = uc.Chrome(options=setup_chrome_options(proxy, load_capsolver, email), version_main=chrome_major, use_subprocess=True)
    except SessionNotCreatedException as e:
        error_text = str(e)
        browser_version = re.search(r"Current browser version is (\d+)\.", error_text)
        if browser_version:
            browser_major = int(browser_version.group(1))
            if browser_major != chrome_major:
                print(f"Chrome/Driver mismatch detected, retrying with Chrome major version {browser_major}")
                driver = uc.Chrome(options=setup_chrome_options(proxy, load_capsolver, email), version_main=browser_major, use_subprocess=True)
            else:
                raise
        else:
            raise
    width = 1024
    height = 768
    driver.set_window_size(width, height)
    return driver

def _first_present_element(driver, selectors):
    for by, selector in selectors:
        elements = driver.find_elements(by, selector)
        for element in elements:
            try:
                if element.is_displayed():
                    return element
            except StaleElementReferenceException:
                continue
    return False

def _first_clickable_element(driver, selectors):
    for by, selector in selectors:
        elements = driver.find_elements(by, selector)
        for element in elements:
            try:
                if element.is_displayed() and element.is_enabled():
                    return element
            except StaleElementReferenceException:
                continue
    return False

def wait_and_send_keys(driver, selectors, value, timeout=20):
    element = WebDriverWait(driver, timeout).until(
        lambda d: _first_present_element(d, selectors)
    )
    try:
        element.clear()
    except Exception:
        pass

    try:
        element.send_keys(value)
    except WebDriverException:
        # Fallback for dynamic Apple fields that sometimes ignore direct send_keys.
        driver.execute_script(
            """
            const el = arguments[0];
            const val = arguments[1];
            el.focus();
            el.value = '';
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            """,
            element,
            value,
        )
    return element

def wait_and_click(driver, selectors, timeout=20):
    element = WebDriverWait(driver, timeout).until(
        lambda d: _first_clickable_element(d, selectors)
    )
    try:
        element.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", element) 
    return element

def switch_to_latest_window(driver):
    handles = driver.window_handles
    if handles:
        driver.switch_to.window(handles[-1])

def wait_document_ready(driver, timeout=20):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )

def ensure_not_blank_page(driver, url_hint, attempts=3):
    for attempt in range(1, attempts + 1):
        switch_to_latest_window(driver)
        try:
            wait_document_ready(driver, timeout=20)
        except TimeoutException:
            pass

        current_url = (driver.current_url or "").lower()
        page_source = (driver.page_source or "").strip()
        if current_url not in ("about:blank", "chrome-error://chromewebdata/") and len(page_source) > 500:
            return

        if attempt < attempts:
            print(f"Detected blank/error page ({current_url}), reloading ({attempt}/{attempts - 1})...")
            target = driver.current_url if driver.current_url and driver.current_url != "about:blank" else url_hint
            driver.get(target)
            sleep(2)

    raise RuntimeError("Failed to load login page (blank/error page after retries).")

def captcha_check(driver):
    try:
        if "accounts.google.com/v3/signin/challenge/recaptcha" in driver.current_url:
            reCAPTCHA_frame = WebDriverWait(driver, 10).until(
                EC.frame_to_be_available_and_switch_to_it((By.XPATH, '//iframe[@title="reCAPTCHA"]'))
            )
            print("Switched to reCAPTCHA frame")
            for _ in range(100):
                if "You are verified" in driver.page_source or "Terverifikasi" in driver.page_source or "diverifikasi" in driver.page_source.lower():
                    print("You are verified")
                    driver.switch_to.default_content()
                    btn = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, '//*[text()="Next" or text()="Berikutnya" or text()="Lanjutkan"]'))
                    )
                    driver.execute_script("arguments[0].click();", btn)
                    print("Submitted the form")
                    break
                else:
                    print("You are not verified yet. Retrying...")
                    sleep(1)
            else:
                print("Verification failed after multiple attempts.")
    except TimeoutException as e:
        print("Timeout occurred:", e)
    except NoSuchElementException as e:
        print("Element not found:", e)
    except Exception as e:
        print("An error occurred:", e)

# Legacy cookie functions removed as we will use Chrome Profiles


def is_growtopia_session_ready(driver):
    page_source = driver.page_source or ""
    page_lower = page_source.lower()
    return (
        "status\":\"success" in page_source
        or "choose your name in growtopia" in page_lower
        or 'id="login-name"' in page_source
    )


def google_credentials_visible(driver):
    selectors = [
        (By.XPATH, '//*[@id="identifierId"]'),
        (By.XPATH, '//*[@id="password"]/div[1]/div/div[1]/input'),
        (By.XPATH, '//*[@id="knowledge-preregistered-email-response"]'),
    ]
    return bool(_first_present_element(driver, selectors))


def try_reuse_google_profile(driver, email):
    switch_to_latest_window(driver)
    if is_growtopia_session_ready(driver):
        print(f"Profile untuk {email} sudah aktif, skip login Gmail.")
        return True

    account_selectors = [
        (By.XPATH, f'//*[@data-identifier="{email}"]'),
        (By.XPATH, f'//*[@data-email="{email}"]'),
        (By.XPATH, f'//*[contains(@aria-label, "{email}")]'),
        (By.XPATH, f'//div[@role="link" or @role="button"][.//*[contains(text(), "{email}")]]'),
    ]
    fallback_account_selectors = [
        (By.XPATH, '//div[@data-identifier]'),
        (By.XPATH, '//li[@data-identifier]'),
        (By.XPATH, '//div[@role="link"][.//*[@data-identifier]]'),
    ]
    continue_selectors = [
        (By.XPATH, '//*[contains(text(), "Continue as") or contains(text(), "Lanjutkan sebagai")]'),
        (By.XPATH, '//*[contains(text(), "Continue") or contains(text(), "Lanjutkan")]'),
        (By.XPATH, '//*[contains(text(), "Authorize") or contains(text(), "Izinkan")]'),
    ]

    clicked = None
    try:
        clicked = _first_clickable_element(driver, account_selectors)
        if not clicked and has_saved_profile(email, provider="google"):
            clicked = _first_clickable_element(driver, fallback_account_selectors)
        if not clicked:
            clicked = _first_clickable_element(driver, continue_selectors)
        if not clicked:
            return False

        label = clicked.text.strip() or email or "saved Google profile"
        driver.execute_script("arguments[0].click();", clicked)
        print(f"Menggunakan profile Google tersimpan: {label}")
        sleep(3)
        captcha_check(driver)
    except Exception as exc:
        print(f"Gagal memakai profile Google tersimpan untuk {email}: {exc}")
        return False

    try:
        WebDriverWait(driver, 15).until(
            lambda d: is_growtopia_session_ready(d) or google_credentials_visible(d)
        )
    except TimeoutException:
        pass

    if google_credentials_visible(driver):
        print(f"Session Google untuk {email} meminta login ulang.")
        return False

    current_url = (driver.current_url or "").lower()
    if "accounts.google.com" in current_url and _first_clickable_element(driver, account_selectors + fallback_account_selectors):
        print(f"Profile Google untuk {email} belum berhasil lanjut otomatis, fallback ke login biasa.")
        return False

    return True

# Google login process
def login_google(driver, email, password, recovery_mail, login_link):
    if is_growtopia_session_ready(driver):
        print(f"Profile untuk {email} sudah berhasil aktif, skip pengisian password.")
        return True

    if has_saved_profile(email, provider="google"):
        print(f"Profile Google tersimpan ditemukan untuk {email}, mencoba authorize otomatis...")

    if try_reuse_google_profile(driver, email):
        return True

    try:
        try:
            email_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="identifierId"]'))
            )
            print("Menunggu 10 detik sebelum mengetik email...")
            sleep(10)
            for char in email:
                email_field.send_keys(char)
                sleep(random.uniform(0.1, 0.3))
            print("Email sent")

            driver.switch_to.default_content()
            btn = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="identifierNext"]/div/button/span'))
            )
            driver.execute_script("arguments[0].click();", btn)
            sleep(5)
            captcha_check(driver)
        except TimeoutException:
            pass

        try:
            pwd_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="password"]/div[1]/div/div[1]/input'))
            )
            print("Menunggu 10 detik sebelum mengetik password...")
            sleep(10)
            for char in password:
                pwd_field.send_keys(char)
                sleep(random.uniform(0.1, 0.3))
            print("Password sent")

            btn2 = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="passwordNext"]/div/button/span'))
            )
            driver.execute_script("arguments[0].click();", btn2)
            sleep(5)
        except TimeoutException:
            pass

        if "Choose how you want to sign in" in driver.page_source or "Pilih cara" in driver.page_source:
            if not recovery_mail:
                raise ValueError("Google asked for recovery email but -recoverymail was not provided.")
            btn = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '//*[contains(text(),"Confirm your recovery email") or contains(text(),"Konfirmasi email pemulihan")]'))
            )
            driver.execute_script("arguments[0].click();", btn)
            sleep(3)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="knowledge-preregistered-email-response"]'))
            ).send_keys(recovery_mail)
            sleep(3)
            btn_next = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '//*[text()="Next" or text()="Berikutnya" or text()="Lanjutkan"]'))
            )
            driver.execute_script("arguments[0].click();", btn_next)
            sleep(3)
    except Exception as e:
        print("An error occurred during Google login:", e)
        return False
    return True

# Apple login process
def login_apple(driver, email, password, login_link):
    email_fields = [
        (By.ID, "account_name_text_field"),
        (By.CSS_SELECTOR, 'input[name="accountName"]'),
        (By.CSS_SELECTOR, 'input[type="email"]'),
    ]
    password_fields = [
        (By.ID, "password_text_field"),
        (By.CSS_SELECTOR, 'input[name="password"]'),
        (By.CSS_SELECTOR, 'input[type="password"]'),
    ]
    submit_buttons = [
        (By.ID, "sign-in"),
        (By.CSS_SELECTOR, 'button[type="submit"]'),
        (By.CSS_SELECTOR, '[role="button"][id="sign-in"]'),
        (By.XPATH, '//button[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "sign in")]'),
    ]
    trust_buttons = [
        (By.ID, "trust-browser-button"),
        (By.ID, "trust-browser"),
        (By.CSS_SELECTOR, 'button[id*="trust"]'),
        (By.XPATH, '//button[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "trust")]'),
        (By.XPATH, '//button[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "continue")]'),
    ]

    try:
        switch_to_latest_window(driver)
        ensure_not_blank_page(driver, login_link, attempts=3)

        wait_and_send_keys(driver, email_fields, email, timeout=40)
        print("Apple ID sent")

        # Apple can be single-step (email+password together) or two-step (email first).
        password_ready = _first_present_element(driver, password_fields)
        if password_ready:
            wait_and_send_keys(driver, password_fields, password, timeout=15)
            print("Apple password sent")
            wait_and_click(driver, submit_buttons, timeout=20)
        else:
            wait_and_click(driver, submit_buttons, timeout=20)
            sleep(1)
            wait_and_send_keys(driver, password_fields, password, timeout=30)
            print("Apple password sent")
            wait_and_click(driver, submit_buttons, timeout=20)
        print("Apple Sign In clicked")
        sleep(2)

        try:
            wait_and_click(driver, trust_buttons, timeout=20)
            print("Apple Trust Device clicked")
        except TimeoutException:
            print("Trust Device prompt not shown (may appear after 2FA/manual approval).")

        try:
            WebDriverWait(driver, 240).until(
                lambda d: "growtopiagame.com" in d.current_url or "status\":\"success" in d.page_source
            )
            print("Apple authorization finished, continuing token step...")
        except TimeoutException:
            print("Apple authorization timeout: complete 2FA/approval in browser, then script will continue automatically next run.")
    except Exception as e:
        print("An error occurred during Apple login:", e)
        return False
    return True

def login(driver, email, password, recovery_mail, provider, login_link):
    if provider == "apple":
        return login_apple(driver, email, password, login_link)
    return login_google(driver, email, password, recovery_mail, login_link)

# Handle post-login process
def handle_post_login(driver, email, login_details, proxy, provider):
    try:
        # Check for both English and Indonesian texts for Continue button
        buttons = driver.find_elements(By.XPATH, '//span[contains(text(), "Continue") or contains(text(), "Lanjutkan")] | //button[contains(text(), "Continue") or contains(text(), "Lanjutkan")]')
        if buttons:
            WebDriverWait(driver, 5).until(EC.element_to_be_clickable(buttons[0])).click()
        else:
            # Fallback to the original xpath
            WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="yDmH0d"]/c-wiz/div/div[3]/div/div/div[2]/div/div/button/span'))
            ).click()
    except Exception:
        print("Continue button not found")

    try:
        random_text = generate_random_text()

        if "Choose your name in Growtopia" in driver.page_source or "nama" in driver.page_source.lower():
            try:
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="login-name"]'))
                ).send_keys(random_text)
                WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="modalShow"]/div/div/div/div/section/div/div[2]/div/form/div[2]/input'))
                ).click()
            except Exception:
                pass


        WebDriverWait(driver, 10).until(lambda d: "status\":\"success" in d.page_source)

        token_pattern = r'"token":"(.*?)"'
        match = re.search(token_pattern, driver.page_source)
        if match:
            token = normalize_ltoken(match.group(1))
            print(f"Validated token: {token}")
            output_line = save_output_line(email, token, login_details)
            state_details = dict(login_details or {})
            state_details["ltoken"] = token
            state_details["lua_ready_line"] = output_line
            save_profile_state(email, provider, status="ready", login_details=state_details)
            try:
                proxy = str(proxy or "").replace("socks5://", "")
                url = "http://localhost:80/addGmailBot"
                data = {'token': token, 'proxy': proxy}
                requests.post(url, data=data)
            except:
                print("Request not successfull")
        else:
            print("Token not found in the page source.")
    except TimeoutException:
        print("Token not found within the specified wait time.")

# Main function
def main(proxy, email, password, recovery_mail, provider, chrome_major):
    post_body, login_details = getLoginUrl.percent_encode(return_details=True)
    login_link = getLoginUrl.getUrl(post_body, provider=provider)
    print(f"Generated {provider} login link: {login_link}")  # Debugging statement

    while login_link is None:
        print(f"Error: The {provider} login link was not generated.")
        post_body, login_details = getLoginUrl.percent_encode(return_details=True)
        login_link = getLoginUrl.getUrl(post_body, provider=provider)
        print(f"Generated {provider} login link: {login_link}")  # Debugging statement
    load_capsolver = provider == "google"
    driver = init_driver(proxy, chrome_major=chrome_major, load_capsolver=load_capsolver, email=email)
        
    if provider == "google":
        print("Mengahangatkan cookie dengan mengunjungi accounts.google.com...")
        driver.get("https://accounts.google.com")
        sleep(2)
        
    driver.get(login_link)
    if provider == "apple":
        ensure_not_blank_page(driver, login_link, attempts=3)

    if provider == "google":
        if has_saved_profile(email, provider="google"):
            print(f"Profile Google untuk {email} terdeteksi. Script akan prioritas pakai session tersimpan.")
        else:
            print(f"Belum ada profile Google valid untuk {email}. Script akan login penuh lalu simpan session.")

    login_ok = login(driver, email, password, recovery_mail, provider, login_link)
    if not login_ok:
        print("Login flow failed before token step.")
        try:
            driver.quit()
        except Exception:
            pass
        return

    try:
        handle_post_login(driver, email, login_details, proxy, provider)
    finally:
        try:
            print(f"Menyimpan profile untuk {email} (tunggu 5 detik...)")
            sleep(5)
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    import concurrent.futures
    import os

    parser = argparse.ArgumentParser(description='Growtopia Social Login Script')
    parser.add_argument('-proxy', type=str, help='Proxy information', required=False)
    parser.add_argument('-mail', type=str, help='Email address', required=False)
    parser.add_argument('-password', type=str, help='Password', required=False)
    parser.add_argument('-recoverymail', type=str, help='Recovery email address (Google only)', required=False, default="")
    parser.add_argument('-provider', type=str, choices=['google', 'apple'], default='google', help='Login provider')
    parser.add_argument('-chromemajor', type=int, default=DEFAULT_CHROME_MAJOR, help='Pinned Chrome major version')
    
    args = parser.parse_args()

    input_file = "input.txt"

    if args.mail and args.password:
        main(args.proxy, args.mail, args.password, args.recoverymail, args.provider, args.chromemajor)
    else:
        if not os.path.exists(input_file):
            print(f"File {input_file} tidak ditemukan, membuat file baru...")
            with open(input_file, "w") as f:
                pass
            print(f"Silakan isi {input_file} dengan format email:password lalu jalankan ulang script.")
        else:
            with open(input_file, "r") as f:
                lines = f.readlines()
            
            accounts = []
            for line in lines:
                line = line.strip()
                if line and ":" in line:
                    parts = line.split(":", 1)
                    if len(parts) >= 2:
                        accounts.append((parts[0].strip(), parts[1].strip()))
            
            if not accounts:
                print(f"Tidak ada akun di {input_file}. Silakan tambahkan dengan format email:password.")
            else:
                print(f"Ditemukan {len(accounts)} akun. Memulai proses dengan 4 worker...")
                
                import subprocess
                try:
                    subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                
                def worker(account):
                    email, password = account
                    delay = random.uniform(1, 5)
                    print(f"Menunda {delay:.2f} detik sebelum memulai proses untuk email: {email}")
                    sleep(delay)
                    try:
                        main(args.proxy, email, password, args.recoverymail, args.provider, args.chromemajor)
                    except Exception as e:
                        print(f"Error pada {email}: {e}")

                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    executor.map(worker, accounts)
                
                print("Proses selesai.")
