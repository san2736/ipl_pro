from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import os
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'ipl-auction-secret-2024')
db_url = os.environ.get('DATABASE_URL', 'postgresql://localhost/ipl_auction')
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
if db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# ─── Models ───────────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    wallet = db.Column(db.Float, default=1000.0)  # in Crores
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    bids = db.relationship('Bid', backref='user', lazy=True)

class Player(db.Model):
    __tablename__ = 'players'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    team = db.Column(db.String(100))
    nationality = db.Column(db.String(50))
    role = db.Column(db.String(50))  # Batsman, Bowler, All-Rounder, WK
    batting_style = db.Column(db.String(50))
    bowling_style = db.Column(db.String(50))
    age = db.Column(db.Integer)
    ipl_caps = db.Column(db.Integer, default=0)
    base_price = db.Column(db.Float, nullable=False)  # in Crores
    current_bid = db.Column(db.Float)
    sold_price = db.Column(db.Float)
    status = db.Column(db.String(20), default='available')  # available, live, sold, unsold
    image_url = db.Column(db.String(300))
    # Stats
    batting_avg = db.Column(db.Float, default=0)
    strike_rate = db.Column(db.Float, default=0)
    runs_scored = db.Column(db.Integer, default=0)
    wickets = db.Column(db.Integer, default=0)
    economy = db.Column(db.Float, default=0)
    bowling_avg = db.Column(db.Float, default=0)
    matches = db.Column(db.Integer, default=0)
    fifties = db.Column(db.Integer, default=0)
    hundreds = db.Column(db.Integer, default=0)
    highest_score = db.Column(db.Integer, default=0)
    best_bowling = db.Column(db.String(20), default='0/0')
    description = db.Column(db.Text)
    # Auction metadata
    auction_order = db.Column(db.Integer, default=0)
    winning_team = db.Column(db.String(100))
    winner_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    bids = db.relationship('Bid', backref='player', lazy=True, cascade='all, delete-orphan')

class Bid(db.Model):
    __tablename__ = 'bids'
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    team_name = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class AuctionSession(db.Model):
    __tablename__ = 'auction_sessions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), default='IPL Auction 2025')
    is_active = db.Column(db.Boolean, default=False)
    current_player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

