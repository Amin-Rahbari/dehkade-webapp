import sqlite3
import os
import math
import random
import time
import secrets
from functools import wraps
from datetime import datetime, timedelta, timezone
import jdatetime
from flask import Flask, render_template, request, redirect, url_for, session, g, flash, abort, get_flashed_messages
from jinja2 import DictLoader
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# ==========================================
# 1. تنظیمات و پیکربندی (System Config)
# ==========================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
UPLOAD_FOLDER = os.path.join(STATIC_FOLDER, 'uploads')
DATABASE = os.path.join(BASE_DIR, 'dehkade.db')

app = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path='/static')

app.secret_key = os.environ.get('SECRET_KEY', 'v32-ultra-secure-key-fixed-final-version-complete')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webp', 'pdf', 'zip', 'txt'}

if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER)
    except OSError as e:
        print(f"Error creating upload folder: {e}")

# ==========================================
# 2. لایه دیتابیس (Database Layer)
# ==========================================

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE, check_same_thread=False, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# ==========================================
# 3. ابزارها و امنیت (Utils & Security)
# ==========================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('لطفاً ابتدا وارد شوید.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.get('_csrf_token')
        if not token or token != request.form.get('_csrf_token'):
            if request.endpoint and 'static' not in request.endpoint:
                abort(403, description="Invalid CSRF Token")

def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(16)
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = generate_csrf_token

def fix_url(path):
    if not path: return ""
    if path.startswith(('http://', 'https://')):
        return path
    return f"/{path.lstrip('/')}"

app.jinja_env.filters['fix_url'] = fix_url

def is_video(path):
    if not path: return False
    return path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv'))

app.jinja_env.filters['is_video'] = is_video

def to_jalali(date_str, format_str="%Y/%m/%d - %H:%M"):
    if not date_str: return ""
    try:
        dt_obj = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                dt_obj = datetime.strptime(date_str, fmt)
                break
            except ValueError: continue
        
        if dt_obj:
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=timezone.utc)
            dt_iran = dt_obj.astimezone(timezone(timedelta(hours=3, minutes=30)))
            j_date = jdatetime.datetime.fromgregorian(datetime=dt_iran)
            return j_date.strftime(format_str)
        return date_str
    except: return date_str

app.jinja_env.filters['jalali'] = to_jalali

def safe_filename_generator(filename):
    if '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in ALLOWED_EXTENSIONS:
            base_name = secure_filename(filename.rsplit('.', 1)[0])[:10]
            return f"{int(time.time())}_{random.randint(1000,9999)}_{base_name}.{ext}"
    return None

def delete_file_from_disk(file_url):
    if not file_url or file_url.startswith(('http', '//')): return
    try:
        clean_url = file_url.lstrip('/')
        if not clean_url.startswith('static/'): return

        urls = clean_url.split(';')
        for url in urls:
            if not url: continue
            filename = os.path.basename(url)
            full_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(full_path):
                os.remove(full_path)
    except Exception as e:
        print(f"Error deleting file: {e}")

@app.context_processor
def inject_globals():
    try:
        db = get_db()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            categories = db.execute("SELECT * FROM categories").fetchall()
            ads = db.execute("SELECT * FROM ads WHERE expires_at > ? ORDER BY id DESC", (now_str,)).fetchall()
            popular_news = db.execute("SELECT id, title, views FROM news ORDER BY views DESC LIMIT 5").fetchall()
        except sqlite3.OperationalError:
            categories, ads, popular_news = [], [], []

        if ads and request.method == 'GET' and not request.path.startswith('/static') and not request.path.startswith('/admin'):
            if random.random() < 0.1:
                ids = [str(ad['id']) for ad in ads]
                if ids:
                    try:
                        db.execute(f"UPDATE ads SET views = views + 10 WHERE id IN ({','.join(ids)})")
                        db.commit()
                    except: pass

        unread_count = 0
        internal_unread_count = 0
        user_id = session.get('user_id')
        role = session.get('role')

        if user_id and role in ['admin', 'support', 'editor', 'marketer']:
            try:
                if role in ['admin', 'support']:
                    unread_count = db.execute("""
                        SELECT count(*) FROM messages 
                        WHERE parent_id IS NULL AND status IN ('new', 'user_reply')
                    """).fetchone()[0]
                
                private_unread = db.execute("SELECT count(*) FROM internal_chats WHERE receiver_id=? AND is_read=0", (user_id,)).fetchone()[0]
                
                last_seen_row = db.execute("SELECT last_seen_group_msg_id FROM users WHERE id=?", (user_id,)).fetchone()
                last_seen_id = last_seen_row[0] if last_seen_row and last_seen_row[0] else 0
                
                group_unread = db.execute("SELECT count(*) FROM internal_chats WHERE receiver_id=0 AND id > ?", (last_seen_id,)).fetchone()[0]
                
                internal_unread_count = private_unread + group_unread

            except Exception as e:
                pass

    except Exception:
        categories, ads, popular_news = [], [], []
        unread_count, internal_unread_count = 0, 0
        now_str = ""

    today_dt = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3, minutes=30)))
    today = jdatetime.date.fromgregorian(date=today_dt.date()).strftime("%A، %d %B %Y")
    
    return dict(
        categories=categories, 
        global_ads=ads, 
        global_popular=popular_news, 
        current_date_str=today, 
        now_str=now_str, 
        unread_msgs_count=unread_count,
        internal_unread_count=internal_unread_count, 
        ver=random.randint(1, 99999)
    )

# ==========================================
# 4. تعریف قالب‌ها (Templates)
# ==========================================

TEMPLATES = {}

UI_SCRIPTS = """
<script>
    document.addEventListener('DOMContentLoaded', function() {
        setTimeout(function() {
            const alerts = document.querySelectorAll('.flash-message');
            alerts.forEach(function(alert) {
                alert.style.transition = "opacity 0.5s ease, transform 0.5s ease";
                alert.style.opacity = "0";
                alert.style.transform = "translateY(-10px)";
                setTimeout(function(){ alert.remove(); }, 500);
            });
        }, 5000);

        const menuBtn = document.getElementById('mobile-menu-btn');
        const mobileMenu = document.getElementById('mobile-menu');
        if(menuBtn && mobileMenu){
            menuBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                mobileMenu.classList.toggle('hidden');
            });
            document.addEventListener('click', (e) => {
                if (!mobileMenu.contains(e.target) && !menuBtn.contains(e.target)) {
                    mobileMenu.classList.add('hidden');
                }
            });
        }

        const adminBtn = document.getElementById('admin-menu-btn');
        const adminSidebar = document.getElementById('admin-sidebar');
        const adminOverlay = document.getElementById('admin-overlay');
        
        function toggleAdminMenu() {
            if(adminSidebar) adminSidebar.classList.toggle('-translate-x-full');
            if(adminOverlay) adminOverlay.classList.toggle('hidden');
        }
        
        if(adminBtn) adminBtn.addEventListener('click', toggleAdminMenu);
        if(adminOverlay) adminOverlay.addEventListener('click', toggleAdminMenu);
    });
</script>
"""

SIDEBAR_TEMPLATE = """
<aside class="space-y-8">
    <div class="bg-white rounded-xl shadow-sm border p-4">
        <div class="flex items-center justify-between mb-4 border-b pb-2">
            <h3 class="font-bold text-gray-700 text-sm">پیام‌های بازرگانی</h3>
            <span class="text-[10px] bg-gray-100 px-2 rounded">Ads</span>
        </div>
        <div class="space-y-4">
            {% for ad in global_ads %}
            <a href="/ad_click/{{ ad.id }}" target="_blank" class="block group relative overflow-hidden rounded-lg border border-gray-100 hover:shadow-md transition">
                {% if ad.image %}
                    <img src="{{ ad.image | fix_url }}" class="w-full object-cover" alt="تبلیغ">
                {% endif %}
                <div class="p-3 bg-gray-50">
                    <p class="text-sm font-bold text-indigo-700 group-hover:underline truncate">{{ ad.title }}</p>
                    {% if ad.description %}
                    <p class="text-xs text-gray-500 mt-1 line-clamp-2">{{ ad.description }}</p>
                    {% endif %}
                </div>
            </a>
            {% else %}
            <a href="/contact" class="block border-2 border-dashed border-gray-300 rounded-lg p-6 text-center hover:bg-gray-50 hover:border-indigo-400 transition group">
                <i class="fas fa-bullhorn text-3xl text-gray-300 group-hover:text-indigo-500 mb-2 transition"></i>
                <p class="text-sm font-bold text-gray-500 group-hover:text-indigo-600">محل تبلیغات شما</p>
                <p class="text-xs text-gray-400 mt-1">برای رزرو تماس بگیرید</p>
            </a>
            {% endfor %}
            {% if global_ads %}
            <a href="/contact" class="block text-center text-xs text-gray-400 hover:text-indigo-600 mt-2">رزرو تبلیغات</a>
            {% endif %}
        </div>
    </div>
    <div class="bg-white rounded-xl shadow-sm border p-5 sticky top-24">
        <h3 class="font-bold text-gray-800 mb-4 flex items-center gap-2 border-r-4 border-red-500 pr-2">محبوب‌ترین‌ها</h3>
        <ul class="space-y-4">
            {% for item in global_popular %}
            <li class="flex gap-3 group">
                <span class="text-3xl font-black text-gray-200 group-hover:text-red-100 transition select-none">{{ loop.index }}</span>
                <div>
                    <h4 class="text-sm font-bold leading-6 group-hover:text-indigo-600 transition">
                        <a href="/post/{{ item.id }}" class="line-clamp-2">{{ item.title }}</a>
                    </h4>
                    <span class="text-[11px] text-gray-400 mt-1 block"><i class="fas fa-eye"></i> {{ item.views }} بازدید</span>
                </div>
            </li>
            {% endfor %}
        </ul>
    </div>
</aside>
"""

