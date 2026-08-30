import os
import json
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify
from datetime import datetime, timedelta
import pandas as pd
import io
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.header import Header, decode_header
import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

app.secret_key = os.environ.get('SECRET_KEY', 'plonaris-crm-secret-key-2026')

CRM_USERNAME = os.environ.get('CRM_USERNAME', 'admin')
CRM_PASSWORD = os.environ.get('CRM_PASSWORD', 'Plonaris2026') 

DATABASE_URL = os.environ.get('DATABASE_URL')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

MAIL_SERVER = os.environ.get('MAIL_SERVER', 'mail.adm.tools')
MAIL_PORT = 465
MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def decode_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                charset = part.get_content_charset() or 'utf-8'
                try:
                    payload = part.get_payload(decode=True)
                    body = payload.decode(charset, errors='ignore')
                    break
                except Exception:
                    pass
    else:
        charset = msg.get_content_charset() or 'utf-8'
        try:
            payload = msg.get_payload(decode=True)
            body = payload.decode(charset, errors='ignore')
        except Exception:
            body = "[Помилка декодування тексту листа]"
            
    return body.strip()

def send_email_notification(to_email, subject, body_text):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print("⚠️ Налаштування пошти відсутні!")
        return False
    try:
        html_body = body_text
        html_content = f"""
        <html>
        <body style="font-family: 'Aptos', Calibri, Arial, sans-serif; color: #1e293b; line-height: 1.6;">
            <div style="font-size: 15px; margin-bottom: 25px;">
                {html_body}
            </div>
            <hr style="border: none; border-top: 1px solid #d3e2d8; margin-top: 30px; margin-bottom: 20px;">
            <table border="0" cellpadding="0" cellspacing="0" style="color: #1e293b;">
                <tr>
                    <td style="vertical-align: top; border-left: 3px solid #2D7F35; padding-left: 15px; font-size: 15px;">
                        <span style="color: #64748b; font-style: italic;">Z poważaniem / Pozdrawiamy</span><br><br>
                        <strong style="font-size: 18px; color: #2D7F35;">PLONARIS Sp. z o.o.</strong><br>
                        <span style="color: #475569; font-weight: 500;">Dział Obsługi Klienta & Sprzedaży</span><br>
                        📧 <a href="mailto:{MAIL_USERNAME}" style="color: #2D7F35; text-decoration: none;">{MAIL_USERNAME}</a>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        msg = MIMEText(html_content, 'html', 'utf-8')
        msg['Subject'] = Header(subject, 'utf-8')
        msg['From'] = MAIL_USERNAME
        msg['To'] = to_email
        
        server = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT)
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.sendmail(MAIL_USERNAME, [to_email], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"❌ Помилка SMTP відправки: {str(e)}")
        return False

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT,
            address TEXT,
            contact_person TEXT,
            position TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            buyer_type TEXT,
            brands TEXT,
            contact_person_2 TEXT,
            position_2 TEXT,
            phone_2 TEXT,
            email_2 TEXT,
            interest_level TEXT,
            next_event_date TEXT,
            next_event_type TEXT,
            mayer_reg TEXT,
            whatsapp_1 TEXT,
            whatsapp_2 TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            deactivation_reason TEXT,
            deal_stage TEXT DEFAULT 'none',
            aftermarket_companies TEXT
        )
    ''')
    
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='clients'")
    existing_columns = [row[0] for row in cursor.fetchall()]
    
    new_fields = {
        'website': 'TEXT', 'buyer_type': 'TEXT', 'brands': 'TEXT', 'position': 'TEXT',
        'contact_person_2': 'TEXT', 'position_2': 'TEXT', 'phone_2': 'TEXT', 'email_2': 'TEXT',
        'interest_level': 'TEXT', 'next_event_date': 'TEXT', 'next_event_type': 'TEXT', 'mayer_reg': 'TEXT',
        'whatsapp_1': 'TEXT', 'whatsapp_2': 'TEXT', 'is_active': 'BOOLEAN DEFAULT TRUE', 'deactivation_reason': 'TEXT',
        'deal_stage': "TEXT DEFAULT 'none'",
        'aftermarket_companies': 'TEXT'
    }
    
    for field, f_type in new_fields.items():
        if field not in existing_columns:
            cursor.execute(f"ALTER TABLE clients ADD COLUMN {field} {f_type};")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS negotiations (
            id SERIAL PRIMARY KEY,
            client_id INTEGER,
            date TEXT,
            result TEXT,
            author TEXT,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='negotiations'")
    existing_neg_columns = [row[0] for row in cursor.fetchall()]
    if 'author' not in existing_neg_columns:
        cursor.execute("ALTER TABLE negotiations ADD COLUMN author TEXT DEFAULT 'Продажі';")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            text TEXT NOT NULL,
            deadline TEXT,
            author TEXT DEFAULT 'Продажі',
            status TEXT DEFAULT 'in_progress'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_plans (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL,
            planned_amount NUMERIC(12, 2) DEFAULT 0,
            month_name TEXT,
            actual_amount NUMERIC(12, 2) DEFAULT 0,
            payment_date TEXT,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lost_demand (
            id SERIAL PRIMARY KEY,
            client_id INTEGER,
            article TEXT NOT NULL,
            title TEXT,
            quantity INTEGER DEFAULT 1,
            status TEXT DEFAULT 'lost',
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE CASCADE
        )
    ''')
    
    conn.commit()
    cursor.close()
    conn.close()

if DATABASE_URL:
    init_db()

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == CRM_USERNAME and password == CRM_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'Невірний логін або пароль'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    search_query = request.args.get('search', '').strip()
    interest_filter = request.args.get('interest', '').strip()
    country_filter = request.args.get('country', '').strip()
    finance_month_filter = request.args.get('finance_month', '').strip()
    status_view = request.args.get('status_view', 'active').strip()
    
    conn = get_db_connection()
    
    with conn.cursor() as fix_cursor:
        fix_cursor.execute("""
            UPDATE clients 
            SET country = CASE 
                WHEN LOWER(country) IN ('польша', 'polska', 'poland') THEN 'Польща'
                WHEN LOWER(country) IN ('украина', 'ukraine') THEN 'Україна'
                WHEN LOWER(country) IN ('германия', 'deutschland', 'germany') THEN 'Німеччина'
                WHEN LOWER(country) IN ('словакия', 'slovakia', 'słowacja') THEN 'Словаччина'
                WHEN LOWER(country) IN ('чехия', 'czechia', 'czech republic') THEN 'Чехія'
                WHEN LOWER(country) IN ('австрия', 'austria', 'австрія') THEN 'Австрія'
                WHEN LOWER(country) IN ('турция', 'turkey', 'türkiye', 'туреччина') THEN 'Туреччина'
                WHEN LOWER(country) IN ('испания', 'spain', 'españa', 'іспанія') THEN 'Іспанія'
                WHEN LOWER(country) IN ('франция', 'france', 'франція') THEN 'Франція'
                WHEN LOWER(country) IN ('аргентина', 'argentina') THEN 'Аргентина'
                WHEN LOWER(country) IN ('китай', 'china') THEN 'Китай'
                ELSE country 
            END
            WHERE country IS NOT NULL AND country != '';
        """)
        conn.commit()
    
    country_cursor = conn.cursor()
    country_cursor.execute("SELECT DISTINCT country FROM clients WHERE country IS NOT NULL AND country != '' ORDER BY country ASC")
    countries = [row[0] for row in country_cursor.fetchall()]
    country_cursor.close()
    
    stats_cursor = conn.cursor()
    stats_cursor.execute("SELECT COUNT(*) FROM clients WHERE is_active IS NOT FALSE")
    total_clients = stats_cursor.fetchone()[0]
    
    stats_cursor.execute("SELECT interest_level, COUNT(*) FROM clients WHERE is_active IS NOT FALSE GROUP BY interest_level")
    raw_interest = stats_cursor.fetchall()
    
    interest_stats = {'не опрацьовано': 0, 'немає зацікавленості': 0, 'середня зацікавленість': 0, 'зацікавленість': 0}
    for row in raw_interest:
        status = row[0] if row[0] else 'не опрацьовано'
        if status in interest_stats:
            interest_stats[status] = row[1]
            
    stats_cursor.execute("SELECT country, COUNT(*) FROM clients WHERE country IS NOT NULL AND country != '' AND is_active IS NOT FALSE GROUP BY country ORDER BY COUNT(*) DESC")
    country_stats = stats_cursor.fetchall()

    stats_cursor.execute("SELECT buyer_type, COUNT(*) FROM clients WHERE buyer_type IS NOT NULL AND buyer_type != 'не вказано' AND buyer_type != '' AND is_active IS NOT FALSE GROUP BY buyer_type ORDER BY COUNT(*) DESC")
    buyer_type_stats = stats_cursor.fetchall()
    stats_cursor.close()
    
    dict_cursor = conn.cursor(cursor_factory=DictCursor)
    finance_sql = """
        SELECT sp.id, sp.client_id, sp.planned_amount, sp.month_name, sp.actual_amount, sp.payment_date,
               c.name as client_name, c.country as client_country
        FROM sales_plans sp
        JOIN clients c ON sp.client_id = c.id
    """
    finance_params = []
    if finance_month_filter:
        finance_sql += " WHERE sp.month_name = %s"
        finance_params.append(finance_month_filter)
        
    finance_sql += " ORDER BY sp.id DESC"
    dict_cursor.execute(finance_sql, finance_params)
    finance_rows = dict_cursor.fetchall()
    
    total_planned = 0
    total_actual = 0
    finance_plans = []
    for row in finance_rows:
        p_amt = float(row['planned_amount'] or 0)
        a_amt = float(row['actual_amount'] or 0)
        total_planned += p_amt
        total_actual += a_amt
        finance_plans.append({
            'id': row['id'], 'client_id': row['client_id'], 'client_name': row['client_name'],
            'country': row['client_country'] if row['client_country'] else '-',
            'planned_amount': p_amt, 'month_name': row['month_name'] if row['month_name'] else '-',
            'actual_amount': a_amt, 'payment_date': row['payment_date'] if row['payment_date'] else '-'
        })
    total_remaining = total_planned - total_actual
    
    dict_cursor.execute("""
        SELECT ld.*, c.name as client_name 
        FROM lost_demand ld 
        LEFT JOIN clients c ON ld.client_id = c.id 
        ORDER BY ld.id DESC
    """)
    raw_demand = dict_cursor.fetchall()
    lost_demand_list = [dict(d) for d in raw_demand]

    dict_cursor.execute("""
        SELECT article, title, COUNT(*) as request_count, SUM(quantity) as total_qty
        FROM lost_demand
        GROUP BY article, title
        ORDER BY request_count DESC, total_qty DESC
        LIMIT 10
    """)
    top_demand_raw = dict_cursor.fetchall()
    top_demand = [dict(t) for t in top_demand_raw]

    dict_cursor.execute("SELECT id, name FROM clients WHERE is_active IS NOT FALSE ORDER BY name ASC")
    all_selector_clients = dict_cursor.fetchall()

    dict_cursor.execute("SELECT id, text, deadline, author, status FROM tasks ORDER BY id DESC")
    tasks_raw = dict_cursor.fetchall()
    tasks = [dict(t) for t in tasks_raw]

    cal_cursor = conn.cursor(cursor_factory=DictCursor)
    cal_cursor.execute("SELECT id, name, country, contact_person, phone, next_event_date, next_event_type FROM clients WHERE next_event_date IS NOT NULL AND next_event_date != '' AND is_active IS NOT FALSE")
    all_raw_cal = cal_cursor.fetchall()
    
    clients_js_data = []
    busy_dates = []
    for r in all_raw_cal:
        c_date = str(r['next_event_date'])
        busy_dates.append(c_date)
        clients_js_data.append({
            'id': int(r['id']),
            'name': str(r['name']).replace('"', '\\"').replace("'", "\\'"),
            'country': str(r['country']).replace('"', '\\"').replace("'", "\\'") if r['country'] else '',
            'contact_person': str(r['contact_person']).replace('"', '\\"').replace("'", "\\'") if r['contact_person'] else '',
            'phone': str(r['phone']) if r['phone'] else '',
            'next_event_date': c_date,
            'next_event_type': str(r['next_event_type']) if r['next_event_type'] else ''
        })
    cal_cursor.close()
    
    cursor = conn.cursor(cursor_factory=DictCursor)
    sql = """
        SELECT c.*, 
               (SELECT MAX(n.date)::TEXT FROM negotiations n WHERE n.client_id = c.id) AS last_activity,
               (SELECT n.result FROM negotiations n WHERE n.client_id = c.id ORDER BY n.id DESC LIMIT 1) AS last_activity_text
        FROM clients c 
        WHERE 1=1
    """
    params = []
    
    if status_view == 'active':
        sql += " AND c.is_active IS NOT FALSE"
    elif status_view == 'archived':
        sql += " AND c.is_active IS FALSE"

    if search_query:
        sql += " AND (LOWER(c.name) LIKE LOWER(%s) OR LOWER(c.contact_person) LIKE LOWER(%s) OR LOWER(c.brands) LIKE LOWER(%s) OR LOWER(c.country) LIKE LOWER(%s) OR LOWER(c.buyer_type) LIKE LOWER(%s) OR LOWER(c.aftermarket_companies) LIKE LOWER(%s))"
        params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])
        
    if interest_filter:
        sql += " AND c.interest_level = %s"
        params.append(interest_filter)
        
    if country_filter:
        sql += " AND c.country = %s"
        params.append(country_filter)
        
    today_str = datetime.now().strftime("%Y-%m-%d")
    sql += f" ORDER BY (CASE WHEN c.next_event_date = '{today_str}' THEN 0 ELSE 1 END), (CASE WHEN (SELECT MAX(n.date) FROM negotiations n WHERE n.client_id = c.id) IS NULL THEN 1 ELSE 0 END), (SELECT MAX(n.date) FROM negotiations n WHERE n.client_id = c.id) DESC, c.name ASC"
    
    cursor.execute(sql, params)
    raw_clients = cursor.fetchall()
    
    clients = []
    for row in raw_clients:
        clients.append({
            'id': int(row['id']),
            'name': row['name'] if row['name'] else '',
            'country': row['country'] if row['country'] else '',
            'address': row['address'] if row['address'] else '',
            'contact_person': row['contact_person'] if row['contact_person'] else '',
            'position': row['position'] if row['position'] else '',
            'phone': row['phone'] if row['phone'] else '',
            'email': row['email'] if row['email'] else '',
            'website': row['website'] if row['website'] else '',
            'buyer_type': row['buyer_type'] if row['buyer_type'] else 'не вказано',
            'brands': row['brands'] if row['brands'] else '-',
            'aftermarket_companies': row['aftermarket_companies'] if row['aftermarket_companies'] else '',
            'interest_level': row['interest_level'] if row['interest_level'] else 'не опрацьовано',
            'deal_stage': row['deal_stage'] if row['deal_stage'] else 'none',
            'last_activity': row['last_activity'] if row['last_activity'] else '',
            'last_activity_text': row['last_activity_text'] if row['last_activity_text'] else '',
            'next_event_date': str(row['next_event_date']) if row['next_event_date'] else '',
            'next_event_type': str(row['next_event_type']) if row['next_event_type'] else '',
            'mayer_reg': row['mayer_reg'] if row['mayer_reg'] else 'Ні',
            'is_active': row['is_active'] if row['is_active'] is not None else True,
            'deactivation_reason': row['deactivation_reason'] if row['deactivation_reason'] else ''
        })
    cursor.close()
    dict_cursor.close()
    conn.close()
    
    json_clients = json.dumps(clients_js_data, ensure_ascii=False)
    json_busy_dates = json.dumps(busy_dates, ensure_ascii=False)
    
    return render_template(
        'index.html', 
        clients=clients, 
        countries=countries, 
        all_selector_clients=all_selector_clients,
        search_query=search_query, 
        interest_filter=interest_filter,
        country_filter=country_filter,
        finance_month_filter=finance_month_filter,
        status_view=status_view,
        total_clients=total_clients,
        interest_stats=interest_stats,
        country_stats=country_stats,
        buyer_type_stats=buyer_type_stats,
        finance_plans=finance_plans, 
        total_planned=total_planned, 
        total_actual=total_actual, 
        total_remaining=total_remaining,
        json_clients=json_clients,
        json_busy_dates=json_busy_dates,
        today_date=today_str,
        tasks=tasks,
        json_tasks=json.dumps(tasks, ensure_ascii=False),
        lost_demand_list=lost_demand_list,
        top_demand=top_demand
    )

# МАРШРУТ АВТОМАТИЧНОГО ІМПОРТУ ПАРТНЕРІВ PLONARIS
@app.route('/run-partners-import')
@login_required
def run_partners_import():
    partners_data = [
        {"name": "BDO", "country": "Польща", "address": "", "contact_person": "", "position": "", "phone": "", "phone_2": "", "email": "", "website": "https://bdo.mos.gov.pl/", "notes": "Реєстрація BDO / Вивіз відходів"},
        {"name": "MAYER PRO s.r.o.", "country": "Словаччина", "address": "Jegenesska 9", "contact_person": "Dmytro Petrychenko", "position": "Regional manager", "phone": "501166523", "phone_2": "+421 907 933 441", "email": "sales@mayer-pro.com", "website": "https://mayer-pro.com/pl", "notes": "Постачальник aftermarket запчастин до сільгосптехніки"},
        {"name": "ZETTA GROUP Sp. z o.o.", "country": "Польща", "address": "ul. Szlak 77/222 31-153 Kraków", "contact_person": "Wiktor Sasulski", "position": "", "phone": "+48 730 898 378", "phone_2": "", "email": "wiktor.sasulski@zettagroup.com.pl", "website": "https://zettagroup.com.pl/pro-kompaniyu/", "notes": "Firma produkuje części do maszyn rolniczych z metali i polimerów stałych. Claas, Geringhoff, John Deere, Case, New Holland, Fantini, Lemken, Oros, Gaspardo i inne."},
        {"name": "IQ Parts  Beyne GMBH", "country": "Австрія", "address": "Bundesstraße 8A-8661 St. Barbara im Mürztal", "contact_person": "Sven Vierstraete", "position": "", "phone": "+43385860556100", "phone_2": "", "email": "svenvierstraete@iqparts.com", "website": "http://www.iqparts.com", "notes": "Виробник робочих органів"},
        {"name": "AMA Star", "country": "Туреччина", "address": "Güzelyurt, 5787 Sk. No:9, 45030 Yunusemre/Manisa", "contact_person": "Ersin Vardar", "position": "", "phone": "+90 539 684 86 46", "phone_2": "", "email": "ersinvardar@startrm.com", "website": "https://amastar.com.tr/en", "notes": "Виробляють диски до дискових борін різних марок."},
        {"name": "Granit Parts", "country": "Польща", "address": "", "contact_person": "Piotr Kowarski", "position": "manager", "phone": "+48 661 300 670", "phone_2": "", "email": "piotr.kowalski@granit-parts.com", "website": "www.granit-parts.pl", "notes": "Логін: 5780340  Пароль: J8fnIF7u"},
        {"name": "OZDOWSKI", "country": "Польща", "address": "ul.Zebrzydowicka 28, 44-217 Rybnik", "contact_person": "Aleksandra Ozdowska", "position": "", "phone": "+48 535 515 139", "phone_2": "", "email": "aleksandra@fhuozdowski.pl", "website": "https://www.zamiennikirolnicze.pl/", "notes": "Виробляють ланцюги для передачі крутного моменту."},
        {"name": "Agri Carb (Ceratizit Group)", "country": "Польща", "address": "", "contact_person": "Aron grelich", "position": "technical sales expert", "phone": "+48 667 756 067", "phone_2": "", "email": "aron.grelich@ceratizit.com", "website": "https://agricarb.com/int/en.html", "notes": "Виробляють робочі органи до різних типів грунтообробного обладнання. Спеціалізуються на долотах з карбід-вольфрамовою наплавкою."},
        {"name": "Agrocolla Navarro", "country": "Іспанія", "address": "", "contact_person": "Santiago Sanchiz Telemin", "position": "", "phone": "+34 628 747 376", "phone_2": "", "email": "export@agricolanavarro.com", "website": "https://agricolanavarro.com/productos/", "notes": "Виробник робочих органів (фрези, мульчувачі, вертикудери, активні культиватори)"},
        {"name": "BUCO", "country": "Аргентина", "address": "", "contact_person": "Mariano Buconic", "position": "", "phone": "+54 9 11 6454 8147", "phone_2": "", "email": "mariano.buconic@buco.com.ar", "website": "www.buco.com.ar", "notes": "Виробник коліс (прикочуючі колеса, колеса глибини тощо)"},
        {"name": "Dosemenler", "country": "Туреччина", "address": "", "contact_person": "Serdar Dósemen", "position": "", "phone": "+90 266 626 10 50", "phone_2": "", "email": "serdar@dosemen.com", "website": "https://www.dosemen.com/", "notes": "Розробляють та виготовляють робочі органи"},
        {"name": "OZAR", "country": "Туреччина", "address": "", "contact_person": "Sidharth Trikha", "position": "", "phone": "+91 941 691 68 20", "phone_2": "", "email": "globalsales@aloktools.com", "website": "https://www.aloktools.com/", "notes": "Виробляють ручний інструмент та інструмент для виробництв."},
        {"name": "STANMAR", "country": "Польща", "address": "", "contact_person": "Grzegorz Szulc", "position": "", "phone": "+48 883 633 073", "phone_2": "", "email": "grzegorz@stanmar.net.pl", "website": "https://stanmar.net.pl/oferta.html", "notes": "Виробник деталей на замовлення. Токарні та фрезерні роботи."},
        {"name": "AGROSALIX", "country": "Польща", "address": "", "contact_person": "Tomasz Wierzba", "position": "", "phone": "+48 508 375 808", "phone_2": "", "email": "agrosalixwierzba@gmail.com", "website": "", "notes": "Запчастини до John Deere (двигун)"},
        {"name": "AGRI-INDUS SAS", "country": "Франція", "address": "", "contact_person": "", "position": "", "phone": "+33344419533", "phone_2": "", "email": "contact@agri-indus.fr", "website": "https://www.agri-indus.fr/", "notes": "Виробник робочих органів з пластинами"},
        {"name": "ROLMUS", "country": "Польща", "address": "ul. Akacjowa 662-300 Września", "contact_person": "Mieczysław Szymkowiak", "position": "", "phone": "+48 61 4366 754", "phone_2": "", "email": "biuro@rolmus.com.pl", "website": "https://www.rolmus.com.pl/rozsiewacze.html", "notes": "Виробник запчастин до вітчизняної польської техніки"},
        {"name": "HUO SHIN ENTERPRISE CO.", "country": "Китай", "address": "", "contact_person": "Julie Wang", "position": "", "phone": "+88647713096", "phone_2": "", "email": "huoshin@ms16.hinet.net", "website": "http://www.wlk.com.tw", "notes": "Виробник сальників та прокладок"},
        {"name": "ZYCHAR", "country": "Польща", "address": "ul. Rolna 4 23-400 Biłgoraj", "contact_person": "", "position": "", "phone": "84 307 01 01", "phone_2": "", "email": "biuro@zychar.pl", "website": "https://www.zychar.pl/", "notes": "Виготовлення бортів до причепів"}
    ]

    conn = get_db_connection()
    cursor = conn.cursor()
    imported = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    for p in partners_data:
        cursor.execute("SELECT id FROM clients WHERE LOWER(name) = LOWER(%s)", (p['name'],))
        row = cursor.fetchone()
        if not row:
            cursor.execute("""
                INSERT INTO clients (
                    name, country, address, contact_person, position, phone, phone_2, 
                    email, website, buyer_type, interest_level, is_active, deal_stage
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'постачальник', 'середня зацікавленість', TRUE, 'none')
                RETURNING id
            """, (
                p['name'], p['country'], p['address'], p['contact_person'], p['position'],
                p['phone'], p['phone_2'], p['email'], p['website']
            ))
            client_id = cursor.fetchone()[0]
            
            if p['notes']:
                cursor.execute("""
                    INSERT INTO negotiations (client_id, date, result, author)
                    VALUES (%s, %s, %s, %s)
                """, (client_id, now_str, f"📋 [Нотатки постачальника]: {p['notes']}", 'CEO'))
            imported += 1

    conn.commit()
    cursor.close()
    conn.close()
    return f"<div style='font-family: sans-serif; padding: 30px; text-align: center;'><h2>✅ Успішно імпортовано {imported} постачальників!</h2><br><a href='/' style='background: #2D7F35; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; font-weight: bold;'>Повернутися в CRM</a></div>"

@app.route('/toggle_client_status/<int:client_id>', methods=['POST'])
@login_required
def toggle_client_status(client_id):
    action = request.form.get('action')
    reason = request.form.get('reason', '').strip()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    if action == 'deactivate':
        cursor.execute("UPDATE clients SET is_active = FALSE, deactivation_reason = %s WHERE id = %s", (reason, client_id))
        log_text = f"⛔ [ДЕАКТИВАЦІЯ] Переведено в архів. Причина: {reason or 'Не вказано'}"
        cursor.execute("INSERT INTO negotiations (client_id, date, result, author) VALUES (%s, %s, %s, %s)", (client_id, current_date, log_text, 'CEO'))
    else:
        cursor.execute("UPDATE clients SET is_active = TRUE, deactivation_reason = NULL WHERE id = %s", (client_id,))
        log_text = "🟢 [АКТИВАЦІЯ] Відновлено з архіву в активну базу."
        cursor.execute("INSERT INTO negotiations (client_id, date, result, author) VALUES (%s, %s, %s, %s)", (client_id, current_date, log_text, 'CEO'))
        
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('client_detail', client_id=client_id))

@app.route('/add_quick_sale/<int:client_id>', methods=['POST'])
@login_required
def add_quick_sale(client_id):
    amount = request.form.get('amount', 0)
    invoice_no = request.form.get('invoice_no', '').strip()
    payment_date = request.form.get('payment_date', '').strip()
    month_name = request.form.get('month_name', '').strip()
    next_action_days = int(request.form.get('next_action_days', 7))
    
    if not payment_date:
        payment_date = datetime.now().strftime("%Y-%m-%d")
        
    ukr_months = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень", "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
    if not month_name:
        month_idx = datetime.now().month - 1
        month_name = ukr_months[month_idx]
        
    try:
        amt_val = float(amount)
    except Exception:
        amt_val = 0.0
        
    if amt_val > 0:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, actual_amount, planned_amount FROM sales_plans WHERE client_id = %s ORDER BY id DESC LIMIT 1",
            (client_id,)
        )
        existing_plan = cursor.fetchone()
        
        if existing_plan:
            plan_id = existing_plan[0]
            current_actual = float(existing_plan[1] or 0)
            new_actual = current_actual + amt_val
            cursor.execute(
                "UPDATE sales_plans SET actual_amount = %s, payment_date = %s WHERE id = %s",
                (new_actual, payment_date, plan_id)
            )
        else:
            cursor.execute(
                "INSERT INTO sales_plans (client_id, planned_amount, month_name, actual_amount, payment_date) VALUES (%s, %s, %s, %s, %s)",
                (client_id, 0.0, month_name, amt_val, payment_date)
            )
        
        try:
            p_date_obj = datetime.strptime(payment_date, "%Y-%m-%d")
            next_date = (p_date_obj + timedelta(days=next_action_days)).strftime("%Y-%m-%d")
        except Exception:
            next_date = (datetime.now() + timedelta(days=next_action_days)).strftime("%Y-%m-%d")
            
        cursor.execute(
            "UPDATE clients SET deal_stage = 'paid_shipped', next_event_date = %s, next_event_type = 'call' WHERE id = %s",
            (next_date, client_id)
        )
        
        inv_text = f" Рахунок/ТТН: {invoice_no}." if invoice_no else ""
        current_dt = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_text = f"💰 [ОПЛАТА] Надійшло {amt_val:,.2f} EUR.{inv_text} Автоматично призначено контроль на {next_date}."
        
        cursor.execute(
            "INSERT INTO negotiations (client_id, date, result, author) VALUES (%s, %s, %s, %s)",
            (client_id, current_dt, log_text, 'Продажі')
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
    return redirect(url_for('client_detail', client_id=client_id))

@app.route('/detect_brands_ai/<int:client_id>', methods=['POST'])
@login_required
def detect_brands_ai(client_id):
    if not GEMINI_API_KEY:
        return jsonify({'success': False, 'message': 'Змінна GEMINI_API_KEY не налаштована!'})
        
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT id, name, website, brands, aftermarket_companies FROM clients WHERE id = %s", (client_id,))
    client = cursor.fetchone()
    
    if not client or not client['website']:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'message': 'У клієнта відсутній веб-сайт для аналізу!'})
        
    client_name = client['name']
    site_url = client['website'].strip()
    if not site_url.startswith('http'):
        site_url = "http://" + site_url

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }
    extracted_text = ""
    
    try:
        page_res = requests.get(site_url, headers=headers, timeout=8, verify=False)
        if page_res.status_code == 200:
            soup = BeautifulSoup(page_res.text, 'html.parser')
            alts = [img.get('alt', '') for img in soup.find_all('img') if img.get('alt')]
            titles = [a.get('title', '') for a in soup.find_all('a') if a.get('title')]
            meta_brand_text = " ".join(alts + titles)

            for element in soup(["script", "style", "svg", "noscript"]):
                element.extract()
                
            raw_page_text = soup.get_text(separator=' ', strip=True)
            extracted_text = f"{meta_brand_text} {raw_page_text}"[:40000]
    except Exception as e:
        print(f"Помилка завантаження HTML {site_url}: {e}")

    prompt = f"""
    Проаналізуй контент сайту '{client_name}' ({site_url}):

    --- КОНТЕНТ САЙТУ ---
    {extracted_text if extracted_text else "Текст сайту недоступний, використовуй знання про " + client_name}
    --- КІНЕЦЬ КОНТЕНТУ ---

    Поверни ВИКЛЮЧНО JSON:
    {{
        "brands": ["Vaderstad", "Gaspardo"],
        "aftermarket": ["Granit Parts", "Kramp"]
    }}
    """
    
    try:
        host = "generativelanguage.googleapis.com"
        model_path = "v1beta/models/gemini-2.5-flash:generateContent"
        url = "https://" + host + "/" + model_path + "?key=" + str(GEMINI_API_KEY).strip()
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0}
        }
        
        res = requests.post(url, json=payload, timeout=15)
        res_data = res.json()
        
        if 'error' in res_data:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'message': f"Помилка API: {res_data['error'].get('message')}"})
            
        raw_text = res_data['candidates'][0]['content']['parts'][0]['text']
        raw_text = raw_text.replace('```json', '').replace('```', '').strip()
        result_data = json.loads(raw_text)
        
        detected_brands = result_data.get('brands', [])
        detected_aftermarket = result_data.get('aftermarket', [])
        
        final_brands_str = ", ".join(sorted(detected_brands)) if detected_brands else "-"
        
        current_aftermarket = [a.strip() for a in (client['aftermarket_companies'] or '').split(',') if a.strip() and a.strip() != '-']
        final_aftermarket_set = set(current_aftermarket + detected_aftermarket)
        final_aftermarket_str = ", ".join(sorted(final_aftermarket_set)) if final_aftermarket_set else "-"
        
        cursor.execute("UPDATE clients SET brands = %s, aftermarket_companies = %s WHERE id = %s", 
                       (final_brands_str, final_aftermarket_str, client_id))
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'brands': detected_brands,
            'aftermarket': list(final_aftermarket_set),
            'detected_aftermarket': detected_aftermarket,
            'message': "Дані успішно оновлено через ШІ"
        })
        
    except Exception as e:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'message': f"Помилка аналізу: {str(e)}"})

@app.route('/add_lost_demand', methods=['POST'])
@login_required
def add_lost_demand():
    client_id = request.form.get('client_id')
    article = request.form.get('article', '').strip()
    title = request.form.get('title', '').strip()
    quantity = request.form.get('quantity', 1)
    status = request.form.get('status', 'lost')
    note = request.form.get('note', '').strip()
    
    try:
        qty_val = int(quantity)
    except Exception:
        qty_val = 1

    if article:
        conn = get_db_connection()
        cursor = conn.cursor()
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        cursor.execute(
            """INSERT INTO lost_demand (client_id, article, title, quantity, status, note, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (client_id if client_id else None, article, title, qty_val, status, note, created_at)
        )
        conn.commit()
        cursor.close()
        conn.close()

    if client_id:
        return redirect(url_for('client_detail', client_id=client_id))
    return redirect(url_for('index', tab='demand'))        

