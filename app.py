# ============================================================
# 1. ПОДКЛЮЧАЕМ БИБЛИОТЕКИ
# ============================================================
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
import bcrypt
import logging
import json
import os

# ============================================================
# 2. СОЗДАЁМ ПРИЛОЖЕНИЕ
# ============================================================
app = Flask(__name__)
app.secret_key = 'x7K9mN2pQ5wR8vY3uL6jH1tE4aB0cF'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ============================================================
# 3. НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================
if not os.path.exists('logs'):
    os.makedirs('logs')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/app.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def log_action(user_id, action, details=None):
    user = User.query.get(user_id) if user_id else None
    log_entry = {
        'user': user.login if user else 'anonymous',
        'user_id': user_id,
        'action': action,
        'time': datetime.now().isoformat(),
        'details': details or {}
    }
    logging.info(json.dumps(log_entry, ensure_ascii=False))

# ============================================================
# 4. ПОДКЛЮЧАЕМ БАЗУ ДАННЫХ И СИСТЕМУ ВХОДА
# ============================================================
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ============================================================
# 4.1 ПРОВЕРКА СУЩЕСТВОВАНИЯ ПОЛЬЗОВАТЕЛЯ
# ============================================================
@app.before_request
def check_user_exists():
    if current_user.is_authenticated:
        user = User.query.get(current_user.id)
        if not user:
            logout_user()
            session.clear()

# ============================================================
# 5. МОДЕЛИ БАЗЫ ДАННЫХ
# ============================================================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='abonent')
    full_name = db.Column(db.String(100))
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=True)
    account = db.relationship('Account', back_populates='owner', foreign_keys=[account_id])

class Account(db.Model):
    __tablename__ = 'accounts'
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), unique=True, nullable=False)
    address = db.Column(db.String(200))
    owner = db.relationship('User', back_populates='account', foreign_keys=[User.account_id])
    meters = db.relationship('Meter', back_populates='account')
    readings = db.relationship('Reading', back_populates='account')
    payments = db.relationship('Payment', back_populates='account')

class ServiceType(db.Model):
    __tablename__ = 'service_types'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True)
    unit = db.Column(db.String(20))
    is_zoned = db.Column(db.Boolean, default=False)

class Meter(db.Model):
    __tablename__ = 'meters'
    id = db.Column(db.Integer, primary_key=True)
    serial_number = db.Column(db.String(50), unique=True)
    service_type_id = db.Column(db.Integer, db.ForeignKey('service_types.id'))
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'))
    is_active = db.Column(db.Boolean, default=True)
    account = db.relationship('Account', back_populates='meters')
    service_type = db.relationship('ServiceType')
    readings = db.relationship('Reading', back_populates='meter')

class Reading(db.Model):
    __tablename__ = 'readings'
    id = db.Column(db.Integer, primary_key=True)
    meter_id = db.Column(db.Integer, db.ForeignKey('meters.id'))
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'))  # ← ВАЖНО!
    period = db.Column(db.String(7))
    value = db.Column(db.Float, nullable=True)
    value_day = db.Column(db.Float, nullable=True)
    value_night = db.Column(db.Float, nullable=True)
    consumption = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    meter = db.relationship('Meter', back_populates='readings')
    account = db.relationship('Account', back_populates='readings')

class Tariff(db.Model):
    __tablename__ = 'tariffs'
    id = db.Column(db.Integer, primary_key=True)
    service_type_id = db.Column(db.Integer, db.ForeignKey('service_types.id'))
    zone = db.Column(db.String(10), nullable=True)
    rate = db.Column(db.Float)
    valid_from = db.Column(db.DateTime)
    valid_to = db.Column(db.DateTime, nullable=True)
    service_type = db.relationship('ServiceType')

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'))
    period = db.Column(db.String(7))
    amount = db.Column(db.Float)
    paid_at = db.Column(db.DateTime, default=datetime.now)
    comment = db.Column(db.String(200), nullable=True)
    account = db.relationship('Account', back_populates='payments')

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except:
        return None

