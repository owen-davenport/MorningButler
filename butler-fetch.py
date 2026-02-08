#!/usr/bin/env python3
from flask import Flask, request, redirect, url_for, send_file, jsonify
import sys, os
import json
import requests
from datetime import datetime
from datetime import timezone
import re
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from dateutil import parser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from tzlocal import get_localzone
import time

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

app = Flask(__name__)

BUNDLED_USER_CONFIG = resource_path("user_config.json")
def user_config_path():
    if hasattr(sys, '_MEIPASS'):
        base_dir = os.path.join(os.path.expanduser("~"), ".morning_butler")
        return os.path.join(base_dir, "user_config.json")
    return os.path.join(os.path.abspath("."), "user_config.json")

USER_CONFIG_FILE = user_config_path()
try:
    PACIFIC = ZoneInfo("America/Los_Angeles")
except ZoneInfoNotFoundError:
    PACIFIC = get_localzone()
BASE_URL = "https://sbccd.instructure.com/api/v1"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "canvas-dashboard-local-script"
}

DEFAULT_CONFIG = {
    "canvas": {
        "enabled": False,
        "token": "",
        "token_expiration": "",
        "courses": [],
        "assignments": [],
        "course_aliases": {}
    },
    "weather": {
        "enabled": True
    },
    "news": {
        "enabled": True
    },
    "theme": "auto",
    "location": {
        "method": "zip",
        "zip_code": "",
        "lat": "",
        "lon": ""
    },
    "assignmentFilters": {
        "hideNoDueDate": True,
        "hideCompleted": False,
        "defaultView": "week"
    },
    "emails": {
        "enabled": False,
        "accounts": []
    }
}

def ensure_user_config_exists():
    if not os.path.exists(USER_CONFIG_FILE):
        os.makedirs(os.path.dirname(USER_CONFIG_FILE), exist_ok=True) if os.path.dirname(USER_CONFIG_FILE) else None
        config_to_write = DEFAULT_CONFIG
        if os.path.exists(BUNDLED_USER_CONFIG) and BUNDLED_USER_CONFIG != USER_CONFIG_FILE:
            try:
                with open(BUNDLED_USER_CONFIG, "r", encoding="utf-8") as f:
                    config_to_write = json.load(f)
            except Exception as e:
                print(f"[WARN] Default config load failed: {e}")
                config_to_write = DEFAULT_CONFIG
        with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_to_write, f, indent=4)

# Cache for API responses (in-memory with timestamp)
API_CACHE = {
    "weather": {"data": None, "timestamp": 0},
    "news": {"data": None, "timestamp": 0},
    "emails": {"data": None, "timestamp": 0}
}
CACHE_TTL = 300  # 5 minutes in seconds

# ---------------------------
# Canvas Helpers
# ---------------------------
def normalize_dt(dt_str):
    if not dt_str:
        return datetime.min.replace(tzinfo=timezone.utc)

    dt = parser.isoparse(dt_str)

    # Canvas mixes tz-aware and tz-naive timestamps
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt
    
def get_display_name(full_course_name):
    return shorten_course_name(full_course_name)

def shorten_course_name(full_course_name):
    if not full_course_name:
        return ""
    name = full_course_name.strip()

    # Strip section numbers / trailing identifiers
    name = name.replace("(", " (")
    name = name.split("(")[0].strip()
    name = name.strip(" -")
    name = re.sub(r"\s*-\s*[A-Za-z]?\d{1,4}[A-Za-z]?\b.*$", "", name).strip()

    # Rule 1: detect course code
    m = re.search(r"([A-Za-z]{2,5})[- ]?(\d{1,3}[A-Za-z]?)", name)
    if m:
        return f"{m.group(1).upper()} {m.group(2).upper()}"

    # Rule 2: remove filler words and shorten
    fillers = {"introduction", "intro", "beginning", "fundamentals", "basic", "advanced"}
    words = [w for w in re.split(r"\s+", name) if w]
    cleaned = []
    for w in words:
        if w.lower().strip(",.:-") in fillers:
            continue
        cleaned.append(w.strip(",.:-"))
    if not cleaned:
        cleaned = words

    trimmed = cleaned[:2]
    if len(trimmed) == 2 and len(trimmed[1]) > 6:
        trimmed[1] = trimmed[1][:4].rstrip(".") + "."
    result = " ".join(trimmed).strip()

    # Rule 4: truncate if still long
    if len(result) > 16:
        result = result[:16].rstrip() + "…"
    return result

