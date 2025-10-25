# ==============================================
# ATTENDANCE TRACKING MODELS
# Add this section to your existing models.py
# ==============================================

class Department(db.Model):
    """Department master for attendance tracking"""
    __tablename__ = 'departments'

    id = db.Column(db.Integer, primary_key=True)
    dept_name = db.Column(db.String(100), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, default=True)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    sort_order = db.Column(db.Integer, default=0)

    submissions = db.relationship('AttendanceSubmission', backref='department', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Department {self.dept_name}>'

class AttendanceSubmission(db.Model):
    """Track attendance sheet submissions with index numbers"""
    __tablename__ = 'attendance_submissions'

    id = db.Column(db.Integer, primary_key=True)
    dept_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False)
    month_year = db.Column(db.String(7), nullable=False)  # '2025-01'
    index_number = db.Column(db.Integer, nullable=True)
    is_real_submission = db.Column(db.Boolean, default=True)
    submission_date = db.Column(db.Date, nullable=True, default=date.today)
    submitted_by = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(20), default='Filed')
    notes = db.Column(db.Text, nullable=True)
    garbage_value = db.Column(db.String(20), nullable=True)

    __table_args__ = (db.UniqueConstraint('dept_id', 'month_year', name='_dept_month_uc'),)

    def __repr__(self):
        return f'<AttendanceSubmission {self.department.dept_name} {self.month_year}>'

    def get_month_name(self):
        try:
            month_obj = datetime.strptime(self.month_year, '%Y-%m')
            return month_obj.strftime('%B %Y')
        except:
            return self.month_year

    def get_display_value(self):
        if not self.is_real_submission and self.garbage_value:
            return f"G:{self.garbage_value}"
        elif self.index_number:
            return str(self.index_number)
        else:
            return "Missing"