# ============================================================
# 6. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def calculate_balance(account_id):
    from sqlalchemy import func
    total_charged = 0.0
    readings = Reading.query.filter_by(account_id=account_id).all()
    for reading in readings:
        meter = Meter.query.get(reading.meter_id)
        if not meter:
            continue
        tariff = Tariff.query.filter(
            Tariff.service_type_id == meter.service_type_id,
            Tariff.valid_from <= datetime.now(),
            (Tariff.valid_to.is_(None) | (Tariff.valid_to >= datetime.now()))
        ).first()
        if tariff and reading.consumption and reading.consumption > 0:
            total_charged += reading.consumption * tariff.rate
    total_paid = db.session.query(func.sum(Payment.amount)).filter_by(account_id=account_id).scalar() or 0.0
    return round(total_charged - total_paid, 2)

def get_tariff_rate(service_type_id, zone=None, date=None):
    if date is None:
        date = datetime.now()
    query = Tariff.query.filter(
        Tariff.service_type_id == service_type_id,
        Tariff.valid_from <= date,
        (Tariff.valid_to.is_(None) | (Tariff.valid_to >= date))
    )
    if zone:
        query = query.filter(Tariff.zone == zone)
    else:
        query = query.filter(Tariff.zone.is_(None))
    tariff = query.first()
    return tariff.rate if tariff else 0

# ============================================================
# 7. МАРШРУТЫ
# ============================================================

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form['login']
        password = request.form['password']
        user = User.query.filter_by(login=login_input).first()
        if user and check_password(password, user.password_hash):
            login_user(user)
            log_action(user.id, 'login', {'success': True})
            return redirect(url_for('dashboard'))
        log_action(None, 'login_failed', {'login': login_input})
        flash('Неверный логин или пароль')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    log_action(current_user.id, 'logout')
    logout_user()
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'admin':
        return redirect(url_for('admin_panel'))
    return render_template('dashboard.html', user=current_user)

# ============================================================
# 7.1 ПЕРЕДАЧА ПОКАЗАНИЙ (ИСПРАВЛЕННАЯ)
# ============================================================

@app.route('/readings', methods=['GET', 'POST'])
@login_required
def readings():
    if request.method == 'POST':
        try:
            meter_id = request.form.get('meter_id')
            period = request.form.get('period')
            value = request.form.get('value', '').strip()
            value_day = request.form.get('value_day', '').strip()
            value_night = request.form.get('value_night', '').strip()
            
            if not meter_id:
                flash('❌ Выберите счётчик!')
                return redirect(url_for('readings'))
            
            meter = Meter.query.get(meter_id)
            if not meter:
                flash('❌ Счётчик не найден!')
                return redirect(url_for('readings'))
            
            if current_user.role == 'abonent' and meter.account_id != current_user.account_id:
                flash('❌ Нет доступа к этому счётчику!')
                return redirect(url_for('readings'))
            
            existing = Reading.query.filter_by(meter_id=meter_id, period=period).first()
            if existing:
                flash(f'⚠️ Показания за {period} уже переданы!')
                return redirect(url_for('readings'))
            
            prev = Reading.query.filter(
                Reading.meter_id == meter_id,
                Reading.period < period
            ).order_by(Reading.period.desc()).first()
            
            # ===== ВАЖНО: ОПРЕДЕЛЯЕМ account_id =====
            if current_user.role == 'abonent':
                account_id = current_user.account_id
            else:
                account_id = meter.account_id
            
            # ===== ОБЫЧНЫЙ СЧЁТЧИК =====
            if not meter.service_type.is_zoned:
                if not value:
                    flash('❌ Введите показание!')
                    return redirect(url_for('readings'))
                
                try:
                    value_float = float(value.replace(',', '.'))
                except ValueError:
                    flash('❌ Показание должно быть числом!')
                    return redirect(url_for('readings'))
                
                if prev and prev.value is not None and value_float < prev.value:
                    flash(f'❌ Новое показание ({value_float}) меньше предыдущего ({prev.value})!')
                    return redirect(url_for('readings'))
                
                prev_value = prev.value if prev and prev.value is not None else 0
                consumption = value_float - prev_value
                if consumption < 0:
                    consumption = 0
                
                reading = Reading(
                    meter_id=meter_id,
                    account_id=account_id,  # ← ВАЖНО!
                    period=period,
                    value=value_float,
                    consumption=consumption
                )
                db.session.add(reading)
                db.session.commit()
                
                log_action(current_user.id, 'submit_reading', {
                    'meter_id': meter_id,
                    'period': period,
                    'consumption': consumption
                })
                
                flash(f'✅ Показания переданы! Потребление: {consumption} {meter.service_type.unit}')
                return redirect(url_for('readings'))
            
            # ===== ЗОННЫЙ СЧЁТЧИК =====
            else:
                if not value_day or not value_night:
                    flash('❌ Введите показания для дня и ночи!')
                    return redirect(url_for('readings'))
                
                try:
                    day = float(value_day.replace(',', '.'))
                    night = float(value_night.replace(',', '.'))
                except ValueError:
                    flash('❌ Показания должны быть числами!')
                    return redirect(url_for('readings'))
                
                if prev:
                    if prev.value_day is not None and day < prev.value_day:
                        flash(f'❌ Дневное показание ({day}) меньше предыдущего ({prev.value_day})!')
                        return redirect(url_for('readings'))
                    if prev.value_night is not None and night < prev.value_night:
                        flash(f'❌ Ночное показание ({night}) меньше предыдущего ({prev.value_night})!')
                        return redirect(url_for('readings'))
                
                prev_day = prev.value_day if prev and prev.value_day is not None else 0
                prev_night = prev.value_night if prev and prev.value_night is not None else 0
                
                consumption_day = day - prev_day
                consumption_night = night - prev_night
                consumption = consumption_day + consumption_night
                
                if consumption < 0:
                    consumption = 0
                
                reading = Reading(
                    meter_id=meter_id,
                    account_id=account_id,  # ← ВАЖНО!
                    period=period,
                    value_day=day,
                    value_night=night,
                    consumption=consumption
                )
                db.session.add(reading)
                db.session.commit()
                
                log_action(current_user.id, 'submit_reading_zoned', {
                    'meter_id': meter_id,
                    'period': period,
                    'consumption_day': consumption_day,
                    'consumption_night': consumption_night
                })
                
                flash(f'✅ Показания переданы! День: {consumption_day} кВт·ч, Ночь: {consumption_night} кВт·ч')
                return redirect(url_for('readings'))
                
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Ошибка: {str(e)}')
            log_action(current_user.id, 'reading_error', {'error': str(e)})
            return redirect(url_for('readings'))
    
    # GET — показываем форму
    if current_user.role == 'abonent':
        account = Account.query.filter_by(id=current_user.account_id).first()
        if account:
            meters = Meter.query.filter_by(account_id=account.id).all()
        else:
            meters = []
    else:
        meters = Meter.query.all()
    
    return render_template('readings.html', meters=meters, user=current_user)