@app.route('/delete_lost_demand/<int:demand_id>', methods=['POST'])
@login_required
def delete_lost_demand(demand_id):
    client_id = request.form.get('client_id')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM lost_demand WHERE id = %s", (demand_id,))
    conn.commit()
    cursor.close()
    conn.close()
    
    if client_id:
        return redirect(url_for('client_detail', client_id=client_id))
    return redirect(url_for('index', tab='demand'))

@app.route('/send_client_email', methods=['POST'])
@login_required
def send_client_email():
    client_id = request.form.get('client_id')
    to_email = request.form.get('email', '').strip()
    subject = request.form.get('subject', '').strip()
    body_text = request.form.get('body', '').strip()
    
    if to_email and body_text:
        success = send_email_notification(to_email, subject, body_text)
        if success:
            current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn = get_db_connection()
            cursor = conn.cursor()
            
            log_text = f"Надіслано Email (Plonaris). Тема: \"{subject}\""
            
            cursor.execute(
                "INSERT INTO negotiations (client_id, date, result, author) VALUES (%s, %s, %s, %s)",
                (client_id, current_date, log_text, 'Продажі')
            )
            conn.commit()
            cursor.close()
            conn.close()
    return redirect(url_for('client_detail', client_id=client_id))

@app.route('/sync_emails', methods=['POST'])
@login_required
def sync_emails():
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        return jsonify({'success': False, 'message': 'Налаштування IMAP відсутні!'})
    try:
        mail = imaplib.IMAP4_SSL('mail.adm.tools', 993)
        mail.login(MAIL_USERNAME, MAIL_PASSWORD)
        mail.select("INBOX")
        
        status, response_data = mail.search(None, 'UNSEEN')
        email_ids = response_data[0].split()
        
        if not email_ids:
            mail.close()
            mail.logout()
            return jsonify({'success': True, 'message': 'Вхідна скринька перевірена. Нових листів немає.'})

        conn = get_db_connection()
        cursor = conn.cursor()
        imported_count = 0

        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            from_header = msg.get("From", "")
            from_email = email.utils.parseaddr(from_header)[1].lower().strip()
            
            subject_header = msg.get("Subject", "")
            subject_decoded = ""
            for part, encoding in decode_header(subject_header):
                if isinstance(part, bytes):
                    subject_decoded += part.decode(encoding or 'utf-8', errors='ignore')
                else:
                    subject_decoded += str(part)
            
            cursor.execute(
                "SELECT id FROM clients WHERE LOWER(email) = %s OR LOWER(email_2) = %s",
                (from_email, from_email)
            )
            client_row = cursor.fetchone()
            
            if client_row:
                client_id = client_row[0]
                email_body = decode_email_body(msg)
                
                final_history_text = f"[📩 Вхідний лист] Тема: \"{subject_decoded.strip()}\"\n\n{email_body}"
                current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                
                cursor.execute(
                    "INSERT INTO negotiations (client_id, date, result, author) VALUES (%s, %s, %s, %s)",
                    (client_id, current_date, final_history_text, 'Продажі')
                )
                mail.store(e_id, '+FLAGS', '\\Seen')
                imported_count += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        mail.close()
        mail.logout()
        
        return jsonify({'success': True, 'message': f'Синхронізація завершена! Імпортовано відповідей: {imported_count}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Помилка IMAP: {str(e)}'})

