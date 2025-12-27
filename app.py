from flask import Flask, request, jsonify
from flask_cors import CORS
from face_recognition import face_service
import os
from datetime import datetime, timedelta
import jwt
from functools import wraps

app = Flask(__name__)
CORS(app, origins=["http://localhost:3000", "http://127.0.0.1:3000"])

# Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')

def token_required(f):
    """Decorator to verify Firebase token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Get token from header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'success': False, 'error': 'Token is missing'}), 401
        
        try:
            # Verify Firebase token
            decoded_token = firebase_service.verify_token(token)
            if not decoded_token:
                return jsonify({'success': False, 'error': 'Invalid token'}), 401
            
            # Add user info to request
            request.user_id = decoded_token['uid']
            request.user_email = decoded_token['email']
            
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 401
        
        return f(*args, **kwargs)
    
    return decorated

def admin_required(f):
    """Decorator to verify admin access"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'success': False, 'error': 'Token is missing'}), 401
        
        try:
            decoded_token = firebase_service.verify_token(token)
            if not decoded_token:
                return jsonify({'success': False, 'error': 'Invalid token'}), 401
            
            # Check if user is admin
            user_data = supabase_service.get_user_data(decoded_token['uid'])
            if not user_data or user_data.get('role') != 'admin':
                return jsonify({'success': False, 'error': 'Admin access required'}), 403
            
            request.user_id = decoded_token['uid']
            request.user_email = decoded_token['email']
            
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 401
        
        return f(*args, **kwargs)
    
    return decorated

@app.route('/')
def home():
    return jsonify({
        'message': 'AI Attendance System API',
        'status': 'running',
        'version': '1.0.0'
    })

@app.route('/upload_face', methods=['POST'])
@token_required
def upload_face():
    """Upload and encode face image"""
    try:
        data = request.get_json()
        image_data = data.get('image_data')
        user_id = data.get('user_id', request.user_id)
        is_reference = data.get('is_reference', False)
        
        if not image_data:
            return jsonify({'success': False, 'error': 'No image data provided'}), 400
        
        # Validate face quality
        is_valid, quality_message = face_service.validate_face_quality(image_data)
        if not is_valid:
            return jsonify({
                'success': False, 
                'error': f'Face quality check failed: {quality_message}'
            }), 400
        
        # Encode face
        encoding, message = face_service.encode_face_from_base64(image_data)
        if not encoding:
            return jsonify({'success': False, 'error': message}), 400
        
        # Save to Firebase
        success = firebase_service.save_face_encoding(user_id, encoding, is_reference)
        
        if success:
            # Upload image to storage
            image_type = 'reference' if is_reference else 'captured'
            image_url = firebase_service.upload_image(image_data, user_id, image_type)
            
            return jsonify({
                'success': True,
                'message': 'Face uploaded successfully',
                'encoding_saved': True,
                'image_url': image_url
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save face encoding'}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/compare_faces', methods=['POST'])
@token_required
def compare_faces():
    """Compare reference face with captured face"""
    try:
        data = request.get_json()
        user_id = data.get('user_id', request.user_id)
        captured_image = data.get('captured_image')
        
        if not captured_image:
            return jsonify({'success': False, 'error': 'No captured image provided'}), 400
        
        # Get reference face encoding
        reference_encoding = firebase_service.get_face_encoding(user_id, is_reference=True)
        if not reference_encoding:
            return jsonify({'success': False, 'error': 'No reference face found'}), 404
        
        # Encode captured face
        captured_encoding, message = face_service.encode_face_from_base64(captured_image)
        if not captured_encoding:
            return jsonify({'success': False, 'error': message}), 400
        
        # Compare faces
        comparison_result = face_service.compare_faces(reference_encoding, captured_encoding)
        
        # Save captured face encoding
        firebase_service.save_face_encoding(user_id, captured_encoding, is_reference=False)
        
        return jsonify(comparison_result)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/mark_attendance', methods=['POST'])
@token_required
def mark_attendance():
    """Mark attendance for user"""
    try:
        data = request.get_json()
        user_id = data.get('user_id', request.user_id)
        confidence = data.get('confidence', 0)
        timestamp = data.get('timestamp')
        
        # Validate confidence threshold
        if confidence < 60:  # 60% confidence threshold
            return jsonify({
                'success': False, 
                'error': 'Face match confidence too low',
                'confidence': confidence
            }), 400
        
        # Mark attendance in Firebase
        success = firebase_service.mark_attendance(user_id, confidence, timestamp)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Attendance marked successfully',
                'confidence': confidence,
                'timestamp': timestamp
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to mark attendance'}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/attendance_records', methods=['GET'])
@admin_required
def get_attendance_records():
    """Get attendance records (admin only)"""
    try:
        # Get date filters
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # Convert string dates to datetime objects
        if start_date:
            start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        if end_date:
            end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        
        records = firebase_service.get_attendance_records(start_date, end_date)
        
        return jsonify({
            'success': True,
            'records': records,
            'count': len(records)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/users', methods=['GET'])
@admin_required
def get_users():
    """Get all users (admin only)"""
    try:
        users = firebase_service.get_all_users()
        
        return jsonify({
            'success': True,
            'users': users,
            'count': len(users)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/system_stats', methods=['GET'])
@admin_required
def get_system_stats():
    """Get system statistics (admin only)"""
    try:
        # Get today's date range
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        
        # Get attendance records for today
        today_records = firebase_service.get_attendance_records(today_start, today_end)
        
        # Calculate stats
        total_users = len(firebase_service.get_all_users())
        present_today = len([r for r in today_records if r.get('status') == 'present'])
        attendance_rate = (present_today / total_users * 100) if total_users > 0 else 0
        
        stats = {
            'total_users': total_users,
            'present_today': present_today,
            'attendance_rate': round(attendance_rate, 2),
            'system_status': 'online',
            'last_updated': datetime.now().isoformat()
        }
        
        return jsonify({
            'success': True,
            'stats': stats
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'firebase': 'connected',
            'face_recognition': 'initialized'
        }
    })

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    
    print(f"Starting AI Attendance System API on port {port}")
    print(f"Debug mode: {debug}")
    
    app.run(host='0.0.0.0', port=port, debug=debug)