#!/usr/bin/env python3
"""
Premium OTP Sender – with User Login, Credits, and Redeem Codes.
Uses Supabase (PostgreSQL) as the database.
"""

import os
import hashlib
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for, flash
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# ---------- Configuration ----------
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY not set")

    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        raise RuntimeError("ADMIN credentials missing")

    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY required")

    API_KEYS = [k.strip() for k in os.environ.get('API_KEYS', '').split(',') if k.strip()]
    API_URL = os.environ.get('API_URL', 'https://rishu-sso.vercel.app/rishu')
    OTP_COST = 1

app = Flask(__name__)
app.config.from_object(Config)

# ---------- Supabase Client ----------
supabase: Client = create_client(
    app.config['SUPABASE_URL'],
    app.config['SUPABASE_KEY']
)

# ---------- Database Helpers ----------

def get_user_by_id(user_id):
    try:
        user_id = int(user_id)
    except:
        return None
    resp = supabase.table('users').select('*').eq('id', user_id).execute()
    return resp.data[0] if resp.data else None

def get_user_by_username(username):
    resp = supabase.table('users').select('*').eq('username', username).execute()
    return resp.data[0] if resp.data else None

def get_user_by_email(email):
    resp = supabase.table('users').select('*').eq('email', email).execute()
    return resp.data[0] if resp.data else None

def add_credits(user_id, amount):
    user = get_user_by_id(user_id)
    if not user:
        return False
    new_credits = user['credits'] + amount
    resp = supabase.table('users').update({'credits': new_credits}).eq('id', user_id).execute()
    return bool(resp.data)

def deduct_credit(user_id):
    user = get_user_by_id(user_id)
    if not user or user['credits'] < 1:
        return False
    new_credits = user['credits'] - 1
    resp = supabase.table('users').update({'credits': new_credits}).eq('id', user_id).execute()
    return bool(resp.data)

def create_user(username, email, password_hash, credits=1):
    data = {
        'username': username,
        'email': email,
        'password_hash': password_hash,
        'credits': credits
    }
    resp = supabase.table('users').insert(data).execute()
    if resp.data:
        return resp.data[0]['id']
    return None

def check_rate_limit(ip):
    now = datetime.now(timezone.utc)
    resp = supabase.table('rate_limits').select('*').eq('ip', ip).execute()
    if not resp.data:
        reset_time = now + timedelta(hours=1)
        supabase.table('rate_limits').insert({
            'ip': ip,
            'count': 1,
            'reset_time': reset_time.isoformat()
        }).execute()
        return True
    else:
        record = resp.data[0]
        reset_time = record.get('reset_time')
        if reset_time and datetime.fromisoformat(reset_time.replace('Z', '+00:00')) < now:
            new_reset = now + timedelta(hours=1)
            supabase.table('rate_limits').update({
                'count': 1,
                'reset_time': new_reset.isoformat()
            }).eq('ip', ip).execute()
            return True
        else:
            count = record.get('count', 0)
            if count >= 5:
                return False
            else:
                supabase.table('rate_limits').update({'count': count + 1}).eq('ip', ip).execute()
                return True

# ---------- Password Helpers ----------
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hash_val):
    return hash_password(password) == hash_val

# ---------- Upstream API ----------
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

# ---------- Decorators ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please log in.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Admin access required.', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ---------- HTML Templates (unchanged from original) ----------
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

# ---------- Page Renderer (uses session cache) ----------
def render_page(content_template, title="OTP Matrix", **context):
    user = None
    if session.get('user_id'):
        if 'user_credits' in session:
            user = {
                'id': session['user_id'],
                'username': session.get('username'),
                'email': session.get('user_email'),
                'credits': session.get('user_credits', 0)
            }
        else:
            user = get_user_by_id(session['user_id'])
            if user:
                session['user_credits'] = user['credits']
                session['username'] = user['username']
                session['user_email'] = user['email']
    content_rendered = render_template_string(content_template, **context)
    return render_template_string(BASE_HTML, title=title, content=content_rendered, user=user)

# ---------- Routes ----------
@app.route('/')
def home():
    return render_page(LANDING_CONTENT, title="Home – Fast OTP Service")

@app.route('/dashboard')
@login_required
def dashboard():
    credits = session.get('user_credits', 0)
    return render_page(DASHBOARD_CONTENT, title="Dashboard Console", credits=credits)