TEMPLATES['base.html'] = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>دهکده خبر | پایگاه خبری معتبر</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet"/>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { font-family: 'Vazirmatn', sans-serif; background-color: #f3f4f6; }
        .line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        .slider-container { position: relative; height: 450px; overflow: hidden; }
        @media (max-width: 768px) { .slider-container { height: 250px; } }
        .slide { position: absolute; inset: 0; opacity: 0; transition: opacity 0.8s; }
        .slide.active { opacity: 1; z-index: 1; }
        .dropdown:hover .dropdown-menu { display: block; animation: fadeIn 0.2s; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: #f1f1f1; }
        ::-webkit-scrollbar-thumb { background: #c7c7c7; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #a0a0a0; }
        .accordion-content { transition: max-height 0.3s ease-out; overflow: hidden; max-height: 0; }
        .accordion-active .accordion-content { max-height: 1000px; transition: max-height 0.5s ease-in; }
    </style>
</head>
<body class="flex flex-col min-h-screen text-gray-800">
    <header class="bg-white shadow-lg sticky top-0 z-50">
        <div class="container mx-auto px-4">
            <div class="flex flex-wrap justify-between items-center py-4 gap-4">
                <button id="mobile-menu-btn" class="md:hidden text-2xl text-indigo-900 focus:outline-none p-2">
                    <i class="fas fa-bars"></i>
                </button>
                <a href="/" class="flex items-center gap-3">
                    <div class="bg-indigo-700 text-white w-12 h-12 rounded-xl flex items-center justify-center shadow-lg"><i class="fas fa-newspaper text-2xl"></i></div>
                    <div class="flex flex-col"><h1 class="text-xl md:text-2xl font-black text-indigo-900 tracking-tight">دهکده خبر</h1><span class="text-[10px] font-bold text-gray-500">صدای رسای جنوب ایران</span></div>
                </a>
                <div class="hidden lg:flex items-center gap-4 text-xs font-bold text-gray-500 bg-gray-50 px-4 py-2 rounded-full border">
                    <span>{{ current_date_str }}</span>
                    <span class="border-r pr-4"><i class="far fa-clock text-indigo-600"></i> <span id="clock">...</span></span>
                </div>
                <div class="flex items-center gap-3">
                    <form action="/search" method="get" class="relative hidden md:block">
                        <input type="text" name="q" placeholder="جستجو..." value="{{ request.args.get('q', '') }}" class="bg-gray-100 rounded-full py-2 px-4 text-sm focus:ring-2 focus:ring-indigo-600 outline-none w-48 transition focus:w-64">
                        <button class="absolute left-3 top-2 text-gray-400"><i class="fas fa-search"></i></button>
                    </form>
                    {% if session.get('user_id') %}
                        {% if session.get('role') == 'user' %}
                             <a href="/profile" class="bg-gray-100 text-indigo-900 px-3 py-2 rounded-lg text-xs md:text-sm font-bold hover:bg-gray-200 transition"><i class="fas fa-user"></i> <span class="hidden md:inline">پروفایل</span></a>
                        {% else %}
                             <a href="/admin" class="bg-indigo-600 text-white px-3 py-2 rounded-lg text-xs md:text-sm font-bold shadow hover:bg-indigo-700 transition">پنل مدیریت</a>
                        {% endif %}
                    {% else %}
                        <a href="/login" class="text-xs md:text-sm font-bold text-indigo-600 hover:text-indigo-800 bg-indigo-50 px-3 py-1.5 rounded-lg">ورود / عضویت</a>
                    {% endif %}
                </div>
            </div>

            <nav class="hidden md:flex items-center gap-1 border-t py-1 text-sm font-bold text-gray-700">
                <a href="/" class="px-3 py-3 hover:text-indigo-600 hover:bg-indigo-50 rounded transition">صفحه اصلی</a>
                {% for cat in categories[:7] %}
                <a href="/category/{{ cat.id }}" class="px-3 py-3 hover:text-indigo-600 hover:bg-indigo-50 rounded transition">{{ cat.name }}</a>
                {% endfor %}
                {% if categories|length > 7 %}
                <div class="relative dropdown px-3 py-3 cursor-pointer hover:bg-indigo-50 rounded">
                    <span class="hover:text-indigo-600 flex items-center gap-1">سایر <i class="fas fa-chevron-down text-xs"></i></span>
                    <div class="dropdown-menu absolute top-full right-0 bg-white shadow-xl rounded-xl border w-48 hidden z-50">
                        {% for cat in categories[7:] %}
                        <a href="/category/{{ cat.id }}" class="block px-4 py-3 hover:bg-gray-50 border-b last:border-0">{{ cat.name }}</a>
                        {% endfor %}
                    </div>
                </div>
                {% endif %}
                <a href="/archive" class="px-3 py-3 hover:text-indigo-600 hover:bg-indigo-50 rounded transition">آرشیو اخبار</a>
                <div class="mr-auto flex gap-1">
                    <a href="/about" class="px-3 py-3 text-gray-500 hover:text-indigo-600">درباره ما</a>
                    <a href="/contact" class="px-3 py-3 text-gray-500 hover:text-indigo-600">ارتباط با ما</a>
                </div>
            </nav>

            <div id="mobile-menu" class="hidden md:hidden border-t py-4 bg-white shadow-inner">
                <form action="/search" method="get" class="mb-4 relative px-3">
                    <input type="text" name="q" placeholder="جستجو..." class="w-full bg-gray-100 rounded-lg py-2 px-4 text-sm focus:ring-2 focus:ring-indigo-600 outline-none">
                    <button class="absolute left-6 top-2 text-gray-400"><i class="fas fa-search"></i></button>
                </form>
                <div class="flex flex-col space-y-2 font-bold text-sm">
                    <a href="/" class="block px-3 py-2 rounded hover:bg-indigo-50">صفحه اصلی</a>
                    {% for cat in categories %}
                    <a href="/category/{{ cat.id }}" class="block px-3 py-2 rounded hover:bg-indigo-50">{{ cat.name }}</a>
                    {% endfor %}
                    <div class="border-t my-2 pt-2"></div>
                    {% if session.get('user_id') %}
                        <a href="/profile" class="block px-3 py-2 rounded hover:bg-indigo-50 text-indigo-700 bg-indigo-50">
                            <i class="fas fa-user-circle ml-2"></i> پروفایل کاربری ({{ session.name }})
                        </a>
                        {% if session.get('role') != 'user' %}
                        <a href="/admin" class="block px-3 py-2 rounded hover:bg-indigo-50 text-indigo-700">
                            <i class="fas fa-tools ml-2"></i> پنل مدیریت
                        </a>
                        {% endif %}
                        <a href="/logout" class="block px-3 py-2 rounded hover:bg-red-50 text-red-600">
                            <i class="fas fa-sign-out-alt ml-2"></i> خروج از حساب
                        </a>
                    {% else %}
                        <a href="/login" class="block px-3 py-2 rounded hover:bg-indigo-50 text-indigo-700"><i class="fas fa-sign-in-alt ml-2"></i> ورود به حساب</a>
                        <a href="/register" class="block px-3 py-2 rounded hover:bg-indigo-50 text-indigo-700"><i class="fas fa-user-plus ml-2"></i> ثبت نام</a>
                    {% endif %}
                    <div class="border-t my-2 pt-2"></div>
                    <a href="/contact" class="block px-3 py-2 rounded hover:bg-indigo-50">ارتباط با ما</a>
                    <a href="/about" class="block px-3 py-2 rounded hover:bg-indigo-50">درباره ما</a>
                </div>
            </div>
        </div>
    </header>
    <main class="container mx-auto px-4 py-8 flex-grow">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
            <div class="fixed top-24 left-4 right-4 md:right-auto md:left-4 md:w-96 z-[100] space-y-2 pointer-events-none">
                {% for category, message in messages %}
                <div class="flash-message pointer-events-auto p-4 rounded-xl flex items-center gap-3 shadow-lg border {{ 'bg-green-50 text-green-800 border-green-200' if category == 'success' else ('bg-yellow-50 text-yellow-800 border-yellow-200' if category == 'warning' else 'bg-red-50 text-red-800 border-red-200') }} transition-all duration-500">
                    <i class="{{ 'fas fa-check-circle' if category == 'success' else 'fas fa-exclamation-circle' }} text-xl"></i>
                    <p class="text-sm font-bold">{{ message }}</p>
                </div>
                {% endfor %}
            </div>
            {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </main>
    <footer class="bg-indigo-950 text-indigo-200 mt-auto pt-12 pb-6">
        <div class="container mx-auto px-4">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-8 mb-8 border-b border-indigo-900 pb-8">
                <div>
                    <h3 class="text-white font-bold text-lg mb-4 flex items-center gap-2"><i class="fas fa-newspaper text-yellow-500"></i> دهکده خبر</h3>
                    <p class="text-sm leading-7 opacity-80 text-justify">دهکده خبر، رسانه مستقل خبری شخصی است که با هدف اعتلای سطح آگاهی عمومی فعالیت می‌کند.</p>
                </div>
                <div>
                    <h3 class="text-white font-bold mb-4">لینک‌های مفید</h3>
                    <ul class="space-y-2 text-sm">
                        <li><a href="/" class="hover:text-white">صفحه اصلی</a></li>
                        <li><a href="/archive" class="hover:text-white">آرشیو اخبار</a></li>
                        <li><a href="/about" class="hover:text-white">درباره ما</a></li>
                        <li><a href="/contact" class="hover:text-white">ارتباط با ما</a></li>
                    </ul>
                </div>
                <div>
                    <h3 class="text-white font-bold mb-4">ارتباط با ما</h3>
                    <ul class="space-y-3 text-sm">
                        <li class="flex items-start gap-3"><i class="fas fa-map-marker-alt text-yellow-500 mt-1"></i><span class="leading-6">هرمزگان، بندرعباس، بلوار علی ابن ابیطالب، روبروی اداره کل زندان‌های استان، دانشگاه ملی مهارت بندرعباس، ساختمان آموزش شماره ۲، طبقه ۳</span></li>
                        <li class="flex items-center gap-3"><i class="fas fa-phone text-yellow-500"></i><span class="dir-ltr">076-42870000</span></li>
                        <li class="flex items-center gap-3"><i class="fas fa-envelope text-yellow-500"></i><span>info@dehkadekhabar.ir</span></li>
                    </ul>
                </div>
            </div>
            <div class="text-center text-xs opacity-50">&copy; 1404 تمامی حقوق محفوظ است. | طراحی: امین رهبری سکل</div>
        </div>
    </footer>
    <script>setInterval(() => { let d = document.getElementById('clock'); if(d) d.innerText = new Date().toLocaleTimeString('fa-IR'); }, 1000);</script>
    """ + UI_SCRIPTS + """
</body>
</html>
"""

TEMPLATES['admin_layout.html'] = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>پنل مدیریت</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet"/>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>body { font-family: 'Vazirmatn'; background: #f3f4f6; }</style>
</head>
<body class="bg-gray-100">
    <div class="flex min-h-screen relative">
        <div class="md:hidden fixed top-0 w-full bg-gray-900 text-white z-50 p-4 flex justify-between items-center shadow-md">
            <span class="font-bold flex items-center gap-2"><i class="fas fa-cog"></i> پنل مدیریت</span>
            <button id="admin-menu-btn" class="text-white focus:outline-none z-50"><i class="fas fa-bars text-xl"></i></button>
        </div>
        <div id="admin-overlay" class="fixed inset-0 bg-black/50 z-40 hidden md:hidden transition-opacity"></div>
        <aside id="admin-sidebar" class="w-72 bg-gray-900 text-gray-300 flex-shrink-0 flex-col h-full fixed md:relative pt-16 md:pt-0 overflow-y-auto transition-transform duration-300 transform -translate-x-full md:translate-x-0 z-50 top-0 right-0 shadow-2xl md:shadow-none">
            <div class="p-6 text-center border-b border-gray-800 bg-gray-950">
                <div class="w-16 h-16 rounded-full bg-indigo-600 flex items-center justify-center mx-auto text-2xl text-white font-bold mb-3 border-4 border-gray-800 shadow-xl">{{ session.name[0] }}</div>
                <h2 class="font-bold text-white">{{ session.name }}</h2>
                <span class="text-xs bg-gray-800 px-2 py-0.5 rounded mt-1 inline-block uppercase">{{ session.role }}</span>
            </div>
            <nav class="flex-1 p-4 space-y-1 text-sm">
                <p class="px-3 text-xs font-bold text-gray-500 uppercase mt-4 mb-2">اصلی</p>
                <a href="/admin" class="block p-3 rounded-lg hover:bg-gray-800 hover:text-white transition flex items-center gap-3"><i class="fas fa-home w-5"></i> داشبورد</a>
                
                {% if session.role in ['admin', 'editor'] %}
                <p class="px-3 text-xs font-bold text-gray-500 uppercase mt-4 mb-2">محتوا</p>
                <a href="/admin/news" class="block p-3 rounded-lg hover:bg-gray-800 hover:text-white transition flex items-center gap-3"><i class="fas fa-newspaper w-5"></i> اخبار</a>
                <a href="/admin/categories" class="block p-3 rounded-lg hover:bg-gray-800 hover:text-white transition flex items-center gap-3"><i class="fas fa-layer-group w-5"></i> دسته‌بندی‌ها</a>
                {% endif %}
                
                {% if session.role in ['admin', 'support'] %}
                <p class="px-3 text-xs font-bold text-gray-500 uppercase mt-4 mb-2">تعاملات</p>
                <a href="/admin/messages" class="block p-3 rounded-lg hover:bg-gray-800 hover:text-white transition flex items-center gap-3 justify-between">
                    <span class="flex items-center gap-3"><i class="fas fa-envelope w-5"></i> تیکت‌ها</span>
                    {% if unread_msgs_count > 0 %}
                    <span class="bg-red-500 text-white text-[10px] px-2 py-0.5 rounded-full shadow-sm animate-pulse">{{ unread_msgs_count }}</span>
                    {% endif %}
                </a>
                {% endif %}

                {% if session.role in ['admin', 'support', 'editor', 'marketer'] %}
                <a href="/admin/chat" class="block p-3 rounded-lg hover:bg-gray-800 hover:text-white transition flex items-center gap-3 justify-between">
                    <span class="flex items-center gap-3"><i class="fas fa-comments w-5"></i> چت داخلی</span>
                    {% if internal_unread_count > 0 %}
                    <span class="bg-blue-500 text-white text-[10px] px-2 py-0.5 rounded-full shadow-sm">{{ internal_unread_count }}</span>
                    {% endif %}
                </a>
                {% endif %}
                
                {% if session.role in ['admin', 'marketer'] %}
                <a href="/admin/ads" class="block p-3 rounded-lg hover:bg-gray-800 hover:text-white transition flex items-center gap-3"><i class="fas fa-ad w-5"></i> تبلیغات</a>
                {% endif %}

                {% if session.role == 'admin' %}
                <p class="px-3 text-xs font-bold text-gray-500 uppercase mt-4 mb-2">سیستم</p>
                <a href="/admin/users" class="block p-3 rounded-lg hover:bg-gray-800 hover:text-white transition flex items-center gap-3"><i class="fas fa-users w-5"></i> کاربران</a>
                {% endif %}
            </nav>
            <div class="p-4 bg-gray-950 border-t border-gray-800">
                <a href="/" class="block p-3 rounded text-center border border-gray-700 hover:bg-gray-800 text-xs mb-2">مشاهده سایت</a>
                <a href="/logout" class="block p-3 rounded text-center bg-red-900/50 hover:bg-red-800 text-red-200 text-xs">خروج امن</a>
            </div>
        </aside>
        
        <div class="flex-1 flex flex-col h-screen overflow-hidden pt-14 md:pt-0">
            <main class="flex-1 p-4 md:p-8 overflow-y-auto">
                {% with messages = get_flashed_messages(with_categories=true) %}
                    {% if messages %}
                    <div class="fixed top-24 left-4 right-4 md:right-auto md:left-4 md:w-96 z-[100] space-y-2 pointer-events-none">
                        {% for category, message in messages %}
                        <div class="flash-message pointer-events-auto p-4 rounded-xl flex items-center gap-3 shadow-lg border {{ 'bg-green-50 text-green-800 border-green-200' if category == 'success' else 'bg-red-50 text-red-800 border-red-200' }} transition-all duration-500">
                            <i class="{{ 'fas fa-check-circle' if category == 'success' else 'fas fa-exclamation-circle' }} text-xl"></i>
                            <p class="text-sm font-bold">{{ message }}</p>
                        </div>
                        {% endfor %}
                    </div>
                    {% endif %}
                {% endwith %}
                {% block admin_content %}{% endblock %}
            </main>
        </div>
    </div>
    """ + UI_SCRIPTS + """
</body>
</html>
"""

TEMPLATES['admin_messages.html'] = """
{% extends "admin_layout.html" %}
{% block admin_content %}
<div class="flex flex-col h-[calc(100vh-100px)]">
    <div class="flex justify-between items-center mb-6">
        <h1 class="text-2xl font-bold">مدیریت تیکت‌ها</h1>
        <div class="flex gap-2">
            <a href="?type=users" class="px-4 py-2 rounded-lg text-sm {{ 'bg-indigo-600 text-white' if request.args.get('type', 'users') == 'users' else 'bg-white text-gray-600' }}">کاربران عضو</a>
            <a href="?type=guests" class="px-4 py-2 rounded-lg text-sm {{ 'bg-indigo-600 text-white' if request.args.get('type') == 'guests' else 'bg-white text-gray-600' }}">کاربران مهمان</a>
        </div>
    </div>

    {% if selected_ticket %}
    <!-- حالت مشاهده مکالمه -->
    <div class="bg-white rounded-2xl shadow-sm border border-gray-100 flex flex-col flex-1 overflow-hidden">
        <!-- هدر تیکت -->
        <div class="p-4 border-b bg-gray-50 flex justify-between items-center">
            <div class="flex items-center gap-3">
                <a href="/admin/messages?type={{ request.args.get('type', 'users') }}" class="text-gray-500 hover:text-indigo-600"><i class="fas fa-arrow-right"></i></a>
                <div>
                    <h2 class="font-bold text-gray-800">{{ selected_ticket.subject }}</h2>
                    <span class="text-xs text-gray-500">{{ selected_ticket.name }} - {{ selected_ticket.created_at | jalali }}</span>
                    {% if selected_ticket.status == 'closed' %}
                        <span class="bg-gray-200 text-gray-600 text-[10px] px-2 rounded ml-2">بسته شده</span>
                    {% endif %}
                </div>
            </div>
            <div class="flex gap-2">
                {% if selected_ticket.status != 'closed' %}
                <a href="/admin/messages/status/{{ selected_ticket.id }}/closed" class="bg-gray-200 text-gray-700 px-3 py-1 rounded text-xs hover:bg-gray-300">بستن تیکت</a>
                {% endif %}
                <form action="/admin/messages/delete/{{ selected_ticket.id }}" method="post" onsubmit="return confirm('حذف کامل تیکت؟')" class="inline">
                    <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                    <button class="bg-red-50 text-red-500 px-3 py-1 rounded text-xs hover:bg-red-100">حذف</button>
                </form>
            </div>
        </div>

        <!-- محتوای پیام‌ها -->
        <div class="flex-1 overflow-y-auto p-6 space-y-4 bg-gray-50">
            <!-- پیام اصلی -->
            <div class="bg-white p-4 rounded-xl border border-gray-200 shadow-sm relative mr-auto ml-10">
                <div class="flex justify-between text-xs text-gray-400 mb-2">
                    <span class="font-bold text-indigo-900">{{ selected_ticket.name }}</span>
                    <span>{{ selected_ticket.created_at | jalali }}</span>
                </div>
                <p class="text-sm text-gray-800 leading-6">{{ selected_ticket.message }}</p>
                {% if selected_ticket.contact_info %}
                <div class="mt-2 pt-2 border-t text-xs text-gray-500">شماره تماس/ایمیل: {{ selected_ticket.contact_info }}</div>
                {% endif %}
            </div>

            <!-- پاسخ‌ها -->
            {% for r in replies %}
            <div class="p-4 rounded-xl border shadow-sm relative w-fit max-w-[85%] {{ 'bg-indigo-100 border-indigo-200 ml-auto mr-10' if r.sender_id else 'bg-white border-gray-200 mr-auto ml-10' }}">
                {% if r.is_read == 0 and not r.sender_id %}
                    <span class="absolute -top-2 -right-2 bg-red-500 text-white text-[9px] px-2 py-0.5 rounded-full animate-pulse shadow">پیام جدید</span>
                {% endif %}
                <div class="flex justify-between text-xs opacity-60 mb-2 gap-4">
                    <span class="font-bold">{{ 'پشتیبان' if r.sender_id else r.name }}</span>
                    <span>{{ r.created_at | jalali }}</span>
                </div>
                <p class="text-sm leading-6">{{ r.message }}</p>
            </div>
            {% endfor %}
        </div>

        <!-- فرم ارسال پاسخ -->
        {% if selected_ticket.status != 'closed' %}
            {% if selected_ticket.user_id %}
            <div class="p-4 border-t bg-white">
                <form action="/admin/messages/reply/{{ selected_ticket.id }}" method="post" class="flex gap-2">
                    <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                    <input type="text" name="reply" placeholder="نوشتن پاسخ به کاربر..." class="flex-grow border rounded-lg px-4 py-3 text-sm focus:ring-2 focus:ring-indigo-500 outline-none" required>
                    <button class="bg-indigo-600 text-white px-6 py-2 rounded-lg font-bold hover:bg-indigo-700 shadow transition">ارسال</button>
                </form>
            </div>
            {% else %}
            <div class="p-4 border-t bg-yellow-50 text-center text-sm text-yellow-800 font-bold border-yellow-200">
                این تیکت از طرف کاربر مهمان ارسال شده است. امکان پاسخگویی سیستمی وجود ندارد. لطفاً با شماره تماس یا ایمیل ارائه شده در متن پیام تماس بگیرید.
            </div>
            {% endif %}
        {% else %}
        <div class="p-4 border-t bg-gray-100 text-center text-sm text-gray-500 font-bold">
            این تیکت بسته شده است.
        </div>
        {% endif %}
    </div>

    {% else %}
    <!-- حالت لیست تیکت‌ها -->
    <div class="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
        <div class="overflow-x-auto">
            <table class="w-full text-right text-sm">
                <thead class="bg-gray-50 border-b text-gray-500">
                    <tr>
                        <th class="p-4">کاربر / موضوع</th>
                        <th class="p-4">وضعیت</th>
                        <th class="p-4">تاریخ آخرین فعالیت</th>
                        <th class="p-4 text-center">عملیات</th>
                    </tr>
                </thead>
                <tbody class="divide-y">
                    {% for m in msgs %}
                    <tr class="hover:bg-gray-50 transition cursor-pointer" onclick="window.location='/admin/messages?view={{ m.id }}&type={{ request.args.get('type', 'users') }}'">
                        <td class="p-4">
                            <div class="flex items-center gap-3">
                                <div class="w-10 h-10 rounded-full flex items-center justify-center font-bold text-white {{ 'bg-red-500' if m.status in ['new', 'user_reply'] else 'bg-gray-400' }}">
                                    {{ m.name[0] }}
                                </div>
                                <div>
                                    <div class="font-bold text-gray-900 flex items-center gap-2">
                                        {{ m.subject }}
                                        {% if m.status in ['new', 'user_reply'] %}
                                            <span class="w-2 h-2 bg-red-500 rounded-full animate-pulse"></span>
                                        {% endif %}
                                    </div>
                                    <div class="text-xs text-gray-500">{{ m.name }}</div>
                                </div>
                            </div>
                        </td>
                        <td class="p-4">
                            {% if m.status == 'new' %}
                                <span class="bg-red-100 text-red-600 px-2 py-1 rounded-full text-xs font-bold">تیکت جدید</span>
                            {% elif m.status == 'user_reply' %}
                                <span class="bg-orange-100 text-orange-600 px-2 py-1 rounded-full text-xs font-bold">پاسخ جدید کاربر</span>
                            {% elif m.status == 'answered' %}
                                <span class="bg-green-100 text-green-600 px-2 py-1 rounded-full text-xs">پاسخ داده شده</span>
                            {% elif m.status == 'closed' %}
                                <span class="bg-gray-200 text-gray-600 px-2 py-1 rounded-full text-xs">بسته شده</span>
                            {% elif m.status == 'pending' %}
                                <span class="bg-yellow-100 text-yellow-600 px-2 py-1 rounded-full text-xs">در حال بررسی</span>
                            {% endif %}
                        </td>
                        <td class="p-4 text-gray-500 dir-ltr text-right">{{ m.created_at | jalali }}</td>
                        <td class="p-4 flex justify-center gap-2" onclick="event.stopPropagation()">
                            <a href="/admin/messages?view={{ m.id }}&type={{ request.args.get('type', 'users') }}" class="bg-indigo-50 text-indigo-600 px-3 py-1 rounded hover:bg-indigo-100 text-xs">مشاهده</a>
                        </td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="4" class="p-8 text-center text-gray-400">هیچ تیکتی یافت نشد.</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    {% endif %}
</div>
{% endblock %}
"""

TEMPLATES['admin_dashboard.html'] = """
{% extends "admin_layout.html" %}
{% block admin_content %}
    <h1 class="text-2xl font-black mb-8 text-gray-800">داشبورد مدیریت</h1>
    <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <p class="text-gray-500 text-sm font-bold">کل اخبار</p>
            <p class="text-3xl font-black text-gray-800 mt-2">{{ c.news }}</p>
        </div>
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <p class="text-gray-500 text-sm font-bold">بازدید کل</p>
            <p class="text-3xl font-black text-gray-800 mt-2">{{ c.views }}</p>
        </div>
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <p class="text-gray-500 text-sm font-bold">تیکت‌های فعال</p>
            <p class="text-3xl font-black text-gray-800 mt-2 flex items-center gap-2">
                {{ c.unread_msgs }}
                <span class="text-xs font-normal text-gray-400">(تعداد گفتگوها)</span>
            </p>
        </div>
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <p class="text-gray-500 text-sm font-bold">تبلیغات فعال</p>
            <p class="text-3xl font-black text-gray-800 mt-2">{{ c.ads }}</p>
        </div>
    </div>
    
    <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 overflow-x-auto">
        <h3 class="font-bold text-lg mb-4">آمار عملکرد تبلیغات</h3>
        <table class="w-full text-right text-sm">
            <thead class="bg-gray-50 text-gray-500">
                <tr><th>تبلیغ</th><th>بازدید (Views)</th><th>کلیک (Clicks)</th><th>انقضا</th></tr>
            </thead>
            <tbody>
                {% for ad in ads_stats %}
                <tr class="border-b last:border-0">
                    <td class="p-3">{{ ad.title }}</td>
                    <td class="p-3">{{ ad.views }}</td>
                    <td class="p-3">{{ ad.clicks }}</td>
                    <td class="p-3 dir-ltr text-right">{{ ad.expires_at | jalali('%Y/%m/%d') }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
{% endblock %}
"""

TEMPLATES['home.html'] = """
{% extends "base.html" %}
{% block content %}
    {% if featured and not request.args.get('page') and not request.args.get('q') %}
    <div class="slider-container rounded-2xl shadow-xl mb-12 bg-gray-900 group relative overflow-hidden">
        {% for item in featured %}
        <div class="slide {{ 'active' if loop.first else '' }} w-full h-full">
            {% set imgs = item.images.split(';') if item.images else [] %}
            <img src="{{ imgs[0] | fix_url }}" class="w-full h-full object-cover opacity-60 group-hover:scale-105 transition duration-[2s]" onerror="this.src='https://via.placeholder.com/800x400?text=No+Image'">
            <div class="absolute inset-0 bg-gradient-to-t from-black via-transparent to-transparent"></div>
            <div class="absolute bottom-0 right-0 p-8 md:p-16 max-w-4xl text-white">
                <span class="bg-yellow-500 text-indigo-900 text-xs font-bold px-3 py-1 rounded-full mb-3 inline-block shadow-lg">{{ item.cat_name }}</span>
                <h2 class="text-xl md:text-5xl font-black mb-4 leading-tight drop-shadow-lg shadow-black">{{ item.title }}</h2>
                <a href="/post/{{ item.id }}" class="inline-block bg-white text-indigo-900 px-6 py-2 rounded-full font-bold text-sm hover:bg-yellow-400 transition">مشاهده خبر</a>
            </div>
        </div>
        {% endfor %}
        <button onclick="changeSlide(1)" class="absolute left-4 top-1/2 bg-white/20 hover:bg-white/40 text-white p-3 rounded-full backdrop-blur z-20"><i class="fas fa-chevron-left"></i></button>
        <button onclick="changeSlide(-1)" class="absolute right-4 top-1/2 bg-white/20 hover:bg-white/40 text-white p-3 rounded-full backdrop-blur z-20"><i class="fas fa-chevron-right"></i></button>
    </div>
    <script>
        let idx = 0; const slides = document.querySelectorAll('.slide');
        function changeSlide(n) { if(!slides.length) return; slides[idx].classList.remove('active'); idx = (idx + n + slides.length) % slides.length; slides[idx].classList.add('active'); }
        if(slides.length) setInterval(()=>changeSlide(1), 5000);
    </script>
    {% endif %}

    <div class="grid grid-cols-1 lg:grid-cols-4 gap-8">
        <div class="lg:col-span-3">
            <h2 class="text-xl font-black border-r-4 border-indigo-600 pr-3 mb-8">{{ page_title if page_title else 'آخرین اخبار' }}</h2>
            <div class="grid md:grid-cols-2 gap-6 mb-8">
                {% for item in latest %}
                <article class="bg-white rounded-2xl shadow-sm border hover:shadow-xl transition group overflow-hidden flex flex-col h-full">
                    <div class="h-48 relative overflow-hidden bg-gray-100">
                        {% set imgs = item.images.split(';') if item.images else [] %}
                        <a href="/post/{{ item.id }}" class="block w-full h-full">
                            {% if imgs and imgs[0] %}
                                <img src="{{ imgs[0] | fix_url }}" class="w-full h-full object-cover group-hover:scale-110 transition duration-700" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
                                <div class="hidden w-full h-full items-center justify-center bg-indigo-50 text-indigo-300">
                                    <div class="text-center">
                                        <i class="fas fa-newspaper text-4xl mb-2"></i>
                                        <span class="block text-xs font-bold">بدون تصویر</span>
                                    </div>
                                </div>
                            {% else %}
                                <div class="w-full h-full flex items-center justify-center bg-indigo-50 text-indigo-300 group-hover:scale-110 transition duration-700">
                                    <div class="text-center">
                                        <i class="fas fa-newspaper text-4xl mb-2"></i>
                                        <span class="block text-xs font-bold">بدون تصویر</span>
                                    </div>
                                </div>
                            {% endif %}
                        </a>
                        <span class="absolute top-3 right-3 bg-white/90 backdrop-blur text-xs font-bold px-2 py-1 rounded text-indigo-900">{{ item.cat_name }}</span>
                    </div>
                    <div class="p-5 flex flex-col flex-grow">
                        <h3 class="font-bold text-gray-800 mb-3 line-clamp-2 leading-7 group-hover:text-indigo-600 transition"><a href="/post/{{ item.id }}">{{ item.title }}</a></h3>
                        <div class="mt-auto flex justify-between text-xs text-gray-400 border-t pt-3">
                            <span class="flex items-center gap-1">
                                <i class="far fa-clock"></i> {{ item.created_at | jalali }}
                                {% if item.updated_at %}
                                    <span class="text-[10px] text-yellow-600 bg-yellow-50 px-1 rounded mr-1">(ویرایش شده)</span>
                                {% endif %}
                            </span>
                            <span><i class="far fa-eye"></i> {{ item.views }}</span>
                        </div>
                    </div>
                </article>
                {% else %}
                <p class="col-span-2 text-center text-gray-500 py-10">موردی یافت نشد.</p>
                {% endfor %}
            </div>
            
            {% if total_pages > 1 %}
            <div class="flex justify-center gap-2 mt-8">
                {% if current_page > 1 %}
                <a href="?page={{ current_page - 1 }}{{ '&q=' + request.args.get('q') if request.args.get('q') else '' }}" class="bg-white border px-4 py-2 rounded-lg hover:bg-indigo-50">قبلی</a>
                {% endif %}
                <span class="bg-indigo-600 text-white px-4 py-2 rounded-lg">{{ current_page }} از {{ total_pages }}</span>
                {% if current_page < total_pages %}
                <a href="?page={{ current_page + 1 }}{{ '&q=' + request.args.get('q') if request.args.get('q') else '' }}" class="bg-white border px-4 py-2 rounded-lg hover:bg-indigo-50">بعدی</a>
                {% endif %}
            </div>
            {% endif %}
        </div>
        <div class="lg:col-span-1">""" + SIDEBAR_TEMPLATE + """</div>
    </div>
{% endblock %}
"""

TEMPLATES['post.html'] = """
{% extends "base.html" %}
{% block content %}
<div class="grid grid-cols-1 lg:grid-cols-4 gap-8">
    <div class="lg:col-span-3">
        <div class="bg-white rounded-3xl shadow-lg border overflow-hidden p-6 md:p-10 mb-8">
            <nav class="text-xs font-bold text-gray-400 mb-6 flex gap-2">
                <a href="/" class="hover:text-indigo-600">خانه</a> / 
                <a href="/category/{{ post.category_id }}" class="hover:text-indigo-600">{{ post.cat_name }}</a>
            </nav>
            <h1 class="text-2xl md:text-4xl font-black text-gray-900 mb-6 leading-snug">{{ post.title }}</h1>
            <div class="flex flex-wrap gap-6 text-xs font-bold text-gray-500 border-b pb-6 mb-6">
                <span class="bg-gray-50 px-3 py-1 rounded-full"><i class="far fa-calendar text-indigo-600"></i> {{ post.created_at | jalali }}</span>
                {% if post.updated_at %}
                    <span class="bg-yellow-50 text-yellow-700 px-3 py-1 rounded-full">ویرایش شده در {{ post.updated_at | jalali }}</span>
                {% endif %}
                <span class="bg-gray-50 px-3 py-1 rounded-full"><i class="far fa-eye text-indigo-600"></i> {{ post.views }} بازدید</span>
                <span class="bg-gray-50 px-3 py-1 rounded-full"><i class="far fa-user text-indigo-600"></i> {{ post.author_name }}</span>
            </div>
            {% if post.video %}
            <div class="mb-8 rounded-2xl overflow-hidden bg-black shadow-xl">
                 <video controls class="w-full max-h-[500px]"><source src="{{ post.video | fix_url }}" type="video/mp4"></video>
            </div>
            {% endif %}
            {% set imgs = post.images.split(';') if post.images else [] %}
            {% if imgs and imgs[0] %}
                <img src="{{ imgs[0] | fix_url }}" class="w-full rounded-2xl shadow-md mb-6" onerror="this.style.display='none'">
                {% if imgs|length > 1 %}
                    <div class="grid grid-cols-4 gap-3 mb-8">
                        {% for img in imgs[1:] %}
                            <div class="cursor-pointer rounded-xl overflow-hidden border-2 border-transparent hover:border-indigo-600 transition" onclick="window.open('{{ img | fix_url }}')">
                                <img src="{{ img | fix_url }}" class="w-full h-20 object-cover hover:scale-110 transition" onerror="this.parentElement.style.display='none'">
                            </div>
                        {% endfor %}
                    </div>
                {% endif %}
            {% endif %}
            <div class="prose max-w-none text-justify leading-9 text-gray-800 font-light text-lg">
                {{ post.content | replace('\\n', '<br>') | safe }}
            </div>
        </div>

        <div class="bg-white rounded-3xl shadow border p-6 md:p-8">
            <h3 class="text-xl font-bold mb-6 flex items-center gap-2"><i class="far fa-comments text-indigo-600"></i> نظرات کاربران</h3>
            
            {% if session.get('user_id') %}
            <form action="/post/{{ post.id }}/comment" method="post" class="mb-8 bg-gray-50 p-4 rounded-xl border border-gray-100">
                <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                <div class="flex justify-between items-center mb-2">
                    <p class="text-sm font-bold">نظر شما چیست؟</p>
                    <select name="rating" class="text-xs border rounded p-1 bg-white">
                        <option value="5">⭐⭐⭐⭐⭐ عالی</option>
                        <option value="4">⭐⭐⭐⭐ خوب</option>
                        <option value="3">⭐⭐⭐ متوسط</option>
                        <option value="2">⭐⭐ ضعیف</option>
                        <option value="1">⭐ بد</option>
                    </select>
                </div>
                <textarea name="content" rows="3" class="w-full border p-3 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none" placeholder="متن نظر..." required></textarea>
                <button class="mt-2 bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-indigo-700 font-bold">ثبت نظر</button>
            </form>
            {% else %}
            <div class="bg-yellow-50 p-4 rounded-xl text-center mb-8 border border-yellow-100">
                <p class="text-yellow-800 text-sm">برای ثبت نظر باید وارد سایت شوید.</p>
                <a href="/login" class="inline-block mt-2 text-indigo-600 font-bold text-sm bg-white px-3 py-1 rounded shadow-sm">ورود / عضویت</a>
            </div>
            {% endif %}

            <div class="space-y-6" id="comments-container">
                {% for c in comments %}
                <div class="comment-item border-b pb-6 last:border-0 last:pb-0 {{ 'bg-yellow-50 p-2 rounded-lg' if session.get('role') in ['admin', 'editor'] and c.is_read == 0 else '' }} {{ 'hidden' if loop.index > 10 else '' }}" id="comment-{{ c.id }}">
                    <div class="flex gap-4">
                        <div class="w-10 h-10 rounded-full bg-indigo-50 flex items-center justify-center text-indigo-500 font-bold border border-indigo-100 shadow-sm flex-shrink-0">
                            {% if c.gender == 'female' %}<i class="fas fa-female"></i>{% else %}<i class="fas fa-male"></i>{% endif %}
                        </div>
                        <div class="flex-grow">
                            <div class="flex items-center justify-between mb-1">
                                <div class="flex items-center gap-2">
                                    <span class="font-bold text-sm">{{ c.name }}</span>
                                    <span class="text-[10px] text-yellow-500">
                                        {% for i in range(c.rating) %}⭐{% endfor %}
                                    </span>
                                    {% if session.get('role') in ['admin', 'editor'] and c.is_read == 0 %}
                                        <span class="bg-red-500 text-white text-[10px] px-2 rounded-full animate-pulse">جدید</span>
                                    {% endif %}
                                </div>
                                <span class="text-[10px] text-gray-400">{{ c.created_at | jalali }}</span>
                            </div>
                            <p class="text-sm text-gray-700 leading-6 mb-2">{{ c.content }}</p>
                            
                            {% if c.updated_at %}
                                <p class="text-[10px] text-gray-400 italic mb-2">(ویرایش شده)</p>
                            {% endif %}

                            <div class="flex items-center gap-3 text-xs">
                                {% if session.get('user_id') %}
                                <button onclick="document.getElementById('reply-form-{{ c.id }}').classList.toggle('hidden')" class="text-indigo-600 hover:text-indigo-800 font-bold cursor-pointer">پاسخ</button>
                                {% endif %}
                                
                                {% if session.get('role') in ['admin', 'support', 'editor'] or session.get('user_id') == c.user_id %}
                                    <button onclick="document.getElementById('edit-form-{{ c.id }}').classList.toggle('hidden')" class="text-blue-500 cursor-pointer">ویرایش</button>
                                    <form action="/comment/delete/{{ c.id }}" method="post" class="inline" onsubmit="return confirm('حذف نظر؟')">
                                        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                                        <button type="submit" class="text-red-500 bg-transparent border-0 cursor-pointer p-0">حذف</button>
                                    </form>
                                {% endif %}
                            </div>

                            <form action="/post/{{ post.id }}/comment" method="post" id="reply-form-{{ c.id }}" class="hidden mt-3 bg-gray-50 p-3 rounded-lg">
                                <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                                <input type="hidden" name="parent_id" value="{{ c.id }}">
                                <textarea name="content" rows="2" class="w-full border p-2 rounded text-sm" placeholder="پاسخ شما..."></textarea>
                                <button class="mt-1 bg-indigo-500 text-white px-3 py-1 rounded text-xs">ارسال پاسخ</button>
                            </form>

                            <form action="/comment/edit/{{ c.id }}" method="post" id="edit-form-{{ c.id }}" class="hidden mt-3 bg-blue-50 p-3 rounded-lg">
                                <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                                <textarea name="content" rows="2" class="w-full border p-2 rounded text-sm">{{ c.content }}</textarea>
                                <button class="mt-1 bg-blue-500 text-white px-3 py-1 rounded text-xs">ذخیره ویرایش</button>
                            </form>

                            {% if c.replies %}
                            <div class="mt-4 mr-4 border-r-2 border-gray-200 pr-4 space-y-4">
                                {% for r in c.replies %}
                                <div class="bg-gray-50 p-3 rounded-lg {{ 'bg-yellow-100' if session.get('role') in ['admin', 'editor'] and r.is_read == 0 else '' }}">
                                    <div class="flex items-center gap-2 mb-1">
                                        <span class="font-bold text-xs text-gray-800">{{ r.name }}</span>
                                        <span class="text-[9px] text-gray-400">{{ r.created_at | jalali }}</span>
                                        {% if session.get('role') in ['admin', 'editor'] and r.is_read == 0 %}
                                            <span class="bg-red-500 text-white text-[9px] px-1 rounded-full animate-pulse">جدید</span>
                                        {% endif %}
                                    </div>
                                    <p class="text-xs text-gray-600">{{ r.content }}</p>
                                    
                                    <div class="flex items-center gap-2 text-[10px] mt-1">
                                        {% if session.get('user_id') %}
                                            <button onclick="document.getElementById('reply-form-{{ r.id }}').classList.toggle('hidden')" class="text-indigo-600 font-bold">پاسخ</button>
                                        {% endif %}
                                        {% if session.get('role') in ['admin', 'support', 'editor'] or session.get('user_id') == r.user_id %}
                                            <form action="/comment/delete/{{ r.id }}" method="post" class="inline" onsubmit="return confirm('حذف؟')">
                                                <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                                                <button class="text-red-500">حذف</button>
                                            </form>
                                        {% endif %}
                                    </div>
                                    <form action="/post/{{ post.id }}/comment" method="post" id="reply-form-{{ r.id }}" class="hidden mt-2">
                                        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                                        <input type="hidden" name="parent_id" value="{{ c.id }}"> <!-- Link to main parent -->
                                        <input type="text" name="content" class="w-full border p-1 rounded text-xs" placeholder="پاسخ به {{ r.name }}...">
                                    </form>
                                </div>
                                {% endfor %}
                            </div>
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% else %}
                <p class="text-center text-gray-400 text-sm">هنوز نظری ثبت نشده است. اولین نفر باشید!</p>
                {% endfor %}
            </div>
            
            {% if comments|length > 10 %}
            <div class="text-center mt-6">
                <button id="load-more-btn" class="bg-gray-100 text-gray-600 px-6 py-2 rounded-full text-sm font-bold hover:bg-gray-200 transition">مشاهده نظرات بیشتر</button>
            </div>
            <script>
                document.getElementById('load-more-btn').addEventListener('click', function() {
                    document.querySelectorAll('.comment-item.hidden').forEach(el => el.classList.remove('hidden'));
                    this.style.display = 'none';
                });
            </script>
            {% endif %}
        </div>
    </div>
    <div class="lg:col-span-1">""" + SIDEBAR_TEMPLATE + """</div>
</div>
{% endblock %}
"""

TEMPLATES['contact.html'] = """
{% extends "base.html" %}
{% block content %}
<div class="grid grid-cols-1 lg:grid-cols-4 gap-8">
    <div class="lg:col-span-3">
        <div class="bg-white rounded-3xl shadow-xl overflow-hidden flex flex-col md:flex-row">
            <div class="md:w-2/5 bg-indigo-900 text-white p-10 relative overflow-hidden flex flex-col justify-between">
                <div>
                    <h2 class="text-3xl font-bold mb-6">ارتباط با ما</h2>
                    <div class="space-y-6 text-sm">
                        <div class="flex gap-4"><i class="fas fa-map-marker-alt text-yellow-400 text-xl"></i> <p class="leading-6">هرمزگان، بندرعباس، بلوار علی ابن ابیطالب، روبروی اداره کل زندان‌های استان، دانشگاه ملی مهارت بندرعباس، ساختمان آموزش شماره ۲، طبقه ۳</p></div>
                        <div class="flex gap-4"><i class="fas fa-phone text-yellow-400 text-xl"></i> <p class="dir-ltr">076-42870000</p></div>
                        <div class="flex gap-4"><i class="fas fa-envelope text-yellow-400 text-xl"></i> <p>info@dehkadekhabar.ir</p></div>
                    </div>
                </div>
            </div>
            <div class="md:w-3/5 p-10">
                <form method="post" action="/contact">
                    <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                    <h3 class="text-xl font-bold text-gray-800 mb-6">ارسال پیام به پشتیبانی</h3>
                    <div class="space-y-4">
                        <input type="text" name="name" placeholder="نام شما" value="{{ session.get('name', '') }}" required class="w-full bg-gray-50 border p-3 rounded-xl">
                        <input type="text" name="contact_info" placeholder="شماره تماس یا ایمیل" required class="w-full bg-gray-50 border p-3 rounded-xl">
                        
                        <div class="relative">
                            <select id="subjectSelect" class="w-full bg-gray-50 border p-3 rounded-xl appearance-none" onchange="toggleOtherSubject(this)">
                                <option value="" disabled selected>انتخاب موضوع پیام...</option>
                                <option value="پشتیبانی فنی">پشتیبانی فنی</option>
                                <option value="فروش و تبلیغات">فروش و تبلیغات</option>
                                <option value="انتقادات و پیشنهادات">انتقادات و پیشنهادات</option>
                                <option value="گزارش باگ">گزارش باگ</option>
                                <option value="other">سایر...</option>
                            </select>
                            <i class="fas fa-chevron-down absolute left-3 top-4 text-gray-400 pointer-events-none"></i>
                        </div>
                        <input type="text" name="subject" id="subjectInput" placeholder="موضوع پیام خود را بنویسید" class="w-full bg-gray-50 border p-3 rounded-xl hidden" required disabled>
                        
                        <textarea name="message" rows="4" placeholder="متن پیام..." required class="w-full bg-gray-50 border p-3 rounded-xl"></textarea>
                        <button class="w-full bg-yellow-500 hover:bg-yellow-400 text-indigo-900 font-bold py-3 rounded-xl">ارسال پیام</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    <div class="lg:col-span-1">""" + SIDEBAR_TEMPLATE + """</div>
</div>
<script>
    function toggleOtherSubject(select) {
        const input = document.getElementById('subjectInput');
        if (select.value === 'other') {
            input.classList.remove('hidden');
            input.disabled = false;
            input.value = '';
            input.focus();
            select.removeAttribute('name'); // Don't send select value
        } else {
            input.classList.add('hidden');
            input.disabled = true;
            input.value = select.value;
            select.setAttribute('name', 'subject'); // Send select value
        }
    }
</script>
{% endblock %}
"""

TEMPLATES['about.html'] = """
{% extends "base.html" %}
{% block content %}
<div class="grid grid-cols-1 lg:grid-cols-4 gap-8">
    <div class="lg:col-span-3 bg-white p-10 rounded-3xl shadow-sm text-justify leading-9 text-gray-700">
        <h1 class="text-3xl font-black text-indigo-900 mb-6">درباره دهکده خبر</h1>
        <p class="mb-4">
            خبرگزاری «دهکده خبر» با هدف اطلاع‌رسانی شفاف، سریع و دقیق رویدادهای استان هرمزگان و جنوب کشور تأسیس شده است.
            ما در این رسانه متعهد هستیم تا صدای مردم خون‌گرم این خطه باشیم.
        </p>
        <div class="bg-indigo-50 p-6 rounded-xl border border-indigo-100">
            <p class="font-bold text-indigo-900 mb-2">اطلاعات پروژه</p>
            <p>این وب‌سایت به عنوان <strong>پروژه پایانی مقطع کاردانی رشته کامپیوتر نرم‌افزار</strong> طراحی شده است.</p>
            <p><strong>طراح و توسعه‌دهنده:</strong> امین رهبری سکل</p>
            <p><strong>دانشگاه:</strong> ملی مهارت (فنی و حرفه‌ای) واحد بندرعباس</p>
        </div>
    </div>
    <div class="lg:col-span-1">""" + SIDEBAR_TEMPLATE + """</div>
</div>
{% endblock %}
"""

TEMPLATES['login.html'] = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>ورود / عضویت</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet"/>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>body { font-family: 'Vazirmatn', sans-serif; }</style>
</head>
<body class="bg-gray-100 flex items-center justify-center h-screen p-4">
    <div class="bg-white p-8 rounded-3xl shadow-xl w-full max-w-md">
        
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
            <div class="mb-6 space-y-2">
                {% for category, message in messages %}
                <div class="flash-message p-3 rounded-lg flex items-center gap-2 text-sm font-bold shadow-sm border {{ 'bg-green-100 text-green-800 border-green-200' if category == 'success' else ('bg-yellow-100 text-yellow-800 border-yellow-200' if category == 'warning' else 'bg-red-100 text-red-800 border-red-200') }}">
                    <i class="{{ 'fas fa-check-circle' if category == 'success' else 'fas fa-exclamation-circle' }}"></i>
                    {{ message }}
                </div>
                {% endfor %}
            </div>
            {% endif %}
        {% endwith %}

        <h2 class="text-2xl font-bold text-center mb-6 text-indigo-900">ورود به حساب</h2>
        <form method="post" action="/login">
            <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
            <input type="text" name="username" placeholder="نام کاربری" required class="w-full bg-gray-50 border p-3 rounded-xl mb-3 text-center outline-none focus:ring-2 focus:ring-indigo-500">
            <input type="password" name="password" placeholder="رمز عبور" required class="w-full bg-gray-50 border p-3 rounded-xl mb-6 text-center outline-none focus:ring-2 focus:ring-indigo-500">
            <button class="w-full bg-indigo-600 text-white font-bold py-3 rounded-xl hover:bg-indigo-700 transition shadow-lg">ورود</button>
        </form>
        <div class="mt-6 text-center border-t pt-4">
            <p class="text-sm text-gray-500 mb-2">حساب کاربری ندارید؟</p>
            <a href="/register" class="text-indigo-600 font-bold hover:underline">ثبت نام کنید</a>
        </div>
        <div class="mt-4 text-center">
            <a href="/" class="text-xs text-gray-400">بازگشت به صفحه اصلی</a>
        </div>
    </div>
    """ + UI_SCRIPTS + """
</body>
</html>
"""

TEMPLATES['register.html'] = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>ثبت نام</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet"/>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>body { font-family: 'Vazirmatn', sans-serif; }</style>
</head>
<body class="bg-gray-100 flex items-center justify-center h-screen p-4">
    <div class="bg-white p-8 rounded-3xl shadow-xl w-full max-w-md">
        
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
            <div class="mb-6 space-y-2">
                {% for category, message in messages %}
                <div class="flash-message p-3 rounded-lg flex items-center gap-2 text-sm font-bold shadow-sm border {{ 'bg-green-100 text-green-800 border-green-200' if category == 'success' else ('bg-yellow-100 text-yellow-800 border-yellow-200' if category == 'warning' else 'bg-red-100 text-red-800 border-red-200') }}">
                    <i class="{{ 'fas fa-check-circle' if category == 'success' else 'fas fa-exclamation-circle' }}"></i>
                    {{ message }}
                </div>
                {% endfor %}
            </div>
            {% endif %}
        {% endwith %}

        <h2 class="text-2xl font-bold text-center mb-6 text-indigo-900">ایجاد حساب کاربری</h2>
        <form method="post">
            <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
            <input type="text" name="name" placeholder="نام و نام خانوادگی" required class="w-full bg-gray-50 border p-3 rounded-xl mb-3 outline-none">
            <input type="text" name="username" placeholder="نام کاربری (انگلیسی)" required class="w-full bg-gray-50 border p-3 rounded-xl mb-3 outline-none dir-ltr">
            <input type="password" name="password" placeholder="رمز عبور" required class="w-full bg-gray-50 border p-3 rounded-xl mb-3 outline-none dir-ltr">
            <select name="gender" class="w-full bg-gray-50 border p-3 rounded-xl mb-6 outline-none">
                <option value="male">آقا</option>
                <option value="female">خانم</option>
            </select>
            <button class="w-full bg-green-600 text-white font-bold py-3 rounded-xl hover:bg-green-700 transition shadow-lg">ثبت نام</button>
        </form>
        <div class="mt-4 text-center">
            <a href="/login" class="text-sm text-indigo-600">قبلاً ثبت نام کرده‌اید؟ ورود</a>
        </div>
    </div>
    """ + UI_SCRIPTS + """
</body>
</html>
"""

TEMPLATES['profile.html'] = """
{% extends "base.html" %}
{% block content %}
<div class="max-w-6xl mx-auto bg-white p-8 rounded-3xl shadow-sm border flex flex-col md:flex-row gap-8 min-h-[600px]">
    
    <!-- Sidebar / User Info -->
    <div class="md:w-1/4 text-center md:border-l md:pl-8 flex flex-col">
        <div class="w-32 h-32 rounded-full bg-indigo-100 mx-auto flex items-center justify-center text-6xl text-indigo-500 mb-4 border-4 border-white shadow-lg">
            {% if session.gender == 'female' %}<i class="fas fa-female"></i>{% else %}<i class="fas fa-male"></i>{% endif %}
        </div>
        <h2 class="text-xl font-black text-gray-800">{{ session.name }}</h2>
        <p class="text-gray-500 text-sm">@{{ session.username }}</p>
        <span class="inline-block bg-gray-100 px-3 py-1 rounded-full text-xs mt-2 self-center">{{ session.role }}</span>
        
        <form action="/profile/update" method="post" class="mt-6 space-y-2">
            <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
            <input type="text" name="name" value="{{ session.name }}" class="w-full text-sm border p-2 rounded text-center">
            <input type="password" name="password" placeholder="تغییر رمز عبور" class="w-full text-sm border p-2 rounded text-center">
            <button class="w-full bg-indigo-600 text-white py-2 rounded text-sm hover:bg-indigo-700">بروزرسانی پروفایل</button>
        </form>

        <a href="/logout" class="block mt-4 bg-red-50 text-red-600 py-2 rounded-lg hover:bg-red-100 text-sm font-bold text-center">خروج از حساب</a>
    </div>

    <!-- Main Content -->
    <div class="md:w-3/4 flex flex-col md:flex-row gap-6">
        
        <!-- Tickets Section -->
        <div class="w-full md:w-1/2">
            <div class="flex items-center justify-between border-b pb-2 mb-4">
                <h3 class="font-bold text-lg"><i class="fas fa-envelope-open-text text-indigo-600 ml-2"></i>تیکت‌های من</h3>
                <a href="/contact" class="text-xs bg-indigo-50 text-indigo-600 px-2 py-1 rounded hover:bg-indigo-100">تیکت جدید</a>
            </div>
            
            <div class="space-y-3 h-[500px] overflow-y-auto pr-2">
                {% if messages_list %}
                    {% for m in messages_list %}
                    <div class="bg-gray-50 border border-gray-100 rounded-xl overflow-hidden shadow-sm transition hover:shadow-md group">
                        <!-- Header (Clickable) -->
                        <div onclick="toggleTicket('ticket-{{ m.id }}')" class="p-4 cursor-pointer flex justify-between items-center bg-white">
                            <div>
                                <h4 class="font-bold text-sm text-gray-800">{{ m.subject }}</h4>
                                <span class="text-[10px] text-gray-400">{{ m.created_at | jalali }}</span>
                            </div>
                            <div class="flex items-center gap-2">
                                {% if m.status == 'answered' %}
                                    <span class="bg-green-100 text-green-700 text-[10px] px-2 py-0.5 rounded-full animate-pulse">پاسخ ادمین</span>
                                {% elif m.status == 'closed' %}
                                    <span class="bg-gray-200 text-gray-600 text-[10px] px-2 py-0.5 rounded-full">بسته</span>
                                {% else %}
                                    <span class="bg-yellow-100 text-yellow-700 text-[10px] px-2 py-0.5 rounded-full">در انتظار</span>
                                {% endif %}
                                <i class="fas fa-chevron-down text-gray-300 group-hover:text-indigo-500 transition"></i>
                            </div>
                        </div>
                        
                        <!-- Content (Hidden by default) -->
                        <div id="ticket-{{ m.id }}" class="hidden border-t bg-gray-50">
                            <div class="p-4 space-y-4">
                                <!-- Original Message -->
                                <div class="bg-white p-3 rounded-lg border text-sm">
                                    <p class="text-gray-700">{{ m.message }}</p>
                                </div>
                                
                                <!-- Replies -->
                                {% if m.replies %}
                                    {% for r in m.replies %}
                                    <div class="{{ 'bg-indigo-100 ml-4' if r.sender_id else 'bg-white mr-4' }} p-3 rounded-lg border text-sm relative">
                                        <div class="flex justify-between text-[10px] opacity-60 mb-1">
                                            <span>{{ 'پشتیبان' if r.sender_id else 'شما' }}</span>
                                            <span>{{ r.created_at | jalali }}</span>
                                        </div>
                                        <p class="text-gray-800">{{ r.message }}</p>
                                    </div>
                                    {% endfor %}
                                {% endif %}
                                
                                <!-- Reply Form -->
                                {% if m.status != 'closed' %}
                                <form action="/profile/reply/{{ m.id }}" method="post" class="flex gap-2 mt-2">
                                    <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                                    <input type="text" name="reply" placeholder="ارسال پاسخ..." class="flex-grow border rounded-lg px-3 py-2 text-xs" required>
                                    <button class="bg-indigo-600 text-white px-3 py-2 rounded-lg text-xs hover:bg-indigo-700">ارسال</button>
                                </form>
                                {% else %}
                                <div class="text-center text-xs text-gray-400 py-2">این گفتگو بسته شده است.</div>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="text-center py-10 text-gray-400">
                        <i class="far fa-envelope text-4xl mb-2 opacity-50"></i>
                        <p class="text-sm">هنوز تیکتی ارسال نکرده‌اید.</p>
                    </div>
                {% endif %}
            </div>
        </div>

        <!-- Comments Section -->
        <div class="w-full md:w-1/2">
            <h3 class="font-bold text-lg border-b pb-2 mb-4"><i class="fas fa-comments text-indigo-600 ml-2"></i>نظرات من</h3>
            
            <div class="space-y-3 h-[500px] overflow-y-auto pr-2">
                {% if grouped_comments %}
                    {% for news_id, data in grouped_comments.items() %}
                    <div class="border rounded-xl overflow-hidden shadow-sm">
                        <!-- News Header -->
                        <div onclick="toggleTicket('news-comments-{{ news_id }}')" class="bg-gray-50 p-3 flex justify-between items-center cursor-pointer hover:bg-gray-100 transition">
                            <h4 class="font-bold text-sm text-indigo-900 truncate w-3/4">{{ data.title }}</h4>
                            <span class="text-xs bg-white border px-2 py-0.5 rounded text-gray-500">{{ data.entries|length }} نظر</span>
                        </div>
                        
                        <!-- Comments List -->
                        <div id="news-comments-{{ news_id }}" class="hidden bg-white border-t">
                            {% for c in data.entries %}
                            <div class="p-3 border-b last:border-0 hover:bg-gray-50 transition relative">
                                <p class="text-sm text-gray-700 mb-1">{{ c.content }}</p>
                                <div class="flex justify-between items-center text-[10px] text-gray-400">
                                    <span>{{ c.created_at | jalali }}</span>
                                    <a href="/post/{{ c.news_id }}#comment-{{ c.id }}" class="text-blue-500 hover:underline">مشاهده در خبر</a>
                                </div>
                                <!-- Check for unread replies from other users to this comment -->
                                {% if c.has_new_reply %}
                                    <span class="absolute top-2 left-2 w-2 h-2 bg-red-500 rounded-full animate-pulse" title="پاسخ جدید"></span>
                                {% endif %}
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="text-center py-10 text-gray-400">
                        <i class="far fa-comment-dots text-4xl mb-2 opacity-50"></i>
                        <p class="text-sm">هنوز نظری ثبت نکرده‌اید.</p>
                    </div>
                {% endif %}
            </div>
        </div>

    </div>
</div>

<script>
    function toggleTicket(id) {
        const el = document.getElementById(id);
        if (el.classList.contains('hidden')) {
            el.classList.remove('hidden');
        } else {
            el.classList.add('hidden');
        }
    }
</script>
{% endblock %}
"""

TEMPLATES['admin_news.html'] = """
{% extends "admin_layout.html" %}
{% block admin_content %}
    <div class="flex flex-col md:flex-row justify-between items-center mb-8 gap-4">
        <h1 class="text-2xl font-bold">مدیریت اخبار</h1>
        <a href="/admin/news/edit/new" class="bg-indigo-600 text-white px-6 py-2 rounded-xl font-bold shadow hover:bg-indigo-700 transition flex items-center gap-2 w-full md:w-auto justify-center"><i class="fas fa-plus"></i> خبر جدید</a>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border overflow-x-auto">
        <table class="w-full text-right text-sm whitespace-nowrap">
            <thead class="bg-gray-50 border-b">
                <tr>
                    <th class="p-4 w-16 text-center">#</th>
                    <th class="p-4">عنوان</th>
                    <th class="p-4">دسته</th>
                    <th class="p-4">آمار</th>
                    <th class="p-4">تاریخ انتشار</th>
                    <th class="p-4 text-center">عملیات</th>
                </tr>
            </thead>
            <tbody>
                {% for item in news %}
                <tr class="border-b last:border-0 hover:bg-gray-50 transition">
                    <td class="p-4 text-center font-bold text-gray-400">{{ loop.index }}</td>
                    <td class="p-4 font-bold text-gray-800 max-w-xs truncate relative">
                        {{ item.title }}
                        {% if item.is_featured %}<span class="mr-2 text-[10px] bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded">ویژه</span>{% endif %}
                        {% if item.unread_comments > 0 %}
                             <span class="absolute -top-1 right-0 w-3 h-3 bg-red-500 rounded-full animate-pulse" title="نظر جدید"></span>
                        {% endif %}
                    </td>
                    <td class="p-4"><span class="bg-gray-100 px-2 py-1 rounded text-xs">{{ item.cat_name }}</span></td>
                    <td class="p-4 text-xs">
                        <span class="block">👁️ {{ item.views }}</span>
                        <span class="block mt-1 text-indigo-600 font-bold">💬 {{ item.comment_count }} نظر</span>
                        {% if item.unread_comments > 0 %}
                        <span class="block mt-1 text-red-500 font-bold text-[10px]">( {{ item.unread_comments }} جدید )</span>
                        {% endif %}
                    </td>
                    <td class="p-4 text-xs text-gray-500 dir-ltr text-right">{{ item.created_at | jalali }}</td>
                    <td class="p-4 flex gap-2 justify-center">
                        <a href="/post/{{ item.id }}" target="_blank" class="w-8 h-8 rounded-lg bg-gray-100 flex items-center justify-center text-gray-500 hover:bg-gray-200" title="مشاهده و مدیریت نظرات"><i class="fas fa-eye"></i></a>
                        <a href="/admin/news/edit/{{ item.id }}" class="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center text-blue-500 hover:bg-blue-100"><i class="fas fa-pen"></i></a>
                        <form action="/admin/news/delete/{{ item.id }}" method="post" onsubmit="return confirm('آیا از حذف این خبر اطمینان دارید؟')">
                            <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                            <button type="submit" class="w-8 h-8 rounded-lg bg-red-50 flex items-center justify-center text-red-500 hover:bg-red-100"><i class="fas fa-trash"></i></button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
{% endblock %}
"""

TEMPLATES['admin_news_form.html'] = """
{% extends "admin_layout.html" %}
{% block admin_content %}
<div class="max-w-4xl mx-auto bg-white p-8 rounded-2xl shadow-sm border">
    <div class="flex items-center gap-2 mb-6 border-b pb-4">
        <a href="/admin/news" class="text-gray-400 hover:text-gray-600"><i class="fas fa-arrow-right"></i></a>
        <h2 class="text-xl font-bold">{{ 'ویرایش خبر' if post else 'افزودن خبر جدید' }}</h2>
    </div>
    <form method="post" enctype="multipart/form-data">
        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
        <div class="grid md:grid-cols-2 gap-6 mb-6">
            <div>
                <label class="block text-sm font-bold mb-2">عنوان خبر</label>
                <input type="text" name="title" value="{{ post.title if post else '' }}" required class="w-full border p-3 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none">
            </div>
            <div>
                <label class="block text-sm font-bold mb-2">دسته‌بندی</label>
                <select name="category_id" class="w-full border p-3 rounded-xl bg-white outline-none">
                    {% for c in cats %}
                    <option value="{{ c.id }}" {{ 'selected' if post and post.category_id == c.id else '' }}>{{ c.name }}</option>
                    {% endfor %}
                </select>
            </div>
        </div>
        <div class="mb-6">
            <label class="block text-sm font-bold mb-2">متن کامل خبر</label>
            <textarea name="content" rows="12" required class="w-full border p-3 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none">{{ post.content if post else '' }}</textarea>
        </div>
        <div class="grid md:grid-cols-2 gap-6 mb-6">
            <div class="border border-dashed p-5 rounded-xl bg-gray-50">
                <p class="font-bold mb-3 text-sm">تصاویر</p>
                {% if post and post.images %}
                    <div class="mb-4 space-y-2">
                        {% for img_path in post.images.split(';') %}
                            {% if img_path %}
                            <div class="flex items-center gap-2 bg-white p-2 rounded border">
                                <img src="{{ img_path | fix_url }}" class="h-10 w-10 object-cover rounded">
                                <span class="text-[10px] text-gray-500 truncate dir-ltr">{{ img_path.split('/')[-1] }}</span>
                            </div>
                            {% endif %}
                        {% endfor %}
                        <label class="flex items-center gap-2 mt-2 cursor-pointer text-red-500 text-xs font-bold bg-red-50 p-2 rounded w-fit">
                            <input type="checkbox" name="delete_all_images" value="1"> 
                            <i class="fas fa-trash"></i> حذف تمام تصاویر قبلی
                        </label>
                    </div>
                {% endif %}
                <p class="text-xs text-gray-400 mb-2">افزودن تصاویر جدید (به لیست فعلی اضافه می‌شود مگر اینکه تیک حذف را بزنید)</p>
                <input type="file" name="images_file" multiple class="w-full text-sm mb-3">
                <input type="text" name="images_url" placeholder="لینک‌های تصویر (با ; جدا کنید)" class="w-full border p-2 rounded-lg text-sm dir-ltr">
            </div>
            
            <div class="border border-dashed p-5 rounded-xl bg-gray-50">
                <p class="font-bold mb-3 text-sm">ویدیو</p>
                {% if post and post.video %}
                    <div class="mb-3 text-xs bg-indigo-50 p-2 rounded text-indigo-700 flex justify-between items-center">
                        <span class="truncate max-w-[150px] dir-ltr">{{ post.video.split('/')[-1] }}</span>
                        <label class="flex items-center gap-1 cursor-pointer text-red-500">
                            <input type="checkbox" name="delete_video" value="1"> حذف
                        </label>
                    </div>
                {% endif %}
                <p class="text-xs text-gray-400 mb-2">جایگزینی ویدیو جدید</p>
                <input type="file" name="video_file" class="w-full text-sm mb-3">
                <input type="text" name="video_url" placeholder="یا لینک ویدیو (mp4)..." class="w-full border p-2 rounded-lg text-sm dir-ltr">
            </div>
        </div>
        <div class="flex items-center gap-3 mb-8 bg-yellow-50 p-4 rounded-xl border border-yellow-100">
            <input type="checkbox" name="is_featured" value="1" id="f" class="w-5 h-5 text-indigo-600 rounded" {{ 'checked' if post and post.is_featured else '' }}>
            <label for="f" class="font-bold text-sm cursor-pointer select-none">این خبر در اسلایدر ویژه نمایش داده شود</label>
        </div>
        <div class="flex justify-end gap-3">
            <a href="/admin/news" class="px-6 py-3 rounded-xl border font-bold text-gray-500 hover:bg-gray-50">انصراف</a>
            <button class="bg-indigo-600 text-white px-8 py-3 rounded-xl font-bold shadow-lg hover:bg-indigo-700 transition">ذخیره تغییرات</button>
        </div>
    </form>
</div>
{% endblock %}
"""

TEMPLATES['admin_chat.html'] = """
{% extends "admin_layout.html" %}
{% block admin_content %}
<div class="h-[calc(100vh-100px)] flex flex-col md:flex-row gap-4">
    <div class="w-full md:w-1/3 bg-white rounded-2xl shadow-sm border overflow-hidden flex flex-col h-1/3 md:h-full">
        <div class="p-4 border-b bg-gray-50 font-bold sticky top-0 z-10">همکاران</div>
        <div class="overflow-y-auto flex-1">
            <a href="?group=1" class="flex items-center gap-3 p-4 hover:bg-indigo-50 transition border-b last:border-0 {{ 'bg-indigo-100' if is_group else '' }}">
                <div class="w-10 h-10 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold relative">
                    <i class="fas fa-users"></i>
                    {% if group_unread > 0 %}
                    <span class="absolute top-0 right-0 w-3 h-3 bg-red-500 rounded-full border-2 border-white"></span>
                    {% endif %}
                </div>
                <span class="font-bold text-sm">چت عمومی</span>
            </a>
            {% for u in admins %}
                {% if u.id != session.user_id %}
                <a href="?user={{ u.id }}" class="flex items-center gap-3 p-4 hover:bg-indigo-50 transition border-b last:border-0 {{ 'bg-indigo-50' if selected_user and selected_user.id == u.id else '' }}">
                    <div class="w-10 h-10 rounded-full bg-indigo-200 flex items-center justify-center text-indigo-700 font-bold relative">
                        {{ u.name[0] }}
                        {% if u.has_unread %}
                            <span class="absolute top-0 right-0 w-3 h-3 bg-red-500 rounded-full border-2 border-white"></span>
                        {% endif %}
                    </div>
                    <div class="flex-1">
                        <div class="flex justify-between">
                            <span class="font-bold text-sm">{{ u.name }}</span>
                            <span class="text-[10px] bg-gray-200 px-2 rounded">{{ u.role }}</span>
                        </div>
                    </div>
                </a>
                {% endif %}
            {% endfor %}
        </div>
    </div>

    <div class="w-full md:w-2/3 bg-white rounded-2xl shadow-sm border flex flex-col overflow-hidden h-2/3 md:h-full">
        {% if selected_user or is_group %}
            <div class="p-4 border-b bg-gray-50 font-bold flex items-center gap-2 sticky top-0 z-10">
                <i class="fas {{ 'fa-users' if is_group else 'fa-comments' }} text-indigo-600"></i> 
                {{ 'چت عمومی همکاران' if is_group else 'گفتگو با ' + selected_user.name }}
            </div>
            
            <div class="flex-1 overflow-y-auto p-4 space-y-4" id="chat-box">
                {% for msg in chat_msgs %}
                <div class="flex {{ 'justify-end' if msg.sender_id == session.user_id else 'justify-start' }} group">
                    <div class="max-w-[75%]">
                          {% if is_group and msg.sender_id != session.user_id %}
                            <div class="text-[10px] text-gray-500 mb-1 ml-1">{{ msg.sender_name }}</div>
                          {% endif %}
                        <div class="p-3 rounded-2xl text-sm relative {{ 'bg-indigo-600 text-white rounded-tl-none' if msg.sender_id == session.user_id else ('bg-yellow-100 text-yellow-900 border border-yellow-200 rounded-tr-none' if msg.is_new_in_view else 'bg-gray-100 text-gray-800 rounded-tr-none') }}">
                            {% if msg.is_new_in_view %}
                                <span class="text-[9px] bg-red-500 text-white px-1 rounded absolute -top-2 -left-2 shadow-sm">جدید</span>
                            {% endif %}
                            {% if msg.file_path %}
                                <div class="mb-2">
                                    {% if msg.file_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')) %}
                                        <img src="{{ msg.file_path | fix_url }}" class="max-w-full rounded-lg cursor-pointer" onclick="window.open(this.src)">
                                    {% else %}
                                        <a href="{{ msg.file_path | fix_url }}" target="_blank" class="flex items-center gap-2 bg-white/20 p-2 rounded hover:bg-white/30 transition">
                                            <i class="fas fa-file"></i> دانلود فایل
                                        </a>
                                    {% endif %}
                                </div>
                            {% endif %}
                            {{ msg.message if msg.message else '' }}
                            <div class="text-[10px] opacity-70 mt-1 text-right dir-ltr flex justify-between items-center gap-2">
                                <span>{{ msg.created_at | jalali('%H:%M') }}</span>
                                {% if msg.sender_id == session.user_id %}
                                    <div class="opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
                                        <button onclick="editMsg('{{ msg.id }}', '{{ msg.message }}')" class="hover:text-yellow-300"><i class="fas fa-pen"></i></button>
                                        <a href="/admin/chat/delete/{{ msg.id }}" class="hover:text-red-300" onclick="return confirm('حذف؟')"><i class="fas fa-trash"></i></a>
                                    </div>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>

            <div id="file-name-display" class="px-4 py-1 text-xs text-indigo-600 bg-indigo-50 hidden"></div>

            <form method="post" enctype="multipart/form-data" class="p-4 border-t bg-gray-50 flex gap-2 items-end">
                <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                {% if is_group %}
                    <input type="hidden" name="is_group" value="1">
                {% else %}
                    <input type="hidden" name="receiver_id" value="{{ selected_user.id }}">
                {% endif %}
                
                <label class="cursor-pointer text-gray-500 hover:text-indigo-600 p-3">
                    <i class="fas fa-paperclip text-xl"></i>
                    <input type="file" name="chat_file" class="hidden" onchange="showFileName(this)">
                </label>
                
                <input type="text" name="message" placeholder="پیام خود را بنویسید..." class="flex-grow border rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-indigo-500" autofocus>
                <button class="bg-indigo-600 text-white p-3 rounded-xl hover:bg-indigo-700 transition aspect-square flex items-center justify-center">
                    <i class="fas fa-paper-plane"></i>
                </button>
            </form>
            
            <!-- Modal Edit -->
            <div id="edit-modal" class="fixed inset-0 bg-black/50 hidden z-50 flex items-center justify-center">
                <div class="bg-white p-6 rounded-xl w-96">
                    <h3 class="font-bold mb-4">ویرایش پیام</h3>
                    <form action="/admin/chat/edit" method="post">
                        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                        <input type="hidden" name="msg_id" id="edit-msg-id">
                        <textarea name="new_message" id="edit-msg-text" class="w-full border p-2 rounded mb-4" rows="3"></textarea>
                        <div class="flex justify-end gap-2">
                            <button type="button" onclick="document.getElementById('edit-modal').classList.add('hidden')" class="bg-gray-200 px-4 py-2 rounded">انصراف</button>
                            <button class="bg-indigo-600 text-white px-4 py-2 rounded">ذخیره</button>
                        </div>
                    </form>
                </div>
            </div>

            <script>
                const chatBox = document.getElementById('chat-box');
                chatBox.scrollTop = chatBox.scrollHeight;
                
                function showFileName(input) {
                    const display = document.getElementById('file-name-display');
                    if(input.files && input.files[0]) {
                        display.innerText = 'فایل انتخاب شده: ' + input.files[0].name;
                        display.classList.remove('hidden');
                    } else {
                        display.classList.add('hidden');
                    }
                }

                function editMsg(id, text) {
                    document.getElementById('edit-msg-id').value = id;
                    document.getElementById('edit-msg-text').value = text;
                    document.getElementById('edit-modal').classList.remove('hidden');
                }
            </script>
        {% else %}
            <div class="flex-1 flex flex-col items-center justify-center text-gray-400">
                <i class="fas fa-comments text-6xl mb-4 opacity-20"></i>
                <p>برای شروع گفتگو یک کاربر یا گروه را انتخاب کنید</p>
            </div>
        {% endif %}
    </div>
</div>
{% endblock %}
"""

TEMPLATES['admin_ads.html'] = """
{% extends "admin_layout.html" %}
{% block admin_content %}
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div class="lg:col-span-1">
            <div class="bg-white p-6 rounded-2xl shadow-sm border sticky top-6">
                <h2 class="font-bold mb-6 text-lg">افزودن / ویرایش تبلیغ</h2>
                <form method="post" enctype="multipart/form-data" class="space-y-4">
                    <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                    <input type="hidden" name="ad_id" value="{{ edit_ad.id if edit_ad else '' }}">
                    <div>
                        <label class="text-xs font-bold text-gray-500">عنوان</label>
                        <input type="text" name="title" value="{{ edit_ad.title if edit_ad else '' }}" class="w-full border p-2 rounded-lg">
                    </div>
                    <div>
                        <label class="text-xs font-bold text-gray-500">لینک مقصد *</label>
                        <input type="text" name="link" value="{{ edit_ad.link if edit_ad else '' }}" required class="w-full border p-2 rounded-lg dir-ltr">
                    </div>
                    <div>
                        <label class="text-xs font-bold text-gray-500">توضیحات (متنی)</label>
                        <textarea name="description" class="w-full border p-2 rounded-lg h-20">{{ edit_ad.description if edit_ad else '' }}</textarea>
                    </div>
                    <div>
                        <label class="text-xs font-bold text-gray-500">مدت اعتبار (روز)</label>
                        <input type="number" name="days" value="{{ remaining_days if edit_ad else 30 }}" class="w-full border p-2 rounded-lg">
                        {% if edit_ad %}
                            <p class="text-[10px] text-gray-400 mt-1">تعداد روزهای باقی‌مانده فعلی. برای تمدید مقدار را تغییر دهید.</p>
                        {% endif %}
                    </div>
                    <div class="border border-dashed p-3 rounded-lg bg-gray-50">
                        <label class="text-xs font-bold text-gray-500 block mb-2">تصویر بنر (فقط عکس)</label>
                        <input type="file" name="ad_image" class="w-full text-xs mb-2" accept="image/*">
                        <input type="text" name="ad_image_url" placeholder="یا لینک مستقیم تصویر..." class="w-full border p-2 rounded-lg text-xs dir-ltr">
                        
                        {% if edit_ad and edit_ad.image %}
                            <div class="flex flex-col gap-2 mt-2 bg-white p-2 border rounded">
                                <span class="text-xs font-bold">تصویر فعلی:</span>
                                <img src="{{ edit_ad.image | fix_url }}" class="h-10 w-auto rounded object-cover">
                                <label class="text-xs text-red-500 flex items-center gap-1 cursor-pointer">
                                    <input type="checkbox" name="delete_image" value="1"> حذف تصویر
                                </label>
                            </div>
                        {% endif %}
                    </div>
                    <button class="w-full bg-green-600 text-white py-3 rounded-xl font-bold hover:bg-green-700">{{ 'ویرایش' if edit_ad else 'ثبت تبلیغ' }}</button>
                    {% if edit_ad %}<a href="/admin/ads" class="block text-center text-xs text-gray-500 mt-2">انصراف</a>{% endif %}
                </form>
            </div>
        </div>
        <div class="lg:col-span-2">
            <div class="bg-white rounded-2xl shadow-sm border overflow-x-auto">
                <table class="w-full text-right text-sm">
                    <thead class="bg-gray-50 border-b">
                        <tr>
                            <th class="p-4">تبلیغ</th>
                            <th class="p-4">آمار</th>
                            <th class="p-4">انقضا</th>
                            <th class="p-4">عملیات</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for ad in ads %}
                        <tr class="border-b last:border-0 hover:bg-gray-50">
                            <td class="p-4">
                                <div class="flex items-center gap-3">
                                    {% if ad.image %}
                                        <img src="{{ ad.image | fix_url }}" class="w-16 h-10 object-cover rounded shadow-sm">
                                    {% else %}
                                        <div class="w-16 h-10 bg-gray-200 rounded flex items-center justify-center text-xs">متن</div>
                                    {% endif %}
                                    <div>
                                        <p class="font-bold text-xs">{{ ad.title }}</p>
                                        <a href="{{ ad.link }}" target="_blank" class="text-[10px] text-blue-500 truncate w-32 block">{{ ad.link }}</a>
                                    </div>
                                </div>
                            </td>
                            <td class="p-4 text-xs">
                                <span class="block">👁️ {{ ad.views }}</span>
                                <span class="block mt-1">🖱️ {{ ad.clicks }}</span>
                            </td>
                            <td class="p-4">
                                <span class="text-xs font-mono dir-ltr">{{ ad.expires_at | jalali('%Y/%m/%d') }}</span>
                                {% if ad.expires_at < now_str %}
                                    <span class="bg-red-100 text-red-600 text-[10px] px-1 rounded ml-1">منقضی</span>
                                {% else %}
                                    <span class="bg-green-100 text-green-600 text-[10px] px-1 rounded ml-1">فعال</span>
                                {% endif %}
                            </td>
                            <td class="p-4 flex gap-2">
                                <a href="/admin/ads?edit={{ ad.id }}" class="text-blue-500 hover:bg-blue-50 p-1 rounded"><i class="fas fa-pen"></i></a>
                                <form action="/admin/ads/delete/{{ ad.id }}" method="post" onsubmit="return confirm('حذف تبلیغ؟')">
                                    <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                                    <button class="text-red-500 hover:bg-red-50 p-1 rounded"><i class="fas fa-trash"></i></button>
                                </form>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
{% endblock %}
"""

TEMPLATES['admin_categories.html'] = """
{% extends "admin_layout.html" %}
{% block admin_content %}
<div class="grid grid-cols-1 md:grid-cols-2 gap-8">
    <div class="bg-white p-6 rounded-2xl shadow-sm border h-fit">
        <h2 class="font-bold mb-4">افزودن / ویرایش دسته‌بندی</h2>
        <form method="post" class="flex gap-2">
            <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
            <input type="hidden" name="cat_id" value="{{ edit_cat.id if edit_cat else '' }}">
            <input type="text" name="name" value="{{ edit_cat.name if edit_cat else '' }}" placeholder="نام دسته..." required class="flex-grow border p-2 rounded-lg">
            <button class="bg-indigo-600 text-white px-4 py-2 rounded-lg font-bold">{{ 'ویرایش' if edit_cat else 'افزودن' }}</button>
            {% if edit_cat %}<a href="/admin/categories" class="bg-gray-200 px-3 py-2 rounded-lg">لغو</a>{% endif %}
        </form>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border overflow-hidden">
        <table class="w-full text-right text-sm">
            <thead class="bg-gray-50 border-b"><tr><th class="p-3">نام دسته</th><th class="p-3 w-24">عملیات</th></tr></thead>
            <tbody>
                {% for c in cats %}
                <tr class="border-b last:border-0 hover:bg-gray-50">
                    <td class="p-3 font-bold">{{ c.name }}</td>
                    <td class="p-3 flex gap-2">
                        <a href="/admin/categories?edit={{ c.id }}" class="text-blue-500"><i class="fas fa-pen"></i></a>
                        <form action="/admin/categories/delete/{{ c.id }}" method="post" onsubmit="return confirm('با حذف دسته، تمام اخبار آن حذف می‌شود! مطمئنید؟')">
                            <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                            <button class="text-red-500"><i class="fas fa-trash"></i></button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
"""

TEMPLATES['admin_users.html'] = """
{% extends "admin_layout.html" %}
{% block admin_content %}
<div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
    <div class="bg-white p-6 rounded-2xl shadow-sm border h-fit">
        <h2 class="font-bold mb-4">مدیریت کاربران و دسترسی‌ها</h2>
        <form method="post" class="space-y-3">
            <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
            <input type="hidden" name="user_id" value="{{ edit_user.id if edit_user else '' }}">
            <input type="text" name="name" value="{{ edit_user.name if edit_user else '' }}" placeholder="نام و نام خانوادگی" required class="w-full border p-2 rounded-lg">
            <input type="text" name="username" value="{{ edit_user.username if edit_user else '' }}" placeholder="نام کاربری" required class="w-full border p-2 rounded-lg dir-ltr">
            <input type="password" name="password" placeholder="{{ 'رمز عبور جدید (اختیاری)' if edit_user else 'رمز عبور' }}" class="w-full border p-2 rounded-lg dir-ltr">
            <select name="role" class="w-full border p-2 rounded-lg bg-white">
                <option value="user" {{ 'selected' if edit_user and edit_user.role=='user' else '' }}>کاربر عادی (ثبت نظر)</option>
                <option value="editor" {{ 'selected' if edit_user and edit_user.role=='editor' else '' }}>سردبیر (مدیریت خبر)</option>
                <option value="support" {{ 'selected' if edit_user and edit_user.role=='support' else '' }}>پشتیبان (پیام‌ها)</option>
                <option value="marketer" {{ 'selected' if edit_user and edit_user.role=='marketer' else '' }}>بازاریاب (تبلیغات)</option>
                <option value="admin" {{ 'selected' if edit_user and edit_user.role=='admin' else '' }}>مدیر کل (دسترسی کامل)</option>
            </select>
            <select name="gender" class="w-full border p-2 rounded-lg bg-white">
                <option value="male" {{ 'selected' if edit_user and edit_user.gender=='male' else '' }}>آقا</option>
                <option value="female" {{ 'selected' if edit_user and edit_user.gender=='female' else '' }}>خانم</option>
            </select>
            <button class="bg-indigo-600 text-white w-full py-2 rounded-lg font-bold">{{ 'ویرایش کاربر' if edit_user else 'ایجاد کاربر جدید' }}</button>
            {% if edit_user %}<a href="/admin/users" class="block text-center text-sm text-gray-500 mt-2">انصراف</a>{% endif %}
        </form>
    </div>
    <div class="bg-white rounded-2xl shadow-sm border overflow-hidden">
        <table class="w-full text-right text-sm">
            <thead class="bg-gray-50 border-b"><tr><th class="p-3">کاربر</th><th class="p-3">نقش</th><th class="p-3">عملیات</th></tr></thead>
            <tbody>
                {% for u in users %}
                <tr class="border-b last:border-0 hover:bg-gray-50">
                    <td class="p-3">
                        <div class="flex items-center gap-2">
                            <span class="w-6 h-6 rounded-full bg-gray-200 flex items-center justify-center text-xs">{{ u.name[0] }}</span>
                            {{ u.name }} <span class="text-xs text-gray-400">({{ u.username }})</span>
                        </div>
                    </td>
                    <td class="p-3"><span class="bg-gray-100 px-2 py-0.5 rounded text-xs">{{ u.role }}</span></td>
                    <td class="p-3 flex gap-2">
                        <a href="/admin/users?edit={{ u.id }}" class="text-blue-500 text-xs">ویرایش</a>
                        {% if u.username != 'admin' %}
                        <form action="/admin/users/delete/{{ u.id }}" method="post" onsubmit="return confirm('حذف کاربر؟')">
                            <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
                            <button class="text-red-500 text-xs">حذف</button>
                        </form>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
"""

TEMPLATES['404.html'] = """
{% extends "base.html" %}
{% block content %}
<div class="flex flex-col items-center justify-center py-20 text-center">
    <div class="text-9xl font-black text-gray-200">404</div>
    <h2 class="text-2xl font-bold text-gray-800 -mt-10 mb-4">صفحه مورد نظر یافت نشد!</h2>
    <p class="text-gray-500 mb-8">متاسفانه صفحه‌ای که به دنبال آن هستید وجود ندارد یا حذف شده است.</p>
    <a href="/" class="bg-indigo-600 text-white px-6 py-3 rounded-xl font-bold shadow hover:bg-indigo-700 transition">بازگشت به صفحه اصلی</a>
</div>
{% endblock %}
"""

TEMPLATES['500.html'] = """
{% extends "base.html" %}
{% block content %}
<div class="flex flex-col items-center justify-center py-20 text-center">
    <div class="text-9xl font-black text-gray-200">500</div>
    <h2 class="text-2xl font-bold text-gray-800 -mt-10 mb-4">خطای داخلی سرور!</h2>
    <p class="text-gray-500 mb-8">مشکلی در سرور رخ داده است. لطفاً دقایقی دیگر تلاش کنید.</p>
    <a href="/" class="bg-indigo-600 text-white px-6 py-3 rounded-xl font-bold shadow hover:bg-indigo-700 transition">بازگشت به صفحه اصلی</a>
</div>
{% endblock %}
"""

# بارگذاری قالب‌ها
app.jinja_loader = DictLoader(TEMPLATES)

# ==========================================
# 5. منطق روت‌ها (Route Logic)
# ==========================================

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

@app.route('/')
def home():
    db = get_db()
    featured = db.execute("SELECT n.*, c.name as cat_name FROM news n JOIN categories c ON n.category_id = c.id WHERE n.is_featured=1 ORDER BY n.created_at DESC LIMIT 5").fetchall()
    
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    
    latest = db.execute("SELECT n.*, c.name as cat_name FROM news n JOIN categories c ON n.category_id = c.id WHERE n.is_featured=0 ORDER BY n.created_at DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
    
    total = db.execute("SELECT count(*) FROM news WHERE is_featured=0").fetchone()[0]
    total_pages = math.ceil(total / per_page)
    
    return render_template('home.html', featured=featured, latest=latest, current_page=page, total_pages=total_pages)

@app.route('/category/<int:cat_id>')
def category(cat_id):
    db = get_db()
    cat = db.execute("SELECT name FROM categories WHERE id=?", (cat_id,)).fetchone()
    if not cat: abort(404)
    
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    
    news = db.execute("SELECT n.*, c.name as cat_name FROM news n JOIN categories c ON n.category_id = c.id WHERE category_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (cat_id, per_page, offset)).fetchall()
    
    total = db.execute("SELECT count(*) FROM news WHERE category_id=?", (cat_id,)).fetchone()[0]
    total_pages = math.ceil(total / per_page)
    
    return render_template('home.html', latest=news, page_title=f"اخبار: {cat['name']}", current_page=page, total_pages=total_pages)

@app.route('/post/<int:post_id>')
def post(post_id):
    db = get_db()
    
    view_key = f'viewed_post_{post_id}'
    if view_key not in session:
        db.execute("UPDATE news SET views = views + 1 WHERE id = ?", (post_id,))
        db.commit()
        session[view_key] = True
        
    post = db.execute("SELECT n.*, c.name as cat_name, u.name as author_name FROM news n JOIN categories c ON n.category_id = c.id JOIN users u ON n.author_id = u.id WHERE n.id = ?", (post_id,)).fetchone()
    if not post: abort(404)
    
    comments = db.execute("SELECT c.*, u.name, u.gender FROM comments c JOIN users u ON c.user_id = u.id WHERE news_id=? AND parent_id IS NULL ORDER BY created_at DESC", (post_id,)).fetchall()
    comments_list = [dict(c) for c in comments]
    
    if session.get('role') in ['admin', 'editor']:
        unread_ids = [c['id'] for c in comments_list if c['is_read'] == 0]
        for c in comments_list:
            replies = db.execute("SELECT c.*, u.name, u.gender FROM comments c JOIN users u ON c.user_id = u.id WHERE parent_id=? ORDER BY created_at ASC", (c['id'],)).fetchall()
            c['replies'] = [dict(r) for r in replies]
            for r in c['replies']:
                if r['is_read'] == 0: unread_ids.append(r['id'])
        
        if unread_ids:
            placeholders = ','.join(['?'] * len(unread_ids))
            db.execute(f"UPDATE comments SET is_read=1 WHERE id IN ({placeholders})", unread_ids)
            db.commit()
    else:
        for c in comments_list:
             replies = db.execute("SELECT c.*, u.name, u.gender FROM comments c JOIN users u ON c.user_id = u.id WHERE parent_id=? ORDER BY created_at ASC", (c['id'],)).fetchall()
             c['replies'] = replies

    return render_template('post.html', post=post, comments=comments_list)

@app.route('/post/<int:post_id>/comment', methods=['POST'])
@login_required
def add_comment(post_id):
    db = get_db()
    parent_id = request.form.get('parent_id')
    rating = request.form.get('rating', 5)
    db.execute("INSERT INTO comments (news_id, user_id, content, parent_id, rating, is_read) VALUES (?, ?, ?, ?, ?, 0)", 
               (post_id, session['user_id'], request.form['content'], parent_id, rating))
    db.commit()
    flash('نظر شما ثبت شد.', 'success')
    return redirect(f'/post/{post_id}#comment-{db.execute("SELECT last_insert_rowid()").fetchone()[0]}')

@app.route('/comment/delete/<int:id>', methods=['POST'])
@login_required
def delete_comment(id):
    db = get_db()
    comment = db.execute("SELECT user_id, news_id FROM comments WHERE id=?", (id,)).fetchone()
    if comment and (session.get('role') in ['admin', 'support', 'editor'] or session.get('user_id') == comment['user_id']):
        db.execute("DELETE FROM comments WHERE parent_id=?", (id,))
        db.execute("DELETE FROM comments WHERE id=?", (id,))
        db.commit()
        flash('نظر حذف شد.', 'success')
        return redirect(f'/post/{comment["news_id"]}')
    abort(403)

@app.route('/comment/edit/<int:id>', methods=['POST'])
@login_required
def edit_comment(id):
    db = get_db()
    comment = db.execute("SELECT user_id, news_id FROM comments WHERE id=?", (id,)).fetchone()
    if comment and (session.get('role') in ['admin', 'support', 'editor'] or session.get('user_id') == comment['user_id']):
        db.execute("UPDATE comments SET content=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (request.form['content'], id))
        db.commit()
        flash('نظر ویرایش شد.', 'success')
        return redirect(f'/post/{comment["news_id"]}')
    abort(403)

@app.route('/archive')
def archive():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    db = get_db()
    news = db.execute("SELECT n.*, c.name as cat_name FROM news n JOIN categories c ON n.category_id = c.id ORDER BY n.created_at DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
    total = db.execute("SELECT count(*) FROM news").fetchone()[0]
    total_pages = math.ceil(total/per_page)
    return render_template('home.html', latest=news, page_title="آرشیو اخبار", current_page=page, total_pages=total_pages)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name')
        contact_info = request.form.get('contact_info')
        subject = request.form.get('subject')
        message = request.form.get('message')
        user_id = session.get('user_id') 
        
        if name and contact_info and message:
            db = get_db()
            db.execute("INSERT INTO messages (name, contact_info, subject, message, user_id, status) VALUES (?, ?, ?, ?, ?, 'new')",
                       (name, contact_info, subject, message, user_id))
            db.commit()
            flash('پیام شما با موفقیت به پشتیبانی ارسال شد.', 'success')
            return redirect('/contact')
        else:
            flash('لطفا تمام فیلدها را پر کنید.', 'error')
    return render_template('contact.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        db = get_db()
        try:
            db.execute("INSERT INTO users (name, username, password, role, gender) VALUES (?, ?, ?, 'user', ?)",
                       (request.form['name'], request.form['username'], generate_password_hash(request.form['password']), request.form['gender']))
            db.commit()
            flash('حساب شما با موفقیت ساخته شد. اکنون وارد شوید.', 'success')
            return redirect('/login')
        except sqlite3.IntegrityError:
            flash('نام کاربری تکراری است.', 'error')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = get_db().execute("SELECT * FROM users WHERE username=?", (request.form['username'],)).fetchone()
        if user and check_password_hash(user['password'], request.form['password']):
            session['user_id'] = user['id']
            session['name'] = user['name']
            session['username'] = user['username']
            session['role'] = user['role']
            session['gender'] = user['gender']
            
            flash(f'خوش آمدید {user["name"]}', 'success')
            if user['role'] == 'user': return redirect('/profile')
            return redirect('/admin')
        flash('نام کاربری یا رمز عبور اشتباه است.', 'error')
    return render_template('login.html')

@app.route('/profile')
@login_required
def profile():
    db = get_db()
    
    user_comments = db.execute("""
        SELECT c.*, n.title as title, n.id as news_id 
        FROM comments c 
        JOIN news n ON c.news_id = n.id 
        WHERE user_id=? AND parent_id IS NULL
        ORDER BY n.id DESC, c.created_at DESC
    """, (session['user_id'],)).fetchall()
    
    grouped_comments = {}
    for c in user_comments:
        c_dict = dict(c)
        has_new_reply = db.execute("SELECT count(*) FROM comments WHERE parent_id=? AND is_read=0", (c['id'],)).fetchone()[0]
        c_dict['has_new_reply'] = (has_new_reply > 0)
        
        nid = c['news_id']
        if nid not in grouped_comments:
            grouped_comments[nid] = {'title': c['title'], 'entries': []}
        grouped_comments[nid]['entries'].append(c_dict)

    messages = db.execute("SELECT * FROM messages WHERE user_id = ? AND parent_id IS NULL ORDER BY created_at DESC", (session['user_id'],)).fetchall() 
    messages_list = [dict(m) for m in messages]
    
    for m in messages_list:
        replies = db.execute("SELECT m.*, u.name as sender_name FROM messages m LEFT JOIN users u ON m.user_id = u.id WHERE parent_id=? ORDER BY m.created_at ASC", (m['id'],)).fetchall()
        m['replies'] = replies

    return render_template('profile.html', grouped_comments=grouped_comments, messages_list=messages_list)

@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    db = get_db()
    name = request.form['name']
    password = request.form['password']
    
    if password:
        db.execute("UPDATE users SET name=?, password=? WHERE id=?", (name, generate_password_hash(password), session['user_id']))
    else:
        db.execute("UPDATE users SET name=? WHERE id=?", (name, session['user_id']))
    
    db.commit()
    session['name'] = name 
    flash('پروفایل بروزرسانی شد.', 'success')
    return redirect('/profile')

@app.route('/profile/reply/<int:msg_id>', methods=['POST'])
@login_required
def profile_reply_message(msg_id):
    db = get_db()
    parent = db.execute("SELECT id, subject, status FROM messages WHERE id=? AND user_id=?", (msg_id, session['user_id'])).fetchone()
    
    if parent and parent['status'] == 'closed':
        flash('این گفتگو بسته شده است و امکان ارسال پاسخ وجود ندارد.', 'error')
        return redirect('/profile')

    msg = request.form.get('reply')
    if msg and parent:
        db.execute("INSERT INTO messages (name, message, user_id, parent_id, subject, status, is_read) VALUES (?, ?, ?, ?, ?, 'user_reply', 0)",
                        (session['name'], msg, session['user_id'], msg_id, parent['subject']))
        
        db.execute("UPDATE messages SET status='user_reply' WHERE id=?", (msg_id,))
        db.commit()
        flash('پاسخ ارسال شد.', 'success')
    return redirect('/profile')

@app.route('/logout')
def logout(): session.clear(); return redirect('/')

@app.route('/search')
def search():
    q = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page

    db = get_db()
    res = db.execute("SELECT n.*, c.name as cat_name FROM news n JOIN categories c ON n.category_id = c.id WHERE title LIKE ? OR content LIKE ? LIMIT ? OFFSET ?", (f'%{q}%', f'%{q}%', per_page, offset)).fetchall()
    total = db.execute("SELECT count(*) FROM news WHERE title LIKE ? OR content LIKE ?", (f'%{q}%', f'%{q}%')).fetchone()[0]
    total_pages = math.ceil(total / per_page)
    
    return render_template('home.html', latest=res, page_title=f"جستجو: {q}", current_page=page, total_pages=total_pages)

# --- Admin Routes ---

@app.route('/admin')
@role_required(['admin', 'editor', 'support', 'marketer'])
def admin_dashboard():
    db = get_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        news_count = db.execute("SELECT count(*) FROM news").fetchone()[0]
        views_count = db.execute("SELECT sum(views) FROM news").fetchone()[0] or 0
        
        unread_msgs = db.execute("SELECT count(*) FROM messages WHERE parent_id IS NULL AND status IN ('new', 'user_reply')").fetchone()[0]
        
        active_ads = db.execute("SELECT count(*) FROM ads WHERE expires_at > ?", (now_str,)).fetchone()[0]
        ads_stats = db.execute("SELECT * FROM ads ORDER BY expires_at DESC").fetchall()
    except:
        news_count, views_count, unread_msgs, active_ads = 0, 0, 0, 0
        ads_stats = []

    c = {
        'news': news_count,
        'views': views_count,
        'unread_msgs': unread_msgs,
        'ads': active_ads
    }
    return render_template('admin_dashboard.html', c=c, ads_stats=ads_stats)

@app.route('/admin/news')
@role_required(['admin', 'editor'])
def admin_news():
    db = get_db()
    news = db.execute("""
        SELECT n.id, n.title, n.views, n.created_at, n.updated_at, n.is_featured, c.name as cat_name, 
        (SELECT COUNT(*) FROM comments WHERE news_id=n.id) as comment_count,
        (SELECT COUNT(*) FROM comments WHERE news_id=n.id AND is_read=0) as unread_comments
        FROM news n 
        JOIN categories c ON n.category_id = c.id 
        ORDER BY n.created_at DESC
    """).fetchall()
    return render_template('admin_news.html', news=news)

@app.route('/admin/news/edit/<news_id>', methods=['GET', 'POST'])
@role_required(['admin', 'editor'])
def admin_news_edit(news_id):
    db = get_db()
    post = db.execute("SELECT * FROM news WHERE id=?", (news_id,)).fetchone() if news_id != 'new' else None
    
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        cat_id = request.form['category_id']
        is_featured = 1 if 'is_featured' in request.form else 0
        
        current_images_list = post['images'].split(';') if (post and post['images']) else []
        new_uploaded_images = []

        if 'images_file' in request.files:
            for f in request.files.getlist('images_file'):
                if f and f.filename:
                    fname = safe_filename_generator(f.filename)
                    if fname:
                        f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                        new_uploaded_images.append(f"static/uploads/{fname}")
        
        url_input = request.form.get('images_url', '').strip()
        if url_input:
            urls = [u.strip() for u in url_input.split(';') if u.strip()]
            new_uploaded_images.extend(urls)

        if request.form.get('delete_all_images'):
            for old_img in current_images_list:
                delete_file_from_disk(old_img)
            final_images_list = new_uploaded_images
        else:
            final_images_list = current_images_list + new_uploaded_images

        final_images_str = ";".join([img for img in final_images_list if img])

        final_video = post['video'] if post else ""
        if request.form.get('delete_video'):
            delete_file_from_disk(final_video)
            final_video = ""
            
        if 'video_file' in request.files:
             f = request.files['video_file']
             if f and f.filename:
                fname = safe_filename_generator(f.filename)
                if fname:
                    f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                    if final_video and not request.form.get('delete_video'):
                         delete_file_from_disk(final_video)
                    final_video = f"static/uploads/{fname}"
        
        if request.form.get('video_url', '').strip():
            final_video = request.form.get('video_url', '').strip()

        if news_id == 'new':
            db.execute("INSERT INTO news (title, content, category_id, author_id, images, video, is_featured) VALUES (?,?,?,?,?,?,?)",
                       (title, content, cat_id, session['user_id'], final_images_str, final_video, is_featured))
            flash('خبر با موفقیت ایجاد شد.', 'success')
        else:
            db.execute("UPDATE news SET title=?, content=?, category_id=?, images=?, video=?, is_featured=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (title, content, cat_id, final_images_str, final_video, is_featured, news_id))
            flash('خبر با موفقیت ویرایش شد.', 'success')
        db.commit()
        return redirect('/admin/news')

    cats = db.execute("SELECT * FROM categories").fetchall()
    return render_template('admin_news_form.html', cats=cats, post=post)

@app.route('/admin/news/delete/<int:id>', methods=['POST'])
@role_required(['admin', 'editor'])
def delete_news(id):
    db = get_db()
    item = db.execute("SELECT images, video FROM news WHERE id=?", (id,)).fetchone()
    if item:
        if item['images']: delete_file_from_disk(item['images'])
        if item['video']: delete_file_from_disk(item['video'])
        
    db.execute("DELETE FROM news WHERE id=?", (id,))
    db.commit()
    flash('خبر حذف شد.', 'success')
    return redirect('/admin/news')

@app.route('/admin/categories', methods=['GET', 'POST'])
@role_required(['admin', 'editor'])
def admin_categories():
    db = get_db()
    edit_cat = None
    if request.args.get('edit'):
        edit_cat = db.execute("SELECT * FROM categories WHERE id=?", (request.args.get('edit'),)).fetchone()

    if request.method == 'POST':
        if request.form.get('cat_id'):
            try:
                db.execute("UPDATE categories SET name=? WHERE id=?", (request.form['name'], request.form['cat_id']))
                flash('دسته ویرایش شد.', 'success')
            except sqlite3.IntegrityError:
                flash('نام دسته تکراری است.', 'error')
        else:
            try:
                db.execute("INSERT INTO categories (name) VALUES (?)", (request.form['name'],))
                flash('دسته جدید افزوده شد.', 'success')
            except sqlite3.IntegrityError:
                flash('این دسته قبلاً وجود دارد.', 'error')
        db.commit()
        return redirect('/admin/categories')
    
    cats = db.execute("SELECT * FROM categories").fetchall()
    return render_template('admin_categories.html', cats=cats, edit_cat=edit_cat)

@app.route('/admin/categories/delete/<int:id>', methods=['POST'])
@role_required(['admin', 'editor'])
def delete_category(id):
    db = get_db()
    news_items = db.execute("SELECT images, video FROM news WHERE category_id=?", (id,)).fetchall()
    for item in news_items:
        if item['images']: delete_file_from_disk(item['images'])
        if item['video']: delete_file_from_disk(item['video'])
        
    db.execute("DELETE FROM news WHERE category_id=?", (id,))
    db.execute("DELETE FROM categories WHERE id=?", (id,))
    db.commit()
    flash('دسته و اخبار مربوط به آن حذف شدند.', 'success')
    return redirect('/admin/categories')

@app.route('/admin/messages')
@role_required(['admin', 'support'])
def admin_messages():
    db = get_db()
    view_type = request.args.get('type', 'users')
    view_id = request.args.get('view')
    
    if view_type == 'guests':
        msgs = db.execute("SELECT * FROM messages WHERE user_id IS NULL ORDER BY created_at DESC").fetchall()
    else:
        msgs = db.execute("SELECT * FROM messages WHERE user_id IS NOT NULL AND parent_id IS NULL ORDER BY created_at DESC").fetchall()
    
    selected_ticket = None
    replies = []
    
    if view_id:
        selected_ticket = db.execute("SELECT * FROM messages WHERE id=?", (view_id,)).fetchone()
        if selected_ticket:
            replies = db.execute("SELECT * FROM messages WHERE parent_id=? ORDER BY created_at ASC", (view_id,)).fetchall()
            
            db.execute("UPDATE messages SET is_read=1 WHERE parent_id=? AND is_read=0", (view_id,))
            db.execute("UPDATE messages SET is_read=1 WHERE id=? AND is_read=0", (view_id,))
            db.commit()

    return render_template('admin_messages.html', msgs=msgs, selected_ticket=selected_ticket, replies=replies)

@app.route('/admin/messages/reply/<int:msg_id>', methods=['POST'])
@role_required(['admin', 'support'])
def admin_reply_message(msg_id):
    db = get_db()
    reply_content = request.form.get('reply')
    original = db.execute("SELECT user_id, name, subject FROM messages WHERE id=?", (msg_id,)).fetchone()
    
    if not original['user_id']:
        flash('امکان پاسخ به کاربران مهمان وجود ندارد.', 'error')
        return redirect(url_for('admin_messages', view=msg_id))

    if original and reply_content:
        db.execute("INSERT INTO messages (name, message, user_id, parent_id, sender_id, subject, status, is_read) VALUES (?, ?, ?, ?, ?, ?, 'answered', 1)",
                   (original['name'], reply_content, original['user_id'], msg_id, session['user_id'], original['subject']))
        
        db.execute("UPDATE messages SET status='answered' WHERE id=?", (msg_id,))
        db.commit()
        flash('پاسخ ارسال شد.', 'success')
        
    return redirect(url_for('admin_messages', view=msg_id))

@app.route('/admin/messages/status/<int:id>/<status>')
@role_required(['admin', 'support'])
def message_status(id, status):
    get_db().execute("UPDATE messages SET status=? WHERE id=?", (status, id)).connection.commit()
    flash('وضعیت پیام تغییر کرد.', 'success')
    return redirect(url_for('admin_messages', view=id))

@app.route('/admin/messages/delete/<int:id>', methods=['POST'])
@role_required(['admin', 'support'])
def delete_message(id):
    db = get_db()
    db.execute("DELETE FROM messages WHERE parent_id=?", (id,))
    db.execute("DELETE FROM messages WHERE id=?", (id,))
    db.commit()
    flash('تیکت حذف شد.', 'success')
    return redirect('/admin/messages')

@app.route('/admin/chat', methods=['GET', 'POST'])
@role_required(['admin', 'support', 'editor', 'marketer'])
def admin_internal_chat():
    db = get_db()
    
    if request.method == 'POST':
        receiver_id = request.form.get('receiver_id')
        message = request.form.get('message')
        is_group = request.form.get('is_group')
        
        file_path = None
        if 'chat_file' in request.files:
            f = request.files['chat_file']
            if f and f.filename:
                fname = safe_filename_generator(f.filename)
                if fname:
                    f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                    file_path = f"static/uploads/{fname}"

        if message or file_path:
            target = 0 if is_group else (int(receiver_id) if receiver_id else 0)
            db.execute("INSERT INTO internal_chats (sender_id, receiver_id, message, file_path) VALUES (?, ?, ?, ?)",
                       (session['user_id'], target, message, file_path))
            db.commit()
            
            if is_group:
                return redirect(url_for('admin_internal_chat', group=1))
            else:
                return redirect(url_for('admin_internal_chat', user=receiver_id))

    admins_rows = db.execute("SELECT id, name, role FROM users WHERE role IN ('admin', 'support', 'editor', 'marketer')").fetchall()
    admins = []
    for u in admins_rows:
        usr = dict(u)
        count = db.execute("SELECT count(*) FROM internal_chats WHERE sender_id=? AND receiver_id=? AND is_read=0", (u['id'], session['user_id'])).fetchone()[0]
        usr['has_unread'] = (count > 0)
        admins.append(usr)
    
    group_unread = 0
    last_seen_row = db.execute("SELECT last_seen_group_msg_id FROM users WHERE id=?", (session['user_id'],)).fetchone()
    last_seen_id = last_seen_row[0] if last_seen_row and last_seen_row[0] else 0
    group_unread = db.execute("SELECT count(*) FROM internal_chats WHERE receiver_id=0 AND id > ?", (last_seen_id,)).fetchone()[0]

    selected_user = None
    chat_msgs_raw = []
    is_group = request.args.get('group') == '1'
    
    if is_group:
          chat_msgs_raw = db.execute("""
            SELECT ic.*, u.name as sender_name 
            FROM internal_chats ic
            JOIN users u ON ic.sender_id = u.id
            WHERE receiver_id = 0
            ORDER BY created_at ASC
        """).fetchall()
          
          chat_msgs = []
          if chat_msgs_raw:
              max_id = chat_msgs_raw[-1]['id']
              if max_id > last_seen_id:
                  db.execute("UPDATE users SET last_seen_group_msg_id = ? WHERE id = ?", (max_id, session['user_id']))
                  db.commit()

              for m in chat_msgs_raw:
                  msg_dict = dict(m)
                  msg_dict['is_new_in_view'] = (m['id'] > last_seen_id) and (m['sender_id'] != session['user_id'])
                  chat_msgs.append(msg_dict)
    else:
        target_id = request.args.get('user')
        if target_id:
            selected_user = db.execute("SELECT id, name FROM users WHERE id=?", (target_id,)).fetchone()
            if selected_user:
                chat_msgs_raw = db.execute("""
                    SELECT * FROM internal_chats 
                    WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
                    ORDER BY created_at ASC
                """, (session['user_id'], target_id, target_id, session['user_id'])).fetchall()
                
                unread_ids = []
                for m in chat_msgs_raw:
                    if m['sender_id'] == int(target_id) and m['is_read'] == 0:
                        unread_ids.append(m['id'])
                
                chat_msgs = []
                for m in chat_msgs_raw:
                    msg_dict = dict(m)
                    msg_dict['is_new_in_view'] = (msg_dict['id'] in unread_ids)
                    chat_msgs.append(msg_dict)
                
                if unread_ids:
                    placeholders = ','.join(['?'] * len(unread_ids))
                    db.execute(f"UPDATE internal_chats SET is_read=1 WHERE id IN ({placeholders})", unread_ids)
                    db.commit()
            else:
                chat_msgs = []
        else:
             chat_msgs = []

    return render_template('admin_chat.html', admins=admins, selected_user=selected_user, chat_msgs=chat_msgs, is_group=is_group, group_unread=group_unread)

@app.route('/admin/chat/edit', methods=['POST'])
@role_required(['admin', 'support', 'editor', 'marketer'])
def edit_chat_msg():
    db = get_db()
    msg_id = request.form.get('msg_id')
    new_text = request.form.get('new_message')
    
    msg = db.execute("SELECT sender_id FROM internal_chats WHERE id=?", (msg_id,)).fetchone()
    if msg and msg['sender_id'] == session['user_id']:
        db.execute("UPDATE internal_chats SET message=? WHERE id=?", (new_text, msg_id))
        db.commit()
    
    return redirect(request.referrer or url_for('admin_internal_chat'))

@app.route('/admin/chat/delete/<int:id>')
@role_required(['admin', 'support', 'editor', 'marketer'])
def delete_chat_msg(id):
    db = get_db()
    msg = db.execute("SELECT sender_id, receiver_id, file_path FROM internal_chats WHERE id=?", (id,)).fetchone()
    
    if msg and msg['sender_id'] == session['user_id']:
        if msg['file_path']: delete_file_from_disk(msg['file_path'])
        db.execute("DELETE FROM internal_chats WHERE id=?", (id,))
        db.commit()
        
    if msg and msg['receiver_id'] == 0:
        return redirect(url_for('admin_internal_chat', group=1))
    elif msg:
        return redirect(url_for('admin_internal_chat', user=msg['receiver_id']))
    return redirect(url_for('admin_internal_chat'))

@app.route('/admin/users', methods=['GET', 'POST'])
@role_required(['admin'])
def admin_users():
    db = get_db()
    edit_user = None
    if request.args.get('edit'):
        edit_user = db.execute("SELECT * FROM users WHERE id=?", (request.args.get('edit'),)).fetchone()

    if request.method == 'POST':
        name = request.form['name']
        username = request.form['username']
        role = request.form['role']
        gender = request.form['gender']
        user_id = request.form.get('user_id')
        
        try:
            if user_id: 
                if request.form['password']:
                    db.execute("UPDATE users SET name=?, username=?, password=?, role=?, gender=? WHERE id=?",
                               (name, username, generate_password_hash(request.form['password']), role, gender, user_id))
                else:
                    db.execute("UPDATE users SET name=?, username=?, role=?, gender=? WHERE id=?",
                               (name, username, role, gender, user_id))
                flash('کاربر ویرایش شد.', 'success')
            else: 
                db.execute("INSERT INTO users (name, username, password, role, gender) VALUES (?, ?, ?, ?, ?)",
                           (name, username, generate_password_hash(request.form['password']), role, gender))
                flash('کاربر جدید ایجاد شد.', 'success')
            db.commit()
            return redirect('/admin/users')
        except sqlite3.IntegrityError: flash('خطا: نام کاربری تکراری است.', 'error')
    
    users = db.execute("SELECT * FROM users").fetchall()
    return render_template('admin_users.html', users=users, edit_user=edit_user)

@app.route('/admin/users/delete/<int:id>', methods=['POST'])
@role_required(['admin'])
def delete_user(id):
    get_db().execute("DELETE FROM users WHERE id=?", (id,)).connection.commit()
    flash('کاربر حذف شد.', 'success')
    return redirect('/admin/users')

@app.route('/admin/ads', methods=['GET', 'POST'])
@role_required(['admin', 'marketer'])
def admin_ads():
    db = get_db()
    edit_ad = None
    remaining_days = 30
    
    if request.args.get('edit'):
        edit_ad = db.execute("SELECT * FROM ads WHERE id=?", (request.args.get('edit'),)).fetchone()
        if edit_ad:
            try:
                exp_date = datetime.strptime(edit_ad['expires_at'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                now_date = datetime.now(timezone.utc)
                delta = exp_date - now_date
                remaining_days = max(1, delta.days)
            except: 
                remaining_days = 30

    if request.method == 'POST':
        img = edit_ad['image'] if edit_ad else ""
        
        if request.form.get('delete_image'):
             delete_file_from_disk(img)
             img = ""
        
        if 'ad_image' in request.files:
            f = request.files['ad_image']
            if f and f.filename:
                fname = safe_filename_generator(f.filename)
                if fname:
                    f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                    if img and not request.form.get('delete_image'): delete_file_from_disk(img) 
                    img = f"static/uploads/{fname}"
        
        if request.form.get('ad_image_url'):
            new_url = request.form.get('ad_image_url').strip()
            if new_url:
                if img and not request.form.get('delete_image') and 'static' in img: delete_file_from_disk(img)
                img = new_url
        
        days = int(request.form.get('days', 30))
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

        if request.form.get('ad_id'):
            db.execute("UPDATE ads SET title=?, description=?, link=?, image=?, expires_at=? WHERE id=?",
                       (request.form.get('title'), request.form.get('description'), request.form['link'], img, expires_at, request.form.get('ad_id')))
            flash('تبلیغ ویرایش شد.', 'success')
        else:
            db.execute("INSERT INTO ads (title, description, link, image, expires_at) VALUES (?,?,?,?,?)",
                       (request.form.get('title'), request.form.get('description'), request.form['link'], img, expires_at))
            flash('تبلیغ جدید ایجاد شد.', 'success')
        db.commit()
        return redirect('/admin/ads')
    
    ads = db.execute("SELECT * FROM ads ORDER BY id DESC").fetchall()
    return render_template('admin_ads.html', ads=ads, edit_ad=edit_ad, remaining_days=remaining_days)

@app.route('/admin/ads/delete/<int:id>', methods=['POST'])
@role_required(['admin', 'marketer'])
def delete_ad(id):
    db = get_db()
    ad = db.execute("SELECT image FROM ads WHERE id=?", (id,)).fetchone()
    if ad and ad['image']: delete_file_from_disk(ad['image'])
    
    db.execute("DELETE FROM ads WHERE id=?", (id,))
    db.commit()
    flash('تبلیغ حذف شد.', 'success')
    return redirect('/admin/ads')

@app.route('/ad_click/<int:ad_id>')
def ad_click(ad_id):
    db = get_db()
    db.execute("UPDATE ads SET clicks = clicks + 1 WHERE id = ?", (ad_id,))
    db.commit()
    link = db.execute("SELECT link FROM ads WHERE id = ?", (ad_id,)).fetchone()
    if link and link['link']:
        return redirect(link['link'])
    return redirect('/')


def init_db_struct():
    with app.app_context():
        db = get_db()
        db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, username TEXT UNIQUE, password TEXT, role TEXT, gender TEXT)')
        
        try:
            db.execute('ALTER TABLE users ADD COLUMN last_seen_group_msg_id INTEGER DEFAULT 0')
        except sqlite3.OperationalError: pass
            
        db.execute('CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY, name TEXT UNIQUE)') 
        
        db.execute('''CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT, category_id INTEGER, author_id INTEGER, 
            images TEXT, video TEXT, is_featured INTEGER DEFAULT 0, views INTEGER DEFAULT 0, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP,
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE,
            FOREIGN KEY(author_id) REFERENCES users(id) ON DELETE CASCADE)''')
        
        db.execute("CREATE INDEX IF NOT EXISTS idx_news_category ON news(category_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at)")
            
        db.execute('''CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY, news_id INTEGER, user_id INTEGER, content TEXT, 
            parent_id INTEGER, rating INTEGER DEFAULT 5, is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP,
            FOREIGN KEY(news_id) REFERENCES news(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
        
        try:
             db.execute("SELECT is_read FROM comments LIMIT 1")
        except:
             db.execute("ALTER TABLE comments ADD COLUMN is_read INTEGER DEFAULT 0")
        
        db.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY, name TEXT, contact_info TEXT, subject TEXT, message TEXT, 
            user_id INTEGER, parent_id INTEGER, sender_id INTEGER,
            status TEXT DEFAULT 'new', is_read INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
        
        try:
             db.execute("SELECT is_read FROM messages LIMIT 1")
        except:
             print(">>> Adding 'is_read' column to messages table for tracking unread replies...")
             db.execute("ALTER TABLE messages ADD COLUMN is_read INTEGER DEFAULT 0")
            
        db.execute('''CREATE TABLE IF NOT EXISTS internal_chats (
            id INTEGER PRIMARY KEY, sender_id INTEGER, receiver_id INTEGER, message TEXT, file_path TEXT,
            is_read INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE)''')

        db.execute('''CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY, title TEXT, description TEXT, image TEXT, link TEXT, 
            views INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, 
            expires_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        try:
            admin_exist = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
            if not admin_exist:
                db.execute("INSERT INTO users (name, username, password, role, gender) VALUES (?, ?, ?, ?, ?)", 
                           ('مدیر سیستم', 'admin', generate_password_hash('123'), 'admin', 'male'))
                print(">>> Admin user created (admin / 123)")
        except Exception as e:
            print(f"Error seeding database: {e}")
        
        db.commit()

if __name__ == '__main__':
    init_db_struct()
    print(">>> سیستم مدیریت محتوای دهکده خبر آماده است: http://127.0.0.1:5000")
    print(">>> برای ورود از نام کاربری admin و رمز عبور 123 استفاده کنید.")
    app.run(debug=True, port=5000)