def test_token(token):
    try:
        r = requests.get(
            f"{BASE_URL}/users/self/profile",
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
            timeout=10
        )
        return r.status_code == 200
    except Exception:
        return False

def get_courses(token):
    try:
        r = requests.get(
            f"{BASE_URL}/courses?enrollment_state=active&per_page=100",
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
            timeout=10
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return []

def get_assignments(course_id, token):
    try:
        r = requests.get(
            f"{BASE_URL}/courses/{course_id}/assignments",
            params={"per_page": 100, "include[]": "submission"},
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
            timeout=10
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return []

def is_real_academic_course(course):
    name = (course.get("name") or "").lower()
    return not any(k in name for k in ["program", "organization", "guardian", "nextup"])

# ---------------------------
# Cache Helper
# ---------------------------
def get_cached(key):
    """Returns cached data if fresh (within TTL), else None"""
    if key in API_CACHE:
        cached = API_CACHE[key]
        if time.time() - cached["timestamp"] < CACHE_TTL:
            return cached["data"]
    return None

def set_cache(key, data):
    """Store data in cache with current timestamp"""
    API_CACHE[key] = {"data": data, "timestamp": time.time()}

# ---------------------------
# Gmail IMAP Helper
# ---------------------------
def _decode_mime_header(value):
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            try:
                decoded.append(part.decode(enc or "utf-8", errors="replace"))
            except Exception:
                decoded.append(part.decode("utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded).strip()

def _extract_snippet(msg, limit=140):
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", "")).lower()
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    text = part.get_payload(decode=True)
                    if not text:
                        continue
                    snippet = text.decode(part.get_content_charset() or "utf-8", errors="replace")
                    return " ".join(snippet.split())[:limit]
                except Exception:
                    continue
    else:
        try:
            text = msg.get_payload(decode=True)
            if text:
                snippet = text.decode(msg.get_content_charset() or "utf-8", errors="replace")
                return " ".join(snippet.split())[:limit]
        except Exception:
            return ""
    return ""

def fetch_gmail_unread(account, limit=5):
    email_addr = (account.get("email") or "").strip()
    app_password = (account.get("app_password") or "").strip()
    host = (account.get("imap_host") or "imap.gmail.com").strip()
    port = int(account.get("imap_port") or 993)
    if not email_addr or not app_password:
        return []

    items = []
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(email_addr, app_password)
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            imap.logout()
            return []
        ids = data[0].split()
        latest_ids = ids[-limit:]
        for msg_id in reversed(latest_ids):
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            from_val = _decode_mime_header(msg.get("From"))
            subject_val = _decode_mime_header(msg.get("Subject"))
            date_val = msg.get("Date")
            timestamp = ""
            if date_val:
                try:
                    timestamp = parsedate_to_datetime(date_val).isoformat()
                except Exception:
                    timestamp = date_val
            items.append({
                "sender": from_val,
                "subject": subject_val,
                "snippet": _extract_snippet(msg),
                "timestamp": timestamp
            })
        imap.logout()
    except Exception as e:
        print(f"[DEBUG] Gmail IMAP error: {e}")
        return []
    return items

def get_gmail_data(config):
    accounts = (config.get("emails") or {}).get("accounts", [])
    if not accounts:
        return []
    results = []
    for acc in accounts:
        email_addr = (acc.get("email") or "").lower()
        host = (acc.get("imap_host") or "imap.gmail.com").lower()
        if "gmail.com" in email_addr or "gmail" in host:
            items = fetch_gmail_unread(acc, limit=5)
            for item in items:
                item["account"] = acc.get("label") or acc.get("email") or "Gmail"
            results.extend(items)
    return results[:5]

# ---------------------------
# Weather Helper (Open-Meteo)
# ---------------------------
def get_weather(zip_code, lat=None, lon=None):
    """Fetch weather from Open-Meteo using ZIP code (no API key required)"""
    cached = get_cached("weather")
    if cached:
        return cached
    
    def weather_code_to_text(code):
        mapping = {
            0: "Clear",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Drizzle",
            55: "Dense drizzle",
            56: "Freezing drizzle",
            57: "Freezing drizzle",
            61: "Slight rain",
            63: "Rain",
            65: "Heavy rain",
            66: "Freezing rain",
            67: "Freezing rain",
            71: "Slight snow",
            73: "Snow",
            75: "Heavy snow",
            77: "Snow grains",
            80: "Rain showers",
            81: "Rain showers",
            82: "Violent rain showers",
            85: "Snow showers",
            86: "Snow showers",
            95: "Thunderstorm",
            96: "Thunderstorm with hail",
            99: "Thunderstorm with hail"
        }
        return mapping.get(code, "Unknown")

    def resolve_timezone(tz_name):
        if tz_name:
            try:
                return ZoneInfo(tz_name), tz_name
            except ZoneInfoNotFoundError:
                local_tz = get_localzone()
                return local_tz, str(local_tz)
        try:
            fallback_tz = ZoneInfo("America/Los_Angeles")
            return fallback_tz, "America/Los_Angeles"
        except ZoneInfoNotFoundError:
            local_tz = get_localzone()
            return local_tz, str(local_tz)

    def with_local_time(payload, tz_name=None):
        tz, tz_id = resolve_timezone(tz_name)
        payload.setdefault("timezone", tz_id)
        payload["local_time"] = datetime.now(tz).isoformat()
        return payload

    try:
        if not os.path.exists(USER_CONFIG_FILE):
            print("[WARN] user_config.json missing; weather may be unavailable.")
    except Exception as e:
        print(f"[WARN] Config load error: {e}")
    
    try:
        if not zip_code:
            return with_local_time({"temp": "N/A", "condition": "No location set"})
        
        location_name = ""
        if lat is None or lon is None:
            # Step 1: Geocode ZIP code to lat/lon using Open-Meteo Geocoding API
            geo_r = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": zip_code, "count": 1, "language": "en"},
                timeout=5
            )
            
            if geo_r.status_code != 200 or not geo_r.json().get("results"):
                return with_local_time({"temp": "N/A", "condition": "Location not found"})
            
            result = geo_r.json()["results"][0]
            lat = result.get("latitude")
            lon = result.get("longitude")
            location_name = result.get("name", "")
        
        # Step 2: Fetch weather from Open-Meteo Weather API
        weather_r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,relative_humidity_2m",
                "temperature_unit": "fahrenheit",
                "timezone": "auto"
            },
            timeout=5
        )
        
        if weather_r.status_code == 200:
            data = weather_r.json()
            current = data.get("current", {})
            code = current.get("weather_code")
            if code is not None:
                try:
                    code = int(code)
                except Exception:
                    code = None
            weather = {
                "temp": int(current.get("temperature_2m")) if current.get("temperature_2m") is not None else "N/A",
                "condition": weather_code_to_text(code) if code is not None else "Unknown",
                "humidity": current.get("relative_humidity_2m"),
                "location": location_name,
                "timezone": data.get("timezone")
            }
            weather = with_local_time(weather, weather.get("timezone"))
            set_cache("weather", weather)
            return weather
        else:
            return with_local_time({"temp": "N/A", "condition": "Unable to fetch"})
    except Exception as e:
        print(f"[DEBUG] Weather fetch error: {e}")
        return with_local_time({"temp": "N/A", "condition": "Error fetching weather"})

# ---------------------------
# News Helper (RSS-Based)
# ---------------------------
def get_news():
    """Fetch news from RSS feeds (no API key required)"""
    cached = get_cached("news")
    if cached:
        return cached
    
    try:
        import xml.etree.ElementTree as ET
        
        # Default RSS feed sources
        feeds = [
            ("Reuters", "https://feeds.reuters.com/reuters/worldNews"),
            ("AP News", "https://apnews.com/apf-topnews?format=rss"),
            ("NPR", "https://feeds.npr.org/1001/rss.xml"),
            ("BBC", "http://feeds.bbci.co.uk/news/rss.xml"),
            ("The Guardian", "https://www.theguardian.com/world/rss"),
            ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml")
        ]
        
        items = []
        for source_name, feed_url in feeds:
            try:
                r = requests.get(feed_url, headers=HEADERS, timeout=5)
                if r.status_code == 200:
                    root = ET.fromstring(r.content)
                    # Parse RSS items
                    rss_items = root.findall(".//item")
                    if rss_items:
                        for item in rss_items[:2]:  # Top 2 from each feed
                            title_elem = item.find("title")
                            link_elem = item.find("link")
                            pub_date_elem = item.find("pubDate")
                            
                            if title_elem is not None and link_elem is not None:
                                items.append({
                                    "title": title_elem.text or "Untitled",
                                    "source": source_name,
                                    "url": link_elem.text or "",
                                    "published": pub_date_elem.text if pub_date_elem is not None else ""
                                })
                    else:
                        # Atom fallback
                        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:2]:
                            title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                            link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
                            updated_elem = entry.find("{http://www.w3.org/2005/Atom}updated")
                            href = link_elem.get("href") if link_elem is not None else ""
                            items.append({
                                "title": title_elem.text if title_elem is not None else "Untitled",
                                "source": source_name,
                                "url": href or "",
                                "published": updated_elem.text if updated_elem is not None else ""
                            })
            except Exception as e:
                print(f"[DEBUG] Error fetching {source_name} RSS: {e}")
                continue
        
        # Sort by published date (newest first) and limit to 5
        try:
            items_sorted = sorted(
                items,
                key=lambda x: parser.isoparse(x["published"]) if x["published"] else datetime.min.replace(tzinfo=timezone.utc),
                reverse=True
            )[:5]
        except:
            items_sorted = items[:5]
        
        # Clean up response
        news = [
            {
                "title": item["title"],
                "source": item["source"],
                "url": item["url"]
            }
            for item in items_sorted
        ]
        
        response = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "items": news
        }
        
        set_cache("news", response)
        return response
    except Exception as e:
        print(f"[DEBUG] News fetch error: {e}")
        # Return cached data if available, else empty
        cached = get_cached("news")
        return cached if cached else {"updated_at": datetime.now(timezone.utc).isoformat(), "items": []}