@app.route('/send-otp', methods=['POST'])
@login_required
def send_otp():
    user = get_user_by_id(session['user_id'])
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 401
    if user['credits'] < 1:
        return jsonify({'success': False, 'message': 'Insufficient credits.'}), 403

    email = request.form.get('email', '').strip()
    username = request.form.get('username', '').strip() or None
    ip = request.remote_addr

    if not email or '@' not in email:
        return jsonify({'success': False, 'message': 'Invalid email.'}), 400

    result, error = send_otp_via_api(email, username)
    if error:
        success = 0
        error_msg = error
    else:
        success = 1 if result.get('data', {}).get('result') == 0 else 0
        error_msg = result.get('data', {}).get('message') if not success else None

    if success == 1:
        if not deduct_credit(user['id']):
            return jsonify({'success': False, 'message': 'Credit deduction failed.'}), 500
        session['user_credits'] = user['credits'] - 1
    else:
        session['user_credits'] = user['credits']

    # Log the request
    log_data = {
        'user_id': user['id'],
        'email': email,
        'username': username,
        'success': success,
        'error_message': error_msg,
        'ip_address': ip,
        'user_agent': request.headers.get('User-Agent'),
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    supabase.table('requests').insert(log_data).execute()

    if success == 1:
        return jsonify({'success': True, 'message': 'OTP sent successfully!', 'credits': session['user_credits']})
    else:
        return jsonify({'success': False, 'message': f"Server error: {error_msg or 'Failed.'}", 'credits': session['user_credits']})

@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        if not username or not email or not password:
            flash('All fields required.', 'error')
            return render_page(REGISTER_CONTENT, title="Register")
        if len(password) < 6:
            flash('Password too short.', 'error')
            return render_page(REGISTER_CONTENT, title="Register")
        if get_user_by_username(username):
            flash('Username taken.', 'error')
            return render_page(REGISTER_CONTENT, title="Register")
        if get_user_by_email(email):
            flash('Email registered.', 'error')
            return render_page(REGISTER_CONTENT, title="Register")
        hashed = hash_password(password)
        user_id = create_user(username, email, hashed, credits=1)
        if user_id:
            flash('Account created! Please log in.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Registration error.', 'error')
    return render_page(REGISTER_CONTENT, title="Register")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        if not email or not password:
            flash('Please fill all fields.', 'error')
            return render_page(LOGIN_CONTENT, title="Login")
        user = get_user_by_email(email)
        if not user or not verify_password(password, user['password_hash']):
            flash('Invalid credentials.', 'error')
            return render_page(LOGIN_CONTENT, title="Login")
        session['user_id'] = user['id']
        session['user_credits'] = user['credits']
        session['username'] = user['username']
        session['user_email'] = user['email']
        flash('Logged in.', 'success')
        return redirect(url_for('dashboard'))
    return render_page(LOGIN_CONTENT, title="Login")

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/profile')
@login_required
def profile():
    user = get_user_by_id(session['user_id'])
    if not user:
        flash('User error.', 'error')
        return redirect(url_for('logout'))
    session['user_credits'] = user['credits']
    # Fetch recent requests
    resp = supabase.table('requests').select('*').eq('user_id', user['id']).order('timestamp', desc=True).limit(20).execute()
    requests_logs = []
    for row in resp.data:
        ts = row.get('timestamp')
        if ts:
            # Convert to string if it's a datetime object
            if hasattr(ts, 'strftime'):
                ts = ts.strftime('%Y-%m-%d %H:%M:%S')
        requests_logs.append({
            'email': row.get('email'),
            'success': row.get('success'),
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
        return jsonify({'success': False, 'message': 'Enter code.'}), 400
    if expected_amount <= 0:
        return jsonify({'success': False, 'message': 'Select a package.'}), 400

    # Check if code exists
    resp = supabase.table('redeem_codes').select('*').eq('code', code).execute()
    if resp.data:
        existing = resp.data[0]
        if existing['status'] == 'approved':
            return jsonify({'success': False, 'message': 'Code already used.'}), 400
        elif existing['status'] == 'rejected':
            return jsonify({'success': False, 'message': 'Code rejected.'}), 400
        else:
            return jsonify({'success': True, 'message': 'Code already under review.'})

    # Insert new redeem code
    redeem_data = {
        'code': code,
        'user_id': session['user_id'],
        'amount': expected_amount,
        'status': 'pending',
        'requested_at': datetime.now(timezone.utc).isoformat(),
        'approved_at': None,
        'admin_notes': None
    }
    supabase.table('redeem_codes').insert(redeem_data).execute()
    return jsonify({'success': True, 'message': 'Code submitted for approval.'})

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == app.config['ADMIN_USERNAME'] and password == app.config['ADMIN_PASSWORD']:
            session['admin_logged_in'] = True
            flash('Admin logged in.', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid admin credentials.', 'error')
    return render_page(ADMIN_LOGIN_CONTENT, title="Admin Login")

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Admin logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    # Counts
    total_users = len(supabase.table('users').select('id', count='exact').execute().data)
    success_otp = len(supabase.table('requests').select('id', count='exact').eq('success', 1).execute().data)
    failed_otp = len(supabase.table('requests').select('id', count='exact').eq('success', 0).execute().data)
    pending_redeems = len(supabase.table('redeem_codes').select('id', count='exact').eq('status', 'pending').execute().data)

    # Pending redeem codes with user details
    resp_codes = supabase.table('redeem_codes').select('*, users(username)').eq('status', 'pending').order('requested_at').execute()
    pending_codes = []
    for row in resp_codes.data:
        pending_codes.append({
            'id': row['id'],
            'code': row['code'],
            'user_id': row['user_id'],
            'amount': row['amount'],
            'username': row.get('users', {}).get('username') if row.get('users') else None,
            'requested_at': row.get('requested_at')
        })

    # Recent logs with username
    resp_logs = supabase.table('requests').select('*, users(username)').order('timestamp', desc=True).limit(50).execute()
    logs = []
    for row in resp_logs.data:
        ts = row.get('timestamp')
        if hasattr(ts, 'strftime'):
            ts = ts.strftime('%Y-%m-%d %H:%M:%S')
        logs.append({
            'id': str(row['id'])[:8],
            'email': row.get('email'),
            'success': row.get('success'),
            'error_message': row.get('error_message'),
            'ip_address': row.get('ip_address'),
            'timestamp': ts,
            'username': row.get('users', {}).get('username') if row.get('users') else None
        })

    return render_page(ADMIN_DASHBOARD_CONTENT, title="Admin Dashboard",
                       total_users=total_users, success_otp=success_otp, failed_otp=failed_otp,
                       pending_redeems=pending_redeems, pending_codes=pending_codes, logs=logs)

@app.route('/admin/approve-code', methods=['POST'])
@admin_required
def admin_approve_code():
    code_id = request.form.get('code_id')
    action = request.form.get('action')
    amount = request.form.get('amount')
    if not code_id or not action:
        flash('Missing parameters.', 'error')
        return redirect(url_for('admin_dashboard'))

    # Fetch the code
    resp = supabase.table('redeem_codes').select('*').eq('id', code_id).execute()
    if not resp.data:
        flash('Code not found.', 'error')
        return redirect(url_for('admin_dashboard'))
    code_data = resp.data[0]

    if action == 'approve':
        try:
            amount = int(amount)
            if amount <= 0:
                flash('Amount must be positive.', 'error')
                return redirect(url_for('admin_dashboard'))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(url_for('admin_dashboard'))

        # Update status
        supabase.table('redeem_codes').update({
            'status': 'approved',
            'amount': amount,
            'approved_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', code_id).execute()

        # Add credits
        add_credits(code_data['user_id'], amount)
        if session.get('user_id') == code_data['user_id']:
            user = get_user_by_id(session['user_id'])
            if user:
                session['user_credits'] = user['credits']
        flash(f'Approved – added {amount} credits.', 'success')
    elif action == 'reject':
        supabase.table('redeem_codes').update({'status': 'rejected'}).eq('id', code_id).execute()
        flash('Rejected.', 'info')
    else:
        flash('Unknown action.', 'error')
    return redirect(url_for('admin_dashboard'))

@app.route('/api/send', methods=['POST'])
def api_send():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON required'}), 400
    api_key = data.get('api_key')
    if not api_key or api_key not in app.config['API_KEYS']:
        return jsonify({'error': 'Unauthorized'}), 401
    email = data.get('email', '').strip()
    if not email or '@' not in email:
        return jsonify({'error': 'Invalid email'}), 400
    username = data.get('username', '').strip() or None
    ip = request.remote_addr

    user_id = data.get('user_id')
    if user_id:
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        if user['credits'] < 1:
            return jsonify({'error': 'Insufficient credits'}), 403
    else:
        if not check_rate_limit(ip):
            return jsonify({'error': 'Rate limit exceeded'}), 429

    result, error = send_otp_via_api(email, username)
    if error:
        success = 0
        error_msg = error
    else:
        success = 1 if result.get('data', {}).get('result') == 0 else 0
        error_msg = result.get('data', {}).get('message') if not success else None

    if success == 1 and user_id:
        if not deduct_credit(user_id):
            return jsonify({'error': 'Credit deduction failed'}), 500

    log_data = {
        'user_id': user_id,
        'email': email,
        'username': username,
        'success': success,
        'error_message': error_msg,
        'ip_address': ip,
        'user_agent': request.headers.get('User-Agent'),
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    supabase.table('requests').insert(log_data).execute()

    if success:
        return jsonify({'status': 'success', 'message': 'OTP sent.'})
    else:
        return jsonify({'status': 'error', 'message': error_msg or 'Failed.'}), 500

# ---------- Main ----------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
