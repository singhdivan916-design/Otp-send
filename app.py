#!/usr/bin/env python3
"""
Premium OTP Sender – with User Login, Credits, and Redeem Codes.
Single‑file Flask app with clean, clear moderate language & glass‑morphism UI.
Optimized for speed: Firestore direct gets, session caching, lazy Firebase init.
"""

import os
import json
import base64
import hashlib
import secrets
import string
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for, flash, g
import requests

# ---------- Firebase & .env Imports ----------
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ---------- Configuration (all from .env) ----------
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY not set in .env file")

    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        raise RuntimeError("ADMIN_USERNAME and ADMIN_PASSWORD must be set in .env")

    # API_KEYS is optional – if not present, the /api/send endpoint will reject all calls
    API_KEYS = [k.strip() for k in os.environ.get('API_KEYS', '').split(',') if k.strip()]
    API_URL = os.environ.get('API_URL', 'https://rishu-sso.vercel.app/rishu')
    OTP_COST = 1

    # Firebase credentials – can be file path, base64 JSON, or raw JSON string
    FIREBASE_CREDENTIALS = os.environ.get('FIREBASE_CREDENTIALS')
    if not FIREBASE_CREDENTIALS:
        raise RuntimeError("FIREBASE_CREDENTIALS not set in .env")

    # Determine if it's a file path
    if os.path.isfile(FIREBASE_CREDENTIALS):
        cred_path = FIREBASE_CREDENTIALS
    else:
        # Try base64 decode
        try:
            decoded = base64.b64decode(FIREBASE_CREDENTIALS).decode('utf-8')
            temp_cred_path = '/tmp/firebase_creds.json'
            with open(temp_cred_path, 'w') as f:
                f.write(decoded)
            cred_path = temp_cred_path
        except Exception:
            # Assume raw JSON string
            try:
                json.loads(FIREBASE_CREDENTIALS)  # validate
                temp_cred_path = '/tmp/firebase_creds.json'
                with open(temp_cred_path, 'w') as f:
                    f.write(FIREBASE_CREDENTIALS)
                cred_path = temp_cred_path
            except:
                raise RuntimeError("FIREBASE_CREDENTIALS must be a file path, base64 JSON, or raw JSON.")

    FIREBASE_CREDENTIALS_PATH = cred_path

app = Flask(__name__)
app.config.from_object(Config)

# ---------- Lazy Firebase Initialization ----------
_firestore_client = None

def get_firestore():
    """Initialize Firebase Admin SDK once and return Firestore client."""
    global _firestore_client
    if _firestore_client is None:
        cred = credentials.Certificate(app.config['FIREBASE_CREDENTIALS_PATH'])
        firebase_admin.initialize_app(cred)
        _firestore_client = firestore.client()
    return _firestore_client

# ---------- Counter for auto‑incrementing IDs ----------
def get_next_id(counter_name):
    """Atomically increment a counter and return the new value."""
    db = get_firestore()
    counter_ref = db.collection('counters').document(counter_name)
    @firestore.transactional
    def increment(transaction):
        snapshot = transaction.get(counter_ref)
        if not snapshot.exists:
            transaction.set(counter_ref, {'value': 1})
            return 1
        else:
            current = snapshot.to_dict().get('value', 0)
            new_value = current + 1
            transaction.update(counter_ref, {'value': new_value})
            return new_value
    transaction = db.transaction()
    return increment(transaction)

# ---------- Database Helpers (Optimized) ----------

def get_user_by_id(user_id):
    """Return a dict representing a user, using direct document fetch."""
    if not isinstance(user_id, int):
        try:
            user_id = int(user_id)
        except:
            return None
    db = get_firestore()
    doc_ref = db.collection('users').document(str(user_id))
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict()
        data['id'] = user_id
        return data
    return None

def get_user_by_username(username):
    db = get_firestore()
    docs = db.collection('users').where('username', '==', username).limit(1).stream()
    for doc in docs:
        data = doc.to_dict()
        data['id'] = data.get('id')
        return data
    return None

def get_user_by_email(email):
    db = get_firestore()
    docs = db.collection('users').where('email', '==', email).limit(1).stream()
    for doc in docs:
        data = doc.to_dict()
        data['id'] = data.get('id')
        return data
    return None

def add_credits(user_id, amount):
    """Add credits to a user's balance."""
    db = get_firestore()
    docs = db.collection('users').where('id', '==', user_id).limit(1).stream()
    for doc in docs:
        ref = doc.reference
        @firestore.transactional
        def update_credits(transaction):
            snapshot = transaction.get(ref)
            if not snapshot.exists:
                return False
            current = snapshot.to_dict().get('credits', 0)
            transaction.update(ref, {'credits': current + amount})
            return True
        transaction = db.transaction()
        return update_credits(transaction)
    return False

def deduct_credit(user_id):
    """Deduct 1 credit if balance >= 1. Returns True on success."""
    db = get_firestore()
    docs = db.collection('users').where('id', '==', user_id).limit(1).stream()
    for doc in docs:
        ref = doc.reference
        @firestore.transactional
        def deduct(transaction):
            snapshot = transaction.get(ref)
            if not snapshot.exists:
                return False
            current = snapshot.to_dict().get('credits', 0)
            if current >= 1:
                transaction.update(ref, {'credits': current - 1})
                return True
            return False
        transaction = db.transaction()
        return deduct(transaction)
    return False

def check_rate_limit(ip):
    """Increment rate limit counter for IP; return True if under limit."""
    db = get_firestore()
    now = datetime.now(timezone.utc)
    ref = db.collection('rate_limits').document(ip)
    @firestore.transactional
    def check_and_update(transaction):
        snapshot = transaction.get(ref)
        if not snapshot.exists:
            reset_time = now + timedelta(hours=1)
            transaction.set(ref, {'count': 1, 'reset_time': reset_time})
            return True
        else:
            data = snapshot.to_dict()
            reset_time = data.get('reset_time')
            if reset_time and reset_time < now:
                new_reset = now + timedelta(hours=1)
                transaction.set(ref, {'count': 1, 'reset_time': new_reset})
                return True
            else:
                count = data.get('count', 0)
                if count >= 5:
                    return False
                else:
                    transaction.update(ref, {'count': count + 1})
                    return True
    transaction = db.transaction()
    return check_and_update(transaction)

def create_user(username, email, password_hash, credits=1):
    """Create a new user document with auto‑incrementing id."""
    db = get_firestore()
    user_id = get_next_id('users')
    user_data = {
        'id': user_id,
        'username': username,
        'email': email,
        'password_hash': password_hash,
        'credits': credits,
        'created_at': firestore.SERVER_TIMESTAMP
    }
    db.collection('users').document(str(user_id)).set(user_data)
    return user_id

# ---------- Password Helpers ----------
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hash_val):
    return hash_password(password) == hash_val

