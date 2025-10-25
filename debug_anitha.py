from app import app
from leave_calculator import LeaveCalculator
from datetime import date
import json

with app.app_context():
    calc = LeaveCalculator()
    res = calc.calculate_leave_summary('38041', date(2025,10,25))
    print(json.dumps(res, default=str, indent=2))