@app.route('/add_task', methods=['POST'])
@login_required
def add_task():
    text = request.form.get('text')
    deadline = request.form.get('deadline', '')
    author = request.form.get('author', 'Продажі')
    if text and deadline:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO tasks (text, deadline, author, status) VALUES (%s, %s, %s, 'in_progress')", (text, deadline, author))
        conn.commit()
        cursor.close()
        conn.close()
    return redirect(url_for('index', tab='tasks'))

@app.route('/edit_task/<int:task_id>', methods=['POST'])
@login_required
def edit_task(task_id):
    text = request.form.get('text', '').strip()
    deadline = request.form.get('deadline', '').strip()
    author = request.form.get('author', 'Продажі')
    if text and deadline:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET text=%s, deadline=%s, author=%s WHERE id=%s", (text, deadline, author, task_id))
        conn.commit()
        cursor.close()
        conn.close()
    return redirect(url_for('index', tab='tasks'))

@app.route('/toggle_task/<int:task_id>', methods=['POST'])
@login_required
def toggle_task(task_id):
    current_status = request.form.get('current_status')
    new_status = 'completed' if current_status == 'in_progress' else 'in_progress'
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET status=%s WHERE id=%s", (new_status, task_id))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('index', tab='tasks'))

@app.route('/delete_task/<int:task_id>', methods=['POST'])
@login_required
def delete_task(task_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE id=%s", (task_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('index', tab='tasks'))

@app.route('/add_client', methods=['POST'])
@login_required
def add_client():
    name = request.form.get('name')
    country = request.form.get('country', '')
    address = request.form.get('address', '')
    buyer_type = request.form.get('buyer_type', 'постачальник')
    interest_level = request.form.get('interest_level', 'не опрацьовано')
    deal_stage = request.form.get('deal_stage', 'none')
    website = request.form.get('website', '')
    next_event_date = request.form.get('next_event_date', '')
    next_event_type = request.form.get('next_event_type', '')
    mayer_reg = request.form.get('mayer_reg', 'Ні')
    whatsapp_1 = request.form.get('whatsapp_1', '')
    whatsapp_2 = request.form.get('whatsapp_2', '')
    aftermarket_companies = request.form.get('aftermarket_companies', '').strip()
    
    if interest_level != 'зацікавленість':
        deal_stage = 'none'

    selected_brands = request.form.getlist('brands')
    brands = ", ".join(selected_brands) if selected_brands else ""
    
    contact_person = request.form.get('contact_person', '')
    position = request.form.get('position', '')
    phone = request.form.get('phone', '')
    email = request.form.get('email', '')
    
    contact_person_2 = request.form.get('contact_person_2', '')
    position_2 = request.form.get('position_2', '')
    phone_2 = request.form.get('phone_2', '')
    email_2 = request.form.get('email_2', '')
    
    if name:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO clients (name, country, address, contact_person, position, phone, email, website, buyer_type, brands, 
                                   contact_person_2, position_2, phone_2, email_2, interest_level, next_event_date, next_event_type, mayer_reg, whatsapp_1, whatsapp_2, is_active, deal_stage, aftermarket_companies) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)""",
            (name, country, address, contact_person, position, phone, email, website, buyer_type, brands,
             contact_person_2, position_2, phone_2, email_2, interest_level, next_event_date, next_event_type, mayer_reg, whatsapp_1, whatsapp_2, deal_stage, aftermarket_companies)
        )
        conn.commit()
        cursor.close()
        conn.close()
    return redirect(url_for('index'))

@app.route('/edit_client/<int:client_id>', methods=['POST'])
@login_required
def edit_client(client_id):
    name = request.form.get('name')
    country = request.form.get('country', '')
    address = request.form.get('address', '')
    buyer_type = request.form.get('buyer_type', 'постачальник')
    interest_level = request.form.get('interest_level', 'не опрацьовано')
    deal_stage = request.form.get('deal_stage', 'none')
    website = request.form.get('website', '')
    next_event_date = request.form.get('next_event_date', '')
    next_event_type = request.form.get('next_event_type', '')
    mayer_reg = request.form.get('mayer_reg', 'Ні')
    whatsapp_1 = request.form.get('whatsapp_1', '')
    whatsapp_2 = request.form.get('whatsapp_2', '')
    aftermarket_companies = request.form.get('aftermarket_companies', '').strip()
    
    if interest_level != 'зацікавленість':
        deal_stage = 'none'
        
    selected_brands = request.form.getlist('brands')
    brands = ", ".join(selected_brands) if selected_brands else ""
    
    contact_person = request.form.get('contact_person', '')
    position = request.form.get('position', '')
    phone = request.form.get('phone', '')
    email = request.form.get('email', '')
    
    contact_person_2 = request.form.get('contact_person_2', '')
    position_2 = request.form.get('position_2', '')
    phone_2 = request.form.get('phone_2', '')
    email_2 = request.form.get('email_2', '')
    
    if name:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE clients SET name=%s, country=%s, address=%s, contact_person=%s, position=%s, phone=%s, email=%s, 
                                  website=%s, buyer_type=%s, brands=%s, contact_person_2=%s, position_2=%s, 
                                  phone_2=%s, email_2=%s, interest_level=%s, next_event_date=%s, next_event_type=%s, mayer_reg=%s, whatsapp_1=%s, whatsapp_2=%s, deal_stage=%s, aftermarket_companies=%s WHERE id=%s""",
            (name, country, address, contact_person, position, phone, email, website, buyer_type, brands,
             contact_person_2, position_2, phone_2, email_2, interest_level, next_event_date, next_event_type, mayer_reg, whatsapp_1, whatsapp_2, deal_stage, aftermarket_companies, client_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
    return redirect(url_for('client_detail', client_id=client_id))

@app.route('/delete_client/<int:client_id>', methods=['POST'])
@login_required
def delete_client(client_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM clients WHERE id = %s", (client_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('index'))

@app.route('/add_finance_plan', methods=['POST'])
@login_required
def add_finance_plan():
    client_id = request.form.get('client_id')
    planned_amount = request.form.get('planned_amount', 0)
    month_name = request.form.get('month_name', '')
    actual_amount = request.form.get('actual_amount', 0)
    payment_date = request.form.get('payment_date', '')
    
    try:
        p_amt = float(planned_amount) if planned_amount else 0.0
    except Exception:
        p_amt = 0.0

    try:
        a_amt = float(actual_amount) if actual_amount else 0.0
    except Exception:
        a_amt = 0.0

    if client_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, planned_amount, actual_amount FROM sales_plans WHERE client_id = %s ORDER BY id DESC LIMIT 1",
            (client_id,)
        )
        existing = cursor.fetchone()
        
        if existing:
            plan_id = existing[0]
            cur_fact = float(existing[2] or 0)
            new_fact = cur_fact + a_amt if a_amt > 0 else cur_fact
            cursor.execute(
                "UPDATE sales_plans SET planned_amount = %s, month_name = %s, actual_amount = %s, payment_date = COALESCE(NULLIF(%s, ''), payment_date) WHERE id = %s",
                (p_amt, month_name, new_fact, payment_date, plan_id)
            )
        else:
            cursor.execute(
                "INSERT INTO sales_plans (client_id, planned_amount, month_name, actual_amount, payment_date) VALUES (%s, %s, %s, %s, %s)",
                (client_id, p_amt, month_name, a_amt, payment_date if payment_date else None)
            )
            
        conn.commit()
        cursor.close()
        conn.close()
    return redirect(url_for('index', tab='finance', finance_month=month_name))

@app.route('/edit_finance_plan/<int:plan_id>', methods=['POST'])
@login_required
def edit_finance_plan(plan_id):
    planned_amount = request.form.get('planned_amount', 0)
    month_name = request.form.get('month_name', '')
    actual_amount = request.form.get('actual_amount', 0)
    payment_date = request.form.get('payment_date', '')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE sales_plans SET planned_amount=%s, month_name=%s, actual_amount=%s, payment_date=%s WHERE id=%s", (planned_amount if planned_amount else 0, month_name, actual_amount if actual_amount else 0, payment_date, plan_id))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('index', tab='finance', finance_month=month_name))

@app.route('/delete_finance_plan/<int:plan_id>', methods=['POST'])
@login_required
def delete_finance_plan(plan_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sales_plans WHERE id=%s", (plan_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('index', tab='finance'))

@app.route('/client/<int:client_id>', methods=['GET', 'POST'])
@login_required
def client_detail(client_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    if request.method == 'POST':
        result_text = request.form.get('result')
        author = request.form.get('author', 'Продажі') 
        contact_type = request.form.get('contact_type', 'call')
        
        type_tags = {
            'call': '[📞 Дзвінок] ',
            'visit': '[🚗 Візит] ',
            'email': '[✉️ Лист] '
        }
        prefix = type_tags.get(contact_type, '')
        
        if result_text:
            final_text = f"{prefix}{result_text}"
            current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            cursor.execute(
                "INSERT INTO negotiations (client_id, date, result, author) VALUES (%s, %s, %s, %s)",
                (client_id, current_date, final_text, author)
            )
            conn.commit()
        return redirect(url_for('client_detail', client_id=client_id))
        
    cursor.execute("SELECT * FROM clients WHERE id = %s", (client_id,))
    raw_client = cursor.fetchone()
    
    client = dict(raw_client) if raw_client else {}
    fields_to_check = ['buyer_type', 'brands', 'website', 'country', 'address', 
                       'contact_person', 'position', 'phone', 'email', 
                       'contact_person_2', 'position_2', 'phone_2', 'email_2', 
                       'interest_level', 'next_event_date', 'next_event_type', 'mayer_reg', 'whatsapp_1', 'whatsapp_2',
                       'is_active', 'deactivation_reason', 'deal_stage', 'aftermarket_companies']
    for field in fields_to_check:
        if field not in client or client[field] is None:
            if field == 'interest_level':
                client[field] = 'не опрацьовано'
            elif field == 'deal_stage':
                client[field] = 'none'
            elif field == 'mayer_reg':
                client[field] = 'Ні'
            elif field == 'is_active':
                client[field] = True
            elif field == 'buyer_type':
                client[field] = 'постачальник'
            else:
                client[field] = ''
    
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(actual_amount), 0) FROM sales_plans WHERE client_id = %s AND actual_amount > 0", (client_id,))
    deal_stats_row = cursor.fetchone()
    deal_count = deal_stats_row[0] if deal_stats_row else 0
    deal_total_sum = float(deal_stats_row[1]) if deal_stats_row else 0.0

    cursor.execute("SELECT * FROM negotiations WHERE client_id = %s ORDER BY id DESC", (client_id,))
    history = cursor.fetchall()

    cursor.execute("SELECT * FROM lost_demand WHERE client_id = %s ORDER BY id DESC", (client_id,))
    client_lost_demand = cursor.fetchall()

    cursor.execute("""
        SELECT next_event_date, COUNT(*) 
        FROM clients 
        WHERE next_event_date IS NOT NULL AND next_event_date != '' AND is_active IS NOT FALSE
        GROUP BY next_event_date
    """)
    events_by_date = dict(cursor.fetchall())
    json_events_by_date = json.dumps(events_by_date, ensure_ascii=False)

    cursor.close()
    conn.close()
    return render_template(
        'client.html', 
        client=client, 
        history=history, 
        client_lost_demand=client_lost_demand, 
        json_events_by_date=json_events_by_date,
        deal_count=deal_count,
        deal_total_sum=deal_total_sum
    )

@app.route('/edit_negotiation/<int:neg_id>', methods=['POST'])
@login_required
def edit_negotiation(neg_id):
    client_id = request.form.get('client_id')
    result_text = request.form.get('result')
    if result_text:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE negotiations SET result = %s WHERE id = %s", (result_text, neg_id))
        conn.commit()
        cursor.close()
        conn.close()
    return redirect(url_for('client_detail', client_id=client_id))

@app.route('/delete_negotiation/<int:neg_id>', methods=['POST'])
@login_required
def delete_negotiation(neg_id):
    client_id = request.form.get('client_id')
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM negotiations WHERE id = %s", (neg_id,))
        conn.commit()
    except Exception as e:
        print(f"❌ Помилка видалення: {str(e)}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for('client_detail', client_id=client_id))

@app.route('/export_excel')
@login_required
def export_excel():
    conn = get_db_connection()
    query = """
        SELECT c.name AS "Назва компанії", c.interest_level AS "Зацікавленість", 
               CASE 
                   WHEN c.deal_stage = 'request' THEN '1. Запит / Підбір'
                   WHEN c.deal_stage = 'offer_sent' THEN '2. Рахунок (КП) надіслано'
                   WHEN c.deal_stage = 'paid_shipped' THEN '3. Оплачено / Відвантажено'
                   WHEN c.deal_stage = 'regular' THEN '4. Постійний партнер'
                   ELSE 'Немає активної угоди'
               END AS "Етап угоди",
               c.buyer_type AS "Тип партнера", c.brands AS "Бренди",
               c.aftermarket_companies AS "Aftermarket оператори",
               c.website AS "Веб-сайт", c.country AS "Країна", c.address AS "Адреса",
               c.contact_person AS "Контактна особа 1", c.position AS "Посада 1", c.phone AS "Телефон 1", c.whatsapp_1 AS "WhatsApp 1", c.email AS "Email 1",
               c.contact_person_2 AS "Контактна особа 2", c.position_2 AS "Посада 2", c.phone_2 AS "Телефон 2", c.whatsapp_2 AS "WhatsApp 2", c.email_2 AS "Email 2",
               c.next_event_date AS "Дата наступної події", c.next_event_type AS "Вид наступної події",
               CASE WHEN c.is_active IS FALSE THEN 'Деактивовано (Архів)' ELSE 'Активний' END AS "Статус",
               c.deactivation_reason AS "Причина деактивації"
        FROM clients c ORDER BY c.name ASC
    """
    df = pd.read_sql(query, conn)
    conn.close()
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Партнери Plonaris')
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Plonaris_Partners_{datetime.now().strftime("%Y-%m-%d")}.xlsx'
    )

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