# ---------- Upstream API Call ----------
def send_otp_via_api(email, username=None):
    payload = {"email": email}
    if username:
        payload["username"] = username
    try:
        resp = requests.post(app.config['API_URL'], json=payload, timeout=30)
        if resp.status_code == 200:
            return resp.json(), None
        else:
            return None, f"Status {resp.status_code}: {resp.text[:100]}"
    except Exception as e:
        return None, str(e)

# ---------- Security Decorators ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Admin access is required.', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ---------- Master Shell View UI Template (unchanged) ----------
BASE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{{ title }} – OTP Matrix</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <link href="https://unpkg.com/aos@2.3.1/dist/aos.css" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #02020F;
            --card-bg: rgba(13, 8, 41, 0.45);
            --border-color: rgba(157, 119, 250, 0.2);
            --glow-color: rgba(168, 85, 247, 0.5);
        }
        html { scroll-behavior: smooth; }
        body {
            font-family: 'Poppins', sans-serif;
            background-color: var(--bg-dark);
            color: #E2E1FF;
            overflow-x: hidden;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        #vanta-bg { position: fixed; width: 100%; height: 100%; top: 0; left: 0; z-index: -1; pointer-events: none; }
        .gradient-text {
            background: linear-gradient(135deg, #A5B4FC, #F472B6, #C084FC);
            background-size: 200% auto;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: text-shimmer 4s linear infinite;
        }
        @keyframes text-shimmer { to { background-position: 200% center; } }
        .glass-card {
            background: var(--card-bg);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid var(--border-color);
            border-radius: 1.25rem;
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .glass-card:hover {
            border-color: rgba(157, 119, 250, 0.35);
            box-shadow: 0 12px 40px rgba(139, 92, 246, 0.15);
        }
        .btn-glow {
            background: linear-gradient(90deg, #7C3AED, #4F46E5);
            color: white; border: none; cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 4px 20px rgba(124, 58, 237, 0.3);
            padding: 0.75rem 1.5rem; border-radius: 0.75rem; font-weight: 600;
            display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem;
        }
        .btn-glow:hover { box-shadow: 0 0 25px rgba(167, 139, 250, 0.6); transform: translateY(-1px); }
        .btn-glow:disabled { opacity: 0.6; cursor: not-allowed; transform: none !important; box-shadow: none !important; }
        .form-input {
            background: rgba(10, 5, 36, 0.6);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            color: white; padding: 0.75rem 1rem;
            width: 100%; outline: none; transition: all 0.3s ease;
        }
        .form-input:focus { border-color: var(--glow-color); box-shadow: 0 0 14px rgba(168, 85, 247, 0.4); background: rgba(15, 8, 50, 0.8); }
        
        #toast-container {
            position: fixed; top: 1.5rem; right: 1.5rem;
            z-index: 9999; display: flex; flex-direction: column; gap: 0.75rem;
            max-width: 400px; width: calc(100% - 3rem);
        }
        .toast-item {
            background: rgba(15, 10, 45, 0.85);
            backdrop-filter: blur(12px);
            border-left: 4px solid #8B5CF6;
            border-radius: 0.5rem; padding: 1rem;
            box-shadow: 0 10px 25px rgba(0,0,0,0.4);
            animation: slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            color: #E2E1FF; font-size: 0.9rem;
        }
        @keyframes slideIn { from { transform: translateX(120%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        .toast-success { border-left-color: #10B981; }
        .toast-error   { border-left-color: #EF4444; }
        .toast-info    { border-left-color: #3B82F6; }
        .toast-warning { border-left-color: #F59E0B; }

        .badge { padding: 0.25rem 0.625rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }
        .badge-success { background: rgba(16, 185, 129, 0.15); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-danger  { background: rgba(239, 68, 68, 0.15); color: #F87171; border: 1px solid rgba(239, 68, 68, 0.3); }
        .badge-warning { background: rgba(245, 158, 11, 0.15); color: #FBBF24; border: 1px solid rgba(245, 158, 11, 0.3); }
    </style>
</head>
<body class="bg-[#02020F]">
    <div id="vanta-bg"></div>
    <div id="toast-container"></div>

    <nav class="fixed top-0 left-0 w-full z-50 glass-card !rounded-none !border-x-0 !border-t-0 backdrop-blur-md">
        <div class="max-w-7xl mx-auto px-2 sm:px-6 lg:px-8 h-16 flex justify-between items-center">
            <a href="{{ url_for('home') }}" class="text-base sm:text-xl font-bold gradient-text flex items-center gap-1 sm:gap-2 whitespace-nowrap">
                <i class="fa-solid fa-paper-plane text-purple-400 text-sm sm:text-base"></i><span>OTP Matrix</span>
            </a>
            <div class="flex items-center gap-1.5 sm:gap-5">
                {% if session.user_id %}
                    <div class="bg-purple-950/40 px-2 py-0.5 sm:px-3 sm:py-1 rounded-lg border border-purple-800/30 text-[10px] sm:text-sm flex items-center gap-1 whitespace-nowrap">
                        <i class="fas fa-wallet text-purple-400"></i>
                        <span class="text-gray-300">Bal: <strong class="text-white user-credits">{{ user.credits if user else 0 }}</strong></span>
                    </div>
                    <a href="{{ url_for('dashboard') }}" class="text-[11px] sm:text-sm font-medium text-gray-300 hover:text-white transition px-1">Dashboard</a>
                    <a href="{{ url_for('profile') }}" class="text-[11px] sm:text-sm font-medium text-gray-300 hover:text-white transition hidden sm:inline-flex items-center gap-1"><i class="fas fa-user text-purple-400"></i> Profile</a>
                    <a href="{{ url_for('logout') }}" class="text-[11px] sm:text-sm font-medium text-red-400 hover:text-red-300 transition px-1">Logout</a>
                {% else %}
                    <a href="{{ url_for('home') }}" class="text-[11px] sm:text-sm font-medium text-gray-300 hover:text-white transition px-1">Home</a>
                    <a href="{{ url_for('login') }}" class="text-[11px] sm:text-sm font-medium text-gray-300 hover:text-white transition px-1">Login</a>
                    <a href="{{ url_for('register') }}" class="bg-purple-600 hover:bg-purple-500 text-white text-[10px] sm:text-xs font-semibold px-2 py-1 sm:px-3 sm:py-1.5 rounded-lg transition shadow-md whitespace-nowrap">Sign Up</a>
                {% endif %}
                {% if session.admin_logged_in %}
                    <a href="{{ url_for('admin_dashboard') }}" class="text-yellow-400 hover:text-yellow-300 text-[10px] sm:text-xs font-semibold border border-yellow-500/30 px-1.5 py-0.5 rounded bg-yellow-950/20 whitespace-nowrap">Admin</a>
                {% endif %}
            </div>
        </div>
    </nav>

    <main class="flex-grow pt-24 pb-16 px-4 max-w-7xl w-full mx-auto box-border">
        {{ content|safe }}
    </main>

    <footer class="w-full border-t border-purple-950/40 bg-[#02020F]/80 backdrop-blur-md py-6 text-center text-sm text-gray-500 mt-auto">
        <div class="max-w-7xl mx-auto px-4 flex flex-col justify-center items-center gap-5">
            <div class="flex flex-col sm:flex-row justify-between items-center w-full gap-4">
                <p>&copy; 2026 OTP Matrix. All rights reserved.</p>
                <div class="flex gap-4 text-xs text-gray-600">
                    <a href="#" class="hover:text-gray-400">Terms of Service</a>
                    <a href="#" class="hover:text-gray-400">Privacy Policy</a>
                    <a href="#" class="hover:text-gray-400">Support</a>
                </div>
            </div>
            
            <a href="https://t.me/FireXDecoder" target="_blank" class="inline-flex items-center gap-2 text-[#0088cc] hover:text-[#00aaff] font-bold transition bg-[#0088cc]/10 px-5 py-2.5 rounded-xl border border-[#0088cc]/30 hover:bg-[#0088cc]/20 hover:scale-105 transform duration-300">
                <i class="fab fa-telegram-plane text-xl"></i> Contact on Telegram
            </a>
        </div>
    </footer>

    <div id="flash-messages" class="hidden">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% for category, message in messages %}
                <div class="flash-data" data-category="{{ category }}" data-message="{{ message }}"></div>
            {% endfor %}
        {% endwith %}
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
    <script src="https://unpkg.com/aos@2.3.1/dist/aos.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r134/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/vanta@latest/dist/vanta.waves.min.js"></script>
    <script>
        AOS.init({ once: true, duration: 800, offset: 40 });

        try {
            VANTA.WAVES({
                el: "#vanta-bg",
                mouseControls: false, touchControls: false, gyroControls: false,
                minHeight: 200.00, minWidth: 200.00, scale: 1.00, scaleMobile: 1.00,
                color: 0x03021a, shininess: 12.00, waveHeight: 8.00, waveSpeed: 0.40, zoom: 1.1
            });
        } catch(e) { console.error("Background animation skipped."); }

        function showToast(message, type = 'success') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast-item toast-${type}`;
            toast.innerHTML = `<div class="flex items-start gap-2">
                <i class="fas ${type === 'success' ? 'fa-circle-check text-green-400' : type === 'error' ? 'fa-circle-xmark text-red-400' : 'fa-circle-info text-blue-400'} mt-0.5"></i>
                <div>${message}</div>
            </div>`;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.transform = 'translateX(120%)';
                toast.style.opacity = '0';
                toast.style.transition = 'all 0.4s ease';
                setTimeout(() => toast.remove(), 400);
            }, 4000);
        }

        document.querySelectorAll('.flash-data').forEach(el => {
            showToast(el.getAttribute('data-message'), el.getAttribute('data-category'));
        });
    </script>
</body>
</html>
'''

# ---------- Web Content Sub-Templates (unchanged) ----------
LANDING_CONTENT = '''
<div class="space-y-20 py-6" data-aos="fade-up">
    <div class="text-center max-w-4xl mx-auto space-y-6 pt-4">
        <span class="px-4 py-1.5 rounded-full text-xs font-semibold uppercase tracking-wider bg-purple-950/60 border border-purple-500/30 text-purple-300">
            <i class="fas fa-sparkles mr-1"></i> Super Fast Delivery Route
        </span>
        <h1 class="text-4xl sm:text-6xl font-extrabold tracking-tight leading-tight text-white">
            Send OTP Messages <br><span class="gradient-text">To Any Email Instantly</span>
        </h1>
        <p class="text-gray-400 text-lg sm:text-xl max-w-2xl mx-auto leading-relaxed">
            The easiest and most reliable platform to send automated verification messages. Simple setup, zero delays, and perfectly optimized for all mobile screens and computers.
        </p>
        <div class="pt-4 flex justify-center gap-4">
            {% if session.user_id %}
                <a href="{{ url_for('dashboard') }}" class="btn-glow !text-base !px-8 !py-3">Open Dashboard <i class="fas fa-arrow-right ml-1 text-sm"></i></a>
            {% else %}
                <a href="{{ url_for('register') }}" class="btn-glow !text-base !px-8 !py-3">Get Started for Free <i class="fas fa-bolt ml-1 text-sm"></i></a>
                <a href="{{ url_for('login') }} " class="glass-card px-6 py-3 rounded-xl hover:bg-purple-900/10 text-sm font-semibold transition flex items-center justify-center border border-purple-800/20">Login Account</a>
            {% endif %}
        </div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 max-w-6xl mx-auto text-center">
        <div class="glass-card p-6" data-aos="fade-up" data-aos-delay="100">
            <p class="text-3xl sm:text-4xl font-extrabold text-white">99.99%</p>
            <p class="text-xs sm:text-sm text-purple-300 uppercase font-medium mt-1">Server Uptime</p>
        </div>
        <div class="glass-card p-6" data-aos="fade-up" data-aos-delay="200">
            <p class="text-3xl sm:text-4xl font-extrabold text-white">&lt; 1.5s</p>
            <p class="text-xs sm:text-sm text-purple-300 uppercase font-medium mt-1">Average Delivery Time</p>
        </div>
        <div class="glass-card p-6" data-aos="fade-up" data-aos-delay="300">
            <p class="text-3xl sm:text-4xl font-extrabold text-white">Secure</p>
            <p class="text-xs sm:text-sm text-purple-300 uppercase font-medium mt-1">Data Encryption</p>
        </div>
        <div class="glass-card p-6" data-aos="fade-up" data-aos-delay="400">
            <p class="text-3xl sm:text-4xl font-extrabold text-white">50k+</p>
            <p class="text-xs sm:text-sm text-purple-300 uppercase font-medium mt-1">OTPs Sent Daily</p>
        </div>
    </div>

    <div class="space-y-12 max-w-6xl mx-auto">
        <div class="text-center space-y-2">
            <h2 class="text-2xl sm:text-3xl font-bold text-white">Why Choose Our Platform?</h2>
            <p class="text-gray-400 text-sm max-w-md mx-auto">Built for ease, transparency, and complete control over your messaging.</p>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="glass-card p-6 space-y-4">
                <div class="w-12 h-12 bg-purple-500/10 border border-purple-500/30 rounded-xl flex items-center justify-center text-purple-400 text-xl">
                    <i class="fas fa-gauge-high"></i>
                </div>
                <h3 class="text-lg font-bold text-white">Intuitive Dashboard</h3>
                <p class="text-gray-400 text-sm leading-relaxed">
                    Manage all your OTP activities from a clean, real‑time dashboard. Check your balance, send messages, and track every action in one place.
                </p>
            </div>
            <div class="glass-card p-6 space-y-4">
                <div class="w-12 h-12 bg-pink-500/10 border border-pink-500/30 rounded-xl flex items-center justify-center text-pink-400 text-xl">
                    <i class="fas fa-coins"></i>
                </div>
                <h3 class="text-lg font-bold text-white">Instant Credit Top‑Up</h3>
                <p class="text-gray-400 text-sm leading-relaxed">
                    Purchase credits instantly using Google Play Redeem Codes. No hidden fees – you see exactly what you pay for, and credits are added immediately upon approval.
                </p>
            </div>
            <div class="glass-card p-6 space-y-4">
                <div class="w-12 h-12 bg-indigo-500/10 border border-indigo-500/30 rounded-xl flex items-center justify-center text-indigo-400 text-xl">
                    <i class="fas fa-clock-rotate-left"></i>
                </div>
                <h3 class="text-lg font-bold text-white">Full Transaction History</h3>
                <p class="text-gray-400 text-sm leading-relaxed">
                    Every OTP request is logged – success or failure – so you can review past activity, troubleshoot issues, and keep a complete record of your usage.
                </p>
            </div>
        </div>
    </div>
</div>
'''

DASHBOARD_CONTENT = '''
<div class="max-w-2xl mx-auto" data-aos="fade-up">
    <div class="glass-card p-4 mb-6 flex flex-col sm:flex-row justify-between items-center gap-4">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-full bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-purple-400">
                <i class="fas fa-wallet"></i>
            </div>
            <div>
                <p class="text-xs text-gray-400">Available Balance</p>
                <p class="text-lg font-bold text-white"><span class="user-credits">{{ credits }}</span> Credits</p>
            </div>
        </div>
        <button onclick="openTopupModal()" class="bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white text-xs font-bold py-2 px-4 rounded-lg shadow-lg flex items-center gap-1.5 transition transform hover:-translate-y-0.5">
            <i class="fas fa-plus-circle"></i> Top Up / Buy Credits
        </button>
    </div>

    <div class="glass-card p-6 sm:p-8">
        <div class="text-center mb-6">
            <div class="w-14 h-14 rounded-full bg-purple-500/10 border border-purple-500/30 flex items-center justify-center mx-auto mb-3">
                <i class="fas fa-paper-plane text-xl text-purple-400"></i>
            </div>
            <h2 class="text-xl sm:text-2xl font-bold text-white">Send OTP Message</h2>
            <p class="text-xs text-gray-400 mt-1">Cost: 1 credit per successfully sent message. (No credits used if sending fails!)</p>
        </div>

        <form id="otpDispatchForm" class="space-y-4">
            <div>
                <label class="block text-xs font-medium text-purple-300 mb-1">Recipient Email Address</label>
                <div class="relative">
                    <i class="fas fa-envelope absolute left-4 top-3.5 text-gray-500 text-sm"></i>
                    <input type="email" name="email" class="form-input pl-11" placeholder="example@gmail.com" required>
                </div>
            </div>
            <div>
                <label class="block text-xs font-medium text-purple-300 mb-1">Username <span class="text-gray-500">(Optional)</span></label>
                <div class="relative">
                    <i class="fas fa-user absolute left-4 top-3.5 text-gray-500 text-sm"></i>
                    <input type="text" name="username" class="form-input pl-11" placeholder="Enter username if required">
                </div>
            </div>
            <button type="submit" class="btn-glow w-full justify-center !py-3 mt-2 text-xs font-bold uppercase tracking-wider">
                Send OTP Now
            </button>
        </form>
    </div>
</div>

<div id="topupModal" class="hidden fixed inset-0 z-50 overflow-y-auto bg-black/70 backdrop-blur-sm">
    <div class="flex min-h-full items-center justify-center p-4">
        <div class="glass-card w-full max-w-lg p-6 sm:p-8 relative bg-[#0c072b] shadow-2xl shadow-purple-900/20 my-8">
            <button type="button" onclick="closeTopupModal()" class="absolute top-4 right-4 text-gray-400 hover:text-white text-lg transition z-10">
                <i class="fas fa-times"></i>
            </button>
            
            <div class="text-center mb-6">
                <h3 class="text-xl font-bold text-white"><i class="fas fa-gem text-purple-400 mr-1"></i> Get More Credits</h3>
                <p class="text-xs text-gray-400 mt-1">Click a package below and buy instantly using a Google Play Redeem Code</p>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
                <div onclick="selectPackage(this, 10)" class="package-card cursor-pointer border border-purple-900/40 bg-purple-950/20 rounded-xl p-4 text-center transition hover:border-purple-500/50">
                    <span class="text-gray-400 text-[10px] uppercase font-bold tracking-wide">Starter</span>
                    <p class="text-2xl font-bold text-white my-1">10 <span class="text-xs font-normal text-purple-300">Credits</span></p>
                    <span class="inline-block bg-purple-900/50 text-purple-300 font-semibold text-xs px-2 py-0.5 rounded border border-purple-700/30">Only ₹10</span>
                </div>
                <div onclick="selectPackage(this, 75)" class="package-card cursor-pointer border-2 border-purple-900/40 bg-purple-950/20 rounded-xl p-4 text-center relative shadow-lg transition hover:border-purple-500/50">
                    <span class="absolute -top-2.5 left-1/2 -translate-x-1/2 bg-purple-500 text-white text-[9px] font-extrabold uppercase px-2 py-0.5 rounded-full tracking-wider">Best Value</span>
                    <span class="text-purple-300 text-[10px] uppercase font-bold tracking-wide">Popular Pack</span>
                    <p class="text-2xl font-bold text-white my-1">75 <span class="text-xs font-normal text-purple-300">Credits</span></p>
                    <span class="inline-block bg-purple-500 text-white font-semibold text-xs px-2 py-0.5 rounded">Only ₹50</span>
                </div>
                <div onclick="selectPackage(this, 175)" class="package-card cursor-pointer border border-purple-900/40 bg-purple-950/20 rounded-xl p-4 text-center transition hover:border-purple-500/50">
                    <span class="text-gray-400 text-[10px] uppercase font-bold tracking-wide">Mega Offer</span>
                    <p class="text-2xl font-bold text-white my-1">175 <span class="text-xs font-normal text-purple-300">Credits</span></p>
                    <span class="inline-block bg-purple-900/50 text-purple-300 font-semibold text-xs px-2 py-0.5 rounded border border-purple-700/30">Only ₹100</span>
                </div>
            </div>

            <div class="bg-purple-950/30 rounded-xl p-4 border border-purple-900/20 text-xs space-y-2 mb-4">
                <p class="text-gray-300 font-medium"><i class="fas fa-info-circle text-purple-400 mr-1"></i> How to add credits:</p>
                <ol class="list-decimal list-inside text-gray-400 space-y-1 ml-1">
                    <li>Select a package above first.</li>
                    <li>Buy a Google Play Redeem Code matching the package price (₹10, ₹50, or ₹100).</li>
                    <li>Paste your Redeem Code below and click submit.</li>
                </ol>
            </div>

            <form id="modalRedeemForm" class="space-y-3">
                <input type="hidden" name="expected_amount" id="expected_amount" value="">
                <div>
                    <label class="block text-xs font-medium text-purple-300 mb-1">Google Play Redeem Code</label>
                    <input type="text" name="redeem_code" class="form-input text-center font-mono tracking-widest text-sm" placeholder="ABCD-EFGH-IJKL-MNOP" required>
                </div>
                <button type="submit" class="btn-glow w-full justify-center !py-2.5 text-xs font-bold uppercase tracking-wider">
                    Submit Redeem Code
                </button>
            </form>
        </div>
    </div>
</div>

<script>
function openTopupModal() {
    document.getElementById('topupModal').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

function closeTopupModal() {
    document.getElementById('topupModal').classList.add('hidden');
    document.body.style.overflow = 'auto';
}

function selectPackage(element, amount) {
    document.querySelectorAll('.package-card').forEach(card => {
        card.classList.remove('border-purple-400', 'bg-purple-900/50', 'ring-2', 'ring-purple-500');
        if(!card.classList.contains('border-2')) {
            card.classList.add('border-purple-900/40', 'bg-purple-950/20');
        }
    });
    element.classList.remove('border-purple-900/40', 'bg-purple-950/20');
    element.classList.add('border-purple-400', 'bg-purple-900/50', 'ring-2', 'ring-purple-500');
    document.getElementById('expected_amount').value = amount;
}

document.getElementById('otpDispatchForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const btn = this.querySelector('button[type="submit"]');
    const prevMarkup = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-circle-notch fa-spin mr-2"></i>Sending message...';

    try {
        const response = await fetch("{{ url_for('send_otp') }}", {
            method: 'POST',
            body: new FormData(this)
        });
        const result = await response.json();
        if (result.success) {
            showToast(result.message, 'success');
            document.querySelectorAll('.user-credits').forEach(el => el.textContent = result.credits);
            this.reset();
        } else {
            showToast(result.message, 'error');
        }
    } catch(err) {
        showToast('Network error. Please check your internet connection.', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = prevMarkup;
    }
});

document.getElementById('modalRedeemForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    if (!document.getElementById('expected_amount').value) {
        showToast('Please select a credit package above first.', 'warning');
        return;
    }
    const btn = this.querySelector('button[type="submit"]');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Submitting...';

    try {
        const response = await fetch("{{ url_for('redeem') }}", {
            method: 'POST',
            body: new FormData(this)
        });
        const res = await response.json();
        if (res.success) {
            showToast(res.message, 'success');
            this.reset();
            document.getElementById('expected_amount').value = '';
            document.querySelectorAll('.package-card').forEach(card => {
                card.classList.remove('border-purple-400', 'bg-purple-900/50', 'ring-2', 'ring-purple-500');
            });
            closeTopupModal();
        } else {
            showToast(res.message, 'error');
        }
    } catch(err) {
        showToast('Submission error. Please try again.', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
});
</script>
'''

LOGIN_CONTENT = '''
<div class="flex items-center justify-center min-h-[60vh]" data-aos="fade-up">
    <div class="glass-card p-6 sm:p-8 w-full max-w-md">
        <div class="text-center mb-6">
            <h3 class="text-2xl font-bold text-white">Login to Your Account</h3>
            <p class="text-xs text-gray-400 mt-1">Enter your registered email and password below</p>
        </div>

        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-purple-300 mb-1">Email Address</label>
                <input type="email" name="email" class="form-input" placeholder="example@gmail.com" required>
            </div>
            <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-purple-300 mb-1">Password</label>
                <input type="password" name="password" class="form-input" placeholder="••••••••" required>
            </div>
            <button type="submit" class="btn-glow w-full justify-center !py-3 mt-2">
                Login
            </button>
        </form>

        <p class="text-center text-xs text-gray-400 mt-5">
            Don't have an account? <a href="{{ url_for('register') }}" class="text-purple-400 hover:underline font-medium">Create one here</a>
        </p>
    </div>
</div>
'''

REGISTER_CONTENT = '''
<div class="flex items-center justify-center min-h-[65vh]" data-aos="fade-up">
    <div class="glass-card p-6 sm:p-8 w-full max-w-md">
        <div class="text-center mb-6">
            <h3 class="text-2xl font-bold text-white">Create a Free Account</h3>
            <p class="text-xs text-gray-400 mt-1">New profiles automatically get <strong class="text-green-400 font-semibold">1 free test credit</strong></p>
        </div>

        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-purple-300 mb-1">Username</label>
                <input type="text" name="username" class="form-input" placeholder="john_doe" required>
            </div>
            <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-purple-300 mb-1">Email Address</label>
                <input type="email" name="email" class="form-input" placeholder="example@gmail.com" required>
            </div>
            <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-purple-300 mb-1">Password</label>
                <input type="password" name="password" class="form-input" placeholder="Minimum 6 characters" minlength="6" required>
            </div>
            <button type="submit" class="btn-glow w-full justify-center !py-3 mt-2">
                Register Account
            </button>
        </form>

        <p class="text-center text-xs text-gray-400 mt-5">
            Already have an account? <a href="{{ url_for('login') }}" class="text-purple-400 hover:underline font-medium">Login here</a>
        </p>
    </div>
</div>
'''

PROFILE_CONTENT = '''
<div class="grid grid-cols-1 lg:grid-cols-3 gap-6" data-aos="fade-up">
    <div class="lg:col-span-1 space-y-6">
        <div class="glass-card p-6">
            <h2 class="text-xl font-bold text-white mb-4"><i class="fas fa-id-card mr-1 text-purple-400"></i> Profile Details</h2>
            <div class="space-y-3 text-xs sm:text-sm">
                <div class="border-b border-purple-950/40 pb-2">
                    <span class="text-gray-400 block text-[11px] uppercase tracking-wider">Username</span>
                    <strong class="text-white">{{ user.username }}</strong>
                </div>
                <div class="border-b border-purple-950/40 pb-2">
                    <span class="text-gray-400 block text-[11px] uppercase tracking-wider">Email Address</span>
                    <strong class="text-white">{{ user.email }}</strong>
                </div>
                <div class="border-b border-purple-950/40 pb-2">
                    <span class="text-gray-400 block text-[11px] uppercase tracking-wider">Credit Balance</span>
                    <strong class="text-purple-400 user-credits">{{ user.credits }} credits</strong>
                </div>
                <div>
                    <span class="text-gray-400 block text-[11px] uppercase tracking-wider">Joined Date</span>
                    <strong class="text-white">{{ user.created_at }}</strong>
                </div>
            </div>
        </div>

        <div class="glass-card p-6">
            <h3 class="text-lg font-bold text-white mb-2"><i class="fas fa-ticket mr-1 text-purple-400"></i> Redeem Codes</h3>
            <p class="text-xs text-gray-400 mb-4">Select the package you purchased and enter your Google Play Redeem Code below.</p>
            <form id="redeemForm" class="space-y-3">
                <select name="expected_amount" class="form-input text-xs text-gray-300" required>
                    <option value="" disabled selected>-- Select Purchased Package --</option>
                    <option value="10">10 Credits (₹10)</option>
                    <option value="75">75 Credits (₹50)</option>
                    <option value="175">175 Credits (₹100)</option>
                </select>
                <input type="text" name="redeem_code" class="form-input" placeholder="Paste your code here" required>
                <button type="submit" class="btn-glow w-full justify-center !py-2.5 text-xs uppercase tracking-wider">Submit Code</button>
            </form>
        </div>
    </div>

    <div class="lg:col-span-2">
        <div class="glass-card p-6 h-full flex flex-col">
            <h3 class="text-lg font-bold text-white mb-4"><i class="fas fa-clock-rotate-left mr-1 text-purple-400"></i> Recent OTP History (Last 20 messages)</h3>
            <div class="overflow-x-auto flex-grow">
                <table class="w-full text-xs sm:text-sm text-left">
                    <thead class="text-[11px] uppercase tracking-wider bg-purple-950/40 text-purple-300 border-b border-purple-900/30">
                        <tr>
                            <th class="px-4 py-3">No.</th>
                            <th class="px-4 py-3">Recipient Email</th>
                            <th class="px-4 py-3">Status</th>
                            <th class="px-4 py-3">Date & Time</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-purple-950/30">
                        {% for req in requests %}
                        <tr class="hover:bg-purple-900/5 transition">
                            <td class="px-4 py-3 text-gray-500 font-mono">#{{ loop.index }}</td>
                            <td class="px-4 py-3 font-medium text-white">{{ req.email }}</td>
                            <td class="px-4 py-3">
                                <span class="badge {{ 'badge-success' if req.success else 'badge-danger' }}">
                                    {{ 'SENT' if req.success else 'FAILED' }}
                                </span>
                            </td>
                            <td class="px-4 py-3 text-gray-400 font-mono text-[11px]">{{ req.timestamp }}</td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="4" class="px-4 py-8 text-center text-gray-500 text-xs uppercase">No OTPs have been sent from this account yet.</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<script>
document.getElementById('redeemForm')?.addEventListener('submit', async function(e) {
    e.preventDefault();
    const btn = this.querySelector('button[type="submit"]');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Verifying...';

    try {
        const response = await fetch("{{ url_for('redeem') }}", {
            method: 'POST',
            body: new FormData(this)
        });
        const res = await response.json();
        if (res.success) {
            showToast(res.message, 'success');
            this.reset();
        } else {
            showToast(res.message, 'error');
        }
    } catch(err) {
        showToast('Error submitting code. Please try again.', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
});
</script>
'''

ADMIN_DASHBOARD_CONTENT = '''
<div class="space-y-6" data-aos="fade-up">
    <div class="flex justify-between items-center border-b border-purple-900/20 pb-4">
        <h2 class="text-2xl sm:text-3xl font-bold text-white"><i class="fas fa-gears mr-1 text-yellow-400"></i> Admin Dashboard</h2>
        <a href="{{ url_for('admin_logout') }}" class="text-xs text-red-400 hover:underline">Logout Admin</a>
    </div>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div class="glass-card p-4">
            <span class="text-gray-400 text-[10px] uppercase tracking-wider block">Total Registered Users</span>
            <p class="text-2xl font-bold text-white mt-1">{{ total_users }}</p>
        </div>
        <div class="glass-card p-4">
            <span class="text-gray-400 text-[10px] uppercase tracking-wider block">Successful OTPs</span>
            <p class="text-2xl font-bold text-green-400 mt-1">{{ success_otp }}</p>
        </div>
        <div class="glass-card p-4">
            <span class="text-gray-400 text-[10px] uppercase tracking-wider block">Failed OTPs</span>
            <p class="text-2xl font-bold text-red-400 mt-1">{{ failed_otp }}</p>
        </div>
        <div class="glass-card p-4">
            <span class="text-gray-400 text-[10px] uppercase tracking-wider block">Pending Redeem Codes</span>
            <p class="text-2xl font-bold text-yellow-400 mt-1">{{ pending_redeems }}</p>
        </div>
    </div>

    <div class="glass-card p-6">
        <h3 class="text-base font-bold text-white mb-3"><i class="fas fa-hourglass-half text-yellow-400 mr-1"></i> Redeem Requests Pending Approval</h3>
        {% if pending_codes %}
        <div class="overflow-x-auto">
            <table class="w-full text-xs text-left">
                <thead class="bg-purple-950/40 text-purple-300 uppercase tracking-wider text-[10px]">
                    <tr>
                        <th class="px-4 py-3">ID</th>
                        <th class="px-4 py-3">Redeem Code</th>
                        <th class="px-4 py-3">User</th>
                        <th class="px-4 py-3">Credits Requested</th>
                        <th class="px-4 py-3">Action</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-purple-950/30">
                    {% for code in pending_codes %}
                    <tr class="hover:bg-purple-900/5">
                        <td class="px-4 py-3 text-gray-500 font-mono">#{{ code.id }}</td>
                        <td class="px-4 py-3 font-mono text-white tracking-wider font-semibold">{{ code.code }}</td>
                        <td class="px-4 py-3 text-purple-200">{{ code.username }}</td>
                        <td class="px-4 py-3">
                            <form method="POST" action="{{ url_for('admin_approve_code') }}" class="inline-flex items-center gap-2">
                                <input type="hidden" name="code_id" value="{{ code.id }}">
                                <input type="number" name="amount" value="{{ code.amount }}" class="form-input !py-1 !px-2 w-20 text-center font-bold" required>
                        </td>
                        <td class="px-4 py-3 flex gap-2">
                                <button type="submit" name="action" value="approve" class="bg-emerald-600 hover:bg-emerald-500 text-white font-semibold py-1 px-3 rounded text-xs transition">Approve</button>
                                <button type="submit" name="action" value="reject" class="bg-rose-600 hover:bg-rose-500 text-white font-semibold py-1 px-3 rounded text-xs transition">Reject</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% else %}
        <p class="text-gray-500 text-xs uppercase tracking-wide py-4 text-center">No pending code requests.</p>
        {% endif %}
    </div>

    <div class="glass-card p-6">
        <h3 class="text-base font-bold text-white mb-3"><i class="fas fa-list text-purple-400 mr-1"></i> Recent Platform Logs (Top 50 Rows)</h3>
        <div class="overflow-x-auto">
            <table class="w-full text-xs text-left">
                <thead class="bg-purple-950/40 text-purple-300 uppercase tracking-wider text-[10px]">
                    <tr>
                        <th class="px-4 py-3">ID</th>
                        <th class="px-4 py-3">User</th>
                        <th class="px-4 py-3">Recipient Email</th>
                        <th class="px-4 py-3">Status</th>
                        <th class="px-4 py-3">Error Details</th>
                        <th class="px-4 py-3">IP Address</th>
                        <th class="px-4 py-3">Date & Time</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-purple-950/30 text-gray-300">
                    {% for log in logs %}
                    <tr class="hover:bg-purple-900/5 transition">
                        <td class="px-4 py-3 text-gray-500 font-mono">#{{ log.id }}</td>
                        <td class="px-4 py-3 font-medium text-purple-200">{{ log.username or 'Guest' }}</td>
                        <td class="px-4 py-3 text-white">{{ log.email }}</td>
                        <td class="px-4 py-3">
                            <span class="badge {{ 'badge-success' if log.success else 'badge-danger' }}">
                                {{ 'OK' if log.success else 'FAIL' }}
                            </span>
                        </td>
                        <td class="px-4 py-3 text-red-400 max-w-[150px] truncate font-mono text-[11px]">{{ log.error_message or '-' }}</td>
                        <td class="px-4 py-3 font-mono text-gray-400 text-[11px]">{{ log.ip_address }}</td>
                        <td class="px-4 py-3 text-gray-400 font-mono text-[11px]">{{ log.timestamp }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
'''

ADMIN_LOGIN_CONTENT = '''
<div class="flex items-center justify-center min-h-[60vh]" data-aos="fade-up">
    <div class="glass-card p-6 sm:p-8 w-full max-w-md">
        <div class="text-center mb-6">
            <h3 class="text-xl font-bold text-yellow-400"><i class="fas fa-user-shield"></i> Admin Control Gateway</h3>
            <p class="text-xs text-gray-400 mt-1">Please provide master administrator keys</p>
        </div>

        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-purple-300 mb-1">Admin Username</label>
                <input type="text" name="username" class="form-input" placeholder="admin" required>
            </div>
            <div>
                <label class="block text-xs font-semibold uppercase tracking-wider text-purple-300 mb-1">Admin Password</label>
                <input type="password" name="password" class="form-input" placeholder="••••••••" required>
            </div>
            <button type="submit" class="btn-glow w-full justify-center !py-3 mt-2" style="background: linear-gradient(90deg, #D97706, #B45309);">
                Enter Dashboard
            </button>
        </form>
    </div>
</div>
'''

# ---------- Page Assembler Helper (uses session cache) ----------
def render_page(content_template, title="OTP Matrix", **context):
    """Render the page with user data from session if available, else from Firestore."""
    user = None
    if session.get('user_id'):
        # Use session cache if present, else fetch from DB and cache
        if 'user_credits' in session and 'username' in session:
            user = {
                'id': session['user_id'],
                'username': session.get('username'),
                'email': session.get('user_email'),
                'credits': session.get('user_credits', 0)
            }
        else:
            user = get_user_by_id(session['user_id'])
            if user:
                session['user_credits'] = user.get('credits', 0)
                session['username'] = user.get('username')
                session['user_email'] = user.get('email')
    content_rendered = render_template_string(content_template, **context)
    return render_template_string(BASE_HTML, title=title, content=content_rendered, user=user)

# ---------- Route Definitions (with session cache updates) ----------

@app.route('/')
def home():
    return render_page(LANDING_CONTENT, title="Home – Fast OTP Service")

@app.route('/dashboard')
@login_required
def dashboard():
    # Refresh credits from session or DB
    user = None
    if session.get('user_id'):
        if 'user_credits' in session:
            user = {'credits': session['user_credits']}
        else:
            user = get_user_by_id(session['user_id'])
            if user:
                session['user_credits'] = user.get('credits', 0)
    credits = user['credits'] if user else 0
    return render_page(DASHBOARD_CONTENT, title="Dashboard Console", credits=credits)

@app.route('/send-otp', methods=['POST'])
@login_required
def send_otp():
    user = get_user_by_id(session['user_id'])
    if not user:
        return jsonify({'success': False, 'message': 'User session not found.'}), 401

    if user['credits'] < 1:
        return jsonify({'success': False, 'message': 'You have 0 credits. Please buy credits to continue.'}), 403

    email = request.form.get('email', '').strip()
    username = request.form.get('username', '').strip() or None
    ip = request.remote_addr

    if not email or '@' not in email:
        return jsonify({'success': False, 'message': 'Please enter a valid email address.'}), 400

    result, error = send_otp_via_api(email, username)
    
    if error:
        success = 0
        error_msg = error
    else:
        success = 1 if result.get('data', {}).get('result') == 0 else 0
        error_msg = result.get('data', {}).get('message') if not success else None

    if success == 1:
        if not deduct_credit(user['id']):
            return jsonify({'success': False, 'message': 'Failed to complete credit update.'}), 500

    # Log the request in Firestore
    db = get_firestore()
    request_data = {
        'user_id': user['id'],
        'email': email,
        'username': username,
        'success': success,
        'error_message': error_msg,
        'ip_address': ip,
        'user_agent': request.headers.get('User-Agent'),
        'timestamp': firestore.SERVER_TIMESTAMP
    }
    db.collection('requests').add(request_data)

    # Update session credit after successful deduction
    updated_user = get_user_by_id(user['id'])
    if updated_user:
        session['user_credits'] = updated_user['credits']

    if success == 1:
        return jsonify({'success': True, 'message': 'OTP sent successfully!', 'credits': session['user_credits']})
    else:
        return jsonify({'success': False, 'message': f"Server error: {error_msg or 'Failed to process request.'}", 'credits': session['user_credits']})

@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not email or not password:
            flash('All fields are required.', 'error')
            return render_page(REGISTER_CONTENT, title="Register Account")

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_page(REGISTER_CONTENT, title="Register Account")

        if get_user_by_username(username):
            flash('Username is already taken.', 'error')
            return render_page(REGISTER_CONTENT, title="Register Account")
        if get_user_by_email(email):
            flash('Email is already registered.', 'error')
            return render_page(REGISTER_CONTENT, title="Register Account")

        hashed = hash_password(password)
        try:
            create_user(username, email, hashed, credits=1)
            flash('Account created successfully! You received 1 free credit. Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'Registration failed: {str(e)}', 'error')

    return render_page(REGISTER_CONTENT, title="Register Account")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        if not email or not password:
            flash('Please fill in all details.', 'error')
            return render_page(LOGIN_CONTENT, title="Login")

        user = get_user_by_email(email)
        if not user or not verify_password(password, user['password_hash']):
            flash('Invalid email or password.', 'error')
            return render_page(LOGIN_CONTENT, title="Login")

        # Store user data in session
        session['user_id'] = user['id']
        session['user_credits'] = user['credits']
        session['username'] = user['username']
        session['user_email'] = user['email']
        flash('Logged in successfully. Welcome back!', 'success')
        return redirect(url_for('dashboard'))

    return render_page(LOGIN_CONTENT, title="Login Account")

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('home'))

@app.route('/profile')
@login_required
def profile():
    # Refresh user data from DB (credits may have changed)
    user = get_user_by_id(session['user_id'])
    if not user:
        flash('User account error.', 'error')
        return redirect(url_for('logout'))
    # Update session cache
    session['user_credits'] = user['credits']

    # Get recent requests for this user
    db = get_firestore()
    requests_ref = db.collection('requests').where('user_id', '==', user['id']).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(20)
    requests_logs = []
    for doc in requests_ref.stream():
        data = doc.to_dict()
        ts = data.get('timestamp')
        if hasattr(ts, 'strftime'):
            ts = ts.strftime('%Y-%m-%d %H:%M:%S')
        requests_logs.append({
            'email': data.get('email'),
            'success': data.get('success'),
            'timestamp': ts
        })

    return render_page(PROFILE_CONTENT, title="My Profile", user=user, requests=requests_logs)

@app.route('/redeem', methods=['POST'])
@login_required
def redeem():
    code = request.form.get('redeem_code', '').strip()
    expected_amount_str = request.form.get('expected_amount', '0').strip()
    
    try:
        expected_amount = int(expected_amount_str)
    except ValueError:
        expected_amount = 0

    if not code:
        return jsonify({'success': False, 'message': 'Please input a valid code string.'}), 400
    
    if expected_amount <= 0:
        return jsonify({'success': False, 'message': 'Please select a valid package.'}), 400

    db = get_firestore()
    # Check if code already exists
    existing = db.collection('redeem_codes').where('code', '==', code).limit(1).stream()
    for doc in existing:
        data = doc.to_dict()
        if data['status'] == 'approved':
            return jsonify({'success': False, 'message': 'This redeem code has already been used.'}), 400
        elif data['status'] == 'rejected':
            return jsonify({'success': False, 'message': 'This redeem code has expired or was rejected.'}), 400
        else:
            return jsonify({'success': True, 'message': 'This code is already under review.'})

    # Insert new redeem code
    redeem_data = {
        'code': code,
        'user_id': session['user_id'],
        'amount': expected_amount,
        'status': 'pending',
        'requested_at': firestore.SERVER_TIMESTAMP,
        'approved_at': None,
        'admin_notes': None
    }
    db.collection('redeem_codes').add(redeem_data)
    return jsonify({'success': True, 'message': 'Code submitted successfully! Credits will be added once approved.'})

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == app.config['ADMIN_USERNAME'] and password == app.config['ADMIN_PASSWORD']:
            session['admin_logged_in'] = True
            flash('Admin dashboard loaded successfully.', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid administrator credentials.', 'error')

    return render_page(ADMIN_LOGIN_CONTENT, title="Admin Login")

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Admin session cleared.', 'info')
    return redirect(url_for('home'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_firestore()
    total_users = len(list(db.collection('users').stream()))
    success_otp = len(list(db.collection('requests').where('success', '==', 1).stream()))
    failed_otp = len(list(db.collection('requests').where('success', '==', 0).stream()))
    pending_redeems = len(list(db.collection('redeem_codes').where('status', '==', 'pending').stream()))

    # Pending redeem codes with user details
    pending_codes = []
    for doc in db.collection('redeem_codes').where('status', '==', 'pending').order_by('requested_at').stream():
        data = doc.to_dict()
        # Fetch username for user_id
        user_docs = db.collection('users').where('id', '==', data['user_id']).limit(1).stream()
        username = None
        for u in user_docs:
            username = u.to_dict().get('username')
        pending_codes.append({
            'id': doc.id,
            'code': data['code'],
            'user_id': data['user_id'],
            'amount': data['amount'],
            'username': username,
            'requested_at': data.get('requested_at')
        })

    # Recent logs (last 50)
    logs = []
    for doc in db.collection('requests').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(50).stream():
        data = doc.to_dict()
        ts = data.get('timestamp')
        if hasattr(ts, 'strftime'):
            ts = ts.strftime('%Y-%m-%d %H:%M:%S')
        # Get username if user_id exists
        username = None
        if data.get('user_id'):
            user_docs = db.collection('users').where('id', '==', data['user_id']).limit(1).stream()
            for u in user_docs:
                username = u.to_dict().get('username')
        logs.append({
            'id': doc.id[:8],
            'email': data.get('email'),
            'success': data.get('success'),
            'error_message': data.get('error_message'),
            'ip_address': data.get('ip_address'),
            'timestamp': ts,
            'username': username
        })

    return render_page(ADMIN_DASHBOARD_CONTENT, title="Admin Command Center",
                       total_users=total_users, success_otp=success_otp, failed_otp=failed_otp,
                       pending_redeems=pending_redeems, pending_codes=pending_codes, logs=logs)

@app.route('/admin/approve-code', methods=['POST'])
@admin_required
def admin_approve_code():
    code_id = request.form.get('code_id')
    action = request.form.get('action')
    amount = request.form.get('amount')

    if not code_id or not action:
        flash('Invalid verification parameters.', 'error')
        return redirect(url_for('admin_dashboard'))

    db = get_firestore()
    doc_ref = db.collection('redeem_codes').document(code_id)
    doc = doc_ref.get()
    if not doc.exists:
        flash('Redeem code reference not found.', 'error')
        return redirect(url_for('admin_dashboard'))

    code_data = doc.to_dict()
    if action == 'approve':
        try:
            amount = int(amount)
            if amount <= 0:
                flash('Credit value configuration must be a positive number.', 'error')
                return redirect(url_for('admin_dashboard'))
        except ValueError:
            flash('Invalid credit amount entered.', 'error')
            return redirect(url_for('admin_dashboard'))

        doc_ref.update({
            'status': 'approved',
            'amount': amount,
            'approved_at': firestore.SERVER_TIMESTAMP
        })
        add_credits(code_data['user_id'], amount)
        # Update session credit if the user is the current one
        if session.get('user_id') == code_data['user_id']:
            user = get_user_by_id(session['user_id'])
            if user:
                session['user_credits'] = user['credits']
        flash(f'Code approved successfully! Added {amount} credits to the user account.', 'success')

    elif action == 'reject':
        doc_ref.update({'status': 'rejected'})
        flash('Redeem code request rejected.', 'info')

    return redirect(url_for('admin_dashboard'))

@app.route('/api/send', methods=['POST'])
def api_send():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON layout required'}), 400

    api_key = data.get('api_key')
    if not api_key or api_key not in app.config['API_KEYS']:
        return jsonify({'error': 'Unauthorized API Key'}), 401

    email = data.get('email', '').strip()
    if not email or '@' not in email:
        return jsonify({'error': 'Invalid recipient email'}), 400

    username = data.get('username', '').strip() or None
    ip = request.remote_addr

    user_id = data.get('user_id')
    if user_id:
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User profile not discovered'}), 404
        if user['credits'] < 1:
            return jsonify({'error': 'Insufficient credits'}), 403
    else:
        if not check_rate_limit(ip):
            return jsonify({'error': 'Rate limit exceeded for guests'}), 429

    result, error = send_otp_via_api(email, username)
    if error:
        success = 0
        error_msg = error
    else:
        success = 1 if result.get('data', {}).get('result') == 0 else 0
        error_msg = result.get('data', {}).get('message') if not success else None

    if success == 1 and user_id:
        if not deduct_credit(user_id):
            return jsonify({'error': 'Transactional ledger update failed'}), 500

    db = get_firestore()
    log_data = {
        'user_id': user_id,
        'email': email,
        'username': username,
        'success': success,
        'error_message': error_msg,
        'ip_address': ip,
        'user_agent': request.headers.get('User-Agent'),
        'timestamp': firestore.SERVER_TIMESTAMP
    }
    db.collection('requests').add(log_data)

    if success:
        return jsonify({'status': 'success', 'message': 'OTP delivery completed successfully.'})
    else:
        return jsonify({'status': 'error', 'message': error_msg or 'Upstream response error'}), 500

# ---------- Server Launch ----------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