# ---------------------------
# Routes
# ---------------------------
@app.route("/")
def index():
    ensure_user_config_exists()
    # Check if user has configured Canvas
    if os.path.exists(USER_CONFIG_FILE):
        with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        canvas_config = config.get("canvas", {})
        if canvas_config.get("enabled") and canvas_config.get("token"):
            # User has Canvas configured, redirect to dashboard
            return redirect(url_for("dashboard"))
    
    # No config or Canvas not enabled, show welcome
    return send_file(resource_path("templates/welcome.html"))

@app.route("/welcome")
def welcome():
    return send_file(resource_path("templates/welcome.html"))

@app.route("/dashboard")
def dashboard():
    return send_file(resource_path("templates/dashboard.html"))

@app.route("/user_config.json")
def get_user_config():
    ensure_user_config_exists()
    return send_file(USER_CONFIG_FILE, mimetype="application/json")

@app.route("/save_preferences", methods=["POST"])
def save_preferences():
    canvas_enabled = request.form.get("canvas-reminders") == "on"
    canvas_token = request.form.get("canvas-API-token", "").strip()
    canvas_expiration = request.form.get("canvas-API-token-expiration", "").strip()
    zip_code = request.form.get("zip-code", "").strip()
    location_lat = request.form.get("location-lat", "").strip()
    location_lon = request.form.get("location-lon", "").strip()
    theme = request.form.get("theme", "auto")
    location_method = request.form.get("location-method", "zip")
    weather_enabled = request.form.get("weather-enabled") == "on"
    news_enabled = request.form.get("news-enabled") == "on"
    emails_enabled = request.form.get("recent-emails") == "on"
    
    # NEW: Capture assignment filters
    hide_no_due_date = request.form.get("hide-no-due-date") == "on"
    hide_completed = request.form.get("hide-completed") == "on"
    default_view = request.form.get("default-assignment-view", "week")

    # Parse course aliases
    course_aliases = {}
    for key, value in request.form.items():
        if key.startswith("course-alias-"):
            course_id = key.replace("course-alias-", "")
            alias = value.strip()
            if alias:
                course_aliases[course_id] = alias

    # Parse email accounts
    email_accounts = []
    for key, value in request.form.items():
        if key.startswith("email-account-label-"):
            suffix = key.replace("email-account-label-", "")
            email_key = f"email-address-{suffix}"
            pass_key = f"email-app-password-{suffix}"
            host_key = f"email-imap-host-{suffix}"
            label = value.strip()
            email_addr = request.form.get(email_key, "").strip()
            app_password = request.form.get(pass_key, "").strip()
            imap_host = request.form.get(host_key, "").strip()
            if label or email_addr or app_password:
                email_accounts.append({
                    "label": label,
                    "email": email_addr,
                    "app_password": app_password,
                    "imap_host": imap_host
                })

    config = {
        "canvas": {
            "enabled": canvas_enabled,
            "token": canvas_token,
            "token_expiration": canvas_expiration,
            "courses": [],
            "assignments": [],
            "course_aliases": course_aliases
        },
        "weather": {
            "enabled": weather_enabled
        },
        "news": {
            "enabled": news_enabled
        },
        "theme": theme,
        "location": {
            "method": location_method,
            "zip_code": zip_code,
            "lat": location_lat,
            "lon": location_lon
        },
        "assignmentFilters": {
            "hideNoDueDate": hide_no_due_date,
            "hideCompleted": hide_completed,
            "defaultView": default_view
        },
        "emails": {
            "enabled": emails_enabled,
            "accounts": email_accounts
        }
    }

    # Fetch Canvas courses & assignments if token is valid
    if canvas_enabled and canvas_token:
        if test_token(canvas_token):
            courses = get_courses(canvas_token)
            assignments_all = []
            for c in courses:
                if is_real_academic_course(c):
                    alias = course_aliases.get(str(c.get("id")), "")
                    c_display = alias if alias else get_display_name(c.get("name", "Unnamed Course"))
                    config["canvas"]["courses"].append({
                        "id": c["id"],
                        "name": c_display,
                        "full_name": c.get("name", "Unnamed Course")
                    })
                    assignment_list = get_assignments(c["id"], canvas_token)
                    for a in assignment_list:
                        assignments_all.append({
                            "course": c_display,
                            "name": a.get("name", "Unnamed Assignment"),
                            "due_at": a.get("due_at"),
                            "submission": a.get("submission", {})
                        })
            # Sort by due date, upcoming first
            config["canvas"]["assignments"] = sorted(
                assignments_all,
                key=lambda x: normalize_dt(x["due_at"])
            )
            print(f"[DEBUG] Fetched {len(assignments_all)} assignments for user")
        else:
            print("[DEBUG] Canvas token invalid")

    # ✅ SAVE TO DISK
    with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

    # ✅ SEND USER TO DASHBOARD
    return redirect(url_for("dashboard"))