# ============================================================
# 7.2 ИСТОРИЯ ПОКАЗАНИЙ
# ============================================================

@app.route('/readings/history')
@login_required
def readings_history():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    if current_user.role == 'abonent':
        query = Reading.query.filter_by(account_id=current_user.account_id)
    else:
        account_id = request.args.get('account_id', type=int)
        if account_id:
            query = Reading.query.filter_by(account_id=account_id)
        else:
            query = Reading.query
    
    query = query.order_by(Reading.period.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    readings_data = []
    for r in pagination.items:
        meter = Meter.query.get(r.meter_id)
        readings_data.append({
            'id': r.id,
            'meter_name': meter.service_type.name if meter and meter.service_type else 'Неизвестно',
            'period': r.period,
            'value': r.value,
            'value_day': r.value_day,
            'value_night': r.value_night,
            'consumption': r.consumption,
            'created_at': r.created_at.strftime('%d.%m.%Y %H:%M') if r.created_at else ''
        })
    
    log_action(current_user.id, 'view_history', {'page': page})
    
    return render_template('history.html', 
                         readings=readings_data,
                         pagination=pagination,
                         page=page,
                         total_pages=pagination.pages,
                         has_prev=pagination.has_prev,
                         has_next=pagination.has_next,
                         prev_num=pagination.prev_num,
                         next_num=pagination.next_num,
                         user=current_user)

# ============================================================
# 7.3 КВИТАНЦИЯ
# ============================================================

@app.route('/receipt')
@login_required
def receipt():
    """Квитанция с выбором периода"""
    
    start_period = request.args.get('start_period')
    end_period = request.args.get('end_period')
    account_id = request.args.get('account_id', type=int)
    
    if current_user.role == 'abonent':
        account_id = current_user.account_id
    else:
        if not account_id and current_user.account_id:
            account_id = current_user.account_id
    
    if not account_id:
        flash('Лицевой счёт не найден')
        return redirect(url_for('dashboard'))
    
    # Если периоды не указаны — показываем форму выбора
    if not start_period or not end_period:
        available_periods = db.session.query(Reading.period).filter_by(
            account_id=account_id
        ).distinct().order_by(Reading.period).all()
        available_periods = [p[0] for p in available_periods if p[0]]
        
        return render_template('receipt_select.html', 
                             periods=available_periods,
                             user=current_user,
                             account_id=account_id)
    
    account = Account.query.get(account_id)
    if not account:
        flash('Лицевой счёт не найден')
        return redirect(url_for('dashboard'))
    
    readings = Reading.query.filter(
        Reading.account_id == account_id,
        Reading.period >= start_period,
        Reading.period <= end_period
    ).order_by(Reading.period).all()
    
    if not readings:
        flash(f'Нет показаний за период с {start_period} по {end_period}')
        return render_template('receipt_select.html', 
                             periods=[],
                             user=current_user,
                             account_id=account_id)
    
    items = []
    total = 0.0
    
    readings_by_meter = {}
    for r in readings:
        if r.meter_id not in readings_by_meter:
            readings_by_meter[r.meter_id] = []
        readings_by_meter[r.meter_id].append(r)
    
    for meter_id, meter_readings in readings_by_meter.items():
        meter = Meter.query.get(meter_id)
        if not meter:
            continue
        
        meter_readings.sort(key=lambda x: x.period)
        
        prev = Reading.query.filter(
            Reading.meter_id == meter.id,
            Reading.period < start_period
        ).order_by(Reading.period.desc()).first()
        
        if meter.service_type.is_zoned:
            rate_day = get_tariff_rate(meter.service_type_id, zone='day')
            rate_night = get_tariff_rate(meter.service_type_id, zone='night')
            
            total_day = 0
            total_night = 0
            
            for i, r in enumerate(meter_readings):
                prev_reading = prev if i == 0 else meter_readings[i-1]
                
                if r.value_day is not None and prev_reading and prev_reading.value_day is not None:
                    cons = r.value_day - prev_reading.value_day
                    if cons > 0:
                        total_day += cons
                
                if r.value_night is not None and prev_reading and prev_reading.value_night is not None:
                    cons = r.value_night - prev_reading.value_night
                    if cons > 0:
                        total_night += cons
            
            if total_day > 0:
                amount = round(total_day * rate_day, 2)
                total += amount
                items.append({
                    'service': f'{meter.service_type.name} (день)',
                    'consumption': round(total_day, 2),
                    'unit': meter.service_type.unit,
                    'rate': rate_day,
                    'amount': amount
                })
            
            if total_night > 0:
                amount = round(total_night * rate_night, 2)
                total += amount
                items.append({
                    'service': f'{meter.service_type.name} (ночь)',
                    'consumption': round(total_night, 2),
                    'unit': meter.service_type.unit,
                    'rate': rate_night,
                    'amount': amount
                })
        else:
            rate = get_tariff_rate(meter.service_type_id)
            total_consumption = 0
            
            for i, r in enumerate(meter_readings):
                prev_reading = prev if i == 0 else meter_readings[i-1]
                
                if r.value is not None and prev_reading and prev_reading.value is not None:
                    cons = r.value - prev_reading.value
                    if cons > 0:
                        total_consumption += cons
            
            if total_consumption > 0:
                amount = round(total_consumption * rate, 2)
                total += amount
                items.append({
                    'service': meter.service_type.name,
                    'consumption': round(total_consumption, 2),
                    'unit': meter.service_type.unit,
                    'rate': rate,
                    'amount': amount
                })
    
    if not items:
        flash(f'Нет начислений за период с {start_period} по {end_period}')
        return render_template('receipt_select.html', 
                             periods=[],
                             user=current_user,
                             account_id=account_id)
    
    balance = calculate_balance(account_id)
    
    return render_template('receipt.html', 
                         items=items,
                         total=round(total, 2),
                         start_period=start_period,
                         end_period=end_period,
                         balance=balance,
                         account_number=account.number,
                         user=current_user)


@app.route('/receipt/<period>')
@login_required
def receipt_by_period(period):
    """Квитанция за конкретный месяц (обратная совместимость)"""
    return redirect(url_for('receipt', start_period=period, end_period=period))

# ============================================================
# 7.4 ОПЛАТЫ
# ============================================================

@app.route('/api/payment/register', methods=['POST'])
@login_required
def register_payment():
    try:
        data = request.get_json()
        period = data.get('period')
        amount = data.get('amount')
        account_id = data.get('account_id')
        comment = data.get('comment', '')
        
        if not period or not amount:
            return jsonify({'status': 'error', 'message': 'Не указан период или сумма'}), 400
        
        try:
            amount = float(amount)
            if amount <= 0:
                return jsonify({'status': 'error', 'message': 'Сумма должна быть больше 0'}), 400
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Некорректная сумма'}), 400
        
        if current_user.role == 'abonent':
            account_id = current_user.account_id
        else:
            if not account_id:
                return jsonify({'status': 'error', 'message': 'Не указан лицевой счёт'}), 400
        
        account = Account.query.get(account_id)
        if not account:
            return jsonify({'status': 'error', 'message': 'Лицевой счёт не найден'}), 404
        
        payment = Payment(
            account_id=account_id,
            period=period,
            amount=round(amount, 2),
            paid_at=datetime.now(),
            comment=comment
        )
        db.session.add(payment)
        db.session.commit()
        
        balance = calculate_balance(account_id)
        
        log_action(current_user.id, 'register_payment', {
            'account_id': account_id,
            'period': period,
            'amount': amount,
            'comment': comment
        })
        
        return jsonify({
            'status': 'ok',
            'message': f'✅ Оплата {amount} ₽ за {period} зарегистрирована!',
            'balance': balance,
            'account_number': account.number
        })
        
    except Exception as e:
        db.session.rollback()
        log_action(current_user.id, 'payment_error', {'error': str(e)})
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/payment/history')
@login_required
def payment_history():
    try:
        period = request.args.get('period')
        account_id = request.args.get('account_id', type=int)
        
        if current_user.role == 'abonent':
            query = Payment.query.filter_by(account_id=current_user.account_id)
        else:
            query = Payment.query
            if account_id:
                query = query.filter_by(account_id=account_id)
        
        if period:
            query = query.filter_by(period=period)
        
        payments = query.order_by(Payment.paid_at.desc()).all()
        
        result = []
        for p in payments:
            account = Account.query.get(p.account_id)
            result.append({
                'id': p.id,
                'account_number': account.number if account else 'N/A',
                'period': p.period,
                'amount': p.amount,
                'comment': getattr(p, 'comment', ''),
                'paid_at': p.paid_at.strftime('%d.%m.%Y %H:%M') if p.paid_at else ''
            })
        
        total_paid = sum(p['amount'] for p in result)
        
        return jsonify({
            'status': 'ok',
            'payments': result,
            'total': total_paid,
            'count': len(result)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================================
# 7.5 АДМИН-ПАНЕЛЬ
# ============================================================

@app.route('/admin')
@login_required
def admin_panel():
    if current_user.role != 'admin':
        flash('Доступ запрещён')
        return redirect(url_for('dashboard'))
    
    accounts = Account.query.all()
    debtors = []
    
    for acc in accounts:
        owner = User.query.filter_by(account_id=acc.id, role='abonent').first()
        balance = calculate_balance(acc.id)
        
        if balance > 0 and owner:
            debtors.append({
                'number': acc.number,
                'balance': balance,
                'owner': owner.full_name if owner else 'Неизвестно'
            })
    
    debtors.sort(key=lambda x: x['balance'], reverse=True)
    
    stats = {
        'accounts': Account.query.count(),
        'meters': Meter.query.count(),
        'readings': Reading.query.count(),
        'debtors': len(debtors)
    }
    
    log_action(current_user.id, 'admin_panel')
    
    return render_template('admin.html', 
                         debtors=debtors[:10],
                         stats=stats,
                         user=current_user)

# ============================================================
# 7.6 ЛОГИ
# ============================================================

@app.route('/admin/logs')
@login_required
def admin_logs():
    if current_user.role != 'admin':
        flash('Доступ запрещён')
        return redirect(url_for('dashboard'))
    
    logs = []
    try:
        with open('logs/app.log', 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines[-100:]:
                logs.append(line.strip())
    except FileNotFoundError:
        logs = ['Лог-файл пока не создан']
    
    return render_template('admin_logs.html', logs=logs, user=current_user)

# ============================================================
# 7.7 УПРАВЛЕНИЕ ДАННЫМИ
# ============================================================

@app.route('/admin/clear_data')
@login_required
def clear_data():
    if current_user.role != 'admin':
        flash('Доступ запрещён')
        return redirect(url_for('dashboard'))
    
    try:
        Reading.query.delete()
        Payment.query.delete()
        db.session.commit()
        log_action(current_user.id, 'clear_data')
        flash('✅ Все показания и оплаты удалены!')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Ошибка: {str(e)}')
    
    return redirect(url_for('admin_panel'))

@app.route('/admin/clean_state')
@login_required
def clean_state():
    if current_user.role != 'admin':
        flash('Доступ запрещён')
        return redirect(url_for('dashboard'))
    
    try:
        Reading.query.delete()
        Payment.query.delete()
        Meter.query.delete()
        Account.query.delete()
        db.session.commit()
        
        abonents = User.query.filter_by(role='abonent').all()
        
        for i, user in enumerate(abonents):
            account = Account(
                number=f'{40500 + i}',
                address=f'ул. Новая, д.{i+1}'
            )
            db.session.add(account)
            db.session.flush()
            user.account_id = account.id
        
        db.session.flush()
        
        elec = ServiceType.query.filter_by(name='Электроэнергия').first()
        water = ServiceType.query.filter_by(name='Вода холодная').first()
        gas = ServiceType.query.filter_by(name='Газ').first()
        heat = ServiceType.query.filter_by(name='Тепло').first()
        
        for user in abonents:
            account = user.account
            if account:
                if elec:
                    db.session.add(Meter(
                        serial_number=f'EL-{account.number}-01',
                        service_type_id=elec.id,
                        account_id=account.id
                    ))
                if water:
                    db.session.add(Meter(
                        serial_number=f'WT-{account.number}-01',
                        service_type_id=water.id,
                        account_id=account.id
                    ))
                if gas:
                    db.session.add(Meter(
                        serial_number=f'GS-{account.number}-01',
                        service_type_id=gas.id,
                        account_id=account.id
                    ))
                if heat:
                    db.session.add(Meter(
                        serial_number=f'HT-{account.number}-01',
                        service_type_id=heat.id,
                        account_id=account.id
                    ))
        
        db.session.flush()
        
        if not Tariff.query.first():
            tariffs = [
                Tariff(service_type_id=elec.id, zone='day', rate=6.20, valid_from=datetime(2025, 1, 1)),
                Tariff(service_type_id=elec.id, zone='night', rate=2.40, valid_from=datetime(2025, 1, 1)),
                Tariff(service_type_id=water.id, rate=42.30, valid_from=datetime(2025, 1, 1)),
                Tariff(service_type_id=gas.id, rate=7.80, valid_from=datetime(2025, 1, 1)),
                Tariff(service_type_id=heat.id, rate=2500.00, valid_from=datetime(2025, 1, 1)),
            ]
            db.session.add_all(tariffs)
        
        db.session.commit()
        
        log_action(current_user.id, 'clean_state_full')
        flash('✅ Все данные очищены! Созданы новые лицевые счета и счётчики.')
        
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Ошибка: {str(e)}')
    
    return redirect(url_for('admin_panel'))

@app.route('/admin/reset_all')
@login_required
def reset_all_data():
    if current_user.role != 'admin':
        flash('Доступ запрещён')
        return redirect(url_for('dashboard'))
    
    try:
        Payment.query.delete()
        Reading.query.delete()
        Meter.query.delete()
        Account.query.delete()
        User.query.delete()
        db.session.commit()
        
        logout_user()
        session.clear()
        
        flash('✅ ВСЕ данные удалены! Перезапустите приложение: python app.py')
        return redirect(url_for('login'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Ошибка: {str(e)}')
        return redirect(url_for('admin_panel'))

# ============================================================
# 7.8 API
# ============================================================

@app.route('/api/meters')
@login_required
def api_meters():
    if current_user.role == 'abonent':
        meters = Meter.query.filter_by(account_id=current_user.account_id).all()
    else:
        meters = Meter.query.all()
    
    result = []
    for m in meters:
        service = ServiceType.query.get(m.service_type_id)
        result.append({
            'id': m.id,
            'serial_number': m.serial_number,
            'service_name': service.name if service else 'Неизвестно',
            'is_zoned': service.is_zoned if service else False,
            'unit': service.unit if service else ''
        })
    return jsonify(result)

@app.route('/api/readings/history')
@login_required
def api_readings_history():
    if current_user.role == 'abonent':
        readings = Reading.query.filter_by(account_id=current_user.account_id).order_by(Reading.period).all()
    else:
        account_id = request.args.get('account_id', type=int)
        if account_id:
            readings = Reading.query.filter_by(account_id=account_id).order_by(Reading.period).all()
        else:
            readings = []
    
    data = {}
    for r in readings:
        if r.period not in data:
            data[r.period] = 0
        data[r.period] += r.consumption or 0
    
    return jsonify([{'period': k, 'consumption': round(v, 2)} for k, v in sorted(data.items())])

# ============================================================
# 7.9 РЕГИСТРАЦИЯ
# ============================================================

@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('❌ Доступ запрещён. Только администратор может создавать пользователей.')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        login = request.form.get('login', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role', 'abonent')
        account_number = request.form.get('account_number', '').strip()
        address = request.form.get('address', '').strip()
        
        if not login or len(login) < 3:
            flash('❌ Логин должен быть не менее 3 символов!')
            return redirect(url_for('register'))
        
        if not password or len(password) < 4:
            flash('❌ Пароль должен быть не менее 4 символов!')
            return redirect(url_for('register'))
        
        if not full_name:
            flash('❌ Введите полное имя!')
            return redirect(url_for('register'))
        
        if User.query.filter_by(login=login).first():
            flash(f'❌ Логин "{login}" уже занят!')
            return redirect(url_for('register'))
        
        try:
            user = User(
                login=login,
                password_hash=hash_password(password),
                role=role,
                full_name=full_name,
                account_id=None
            )
            db.session.add(user)
            db.session.flush()
            
            if role == 'abonent':
                if not account_number:
                    last_account = Account.query.order_by(Account.id.desc()).first()
                    if last_account:
                        try:
                            last_num = int(last_account.number)
                            account_number = str(last_num + 1)
                        except:
                            account_number = '40513'
                    else:
                        account_number = '40513'
                
                if Account.query.filter_by(number=account_number).first():
                    flash(f'❌ Номер лицевого счёта {account_number} уже занят!')
                    db.session.rollback()
                    return redirect(url_for('register'))
                
                account = Account(
                    number=account_number,
                    address=address or f'ул. Новая, д.{account_number}'
                )
                db.session.add(account)
                db.session.flush()
                
                user.account_id = account.id
                
                elec = ServiceType.query.filter_by(name='Электроэнергия').first()
                water = ServiceType.query.filter_by(name='Вода холодная').first()
                gas = ServiceType.query.filter_by(name='Газ').first()
                heat = ServiceType.query.filter_by(name='Тепло').first()
                
                if elec:
                    db.session.add(Meter(
                        serial_number=f'EL-{account.number}-01',
                        service_type_id=elec.id,
                        account_id=account.id
                    ))
                if water:
                    db.session.add(Meter(
                        serial_number=f'WT-{account.number}-01',
                        service_type_id=water.id,
                        account_id=account.id
                    ))
                if gas:
                    db.session.add(Meter(
                        serial_number=f'GS-{account.number}-01',
                        service_type_id=gas.id,
                        account_id=account.id
                    ))
                if heat:
                    db.session.add(Meter(
                        serial_number=f'HT-{account.number}-01',
                        service_type_id=heat.id,
                        account_id=account.id
                    ))
                
                flash(f'✅ Абонент {full_name} создан! Лицевой счёт №{account.number}')
            else:
                flash(f'✅ Администратор {full_name} создан!')
            
            db.session.commit()
            log_action(current_user.id, 'register_user', {'new_user': login, 'role': role})
            return redirect(url_for('admin_panel'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Ошибка: {str(e)}')
            return redirect(url_for('register'))
    
    return render_template('register.html', user=current_user)

# ============================================================
# 8. ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ
# ============================================================

def init_db():
    with app.app_context():
        db.create_all()
        
        if User.query.first():
            print("✅ База данных уже инициализирована")
            return
        
        print("🔄 Создание тестовых данных...")
        
        service_types = [
            {'name': 'Электроэнергия', 'unit': 'кВт·ч', 'is_zoned': True},
            {'name': 'Вода холодная', 'unit': 'м³', 'is_zoned': False},
            {'name': 'Газ', 'unit': 'м³', 'is_zoned': False},
            {'name': 'Тепло', 'unit': 'Гкал', 'is_zoned': False},
        ]
        
        for st_data in service_types:
            existing = ServiceType.query.filter_by(name=st_data['name']).first()
            if not existing:
                db.session.add(ServiceType(
                    name=st_data['name'],
                    unit=st_data['unit'],
                    is_zoned=st_data['is_zoned']
                ))
        
        db.session.flush()
        
        admin = User(
            login='admin',
            password_hash=hash_password('admin123'),
            role='admin',
            full_name='Администратор Системы',
            account_id=None
        )
        abonent1 = User(
            login='abonent',
            password_hash=hash_password('abonent123'),
            role='abonent',
            full_name='Иванов Иван Иванович',
            account_id=None
        )
        abonent2 = User(
            login='petrov',
            password_hash=hash_password('petrov123'),
            role='abonent',
            full_name='Петров Петр Петрович',
            account_id=None
        )
        
        db.session.add_all([admin, abonent1, abonent2])
        db.session.flush()
        
        account1 = Account(number='40512', address='ул. Ленина, д.1, кв.5')
        account2 = Account(number='40190', address='ул. Пушкина, д.10, кв.23')
        db.session.add_all([account1, account2])
        db.session.flush()
        
        abonent1.account_id = account1.id
        abonent2.account_id = account2.id
        db.session.flush()
        
        elec = ServiceType.query.filter_by(name='Электроэнергия').first()
        water = ServiceType.query.filter_by(name='Вода холодная').first()
        gas = ServiceType.query.filter_by(name='Газ').first()
        heat = ServiceType.query.filter_by(name='Тепло').first()
        
        # Счётчики для абонента 1
        db.session.add_all([
            Meter(serial_number='EL-40512-01', service_type_id=elec.id, account_id=account1.id),
            Meter(serial_number='WT-40512-01', service_type_id=water.id, account_id=account1.id),
            Meter(serial_number='GS-40512-01', service_type_id=gas.id, account_id=account1.id)
        ])
        
        # Счётчики для абонента 2
        db.session.add_all([
            Meter(serial_number='EL-40190-01', service_type_id=elec.id, account_id=account2.id),
            Meter(serial_number='WT-40190-01', service_type_id=water.id, account_id=account2.id)
        ])
        
        db.session.flush()
        
        m1 = Meter.query.filter_by(serial_number='EL-40512-01').first()
        m2 = Meter.query.filter_by(serial_number='WT-40512-01').first()
        m3 = Meter.query.filter_by(serial_number='GS-40512-01').first()
        
        # Предыдущие показания (апрель)
        db.session.add_all([
            Reading(meter_id=m1.id, account_id=account1.id, period='2025-04', value_day=4217, value_night=2100, consumption=0),
            Reading(meter_id=m2.id, account_id=account1.id, period='2025-04', value=150, consumption=0),
            Reading(meter_id=m3.id, account_id=account1.id, period='2025-04', value=100, consumption=0)
        ])
        
        # Текущие показания (май)
        db.session.add_all([
            Reading(meter_id=m1.id, account_id=account1.id, period='2025-05', value_day=4403, value_night=2198, consumption=284),
            Reading(meter_id=m2.id, account_id=account1.id, period='2025-05', value=159, consumption=9),
            Reading(meter_id=m3.id, account_id=account1.id, period='2025-05', value=114, consumption=14)
        ])
        
        db.session.add_all([
            Tariff(service_type_id=elec.id, zone='day', rate=6.20, valid_from=datetime(2025, 1, 1)),
            Tariff(service_type_id=elec.id, zone='night', rate=2.40, valid_from=datetime(2025, 1, 1)),
            Tariff(service_type_id=water.id, rate=42.30, valid_from=datetime(2025, 1, 1)),
            Tariff(service_type_id=gas.id, rate=7.80, valid_from=datetime(2025, 1, 1)),
            Tariff(service_type_id=heat.id, rate=2500.00, valid_from=datetime(2025, 1, 1))
        ])
        
        db.session.add(Payment(
            account_id=account1.id,
            period='2025-05',
            amount=500.00,
            comment='Частичная оплата за май 2025'
        ))
        
        db.session.commit()
        
        print("=" * 50)
        print("✅ Тестовые данные успешно созданы!")
        print("=" * 50)
        print("📝 ДАННЫЕ ДЛЯ ВХОДА:")
        print("   Администратор: login='admin', password='admin123'")
        print("   Абонент №40512: login='abonent', password='abonent123'")
        print("   Абонент №40190: login='petrov', password='petrov123'")
        print("=" * 50)

# ============================================================
# 9. ЗАПУСК
# ============================================================

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)