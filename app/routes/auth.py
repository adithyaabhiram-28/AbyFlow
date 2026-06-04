from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from app.extentions import db, bcrypt
from app.models import User

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()

    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password are required'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already exists'}), 409
    
    hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')

    user = User(email=data['email'], password_hash=hashed_password)
    db.session.add(user)
    db.session.commit()

    return jsonify({'message': 'User created successfully', 'email': user.email}), 201

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data.get('email')).first()

    if user and bcrypt.check_password_hash(user.password_hash, data.get('password')):
        token = create_access_token(identity=user.email, expires_delta=False)
        return jsonify({'message': 'Login successful', 'token': token}), 200
    
    return jsonify({'error': 'Invalid email or password'}), 401

@auth_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def dashboard():
    current_user_email = get_jwt_identity()
    user = User.query.filter_by(email=current_user_email).first()

    return jsonify({'message': f"Welcome to AbyFlow, {user.email}", "plan_tier": user.plan_tier}), 200