# ─── Auth Helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = db.session.get(User, session['user_id'])
        if not user or not user.is_admin:
            flash('Admin access required', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    if 'user_id' in session:
        return db.session.get(User, session['user_id'])
    return None

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    user = get_current_user()
    players = Player.query.filter_by(status='available').order_by(Player.auction_order).limit(12).all()
    live_player = Player.query.filter_by(status='live').first()
    stats = {
        'total_players': Player.query.count(),
        'sold': Player.query.filter_by(status='sold').count(),
        'available': Player.query.filter_by(status='available').count(),
        'live': 1 if live_player else 0
    }
    return render_template('index.html', user=user, players=players, live_player=live_player, stats=stats)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        team_name = request.form.get('team_name', '').strip()

        if User.query.filter_by(username=username).first():
            flash('Username already taken', 'error')
            return render_template('register.html')
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return render_template('register.html')

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            wallet=1000.0
        )
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        session['username'] = user.username
        flash('Welcome to IPL Auction!', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('index'))
        flash('Invalid credentials', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/players')
def players():
    user = get_current_user()
    query = Player.query
    role = request.args.get('role')
    nationality = request.args.get('nationality')
    status = request.args.get('status')
    search = request.args.get('search', '')
    sort = request.args.get('sort', 'auction_order')

    if role:
        query = query.filter_by(role=role)
    if nationality:
        query = query.filter_by(nationality=nationality)
    if status:
        query = query.filter_by(status=status)
    if search:
        query = query.filter(Player.name.ilike(f'%{search}%'))
    
    if sort == 'base_price_desc':
        query = query.order_by(Player.base_price.desc())
    elif sort == 'base_price_asc':
        query = query.order_by(Player.base_price.asc())
    elif sort == 'strike_rate':
        query = query.order_by(Player.strike_rate.desc())
    elif sort == 'batting_avg':
        query = query.order_by(Player.batting_avg.desc())
    elif sort == 'name':
        query = query.order_by(Player.name.asc())
    else:
        query = query.order_by(Player.auction_order.asc())

    players = query.all()
    return render_template('players.html', user=user, players=players,
                           roles=['Batsman', 'Bowler', 'All-Rounder', 'Wicket-Keeper'],
                           nationalities=['Indian', 'Australian', 'English', 'South African', 'West Indian', 'Sri Lankan', 'New Zealander', 'Pakistani', 'Bangladeshi', 'Afghan'],
                           filters={'role': role, 'nationality': nationality, 'status': status, 'search': search, 'sort': sort})

@app.route('/player/<int:player_id>')
def player_detail(player_id):
    user = get_current_user()
    player = Player.query.get_or_404(player_id)
    bids = Bid.query.filter_by(player_id=player_id).order_by(Bid.amount.desc()).limit(10).all()
    top_bid = bids[0] if bids else None
    return render_template('player_detail.html', user=user, player=player, bids=bids, top_bid=top_bid)

@app.route('/auction')
@login_required
def auction():
    user = get_current_user()
    live_player = Player.query.filter_by(status='live').first()
    session_obj = AuctionSession.query.filter_by(is_active=True).first()
    if live_player:
        bids = Bid.query.filter_by(player_id=live_player.id).order_by(Bid.amount.desc()).limit(5).all()
    else:
        bids = []
    return render_template('auction.html', user=user, live_player=live_player, bids=bids, auction_session=session_obj)

@app.route('/api/bid', methods=['POST'])
@login_required
def place_bid():
    user = get_current_user()
    data = request.get_json()
    player_id = data.get('player_id')
    amount = float(data.get('amount', 0))

    player = Player.query.get_or_404(player_id)

    if player.status != 'live':
        return jsonify({'success': False, 'error': 'Player is not currently in auction'}), 400

    min_bid = player.current_bid if player.current_bid else player.base_price
    if amount <= min_bid:
        return jsonify({'success': False, 'error': f'Bid must be higher than ₹{min_bid:.2f} Cr'}), 400

    if user.wallet < amount:
        return jsonify({'success': False, 'error': 'Insufficient wallet balance'}), 400

    bid = Bid(player_id=player_id, user_id=user.id, amount=amount, team_name=user.username)
    player.current_bid = amount
    db.session.add(bid)
    db.session.commit()

    # Emit real-time update
    socketio.emit('bid_update', {
        'player_id': player_id,
        'amount': amount,
        'bidder': user.username,
        'timestamp': bid.timestamp.isoformat()
    }, room=f'player_{player_id}')
    socketio.emit('bid_update', {
        'player_id': player_id,
        'amount': amount,
        'bidder': user.username,
        'timestamp': bid.timestamp.isoformat()
    }, room='auction_room')

    return jsonify({'success': True, 'new_bid': amount, 'bidder': user.username})

# ─── Admin Routes ──────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin_dashboard():
    user = get_current_user()
    players = Player.query.order_by(Player.auction_order).all()
    users = User.query.filter_by(is_admin=False).all()
    bids_count = Bid.query.count()
    sold_value = db.session.query(db.func.sum(Player.sold_price)).filter(Player.status=='sold').scalar() or 0
    session_obj = AuctionSession.query.first()
    return render_template('admin.html', user=user, players=players, users=users,
                           bids_count=bids_count, sold_value=sold_value, auction_session=session_obj)

@app.route('/admin/player/add', methods=['POST'])
@admin_required
def add_player():
    data = request.form
    player = Player(
        name=data.get('name'),
        team=data.get('team'),
        nationality=data.get('nationality'),
        role=data.get('role'),
        batting_style=data.get('batting_style'),
        bowling_style=data.get('bowling_style'),
        age=int(data.get('age', 0)),
        ipl_caps=int(data.get('ipl_caps', 0)),
        base_price=float(data.get('base_price', 0.5)),
        image_url=data.get('image_url', ''),
        batting_avg=float(data.get('batting_avg', 0)),
        strike_rate=float(data.get('strike_rate', 0)),
        runs_scored=int(data.get('runs_scored', 0)),
        wickets=int(data.get('wickets', 0)),
        economy=float(data.get('economy', 0)),
        bowling_avg=float(data.get('bowling_avg', 0)),
        matches=int(data.get('matches', 0)),
        fifties=int(data.get('fifties', 0)),
        hundreds=int(data.get('hundreds', 0)),
        highest_score=int(data.get('highest_score', 0)),
        best_bowling=data.get('best_bowling', '0/0'),
        description=data.get('description', ''),
        auction_order=Player.query.count() + 1
    )
    db.session.add(player)
    db.session.commit()
    flash(f'{player.name} added successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/player/<int:player_id>/go-live', methods=['POST'])
@admin_required
def go_live(player_id):
    # Stop any currently live player
    Player.query.filter_by(status='live').update({'status': 'available'})
    player = Player.query.get_or_404(player_id)
    player.status = 'live'
    player.current_bid = player.base_price

    # Update session
    session_obj = AuctionSession.query.first()
    if not session_obj:
        session_obj = AuctionSession(is_active=True)
        db.session.add(session_obj)
    session_obj.current_player_id = player_id
    session_obj.is_active = True
    db.session.commit()

    socketio.emit('player_live', {
        'player_id': player_id,
        'name': player.name,
        'base_price': player.base_price,
        'role': player.role,
        'image_url': player.image_url or ''
    }, room='auction_room')

    flash(f'{player.name} is now live!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/player/<int:player_id>/sell', methods=['POST'])
@admin_required
def sell_player(player_id):
    player = Player.query.get_or_404(player_id)
    top_bid = Bid.query.filter_by(player_id=player_id).order_by(Bid.amount.desc()).first()
    if top_bid:
        player.status = 'sold'
        player.sold_price = top_bid.amount
        player.winner_user_id = top_bid.user_id
        player.winning_team = top_bid.team_name
        winner = db.session.get(User, top_bid.user_id)
        if winner:
            winner.wallet -= top_bid.amount
        db.session.commit()
        socketio.emit('player_sold', {
            'player_id': player_id,
            'name': player.name,
            'sold_price': top_bid.amount,
            'winner': top_bid.team_name
        }, room='auction_room')
        flash(f'{player.name} sold to {top_bid.team_name} for ₹{top_bid.amount:.2f} Cr!', 'success')
    else:
        player.status = 'unsold'
        db.session.commit()
        flash(f'{player.name} marked as unsold.', 'warning')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/player/<int:player_id>/unsold', methods=['POST'])
@admin_required
def mark_unsold(player_id):
    player = Player.query.get_or_404(player_id)
    player.status = 'unsold'
    db.session.commit()
    socketio.emit('player_unsold', {'player_id': player_id, 'name': player.name}, room='auction_room')
    flash(f'{player.name} marked as unsold.', 'warning')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/seed', methods=['POST'])
@admin_required
def seed_players():
    if Player.query.count() > 0:
        flash('Players already seeded!', 'warning')
        return redirect(url_for('admin_dashboard'))
    seed_ipl_players()
    flash('IPL players seeded successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/player/<int:player_id>/delete', methods=['POST'])
@admin_required
def delete_player(player_id):
    player = Player.query.get_or_404(player_id)
    db.session.delete(player)
    db.session.commit()
    flash(f'{player.name} deleted.', 'success')
    return redirect(url_for('admin_dashboard'))

# ─── API endpoints ─────────────────────────────────────────────────────────────

@app.route('/api/player/<int:player_id>/bids')
def get_bids(player_id):
    bids = Bid.query.filter_by(player_id=player_id).order_by(Bid.amount.desc()).limit(10).all()
    return jsonify([{
        'amount': b.amount,
        'bidder': b.team_name,
        'timestamp': b.timestamp.isoformat()
    } for b in bids])

@app.route('/api/live-player')
def live_player_api():
    player = Player.query.filter_by(status='live').first()
    if not player:
        return jsonify({'live': False})
    top_bid = Bid.query.filter_by(player_id=player.id).order_by(Bid.amount.desc()).first()
    return jsonify({
        'live': True,
        'id': player.id,
        'name': player.name,
        'role': player.role,
        'base_price': player.base_price,
        'current_bid': player.current_bid or player.base_price,
        'image_url': player.image_url or '',
        'top_bidder': top_bid.team_name if top_bid else None
    })

# ─── SocketIO ──────────────────────────────────────────────────────────────────

@socketio.on('join_auction')
def handle_join(data):
    join_room('auction_room')

@socketio.on('join_player')
def handle_join_player(data):
    join_room(f'player_{data["player_id"]}')

# ─── DB Init + Seed ────────────────────────────────────────────────────────────

def seed_ipl_players():
    players_data = [
        # Batsmen
        {"name": "Virat Kohli", "team": "Royal Challengers Bangalore", "nationality": "Indian", "role": "Batsman",
         "batting_style": "Right-handed", "bowling_style": "Right-arm medium", "age": 35, "ipl_caps": 237,
         "base_price": 2.0, "matches": 237, "runs_scored": 7263, "batting_avg": 37.25, "strike_rate": 130.02,
         "fifties": 50, "hundreds": 8, "highest_score": 113, "wickets": 4, "economy": 8.5,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/315700/315726.png",
         "description": "The Run Machine. One of the greatest batsmen in IPL history with 7000+ runs."},
        {"name": "Rohit Sharma", "team": "Mumbai Indians", "nationality": "Indian", "role": "Batsman",
         "batting_style": "Right-handed", "bowling_style": "Right-arm off-break", "age": 37, "ipl_caps": 243,
         "base_price": 2.0, "matches": 243, "runs_scored": 6211, "batting_avg": 30.3, "strike_rate": 130.2,
         "fifties": 40, "hundreds": 2, "highest_score": 109, "wickets": 15, "economy": 7.8,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/320700/320757.png",
         "description": "The Hitman. Five-time IPL champion and legendary opener."},
        {"name": "Shubman Gill", "team": "Gujarat Titans", "nationality": "Indian", "role": "Batsman",
         "batting_style": "Right-handed", "bowling_style": "Right-arm off-break", "age": 24, "ipl_caps": 93,
         "base_price": 1.5, "matches": 93, "runs_scored": 3065, "batting_avg": 39.3, "strike_rate": 137.5,
         "fifties": 20, "hundreds": 3, "highest_score": 129, "wickets": 0, "economy": 0,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/336400/336435.png",
         "description": "Future of Indian batting. Elegant stroke-maker with an impressive IPL record."},
        {"name": "KL Rahul", "team": "Lucknow Super Giants", "nationality": "Indian", "role": "Wicket-Keeper",
         "batting_style": "Right-handed", "bowling_style": "Right-arm off-break", "age": 32, "ipl_caps": 132,
         "base_price": 2.0, "matches": 132, "runs_scored": 4683, "batting_avg": 47.3, "strike_rate": 136.1,
         "fifties": 42, "hundreds": 3, "highest_score": 132, "wickets": 0, "economy": 0,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/315700/315759.png",
         "description": "Consistent run-scorer and reliable wicket-keeper."},
        # All-Rounders
        {"name": "Hardik Pandya", "team": "Mumbai Indians", "nationality": "Indian", "role": "All-Rounder",
         "batting_style": "Right-handed", "bowling_style": "Right-arm fast-medium", "age": 30, "ipl_caps": 120,
         "base_price": 2.0, "matches": 120, "runs_scored": 2298, "batting_avg": 28.1, "strike_rate": 147.1,
         "fifties": 11, "hundreds": 0, "highest_score": 91, "wickets": 95, "economy": 8.9, "bowling_avg": 27.2,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/321900/321940.png",
         "description": "Impact all-rounder with big-hitting ability and genuine pace."},
        {"name": "Ravindra Jadeja", "team": "Chennai Super Kings", "nationality": "Indian", "role": "All-Rounder",
         "batting_style": "Left-handed", "bowling_style": "Slow left-arm orthodox", "age": 35, "ipl_caps": 226,
         "base_price": 2.0, "matches": 226, "runs_scored": 2758, "batting_avg": 26.5, "strike_rate": 127.0,
         "fifties": 7, "hundreds": 0, "highest_score": 62, "wickets": 154, "economy": 7.6, "bowling_avg": 29.4,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/315700/315762.png",
         "description": "Sir Jadeja - brilliant fielder, economical spinner, and handy lower-order batsman."},
        {"name": "Suryakumar Yadav", "team": "Mumbai Indians", "nationality": "Indian", "role": "Batsman",
         "batting_style": "Right-handed", "bowling_style": "Right-arm off-break", "age": 33, "ipl_caps": 141,
         "base_price": 2.0, "matches": 141, "runs_scored": 3226, "batting_avg": 29.3, "strike_rate": 148.4,
         "fifties": 20, "hundreds": 2, "highest_score": 103, "wickets": 0, "economy": 0,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/336400/336442.png",
         "description": "360-degree batsman. The best T20 batter in the world."},
        # Bowlers
        {"name": "Jasprit Bumrah", "team": "Mumbai Indians", "nationality": "Indian", "role": "Bowler",
         "batting_style": "Right-handed", "bowling_style": "Right-arm fast", "age": 30, "ipl_caps": 135,
         "base_price": 2.0, "matches": 135, "runs_scored": 56, "batting_avg": 5.6, "strike_rate": 70.0,
         "fifties": 0, "hundreds": 0, "highest_score": 10, "wickets": 167, "economy": 7.4, "bowling_avg": 23.5,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/321900/321930.png",
         "description": "The best death bowler in T20 cricket. Unplayable yorker and unique action."},
        {"name": "Mohammed Shami", "team": "Gujarat Titans", "nationality": "Indian", "role": "Bowler",
         "batting_style": "Right-handed", "bowling_style": "Right-arm fast-medium", "age": 34, "ipl_caps": 110,
         "base_price": 1.5, "matches": 110, "runs_scored": 72, "batting_avg": 8.0, "strike_rate": 90.0,
         "fifties": 0, "hundreds": 0, "highest_score": 15, "wickets": 134, "economy": 8.6, "bowling_avg": 26.5,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/320700/320767.png",
         "description": "Swing maestro and powerplay specialist."},
        {"name": "Yuzvendra Chahal", "team": "Rajasthan Royals", "nationality": "Indian", "role": "Bowler",
         "batting_style": "Right-handed", "bowling_style": "Right-arm leg-break", "age": 33, "ipl_caps": 160,
         "base_price": 1.0, "matches": 160, "runs_scored": 28, "batting_avg": 4.7, "strike_rate": 60.0,
         "fifties": 0, "hundreds": 0, "highest_score": 8, "wickets": 187, "economy": 7.9, "bowling_avg": 22.4,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/318800/318874.png",
         "description": "IPL's highest wicket-taker among active spinners. Lethal leg-spin."},
        # Overseas Players
        {"name": "Jos Buttler", "team": "Rajasthan Royals", "nationality": "English", "role": "Wicket-Keeper",
         "batting_style": "Right-handed", "bowling_style": "Right-arm off-break", "age": 33, "ipl_caps": 106,
         "base_price": 2.0, "matches": 106, "runs_scored": 3582, "batting_avg": 40.1, "strike_rate": 149.1,
         "fifties": 25, "hundreds": 6, "highest_score": 124, "wickets": 0, "economy": 0,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/315700/315770.png",
         "description": "The Orange Cap holder. Explosive opener who destroyed bowlers in 2022."},
        {"name": "Pat Cummins", "team": "Sunrisers Hyderabad", "nationality": "Australian", "role": "All-Rounder",
         "batting_style": "Right-handed", "bowling_style": "Right-arm fast", "age": 31, "ipl_caps": 66,
         "base_price": 2.0, "matches": 66, "runs_scored": 573, "batting_avg": 22.0, "strike_rate": 148.0,
         "fifties": 2, "hundreds": 0, "highest_score": 56, "wickets": 64, "economy": 9.1, "bowling_avg": 31.2,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/315700/315774.png",
         "description": "World-class pace and big-hitting ability. Premium overseas all-rounder."},
        {"name": "Rashid Khan", "team": "Gujarat Titans", "nationality": "Afghan", "role": "All-Rounder",
         "batting_style": "Right-handed", "bowling_style": "Right-arm leg-break", "age": 25, "ipl_caps": 112,
         "base_price": 2.0, "matches": 112, "runs_scored": 447, "batting_avg": 14.5, "strike_rate": 145.0,
         "fifties": 1, "hundreds": 0, "highest_score": 40, "wickets": 142, "economy": 6.7, "bowling_avg": 20.0,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/321900/321926.png",
         "description": "The best T20 spinner in the world. Economy king with match-winning ability."},
        {"name": "David Warner", "team": "Delhi Capitals", "nationality": "Australian", "role": "Batsman",
         "batting_style": "Left-handed", "bowling_style": "Right-arm leg-break", "age": 37, "ipl_caps": 184,
         "base_price": 1.5, "matches": 184, "runs_scored": 6565, "batting_avg": 40.7, "strike_rate": 140.0,
         "fifties": 60, "hundreds": 4, "highest_score": 126, "wickets": 1, "economy": 9.0,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/315700/315753.png",
         "description": "Six-time Orange Cap winner. One of the greatest IPL batsmen of all time."},
        {"name": "Kagiso Rabada", "team": "Punjab Kings", "nationality": "South African", "role": "Bowler",
         "batting_style": "Right-handed", "bowling_style": "Right-arm fast", "age": 29, "ipl_caps": 74,
         "base_price": 1.5, "matches": 74, "runs_scored": 88, "batting_avg": 8.8, "strike_rate": 92.0,
         "fifties": 0, "hundreds": 0, "highest_score": 19, "wickets": 98, "economy": 8.4, "bowling_avg": 22.5,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/315700/315777.png",
         "description": "Purple Cap holder. Express pace and lethal yorkers."},
        {"name": "Rishabh Pant", "team": "Delhi Capitals", "nationality": "Indian", "role": "Wicket-Keeper",
         "batting_style": "Left-handed", "bowling_style": "Right-arm off-break", "age": 26, "ipl_caps": 111,
         "base_price": 2.0, "matches": 111, "runs_scored": 3284, "batting_avg": 35.6, "strike_rate": 148.1,
         "fifties": 18, "hundreds": 1, "highest_score": 128, "wickets": 0, "economy": 0,
         "image_url": "https://img1.hscicdn.com/image/upload/f_auto,t_gn_icon_w_84/lsci/db/PICTURES/CMS/321900/321935.png",
         "description": "Most explosive wicket-keeper batsman. Known for audacious strokeplay."},
    ]

    for i, p in enumerate(players_data):
        player = Player(**p, auction_order=i+1)
        db.session.add(player)
    db.session.commit()

def create_admin():
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@iplauction.com',
            password_hash=generate_password_hash('admin123'),
            is_admin=True,
            wallet=99999.0
        )
        db.session.add(admin)
        db.session.commit()
        print("Admin created: admin / admin123")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_admin()
    socketio.run(app, debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