@app.route("/dashboard.js")
def dashboard_js():
    return send_file(resource_path("static/dashboard.js"), mimetype="application/javascript")

@app.route("/weather")
def weather():
    if not os.path.exists(USER_CONFIG_FILE):
        return jsonify({"temp": "N/A", "condition": "Not configured"})
    
    with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    loc = config.get("location", {})
    zip_code = loc.get("zip_code", "")
    lat = loc.get("lat") or None
    lon = loc.get("lon") or None
    weather_data = get_weather(zip_code, lat=lat, lon=lon)
    return jsonify(weather_data)

@app.route("/news")
def news():
    news_data = get_news()
    return jsonify(news_data)

@app.route("/gmail_data")
def gmail_data():
    if not os.path.exists(USER_CONFIG_FILE):
        return jsonify({"items": []})
    with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    items = get_gmail_data(config)
    return jsonify({"items": items})

@app.route("/canvas_data")
def canvas_data():
    if not os.path.exists(USER_CONFIG_FILE):
        return jsonify({"assignments": [], "announcements": []})

    with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    canvas_config = config.get("canvas", {})
    token = canvas_config.get("token")
    if not token or not test_token(token):
        return jsonify({"assignments": [], "announcements": []})

    cache_key = f"canvas:{token}"
    cached = get_cached(cache_key)
    if cached:
        return jsonify(cached)

    # Fetch courses
    courses_raw = get_courses(token)
    courses = [c for c in courses_raw if is_real_academic_course(c)]
    aliases = (config.get("canvas", {}) or {}).get("course_aliases", {})

    # Assignments
    assignments = []
    for c in courses:
        a_list = get_assignments(c["id"], token)
        for a in a_list:
            display_name = aliases.get(str(c.get("id")), "") or get_display_name(c["name"])
            assignments.append({
                "course": display_name,
                "name": a.get("name", "Unnamed Assignment"),
                "due_at": a.get("due_at"),
                "submission": a.get("submission", {})
            })

    # Sort by due date
    assignments = sorted(
        assignments,
        key=lambda x: normalize_dt(x["due_at"])
    )

    # Announcements
    announcements = []
    for c in courses:
        url = f"{BASE_URL}/courses/{c['id']}/discussion_topics"
        params = {"only_announcements": "true", "per_page": 50}
        course_announcements = []
        while url:
            try:
                r = requests.get(
                    url,
                    headers={**HEADERS, "Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=10
                )
                if r.status_code != 200:
                    break
                batch = r.json()
                course_announcements.extend(batch)
                url = r.links.get("next", {}).get("url")
                params = None
                time.sleep(0.1)
            except Exception:
                break

        # Deduplicate and pick latest 2 announcements
        dedup = {}
        for a in course_announcements:
            aid = a.get("id")
            if aid and aid not in dedup:
                dedup[aid] = a
        latest = sorted(
            dedup.values(),
            key=lambda x: normalize_dt(x.get("posted_at")),
            reverse=True
        )[:2]

        for a in latest:
            display_name = aliases.get(str(c.get("id")), "") or get_display_name(c["name"])
            announcements.append({
                "course": display_name,
                "title": a.get("title", "Untitled"),
                "posted": a.get("posted_at")
            })

    payload = {"assignments": assignments, "announcements": announcements}
    set_cache(cache_key, payload)
    return jsonify(payload)

# ---------------------------
# Run server
# ---------------------------
if __name__ == "__main__":
    try:
        os.makedirs(os.path.dirname(USER_CONFIG_FILE), exist_ok=True) if os.path.dirname(USER_CONFIG_FILE) else None
        import threading
        import webbrowser

        try:
            if not os.path.exists(USER_CONFIG_FILE):
                print("[WARN] user_config.json missing; weather will be unavailable.")
            else:
                with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
        except Exception as e:
            print(f"[WARN] Startup config check failed: {e}")

        def _open_browser():
            time.sleep(1)
            webbrowser.open("http://127.0.0.1:5050")

        try:
            threading.Thread(target=_open_browser, daemon=True).start()
        except Exception as e:
            print(f"[WARN] Browser auto-open failed: {e}")
    except Exception as e:
        print(f"[WARN] Startup wrapper error: {e}")

    try:
        app.run(debug=False, port=5050)
    except Exception as e:
        print(f"[ERROR] Server failed to start: {e}")
