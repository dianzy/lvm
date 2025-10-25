from app import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import logging

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    emp_no = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.emp_no}>'

class MasterData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    emp_no = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    doj = db.Column(db.Date, nullable=False)
    pl = db.Column(db.Float, default=0)
    partial_pl_days = db.Column(db.Float, default=0)
    cl = db.Column(db.Float, default=0)
    sl = db.Column(db.Float, default=0)
    rh = db.Column(db.Float, default=0)
    lop = db.Column(db.Float, default=0)
    l = db.Column(db.String(1), default='C')  # Employee status: P=Probationer, C=Confirmed/Permanent, R=Retired

    def get_emp_status(self):
        """Get employee status with graceful fallback if L column doesn't exist"""
        try:
            # Try to access L column if it exists
            return getattr(self, 'l', 'C')  # Default to 'C' if not found
        except Exception:
            return 'C'  # Default to Confirmed

    def set_emp_status(self, status):
        """Set employee status if column exists"""
        try:
            setattr(self, 'l', status)
        except Exception as e:
            logging.warning(f"Could not set L column: {e}")

    def __repr__(self):
        return f'<MasterData {self.emp_no}>'

class LeaveEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    emp_no = db.Column(db.String(20), nullable=False)
    lvfrom = db.Column(db.Date, nullable=False)
    lvto = db.Column(db.Date, nullable=True)
    session = db.Column(db.String(10), nullable=True)
    type = db.Column(db.String(20), nullable=False)
    sltype = db.Column(db.String(10), nullable=True)
    reason = db.Column(db.String(200), nullable=True)
    is_entered = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f'<LeaveEntry {self.emp_no} {self.type}>'

class AttendanceDepartment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<AttendanceDepartment {self.name}>'

class AttendanceIndex(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(db.Integer, db.ForeignKey('attendance_department.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    index_value = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    department = db.relationship('AttendanceDepartment', backref='indices')

    def __repr__(self):
        return f'<AttendanceIndex {self.department_id} {self.year}-{self.month} = {self.index_value}>'
