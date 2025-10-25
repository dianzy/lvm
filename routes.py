from flask import render_template, request, redirect, url_for, flash, session, jsonify, send_file
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash
import pandas as pd
import io
import os
import time
from datetime import datetime, date, timedelta
from app import app, db
from models import User, MasterData, LeaveEntry, AttendanceDepartment, AttendanceIndex
from leave_calculator import LeaveCalculator
import logging

# Add date to Jinja2 global context
@app.context_processor
def inject_date():
    return {'date': date}

# Add normalize_emp_no as a Jinja2 filter
@app.template_filter('normalize_emp_no')
def normalize_emp_no_filter(emp_no):
    """Jinja2 filter to normalize employee numbers in templates"""
    return normalize_emp_no(emp_no)

# ---------- Employee Number Normalization ----------
def normalize_emp_no(emp_no):
    """
    Normalize employee number to remove trailing .0 from numeric values
    Example: 29813.0 -> 29813, ABC123 -> ABC123
    """
    if emp_no is None or emp_no == '':
        return ''
    
    emp_no_str = str(emp_no).strip()
    
    # If it's empty or nan after stripping, return empty
    if emp_no_str.lower() in ['', 'nan', 'nat', 'none', 'null']:
        return ''
    
    # If it ends with .0, remove it
    if emp_no_str.endswith('.0'):
        emp_no_str = emp_no_str[:-2]
    
    # Try to convert to int if it's purely numeric (removes any float formatting)
    try:
        # If it's a valid number, convert to int then back to string
        float_val = float(emp_no_str)
        if float_val == int(float_val):  # It's a whole number
            return str(int(float_val))
    except:
        pass
    
    return emp_no_str

# ---------- Enhanced Employee Lookup ----------
def get_employee_by_number(emp_no):
    """Enhanced employee lookup that handles multiple formats"""
    # First normalize the employee number
    emp_no_normalized = normalize_emp_no(emp_no)
    
    # Try normalized lookup first
    emp = MasterData.query.filter_by(emp_no=emp_no_normalized).first()
    if emp:
        return emp
    
    # Fallback to legacy formats for backward compatibility
    emp_no_str = str(emp_no).strip()
    
    # Try with .0 suffix
    emp_no_with_decimal = emp_no_normalized + '.0'
    emp = MasterData.query.filter_by(emp_no=emp_no_with_decimal).first()
    if emp:
        return emp
    
    # Try original format if different from normalized
    if emp_no_str != emp_no_normalized:
        emp = MasterData.query.filter_by(emp_no=emp_no_str).first()
        if emp:
            return emp

    return None

# ---------- Robust date parser ----------
def parse_any_date(val):
    s = str(val).strip()
    if s.lower() in ['', 'nan', 'nat', 'none', 'null']:
        return None

    # 1) Exact, unambiguous formats
    fmts = [
        '%Y-%m-%d',    # 2025-09-20
        '%d-%m-%Y',    # 20-09-2025
        '%d/%m/%Y',    # 20/09/2025
        '%m-%d-%Y',    # 09-20-2025
        '%m/%d/%Y',    # 09/20/2025
        '%d-%b-%Y',    # 20-Sep-2025
        '%d/%b/%Y',    # 20/Sep/2025
        '%d-%B-%Y',    # 20-September-2025
        '%d/%B-%Y',    # 20/September/2025
        '%d.%m.%Y',    # 20.09.2025
        '%Y/%m/%d',    # 2025/09/20
    ]

    for fmt in fmts:
        try:
            return pd.to_datetime(s, format=fmt).date()
        except Exception:
            pass

    # 2) Flexible parse, try dd/mm and mm/dd interpretations
    for dayfirst in (True, False):
        dt = pd.to_datetime(s, errors='coerce', dayfirst=dayfirst, infer_datetime_format=True)
        if not pd.isna(dt):
            return dt.date()

    # 3) Excel serial number fallback
    try:
        n = float(s)
        if n > 0:
            dt = pd.to_datetime(n, unit='D', origin='1899-12-30', errors='coerce')
            if not pd.isna(dt):
                return dt.date()
    except Exception:
        pass

    return None

# ---------- Parse partial PL from fraction format ----------
def parse_partial_pl(val):
    if pd.isna(val):
        return 0.0

    s = str(val).strip()
    if s.lower() in ['', 'nan', 'nat', 'none', 'null']:
        return 0.0

    # Check if it's a fraction format like "5/11"
    if '/' in s:
        try:
            parts = s.split('/')
            if len(parts) == 2:
                numerator = int(parts[0].strip())
                denominator = int(parts[1].strip())
                if denominator == 11:
                    return float(numerator)
                else:
                    return float(numerator * 11 / denominator)
        except Exception:
            pass

    # Try as regular number
    try:
        return float(s)
    except Exception:
        return 0.0

# ---------- Parse employee status (L column) ----------
def parse_employee_status(val):
    if pd.isna(val):
        return 'C'

    s = str(val).strip().upper()
    if s in ['P', 'C', 'R']:
        return s
    return 'C'  # Default to Confirmed

# ---------- Safe database operations ----------
def safe_delete_all(model_class, max_retries=5):
    for attempt in range(max_retries):
        try:
            db.session.close()
            records_deleted = model_class.query.delete()
            db.session.commit()
            logging.info(f"Deleted {records_deleted} records from {model_class.__name__}")
            return True

        except Exception as e:
            db.session.rollback()
            logging.warning(f"Delete attempt {attempt + 1} failed: {str(e)}")

            if attempt < max_retries - 1:
                time.sleep(0.5)  # Wait 500ms before retry
            else:
                logging.error(f"Failed to delete records after {max_retries} attempts")
                return False

    return False

def safe_bulk_insert(records, max_retries=5):
    for attempt in range(max_retries):
        try:
            db.session.close()
            for record in records:
                db.session.add(record)

            db.session.commit()
            logging.info(f"Successfully inserted {len(records)} records")
            return True

        except Exception as e:
            db.session.rollback()
            logging.warning(f"Insert attempt {attempt + 1} failed: {str(e)}")

            if attempt < max_retries - 1:
                time.sleep(0.5)  # Wait 500ms before retry
            else:
                logging.error(f"Failed to insert records after {max_retries} attempts")
                return False

    return False

# ---------- Leave Entry Constants and Helpers ----------
REASON_DEFAULTS = {
    'SL_FP': 'Viral fever',
    'SL_HP': 'Viral fever',
    'S': 'Viral fever',
    'RH': 'RH',
    'L': 'LOP',
    'M': 'Maternity',
    'E': 'Encashment',
    'OTHERS': '',
    'CL': 'Personal work',
    'CL_HALFDAY': 'Personal work',
    'PL': 'Personal work'
}

VALID_LEAVE_TYPES = ['CL', 'CL_HALFDAY', 'SL_FP', 'SL_HP', 'PL', 'OTHERS', 'E', 'M', 'L', 'RH']

def check_leave_overlap(emp_no, lvfrom, lvto, exclude_id=None):
    query1 = LeaveEntry.query.filter_by(emp_no=str(emp_no))
    query2 = LeaveEntry.query.filter_by(emp_no=f"{emp_no}.0")

    if exclude_id:
        query1 = query1.filter(LeaveEntry.id != exclude_id)
        query2 = query2.filter(LeaveEntry.id != exclude_id)

    existing_leaves = query1.all() + query2.all()

    for leave in existing_leaves:
        if leave.type.upper() == 'E':
            continue

        existing_from = leave.lvfrom
        existing_to = leave.lvto or leave.lvfrom

        if lvfrom <= existing_to and lvto >= existing_from:
            return True, f"Overlaps with existing {leave.type} leave from {existing_from} to {existing_to}"

    return False, None

def check_negative_balance_warning(emp_no, lvfrom, lvto, leave_type, session_val=None, exclude_id=None):
    try:
        calculator = LeaveCalculator()
        
        # For PL leaves, use starting date as "as on date" for balance check (as per user requirement)
        # For other leaves, use ending date
        check_date = lvfrom if leave_type in ['PL', 'E'] else lvto
        
        current_result = calculator.calculate_leave_summary(emp_no, check_date)
        if not current_result['success']:
            return False, None

        current_summary = current_result['data']

        days = (lvto - lvfrom).days + 1
        if session_val in ['F', 'A']:
            days = 0.5
        elif leave_type == 'CL_HALFDAY':
            days = 0.5
        elif leave_type == 'E':
            days = 15  # Encashment = 15 PL days

        warnings = []

        if leave_type in ['PL', 'E']:
            pl_total = current_summary['closing_balances']['pl'] * 11 + current_summary['closing_balances']['pl_part']
            new_pl_total = pl_total - (days * 11)
            if new_pl_total < 0:
                warnings.append(f"PL (would become {new_pl_total/11:.2f} as on {lvfrom})")

        elif leave_type in ['CL', 'CL_HALFDAY']:
            new_cl = current_summary['closing_balances']['cl'] - days
            if new_cl < 0:
                warnings.append(f"CL (would become {new_cl})")
            
            # Check half-day CL occasions limit (max 6 occasions)
            if days == 0.5:  # This is a half-day CL
                    # Count existing half-day CL occasions only in the same calendar year
                    # Determine the year to check from lvfrom
                    check_year = lvfrom.year
                    year_start = date(check_year, 1, 1)
                    year_end = date(check_year, 12, 31)

                    query1 = LeaveEntry.query.filter_by(emp_no=str(emp_no)).filter(LeaveEntry.lvfrom >= year_start, LeaveEntry.lvfrom <= year_end)
                    query2 = LeaveEntry.query.filter_by(emp_no=f"{emp_no}.0").filter(LeaveEntry.lvfrom >= year_start, LeaveEntry.lvfrom <= year_end)

                    if exclude_id:
                        query1 = query1.filter(LeaveEntry.id != exclude_id)
                        query2 = query2.filter(LeaveEntry.id != exclude_id)

                    all_leaves = query1.all() + query2.all()

                    halfday_cl_count = 0
                    for leave in all_leaves:
                        leave_type_upper = leave.type.upper()
                        # Count CL_HALFDAY or CL with session F/A as half-day occasions
                        if leave_type_upper == 'CL_HALFDAY':
                            halfday_cl_count += 1
                        elif leave_type_upper == 'CL' and leave.session and leave.session.upper() in ['F', 'A']:
                            halfday_cl_count += 1

                    # Add current half-day leave
                    new_halfday_count = halfday_cl_count + 1

                    if new_halfday_count > 6:
                        warnings.append(f"Half-day CL occasions exceeded (current: {halfday_cl_count}, will become: {new_halfday_count}, max allowed: 6)")

        elif leave_type in ['SL_FP', 'SL_HP', 'S']:
            sl_deduction = days * 2 if leave_type == 'SL_FP' or (leave_type == 'S' and session_val == 'F') else days
            new_sl = current_summary['closing_balances']['sl'] - sl_deduction
            if new_sl < 0:
                warnings.append(f"SL (would become {new_sl})")

        elif leave_type == 'RH':
            new_rh = current_summary['closing_balances']['rh'] - days
            if new_rh < 0:
                warnings.append(f"RH (would become {new_rh})")

        if warnings:
            return True, f"⚠️ Warning: This leave will result in NEGATIVE balance for {', '.join(warnings)}"

        return False, None

    except Exception as e:
        logging.error(f"Error checking negative balance: {e}")
        return False, None

# ---------- Main Routes ----------

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        emp_no = request.form['emp_no'].strip()
        password = request.form['password']

        user = User.query.filter_by(emp_no=emp_no).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['emp_no'] = user.emp_no
            session['is_admin'] = user.is_admin
            session['name'] = user.name
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid employee number or password', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    total_employees = MasterData.query.count()
    total_leave_entries = LeaveEntry.query.count()
    return render_template('dashboard.html', 
                         total_employees=total_employees,
                         total_leave_entries=total_leave_entries)

@app.route('/admin')
def admin():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    users = User.query.all()
    return render_template('admin.html', users=users)

@app.route('/entry')
def entry():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('entry.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # Master upload
        if 'master_file' in request.files:
            file = request.files['master_file']
            if file and file.filename and file.filename.endswith('.csv'):
                try:
                    df = pd.read_csv(file.stream)
                    df.columns = df.columns.str.strip().str.lower()

                    required_cols = ['emp_no', 'name', 'doj', 'pl', 'partial_pl_days', 'cl', 'sl', 'rh']
                    missing_cols = [c for c in required_cols if c not in df.columns]

                    if missing_cols:
                        available_cols = list(df.columns)
                        flash(f"Master CSV missing columns: {', '.join(missing_cols)}. Available columns: {', '.join(available_cols)}", 'error')
                        return redirect(url_for('upload'))

                    rows_to_add = []

                    for _, row in df.iterrows():
                        try:
                            emp_no_val = normalize_emp_no(row['emp_no'])
                            if emp_no_val == '':
                                continue

                            doj = parse_any_date(row['doj'])
                            if doj is None:
                                logging.warning("Skipping master row due to invalid doj: %s", row.get('doj'))
                                continue

                            partial_pl_val = parse_partial_pl(row['partial_pl_days'])
                            emp_status = 'C'  # Default
                            if 'l' in df.columns:
                                emp_status = parse_employee_status(row['l'])

                            md = MasterData(
                                emp_no=emp_no_val,
                                name=str(row['name']).strip(),
                                doj=doj,
                                pl=float(row['pl']) if pd.notna(row['pl']) else 0.0,
                                partial_pl_days=partial_pl_val,
                                cl=float(row['cl']) if pd.notna(row['cl']) else 0.0,
                                sl=float(row['sl']) if pd.notna(row['sl']) else 0.0,
                                rh=float(row['rh']) if pd.notna(row['rh']) else 0.0,
                                lop=float(row['lop']) if 'lop' in df.columns and pd.notna(row['lop']) else 0.0
                            )

                            if hasattr(md, 'set_emp_status'):
                                md.set_emp_status(emp_status)

                            rows_to_add.append(md)

                        except Exception as e:
                            logging.warning("Skipping invalid master row: %s", e)
                            continue

                    if not rows_to_add:
                        flash('No valid rows found in master CSV', 'error')
                        return redirect(url_for('upload'))

                    logging.info(f"Attempting to delete existing master data...")
                    if not safe_delete_all(MasterData):
                        flash('Error clearing existing master data. Database may be locked.', 'error')
                        return redirect(url_for('upload'))

                    logging.info(f"Attempting to insert {len(rows_to_add)} master records...")
                    if not safe_bulk_insert(rows_to_add):
                        flash('Error inserting master data. Database may be locked.', 'error')
                        return redirect(url_for('upload'))

                    flash(f"Master data uploaded successfully! Inserted: {len(rows_to_add)} employees", 'success')

                except Exception as e:
                    flash(f'Error uploading master data: {str(e)}', 'error')
                    logging.error("Master upload error: %s", e)

        # Leave upload
        if 'leave_file' in request.files:
            file = request.files['leave_file']
            if file and file.filename and file.filename.endswith('.csv'):
                try:
                    df = pd.read_csv(file.stream)
                    df.columns = df.columns.str.strip().str.lower()

                    required_cols = ['emp_no', 'lvfrom', 'type']
                    missing_cols = [c for c in required_cols if c not in df.columns]

                    if missing_cols:
                        available_cols = list(df.columns)
                        flash(f"Leave CSV missing columns: {', '.join(missing_cols)}. Available columns: {', '.join(available_cols)}", 'error')
                        return redirect(url_for('upload'))

                    rows_to_add = []
                    for _, row in df.iterrows():
                        try:
                            emp_no_val = normalize_emp_no(row['emp_no'])
                            if emp_no_val == '':
                                continue

                            lvfrom = parse_any_date(row['lvfrom'])
                            if lvfrom is None:
                                logging.warning("Skipping leave row due to invalid lvfrom: %s", row.get('lvfrom'))
                                continue

                            lvto = None
                            if 'lvto' in df.columns and pd.notna(row.get('lvto')):
                                lvto_val = str(row.get('lvto')).strip()
                                if lvto_val.lower() not in ['', 'nan', 'nat', 'none', 'null']:
                                    lvto = parse_any_date(lvto_val)

                            session_val = None
                            if 'session' in df.columns and pd.notna(row.get('session')):
                                session_temp = str(row.get('session')).strip()
                                if session_temp.lower() not in ['', 'nan', 'nat', 'none', 'null']:
                                    session_val = session_temp

                            sltype_val = None
                            if 'sltype' in df.columns and pd.notna(row.get('sltype')):
                                sltype_temp = str(row.get('sltype')).strip()
                                if sltype_temp.lower() not in ['', 'nan', 'nat', 'none', 'null']:
                                    sltype_val = sltype_temp

                            reason_val = None
                            if 'reason' in df.columns and pd.notna(row.get('reason')):
                                reason_temp = str(row.get('reason')).strip()
                                if reason_temp.lower() not in ['', 'nan', 'nat', 'none', 'null']:
                                    reason_val = reason_temp

                            leave_entry = LeaveEntry(
                                emp_no=emp_no_val,
                                lvfrom=lvfrom,
                                lvto=lvto,
                                session=session_val,
                                type=str(row['type']).strip(),
                                sltype=sltype_val,
                                reason=reason_val
                            )
                            rows_to_add.append(leave_entry)

                        except Exception as e:
                            logging.warning("Skipping invalid leave entry row: %s", e)
                            continue

                    if not rows_to_add:
                        flash('No valid rows found in leave CSV', 'error')
                        return redirect(url_for('upload'))

                    logging.info(f"Attempting to delete existing leave entries...")
                    if not safe_delete_all(LeaveEntry):
                        flash('Error clearing existing leave entries. Database may be locked.', 'error')
                        return redirect(url_for('upload'))

                    logging.info(f"Attempting to insert {len(rows_to_add)} leave records...")
                    if not safe_bulk_insert(rows_to_add):
                        flash('Error inserting leave entries. Database may be locked.', 'error')
                        return redirect(url_for('upload'))

                    flash(f"Leave entries uploaded successfully! Inserted: {len(rows_to_add)}", 'success')

                except Exception as e:
                    flash(f'Error uploading leave entries: {str(e)}', 'error')
                    logging.error("Leave upload error: %s", e)

    return render_template('upload.html')

@app.route('/summary', methods=['GET', 'POST'])
def summary():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        emp_no = request.form.get('emp_no', session.get('emp_no'))
        as_on_date_str = request.form['as_on_date']

        try:
            as_on_date = parse_any_date(as_on_date_str)
            if as_on_date is None:
                raise ValueError("Invalid date")

            calculator = LeaveCalculator()
            result = calculator.calculate_leave_summary(emp_no, as_on_date)

            if result['success']:
                return render_template('summary.html',
                                     summary=result['data'],
                                     emp_no=emp_no,
                                     as_on_date=as_on_date)
            else:
                flash(result['error'], 'error')
        except ValueError:
            flash('Invalid date format', 'error')
        except Exception as e:
            flash(f'Error calculating summary: {str(e)}', 'error')
            logging.error("Summary calculation error: %s", e)

    return render_template('summary.html',
                         emp_no=session.get('emp_no'),
                         as_on_date=date.today())

# ---------- ENHANCED: LOP/SL_HP Deduction Report with Better Handling ----------

@app.route('/deduction_report', methods=['GET', 'POST'])
def deduction_report():
    """Enhanced LOP/SL_HP report - Entry Order with Missing Employee Warnings"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        as_on_date_str = request.form['as_on_date']
        year = int(request.form.get('year', date.today().year))

        try:
            as_on_date = parse_any_date(as_on_date_str)
            if as_on_date is None:
                raise ValueError("Invalid date")

            start_date = date(year, 1, 1)

            # Get all leave entries in the date range - ORDERED BY ID (entry order)
            leaves_query = LeaveEntry.query.filter(
                LeaveEntry.lvfrom >= start_date,
                LeaveEntry.lvfrom <= as_on_date
            ).order_by(LeaveEntry.id.asc()).all()

            leave_types_found = set()
            for leave in leaves_query:
                leave_types_found.add(leave.type)
                if leave.sltype:
                    leave_types_found.add(f"{leave.type}_{leave.sltype}")

            print(f"DEBUG: Leave types found: {leave_types_found}")
            print(f"DEBUG: Total leaves in period: {len(leaves_query)}")

            missing_employees = set()
            lop_entries = []
            sl_hp_entries = []
            all_deduction_entries = []

            for leave in leaves_query:
                emp_no = leave.emp_no

                # Enhanced LOP/SL_HP detection
                is_lop = leave.type.upper() == 'L'

                leave_type_upper = leave.type.upper()
                sltype_upper = (leave.sltype or '').upper()
                is_sl_hp = (leave_type_upper == 'SL_HP' or 
                           (leave_type_upper == 'S' and sltype_upper == 'H') or
                           (leave_type_upper == 'SL' and sltype_upper == 'H') or
                           (leave_type_upper == 'SL' and sltype_upper == 'HP') or
                           leave_type_upper == 'SLHP')

                if is_lop or is_sl_hp:
                    # Enhanced employee lookup
                    emp = get_employee_by_number(emp_no)

                    if not emp:
                        print(f"WARNING: Employee {emp_no} not found in master data - SKIPPING entry ID {leave.id}")
                        missing_employees.add(emp_no)
                        continue  # Skip entries without master data

                    print(f"DEBUG: Found employee {emp_no} -> {emp.name}")

                    # Calculate days
                    leave_from = leave.lvfrom
                    leave_to = leave.lvto or leave.lvfrom

                    if leave_to > as_on_date:
                        leave_to = as_on_date

                    days = (leave_to - leave_from).days + 1
                    if leave.session in ['F', 'A']:
                        days = 0.5

                    entry = {
                        'id': leave.id,
                        'emp_no': emp_no,
                        'emp_name': emp.name,
                        'from': leave_from.strftime('%d-%m-%Y'),
                        'to': leave_to.strftime('%d-%m-%Y') if leave_to != leave_from else '',
                        'days': days,
                        'reason': leave.reason or '',
                        'type': 'LOP' if is_lop else 'SL_HP',
                        'original_type': leave.type,
                        'sltype': leave.sltype or '',
                        'is_entered': leave.is_entered
                    }

                    if is_lop:
                        lop_entries.append(entry)
                    if is_sl_hp:
                        sl_hp_entries.append(entry)

                    all_deduction_entries.append(entry)

            # Report missing employees
            if missing_employees:
                print(f"WARNING: {len(missing_employees)} employees have LOP/SL_HP entries but no master data:")
                for emp in sorted(missing_employees):
                    print(f"  - {emp}")
                flash(f"Warning: {len(missing_employees)} employees with LOP/SL_HP entries not found in master data: {', '.join(sorted(missing_employees))}. These entries were excluded from the report.", 'warning')

            print(f"DEBUG: Final counts - LOP: {len(lop_entries)}, SL_HP: {len(sl_hp_entries)}, Total: {len(all_deduction_entries)}")

            return render_template('deduction_report.html',
                                 lop_entries=lop_entries,
                                 sl_hp_entries=sl_hp_entries,
                                 all_deduction_entries=all_deduction_entries,
                                 missing_employees=list(missing_employees),
                                 as_on_date=as_on_date,
                                 start_date=start_date,
                                 year=year)

        except ValueError:
            flash('Invalid date format', 'error')
        except Exception as e:
            flash(f'Error generating deduction report: {str(e)}', 'error')
            logging.error("Deduction report error: %s", e)

    return render_template('deduction_report.html', 
                         as_on_date=date.today(),
                         start_date=date(date.today().year, 1, 1),
                         year=date.today().year)

@app.route('/export_deduction_excel')
def export_deduction_excel():
    """Export LOP/SL_HP deduction data to Excel - Entry Order Preserved"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        as_on_date_str = request.args.get('as_on_date', '')
        year = int(request.args.get('year', date.today().year))

        if not as_on_date_str:
            flash('Missing date parameter', 'error')
            return redirect(url_for('deduction_report'))

        as_on_date = parse_any_date(as_on_date_str)
        start_date = date(year, 1, 1)

        # Get deduction data in ENTRY ORDER (same logic as deduction_report)
        leaves_query = LeaveEntry.query.filter(
            LeaveEntry.lvfrom >= start_date,
            LeaveEntry.lvfrom <= as_on_date
        ).order_by(LeaveEntry.id.asc()).all()

        lop_details = []
        sl_hp_details = []
        all_deduction_details = []
        missing_employees = []

        entry_counter = 0

        for leave in leaves_query:
            emp_no = leave.emp_no

            # Same detection logic as deduction_report
            is_lop = leave.type.upper() == 'L'

            leave_type_upper = leave.type.upper()
            sltype_upper = (leave.sltype or '').upper()
            is_sl_hp = (leave_type_upper == 'SL_HP' or 
                       (leave_type_upper == 'S' and sltype_upper == 'H') or
                       (leave_type_upper == 'SL' and sltype_upper == 'H') or
                       (leave_type_upper == 'SL' and sltype_upper == 'HP') or
                       leave_type_upper == 'SLHP')

            if is_lop or is_sl_hp:
                emp = get_employee_by_number(emp_no)
                if not emp:
                    missing_employees.append(emp_no)
                    continue  # Skip entries without master data

                entry_counter += 1

                leave_from = leave.lvfrom
                leave_to = leave.lvto or leave.lvfrom
                if leave_to > as_on_date:
                    leave_to = as_on_date

                days = (leave_to - leave_from).days + 1
                if leave.session in ['F', 'A']:
                    days = 0.5

                entry_data = {
                    'Entry No': entry_counter,
                    'Emp No': emp_no,
                    'Name': emp.name,
                    'From': leave_from.strftime('%d-%m-%Y'),
                    'To': leave_to.strftime('%d-%m-%Y'),
                    'Days': days,
                    'Type': leave.type,
                    'SL Type': leave.sltype or '',
                    'Reason': leave.reason or '',
                    'Database ID': leave.id
                }

                all_deduction_details.append({
                    **entry_data,
                    'Category': 'LOP' if is_lop else 'SL_HP'
                })

                if is_lop:
                    lop_details.append(entry_data)
                if is_sl_hp:
                    sl_hp_details.append(entry_data)

        # Create Excel file with multiple sheets
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            # All Deduction Details - Entry Order
            if all_deduction_details:
                df_all = pd.DataFrame(all_deduction_details)
                df_all.to_excel(writer, sheet_name='All Deduction Details', index=False)
            else:
                pd.DataFrame([{'Message': 'No LOP/SL_HP entries found'}]).to_excel(writer, sheet_name='All Deduction Details', index=False)

            # Missing employees sheet
            if missing_employees:
                missing_df = pd.DataFrame({'Missing Employee Numbers': missing_employees})
                missing_df.to_excel(writer, sheet_name='Missing Employees', index=False)

            # LOP Details
            if lop_details:
                pd.DataFrame(lop_details).to_excel(writer, sheet_name='LOP Details', index=False)

            # SL HP Details
            if sl_hp_details:
                pd.DataFrame(sl_hp_details).to_excel(writer, sheet_name='SL HP Details', index=False)

        output.seek(0)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'salary_deduction_report_entry_order_{year}_{timestamp}.xlsx'

        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        flash(f'Error exporting deduction report: {str(e)}', 'error')
        return redirect(url_for('deduction_report'))

@app.route('/mark_entries_entered', methods=['POST'])
def mark_entries_entered():
    """Mark deduction entries as entered up to a specific entry ID"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        entry_id = request.form.get('entry_id', type=int)
        as_on_date_str = request.form.get('as_on_date', '')
        year = request.form.get('year', type=int)
        
        if not entry_id:
            flash('No entry selected', 'error')
            return redirect(url_for('deduction_report'))
        
        # Get the date range
        as_on_date = parse_any_date(as_on_date_str)
        start_date = date(year, 1, 1)
        
        # Get all deduction entries in the same order as the report
        leaves_query = LeaveEntry.query.filter(
            LeaveEntry.lvfrom >= start_date,
            LeaveEntry.lvfrom <= as_on_date
        ).order_by(LeaveEntry.id.asc()).all()
        
        # Mark all LOP/SL_HP entries up to the selected entry_id as entered
        marked_count = 0
        for leave in leaves_query:
            # Check if this is a LOP or SL_HP entry
            is_lop = leave.type.upper() == 'L'
            leave_type_upper = leave.type.upper()
            sltype_upper = (leave.sltype or '').upper()
            is_sl_hp = (leave_type_upper == 'SL_HP' or 
                       (leave_type_upper == 'S' and sltype_upper == 'H') or
                       (leave_type_upper == 'SL' and sltype_upper == 'H') or
                       (leave_type_upper == 'SL' and sltype_upper == 'HP') or
                       leave_type_upper == 'SLHP')
            
            if is_lop or is_sl_hp:
                # Check if employee exists
                emp = get_employee_by_number(leave.emp_no)
                if emp:
                    leave.is_entered = True
                    marked_count += 1
                    
                    # Stop when we reach the selected entry
                    if leave.id == entry_id:
                        break
        
        db.session.commit()
        flash(f'Successfully marked {marked_count} entries as entered', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error marking entries: {str(e)}', 'error')
        logging.error("Mark entries error: %s", e)
    
    return redirect(url_for('deduction_report'))

# ---------- NEW: Master Data Management Routes ----------

@app.route('/master_data', methods=['GET', 'POST'])
def master_data_management():
    """Master data management - view, edit, add employees"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    # Search functionality
    search_query = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)

    # Base query
    query = MasterData.query

    if search_query:
        query = query.filter(
            db.or_(
                MasterData.emp_no.ilike(f'%{search_query}%'),
                MasterData.name.ilike(f'%{search_query}%')
            )
        )

    # Pagination
    employees = query.order_by(MasterData.emp_no).paginate(
        page=page, per_page=20, error_out=False
    )

    return render_template('master_data_management.html', 
                         employees=employees, 
                         search_query=search_query)

@app.route('/master_data/add', methods=['GET', 'POST'])
def add_employee():
    """Add new employee to master data"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        try:
            emp_no = request.form['emp_no'].strip()
            name = request.form['name'].strip()
            doj_str = request.form['doj']
            emp_status = request.form['emp_status']

            # Parse opening balances
            pl = float(request.form['pl']) if request.form['pl'] else 0.0
            partial_pl = float(request.form['partial_pl_days']) if request.form['partial_pl_days'] else 0.0
            cl = float(request.form['cl']) if request.form['cl'] else 0.0
            sl = float(request.form['sl']) if request.form['sl'] else 0.0
            rh = float(request.form['rh']) if request.form['rh'] else 0.0
            lop = float(request.form['lop']) if request.form.get('lop') else 0.0

            # Validate required fields
            if not emp_no or not name or not doj_str:
                flash('Employee number, name, and DOJ are required', 'error')
                return render_template('add_edit_employee.html')

            # Check if employee already exists
            if MasterData.query.filter_by(emp_no=emp_no).first():
                flash(f'Employee {emp_no} already exists', 'error')
                return render_template('add_edit_employee.html')

            # Parse DOJ
            doj = parse_any_date(doj_str)
            if not doj:
                flash('Invalid date of joining format', 'error')
                return render_template('add_edit_employee.html')

            # Create new employee
            new_employee = MasterData(
                emp_no=emp_no,
                name=name,
                doj=doj,
                pl=pl,
                partial_pl_days=partial_pl,
                cl=cl,
                sl=sl,
                rh=rh,
                lop=lop
            )

            # Set employee status if model supports it
            if hasattr(new_employee, 'set_emp_status'):
                new_employee.set_emp_status(emp_status)

            db.session.add(new_employee)
            db.session.commit()

            # Create user account
            if not User.query.filter_by(emp_no=emp_no).first():
                new_user = User(
                    emp_no=emp_no,
                    name=name,
                    password_hash=generate_password_hash(emp_no)
                )
                db.session.add(new_user)
                db.session.commit()

            flash(f'Employee {emp_no} - {name} added successfully!', 'success')
            return redirect(url_for('master_data_management'))

        except Exception as e:
            db.session.rollback()
            flash(f'Error adding employee: {str(e)}', 'error')
            logging.error("Add employee error: %s", e)

    return render_template('add_edit_employee.html', employee=None)

@app.route('/master_data/edit/<emp_no>', methods=['GET', 'POST'])
def edit_employee(emp_no):
    """Edit existing employee master data"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    # Use enhanced lookup to find employee
    employee = get_employee_by_number(emp_no)
    if not employee:
        flash(f'Employee {emp_no} not found', 'error')
        return redirect(url_for('master_data_management'))

    if request.method == 'POST':
        try:
            # Update employee details
            employee.name = request.form['name'].strip()

            # Parse DOJ
            doj_str = request.form['doj']
            doj = parse_any_date(doj_str)
            if not doj:
                flash('Invalid date of joining format', 'error')
                return render_template('add_edit_employee.html', employee=employee)
            employee.doj = doj

            # Update opening balances
            employee.pl = float(request.form['pl']) if request.form['pl'] else 0.0
            employee.partial_pl_days = float(request.form['partial_pl_days']) if request.form['partial_pl_days'] else 0.0
            employee.cl = float(request.form['cl']) if request.form['cl'] else 0.0
            employee.sl = float(request.form['sl']) if request.form['sl'] else 0.0
            employee.rh = float(request.form['rh']) if request.form['rh'] else 0.0
            employee.lop = float(request.form['lop']) if request.form.get('lop') else 0.0

            # Update employee status if model supports it
            emp_status = request.form['emp_status']
            if hasattr(employee, 'set_emp_status'):
                employee.set_emp_status(emp_status)

            db.session.commit()

            # Update user name if user exists
            user = User.query.filter_by(emp_no=employee.emp_no).first()
            if user:
                user.name = employee.name
                db.session.commit()

            flash(f'Employee {employee.emp_no} - {employee.name} updated successfully!', 'success')
            return redirect(url_for('master_data_management'))

        except Exception as e:
            db.session.rollback()
            flash(f'Error updating employee: {str(e)}', 'error')
            logging.error("Edit employee error: %s", e)

    return render_template('add_edit_employee.html', employee=employee)

@app.route('/master_data/delete/<emp_no>', methods=['POST'])
def delete_employee(emp_no):
    """Delete employee from master data (admin only)"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    try:
        employee = get_employee_by_number(emp_no)
        if not employee:
            flash(f'Employee {emp_no} not found', 'error')
            return redirect(url_for('master_data_management'))

        # Check if employee has leave entries
        leave_count = LeaveEntry.query.filter(
            db.or_(
                LeaveEntry.emp_no == employee.emp_no,
                LeaveEntry.emp_no == f"{employee.emp_no}.0"
            )
        ).count()

        if leave_count > 0:
            flash(f'Cannot delete employee {emp_no} - has {leave_count} leave entries. Delete leave entries first.', 'error')
            return redirect(url_for('master_data_management'))

        emp_name = employee.name

        # Delete user account if exists
        user = User.query.filter_by(emp_no=employee.emp_no).first()
        if user:
            db.session.delete(user)

        # Delete employee
        db.session.delete(employee)
        db.session.commit()

        flash(f'Employee {emp_no} - {emp_name} deleted successfully!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting employee: {str(e)}', 'error')
        logging.error("Delete employee error: %s", e)

    return redirect(url_for('master_data_management'))

# Continue with remaining routes...


@app.route('/api/employee_summary/<emp_no>')
def get_employee_summary(emp_no):
    """Get quick summary of employee for master data management"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        employee = get_employee_by_number(emp_no)
        if not employee:
            return jsonify({'error': 'Employee not found'}), 404

        # Get leave summary for current year
        calculator = LeaveCalculator()
        as_on_date = date.today()
        result = calculator.calculate_leave_summary(emp_no, as_on_date)

        summary = {
            'emp_no': employee.emp_no,
            'name': employee.name,
            'doj': employee.doj.strftime('%d-%m-%Y'),
            'emp_status': employee.get_emp_status() if hasattr(employee, 'get_emp_status') else 'C',
            'opening_balances': {
                'pl': employee.pl,
                'partial_pl': employee.partial_pl_days,
                'cl': employee.cl,
                'sl': employee.sl,
                'rh': employee.rh
            }
        }

        if result['success']:
            summary['current_balances'] = result['data']['closing_balances']
            summary['leave_entries_count'] = len(result['data']['leave_details'])

        return jsonify({'success': True, 'data': summary})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ---------- Enhanced Bulk Reports ----------

@app.route('/bulk_summary', methods=['GET', 'POST'])
def bulk_summary():
    """Enhanced bulk summary with detailed leave tables"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        employee_list = request.form.get('employee_list', '').strip()
        as_on_date_str = request.form['as_on_date']

        try:
            as_on_date = parse_any_date(as_on_date_str)
            if as_on_date is None:
                raise ValueError("Invalid date")

            # Parse employee numbers
            emp_numbers = []
            if employee_list:
                raw_emps = employee_list.replace('\n', ',').split(',')
                for emp in raw_emps:
                    emp = emp.strip()
                    if emp:
                        emp_numbers.append(emp)

            if not emp_numbers:
                flash('Please enter at least one employee number', 'error')
                return render_template('bulk_summary.html', as_on_date=as_on_date)

            calculator = LeaveCalculator()
            bulk_results = []

            for emp_no in emp_numbers:
                result = calculator.calculate_leave_summary(emp_no, as_on_date)
                if result['success']:
                    data = result['data']

                    # Extract LOP and SL_HP details
                    lop_entries = []
                    sl_hp_entries = []

                    for leave in data['leave_details']:
                        if leave['type'] == 'L':  # LOP
                            lop_entries.append({
                                'from': leave['lv_from'],
                                'to': leave['lv_to'] or leave['lv_from'],
                                'days': leave['days'],
                                'reason': leave['reason']
                            })
                        elif leave['type'] in ['SL_HP', 'S'] and leave.get('sl_type') == 'H':  # SL Half Pay
                            sl_hp_entries.append({
                                'from': leave['lv_from'],
                                'to': leave['lv_to'] or leave['lv_from'],
                                'days': leave['days'],
                                'reason': leave['reason']
                            })

                    bulk_results.append({
                        'emp_no': emp_no,
                        'emp_name': data['emp_name'],
                        'emp_status': data['emp_status'],
                        'doj': data['doj'],
                        'closing_balances': data['closing_balances'],
                        'used_balances': data['used_balances'],
                        'opening_balances': data['opening_balances'],
                        'other_details': data['other_details'],
                        'leave_details': data['leave_details'],
                        'lop_entries': lop_entries,
                        'sl_hp_entries': sl_hp_entries,
                        'total_lop_days': sum(entry['days'] for entry in lop_entries),
                        'total_sl_hp_days': sum(entry['days'] for entry in sl_hp_entries),
                        'leave_count': len(data['leave_details'])
                    })
                else:
                    bulk_results.append({
                        'emp_no': emp_no,
                        'error': result['error']
                    })

            return render_template('bulk_summary.html',
                                 bulk_results=bulk_results,
                                 employee_list=employee_list,
                                 as_on_date=as_on_date)

        except ValueError:
            flash('Invalid date format', 'error')
        except Exception as e:
            flash(f'Error calculating bulk summary: {str(e)}', 'error')
            logging.error("Bulk summary error: %s", e)

    return render_template('bulk_summary.html', as_on_date=date.today())

@app.route('/export_bulk_excel')
def export_bulk_excel():
    """Export bulk summary data to Excel"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        employee_list = request.args.get('employees', '')
        as_on_date_str = request.args.get('as_on_date', '')

        if not employee_list or not as_on_date_str:
            flash('Missing parameters for bulk export', 'error')
            return redirect(url_for('bulk_summary'))

        as_on_date = parse_any_date(as_on_date_str)
        emp_numbers = [emp.strip() for emp in employee_list.replace('\n', ',').split(',') if emp.strip()]

        calculator = LeaveCalculator()

        # Prepare data for Excel sheets
        summary_data = []
        leave_details_data = []
        lop_data = []
        sl_hp_data = []

        for emp_no in emp_numbers:
            result = calculator.calculate_leave_summary(emp_no, as_on_date)
            if result['success']:
                data = result['data']

                # Summary sheet data
                summary_data.append({
                    'Emp No': emp_no,
                    'Name': data['emp_name'],
                    'DOJ': data['doj'],
                    'Status': data['emp_status'],
                    'PL Opening': data['opening_balances']['pl'],
                    'PL Partial Opening': data['opening_balances']['pl_part'],
                    'CL Opening': data['opening_balances']['cl'],
                    'SL Opening': data['opening_balances']['sl'],
                    'RH Opening': data['opening_balances']['rh'],
                    'PL Used': data['used_balances']['pl'],
                    'CL Used': data['used_balances']['cl'],
                    'SL Used': data['used_balances']['sl'],
                    'RH Used': data['used_balances']['rh'],
                    'PL Closing': data['closing_balances']['pl'],
                    'PL Partial Closing': data['closing_balances']['pl_part'],
                    'CL Closing': data['closing_balances']['cl'],
                    'SL Closing': data['closing_balances']['sl'],
                    'RH Closing': data['closing_balances']['rh'],
                    'LOP Days': data['other_details']['lop_days'],
                    'Leave Entries': len(data['leave_details'])
                })

                # Detailed leave entries
                for leave in data['leave_details']:
                    leave_details_data.append({
                        'Emp No': emp_no,
                        'Name': data['emp_name'],
                        'From': leave['lv_from'],
                        'To': leave['lv_to'] or leave['lv_from'],
                        'Days': leave['days'],
                        'Type': leave['type'],
                        'SL Type': leave.get('sl_type', ''),
                        'Session': leave.get('session', ''),
                        'Reason': leave['reason']
                    })

                # LOP details
                for leave in data['leave_details']:
                    if leave['type'] == 'L':
                        lop_data.append({
                            'Emp No': emp_no,
                            'Name': data['emp_name'],
                            'From': leave['lv_from'],
                            'To': leave['lv_to'] or leave['lv_from'],
                            'Days': leave['days'],
                            'Reason': leave['reason']
                        })

                # SL Half Pay details
                for leave in data['leave_details']:
                    if leave['type'] in ['SL_HP', 'S'] and leave.get('sl_type') == 'H':
                        sl_hp_data.append({
                            'Emp No': emp_no,
                            'Name': data['emp_name'],
                            'From': leave['lv_from'],
                            'To': leave['lv_to'] or leave['lv_from'],
                            'Days': leave['days'],
                            'Reason': leave['reason']
                        })

        # Create Excel file
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            # Summary sheet
            if summary_data:
                pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)

            # Detailed leave entries
            if leave_details_data:
                pd.DataFrame(leave_details_data).to_excel(writer, sheet_name='All Leave Details', index=False)

            # LOP sheet
            if lop_data:
                pd.DataFrame(lop_data).to_excel(writer, sheet_name='LOP Details', index=False)
            else:
                pd.DataFrame([{'Message': 'No LOP entries found'}]).to_excel(writer, sheet_name='LOP Details', index=False)

            # SL Half Pay sheet
            if sl_hp_data:
                pd.DataFrame(sl_hp_data).to_excel(writer, sheet_name='SL Half Pay', index=False)
            else:
                pd.DataFrame([{'Message': 'No SL Half Pay entries found'}]).to_excel(writer, sheet_name='SL Half Pay', index=False)

        output.seek(0)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'bulk_leave_report_detailed_{timestamp}.xlsx'

        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        flash(f'Error exporting bulk data: {str(e)}', 'error')
        return redirect(url_for('bulk_summary'))

# ---------- Leave Entry API Endpoints ----------

@app.route('/api/leaves/<emp_no>')
def get_employee_leaves(emp_no):
    """Get all leaves for an employee"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # Normalize employee number first
        emp_no_normalized = normalize_emp_no(emp_no)
        
        # Get ALL leaves for this employee using enhanced lookup (check both formats for compatibility)
        leaves_str = LeaveEntry.query.filter_by(emp_no=emp_no_normalized).all()
        leaves_float = LeaveEntry.query.filter_by(emp_no=f"{emp_no_normalized}.0").all()
        all_leaves = leaves_str + leaves_float

        # Remove duplicates based on ID
        seen_ids = set()
        unique_leaves = []
        for leave in all_leaves:
            if leave.id not in seen_ids:
                unique_leaves.append(leave)
                seen_ids.add(leave.id)

        # Sort by date ascending
        unique_leaves.sort(key=lambda x: x.lvfrom)

        leaves_data = []
        for idx, leave in enumerate(unique_leaves, 1):
            leaves_data.append({
                'id': leave.id,
                'sl_no': idx,
                'emp_no': leave.emp_no,
                'lvfrom': leave.lvfrom.strftime('%Y-%m-%d'),
                'lvto': leave.lvto.strftime('%Y-%m-%d') if leave.lvto else '',
                'session': leave.session or '',
                'type': leave.type,
                'sltype': leave.sltype or '',
                'reason': leave.reason or ''
            })

        return jsonify({'success': True, 'leaves': leaves_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/leaves', methods=['POST'])
def create_leave():
    """Create a new leave entry"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()

        emp_no_raw = data.get('emp_no', '').strip()
        emp_no = normalize_emp_no(emp_no_raw)  # Normalize employee number
        lvfrom_str = data.get('lvfrom', '').strip()
        lvto_str = data.get('lvto', '').strip()
        leave_type = data.get('type', '').strip().upper()
        
        # Normalize "CL HALF DAY" or similar variations to "CL_HALFDAY"
        if 'CL' in leave_type and 'HALF' in leave_type:
            leave_type = 'CL_HALFDAY'
        
        session_val = data.get('session', '').strip().upper()
        sltype_val = data.get('sltype', '').strip().upper()
        reason = data.get('reason', '').strip()

        # Validation
        if not emp_no or not lvfrom_str or not leave_type:
            return jsonify({'error': 'Employee number, from date, and leave type are required'}), 400

        if leave_type not in VALID_LEAVE_TYPES:
            return jsonify({'error': f'Invalid leave type. Must be one of: {", ".join(VALID_LEAVE_TYPES)}'}), 400

        # Parse dates
        lvfrom = datetime.strptime(lvfrom_str, '%Y-%m-%d').date()
        lvto = datetime.strptime(lvto_str, '%Y-%m-%d').date() if lvto_str else lvfrom

        # Date validation
        if lvfrom > lvto:
            return jsonify({'error': 'From date cannot be greater than To date'}), 400

        # Check for overlaps (except E type)
        if leave_type != 'E':
            has_overlap, overlap_msg = check_leave_overlap(emp_no, lvfrom, lvto)
            if has_overlap:
                return jsonify({'error': f'Leave overlap detected: {overlap_msg}'}), 400

        # Check half-day CL occasions limit (max 6 occasions)
        if leave_type == 'CL_HALFDAY' or (leave_type == 'CL' and session_val in ['F', 'A']):
            # Count existing half-day CL occasions
            # Only consider half-day CL occasions within the same calendar year as the requested lvfrom
            year_start = date(lvfrom.year, 1, 1)
            year_end = date(lvfrom.year, 12, 31)

            query1 = LeaveEntry.query.filter_by(emp_no=str(emp_no)).filter(LeaveEntry.lvfrom >= year_start, LeaveEntry.lvfrom <= year_end)
            query2 = LeaveEntry.query.filter_by(emp_no=f"{emp_no}.0").filter(LeaveEntry.lvfrom >= year_start, LeaveEntry.lvfrom <= year_end)
            all_leaves = query1.all() + query2.all()

            halfday_cl_count = 0
            for leave in all_leaves:
                leave_type_upper = leave.type.upper()
                if leave_type_upper == 'CL_HALFDAY':
                    halfday_cl_count += 1
                elif leave_type_upper == 'CL' and leave.session and leave.session.upper() in ['F', 'A']:
                    halfday_cl_count += 1

            if halfday_cl_count >= 6:
                return jsonify({'error': f'Half-day CL occasions limit exceeded. Maximum 6 occasions allowed per year. Current count: {halfday_cl_count}'}), 400

        # Set default reason if not provided
        if not reason or reason == 'auto':
            reason = REASON_DEFAULTS.get(leave_type, '')

        # Check for negative balance warning
        has_warning, warning_msg = check_negative_balance_warning(emp_no, lvfrom, lvto, leave_type, session_val)

        # Create leave entry
        leave_entry = LeaveEntry(
            emp_no=emp_no,
            lvfrom=lvfrom,
            lvto=lvto,
            session=session_val if session_val else None,
            type=leave_type,
            sltype=sltype_val if sltype_val else None,
            reason=reason
        )

        db.session.add(leave_entry)
        db.session.commit()

        response_data = {'success': True, 'id': leave_entry.id}
        if has_warning:
            response_data['warning'] = warning_msg

        return jsonify(response_data)

    except ValueError as e:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/leaves/<int:leave_id>', methods=['PUT'])
def update_leave(leave_id):
    """Update an existing leave entry"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        leave_entry = LeaveEntry.query.get_or_404(leave_id)
        data = request.get_json()

        lvfrom_str = data.get('lvfrom', '').strip()
        lvto_str = data.get('lvto', '').strip()
        leave_type = data.get('type', '').strip().upper()
        
        # Normalize "CL HALF DAY" or similar variations to "CL_HALFDAY"
        if 'CL' in leave_type and 'HALF' in leave_type:
            leave_type = 'CL_HALFDAY'
        
        session_val = data.get('session', '').strip().upper()
        sltype_val = data.get('sltype', '').strip().upper()
        reason = data.get('reason', '').strip()

        # Parse dates
        lvfrom = datetime.strptime(lvfrom_str, '%Y-%m-%d').date() if lvfrom_str else leave_entry.lvfrom
        lvto = datetime.strptime(lvto_str, '%Y-%m-%d').date() if lvto_str else (leave_entry.lvto or leave_entry.lvfrom)

        # Date validation
        if lvfrom > lvto:
            return jsonify({'error': 'From date cannot be greater than To date'}), 400

        # Check for overlaps (except E type)
        if leave_type != 'E':
            has_overlap, overlap_msg = check_leave_overlap(leave_entry.emp_no, lvfrom, lvto, exclude_id=leave_id)
            if has_overlap:
                return jsonify({'error': f'Leave overlap detected: {overlap_msg}'}), 400

        # Check half-day CL occasions limit (max 6 occasions)
        if leave_type == 'CL_HALFDAY' or (leave_type == 'CL' and session_val in ['F', 'A']):
            # Count existing half-day CL occasions (excluding current leave being edited)
            # Only consider half-day CL occasions within the same calendar year as the requested lvfrom
            year_start = date(lvfrom.year, 1, 1)
            year_end = date(lvfrom.year, 12, 31)

            query1 = LeaveEntry.query.filter_by(emp_no=str(leave_entry.emp_no)).filter(LeaveEntry.id != leave_id).filter(LeaveEntry.lvfrom >= year_start, LeaveEntry.lvfrom <= year_end)
            query2 = LeaveEntry.query.filter_by(emp_no=f"{leave_entry.emp_no}.0").filter(LeaveEntry.id != leave_id).filter(LeaveEntry.lvfrom >= year_start, LeaveEntry.lvfrom <= year_end)
            all_leaves = query1.all() + query2.all()

            halfday_cl_count = 0
            for leave in all_leaves:
                leave_type_upper = leave.type.upper()
                if leave_type_upper == 'CL_HALFDAY':
                    halfday_cl_count += 1
                elif leave_type_upper == 'CL' and leave.session and leave.session.upper() in ['F', 'A']:
                    halfday_cl_count += 1

            if halfday_cl_count >= 6:
                return jsonify({'error': f'Half-day CL occasions limit exceeded. Maximum 6 occasions allowed per year. Current count: {halfday_cl_count}'}), 400

        # Set default reason if requested
        if reason == 'auto':
            reason = REASON_DEFAULTS.get(leave_type, '')

        # Check for negative balance warning
        has_warning, warning_msg = check_negative_balance_warning(leave_entry.emp_no, lvfrom, lvto, leave_type, session_val, exclude_id=leave_id)

        # Update fields
        leave_entry.lvfrom = lvfrom
        leave_entry.lvto = lvto
        leave_entry.type = leave_type
        leave_entry.session = session_val if session_val else None
        leave_entry.sltype = sltype_val if sltype_val else None
        if reason is not None:
            leave_entry.reason = reason

        db.session.commit()

        response_data = {'success': True}
        if has_warning:
            response_data['warning'] = warning_msg

        return jsonify(response_data)

    except ValueError as e:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/leaves/<int:leave_id>', methods=['DELETE'])
def delete_leave(leave_id):
    """Delete a leave entry"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        leave_entry = LeaveEntry.query.get_or_404(leave_id)
        db.session.delete(leave_entry)
        db.session.commit()

        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ---------- Other Routes ----------

@app.route('/export_excel')
def export_excel():
    """Export both master data and leave entries to Excel - with exact column order as requested"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        # Get master data - PRESERVE ENTRY ORDER (no sorting by emp_no)
        master_data = MasterData.query.order_by(MasterData.id.asc()).all()  # Use ID order to preserve entry order
        master_rows = []

        for emp in master_data:
            master_rows.append({
                'emp_no': emp.emp_no,
                'name': emp.name,
                'doj': emp.doj.strftime('%Y-%m-%d'),
                'pl': int(emp.pl),
                'partial_pl_days': int(emp.partial_pl_days),
                'cl': int(emp.cl),
                'sl': int(emp.sl),
                'rh': int(emp.rh),
                'lop': int(emp.lop),  # ADDED - This was missing!
                'l': emp.get_emp_status() if hasattr(emp, 'get_emp_status') else 'C'
            })

        # Get leave entries - PRESERVE ENTRY ORDER (no sorting by emp_no, lvfrom)
        leave_entries = LeaveEntry.query.order_by(LeaveEntry.id.asc()).all()  # Use ID order to preserve entry order
        leave_rows = []

        for leave in leave_entries:
            # Calculate days using the same logic as in routes.py line 265
            if leave.lvto and leave.lvfrom:
                days = (leave.lvto - leave.lvfrom).days + 1
            else:
                # If lvto is null, calculate as single day
                days = 1

            # Create row with YOUR EXACT COLUMN ORDER: emp_no, type, lvfrom, session, lvto, days, sltype, reason
            leave_rows.append({
                'emp_no': leave.emp_no,
                'type': leave.type,
                'lvfrom': leave.lvfrom.strftime('%Y-%m-%d'),
                'session': leave.session or '',
                'lvto': leave.lvto.strftime('%Y-%m-%d') if leave.lvto else '',
                'days': days,  # CALCULATED field
                'sltype': leave.sltype or '',
                'reason': leave.reason or ''
            })

        # Create Excel file in memory
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            # Master sheet - using DataFrame with explicit column order
            master_df = pd.DataFrame(master_rows)

            # Reorder columns to match import order exactly
            master_column_order = ['emp_no', 'name', 'doj', 'pl', 'partial_pl_days', 'cl', 'sl', 'rh', 'lop', 'l']
            master_df = master_df[master_column_order]
            master_df.to_excel(writer, sheet_name='master', index=False)

            # Leave entry sheet - using DataFrame with YOUR EXACT COLUMN ORDER
            leave_df = pd.DataFrame(leave_rows)

            # YOUR EXACT REQUESTED ORDER: emp_no, type, lvfrom, session, lvto, days, sltype, reason
            leave_column_order = ['emp_no', 'type', 'lvfrom', 'session', 'lvto', 'days', 'sltype', 'reason']
            # Only include columns that exist in the dataframe
            leave_column_order = [col for col in leave_column_order if col in leave_df.columns]
            leave_df = leave_df[leave_column_order]
            leave_df.to_excel(writer, sheet_name='leaveentry', index=False)

        output.seek(0)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'leave_management_export_{timestamp}.xlsx'

        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        flash(f'Error exporting to Excel: {str(e)}', 'error')
        return redirect(url_for('entry'))

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if new_password != confirm_password:
            flash('New passwords do not match', 'error')
            return render_template('change_password.html')

        user = User.query.get(session['user_id'])
        if not user or not user.check_password(current_password):
            flash('Current password is incorrect', 'error')
            return render_template('change_password.html')

        user.set_password(new_password)
        db.session.commit()
        flash('Password changed successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('change_password.html')

@app.route('/reset_password/<int:user_id>', methods=['POST'])
def reset_password(user_id):
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    new_password = request.form['new_password']
    user = User.query.get_or_404(user_id)
    user.set_password(new_password)
    db.session.commit()

    flash(f'Password reset for {user.emp_no} successfully!', 'success')
    return redirect(url_for('admin'))

@app.route('/api/employees')
def api_employees():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    employees = MasterData.query.all()
    return jsonify([{'emp_no': emp.emp_no, 'name': emp.name} for emp in employees])

@app.route('/api/employee_search')
def employee_search():
    """Search employees by name or emp_no for autocomplete"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        query = request.args.get('q', '').strip()
        if not query or len(query) < 2:
            return jsonify([])

        # Search by name or emp_no
        employees = MasterData.query.filter(
            db.or_(
                MasterData.name.ilike(f'%{query}%'),
                MasterData.emp_no.ilike(f'%{query}%')
            )
        ).limit(20).all()

        results = []
        for emp in employees:
            results.append({
                'emp_no': emp.emp_no,
                'name': emp.name,
                'display': f"{emp.emp_no} - {emp.name}"
            })

        return jsonify(results)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== ATTENDANCE TRACKING ROUTES ==========

@app.route('/attendance')
def attendance():
    """View attendance tracking grid"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if not session.get('is_admin'):
        flash('Only admin can access attendance tracking', 'error')
        return redirect(url_for('dashboard'))
    
    # Get current year or from query parameter
    current_year = datetime.now().year
    year = request.args.get('year', current_year, type=int)
    
    # Get all departments ordered by sort_order and name
    departments = AttendanceDepartment.query.order_by(AttendanceDepartment.sort_order, AttendanceDepartment.name).all()
    
    # Get all attendance indices for the selected year
    indices = AttendanceIndex.query.filter_by(year=year).all()
    
    # Create a dictionary for quick lookup: {(dept_id, month): index_value}
    index_dict = {}
    for idx in indices:
        key = (idx.department_id, idx.month)
        index_dict[key] = idx.index_value
    
    # Month names
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    
    # Helper function to extract numeric value from index (handles "170a" style)
    import re
    def extract_numeric(value):
        if not value:
            return 0
        match = re.match(r'^(\d+)', str(value))
        if match:
            return int(match.group(1))
        return 0
    
    # Calculate GLOBAL next index number (across all departments and months)
    global_max_numeric = 0
    for idx in indices:
        if idx.index_value:
            numeric_val = extract_numeric(idx.index_value)
            if numeric_val > global_max_numeric:
                global_max_numeric = numeric_val
    
    global_next_index = global_max_numeric + 1 if global_max_numeric > 0 else 1
    
    # Prepare data for template and calculate department progress
    grid_data = []
    dept_progress = []
    
    for dept in departments:
        row = {'dept': dept, 'indices': []}
        latest_month = None
        latest_month_name = None
        
        for month_num in range(1, 13):
            key = (dept.id, month_num)
            index_value = index_dict.get(key, '')
            row['indices'].append(index_value)
            
            # Track latest month with a value
            if index_value:
                if latest_month is None or month_num > latest_month:
                    latest_month = month_num
                    latest_month_name = months[month_num - 1]
        
        # Add to department progress report
        dept_progress.append({
            'dept_id': dept.id,
            'dept_name': dept.name,
            'latest_month': latest_month_name if latest_month_name else 'No submissions',
            'latest_month_num': latest_month if latest_month else 0
        })
        
        grid_data.append(row)
    
    return render_template('attendance.html', 
                         grid_data=grid_data,
                         months=months,
                         year=year,
                         departments=departments,
                         dept_progress=dept_progress,
                         global_next_index=global_next_index)

@app.route('/attendance/update_index', methods=['POST'])
def update_attendance_index():
    """Update a single index value"""
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        dept_id = request.form.get('dept_id', type=int)
        year = request.form.get('year', type=int)
        month = request.form.get('month', type=int)
        index_value = request.form.get('index_value', '').strip()
        
        # Find existing index or create new one
        idx = AttendanceIndex.query.filter_by(
            department_id=dept_id,
            year=year,
            month=month
        ).first()
        
        if idx:
            if index_value:
                idx.index_value = index_value
                idx.updated_at = datetime.utcnow()
            else:
                # Delete if empty value
                db.session.delete(idx)
        else:
            if index_value:
                idx = AttendanceIndex(
                    department_id=dept_id,
                    year=year,
                    month=month,
                    index_value=index_value
                )
                db.session.add(idx)
        
        db.session.commit()
        # Recalculate global next index for the given year and return it
        try:
            import re
            indices_for_year = AttendanceIndex.query.filter_by(year=year).all()
            max_numeric = 0
            for aidx in indices_for_year:
                if aidx.index_value:
                    m = re.match(r'^(\d+)', str(aidx.index_value))
                    if m:
                        val = int(m.group(1))
                        if val > max_numeric:
                            max_numeric = val
            next_index = max_numeric + 1 if max_numeric > 0 else 1
        except Exception:
            # Fallback in case anything goes wrong
            next_index = None

        # Determine latest submitted month for this department after commit
        try:
            # Query months for this dept/year and find the max month with a value
            idxs = AttendanceIndex.query.filter_by(department_id=dept_id, year=year).all()
            latest_month_num = 0
            latest_month_name = None
            for i in idxs:
                if i.index_value:
                    if i.month and (i.month > latest_month_num):
                        latest_month_num = i.month
            if latest_month_num:
                months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
                latest_month_name = months[latest_month_num - 1]
            else:
                latest_month_name = 'No submissions'

            return jsonify({
                'success': True,
                'next_index': next_index,
                'dept_progress_update': {
                    'dept_id': dept_id,
                    'latest_month_num': latest_month_num,
                    'latest_month': latest_month_name
                }
            })
        except Exception:
            return jsonify({'success': True, 'next_index': next_index})
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating attendance index: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/attendance/upload', methods=['POST'])
def upload_attendance_excel():
    """Upload Excel file with departments and indices"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Unauthorized', 'error')
        return redirect(url_for('attendance'))
    
    if 'file' not in request.files:
        flash('No file uploaded', 'error')
        return redirect(url_for('attendance'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('attendance'))
    
    try:
        # Read Excel file
        df = pd.read_excel(file)
        
        # Expected format: First column is department name, subsequent columns are months
        # First row should be header with month names
        
        if df.empty or len(df.columns) < 2:
            flash('Invalid file format. Expected: Department name in first column, months in subsequent columns', 'error')
            return redirect(url_for('attendance'))
        
        # Get year from form or use current year
        year = int(request.form.get('year', datetime.now().year))
        
        # Month mapping
        month_map = {
            'jan': 1, 'january': 1,
            'feb': 2, 'february': 2,
            'mar': 3, 'march': 3,
            'apr': 4, 'april': 4,
            'may': 5,
            'jun': 6, 'june': 6,
            'jul': 7, 'july': 7,
            'aug': 8, 'august': 8,
            'sep': 9, 'september': 9, 'sept': 9,
            'oct': 10, 'october': 10,
            'nov': 11, 'november': 11,
            'dec': 12, 'december': 12
        }
        
        # Process departments and indices
        departments_added = 0
        indices_added = 0
        
        for index, row in df.iterrows():
            # Get department name from first column
            dept_name = str(row.iloc[0]).strip()
            
            if not dept_name or dept_name.lower() in ['nan', 'none', '']:
                continue
            
            # Check if department exists, if not create it
            dept = AttendanceDepartment.query.filter_by(name=dept_name).first()
            if not dept:
                dept = AttendanceDepartment(name=dept_name, sort_order=index)
                db.session.add(dept)
                db.session.flush()  # Get the ID
                departments_added += 1
            
            # Process month columns
            for col_idx, col_name in enumerate(df.columns[1:]):
                col_name_clean = str(col_name).strip().lower()
                
                # Try to map column name to month number
                month_num = month_map.get(col_name_clean)
                
                if month_num:
                    # Get index value
                    index_val = str(row.iloc[col_idx + 1]).strip()
                    
                    if index_val and index_val.lower() not in ['nan', 'none', '']:
                        # Check if index already exists
                        idx = AttendanceIndex.query.filter_by(
                            department_id=dept.id,
                            year=year,
                            month=month_num
                        ).first()
                        
                        if idx:
                            idx.index_value = index_val
                            idx.updated_at = datetime.utcnow()
                        else:
                            idx = AttendanceIndex(
                                department_id=dept.id,
                                year=year,
                                month=month_num,
                                index_value=index_val
                            )
                            db.session.add(idx)
                            indices_added += 1
        
        db.session.commit()
        flash(f'Successfully uploaded! Departments added/updated: {departments_added}, Indices added: {indices_added}', 'success')
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error uploading attendance Excel: {str(e)}")
        flash(f'Error uploading file: {str(e)}', 'error')
    
    return redirect(url_for('attendance'))


@app.route('/rollout', methods=['GET', 'POST'])
def rollout():
    """Rollout feature: compute closing PL/SL as on date and produce master.csv format file with updated balances

    Notes/assumptions:
    - Uses LeaveCalculator.calculate_leave_summary(emp_no, as_on_date) to obtain closing PL and PL partial.
    - CL will be set to 12 for all employees and RH to 2 for all employees in the output file.
    - LOP for current year is calculated by summing L-type leave days where lvfrom is in the same year as as_on_date.
    - SL is computed using this assumption (please confirm if you want a different formula):
        SL = max(0, ((as_on_date - Jan 1, 2025).days - lop_days_current_year) / 12)
      This is a days-based heuristic that divides remaining days by 12; result rounded to 2 decimals.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        as_on_date_str = request.form.get('as_on_date', '').strip()
        try:
            as_on_date = parse_any_date(as_on_date_str)
            if as_on_date is None:
                raise ValueError('Invalid date')

            # Prepare rollout CSV rows
            employees = MasterData.query.order_by(MasterData.emp_no).all()
            rows = []
            calculator = LeaveCalculator()

            for emp in employees:
                try:
                    # Get closing balances via calculator
                    result = calculator.calculate_leave_summary(emp.emp_no, as_on_date)
                    if result['success']:
                        closing = result['data']['closing_balances']
                        pl_val = closing.get('pl', 0)
                        pl_part = closing.get('pl_part', 0)
                    else:
                        # Fallback to employee stored values
                        pl_val = emp.pl if hasattr(emp, 'pl') else 0
                        pl_part = emp.partial_pl_days if hasattr(emp, 'partial_pl_days') else 0

                    # Calculate LOP for the current year (not cumulative)
                    year_start = date(as_on_date.year, 1, 1)
                    year_end = date(as_on_date.year, 12, 31)

                    lop_days = 0.0
                    # Query leaves of type 'L' for this emp where lvfrom is in the current year
                    leaves_q1 = LeaveEntry.query.filter_by(emp_no=emp.emp_no).filter(LeaveEntry.lvfrom >= year_start, LeaveEntry.lvfrom <= as_on_date).all()
                    leaves_q2 = LeaveEntry.query.filter_by(emp_no=f"{emp.emp_no}.0").filter(LeaveEntry.lvfrom >= year_start, LeaveEntry.lvfrom <= as_on_date).all()
                    leaves_all = leaves_q1 + leaves_q2
                    for leave in leaves_all:
                        if leave.type and leave.type.upper() == 'L':
                            lvfrom = leave.lvfrom
                            lvto = leave.lvto or leave.lvfrom
                            if lvto > as_on_date:
                                lvto = as_on_date
                            days = (lvto - lvfrom).days + 1
                            if leave.session and leave.session.upper() in ['F', 'A']:
                                days = 0.5
                            lop_days += days

                    # Compute SL using the assumed formula and add the closing SL balance
                    base_date = date(2025, 1, 1)
                    days_since_base = (as_on_date - base_date).days if as_on_date >= base_date else 0
                    computed_sl_increment = max(0.0, (days_since_base - lop_days) / 12.0)

                    # Use closing SL from calculator when available, otherwise fallback to stored employee SL
                    if result['success']:
                        sl_closing = closing.get('sl', 0)
                    else:
                        sl_closing = emp.sl if hasattr(emp, 'sl') else 0

                    # Add closing SL and computed increment, then floor decimals and cap at 240
                    combined_sl = float(sl_closing) + float(computed_sl_increment)
                    # Ignore decimals (take floor towards zero)
                    sl_val = int(combined_sl)
                    # Enforce non-negative and cap at 240
                    if sl_val < 0:
                        sl_val = 0
                    if sl_val > 240:
                        sl_val = 240

                    # CL and RH fixed values
                    cl_val = 12
                    rh_val = 2

                    # LOP in master file: prefer cumulative LOP from calculator if available
                    # The calculator returns cumulative_lop under data['opening_balances']['cumulative_lop']
                    lop_master = 0
                    if result and result.get('success'):
                        data_map = result['data'] or {}
                        opening_map = data_map.get('opening_balances', {}) or {}
                        # Prefer cumulative_lop (opening + lop_days) when present
                        if opening_map.get('cumulative_lop') is not None:
                            try:
                                lop_master = int(opening_map.get('cumulative_lop', 0))
                            except Exception:
                                lop_master = int(float(opening_map.get('cumulative_lop', 0))) if opening_map.get('cumulative_lop') is not None else 0
                        else:
                            # Fallback to other_details.lop_days (year-to-date LOP) if cumulative not present
                            other = data_map.get('other_details', {}) or {}
                            lop_master = int(other.get('lop_days', 0)) if other.get('lop_days') is not None else (int(emp.lop) if hasattr(emp, 'lop') and emp.lop is not None else 0)
                    else:
                        lop_master = int(emp.lop) if hasattr(emp, 'lop') and emp.lop is not None else 0

                    emp_status = emp.get_emp_status() if hasattr(emp, 'get_emp_status') else 'C'

                    rows.append({
                        'emp_no': emp.emp_no,
                        'name': emp.name,
                        'doj': emp.doj.strftime('%Y-%m-%d') if emp.doj else '',
                        'pl': float(pl_val),
                        'partial_pl_days': float(pl_part),
                        'cl': cl_val,
                        'sl': sl_val,
                        'rh': rh_val,
                        'lop': lop_master,
                        'l': emp_status
                    })

                except Exception as e:
                    logging.error(f"Error processing employee {emp.emp_no} during rollout: {e}")
                    continue

            # Create CSV in memory with exact column order
            import pandas as pd
            output = io.BytesIO()
            df = pd.DataFrame(rows)
            master_column_order = ['emp_no', 'name', 'doj', 'pl', 'partial_pl_days', 'cl', 'sl', 'rh', 'lop', 'l']
            df = df[master_column_order]

            # Write CSV to BytesIO
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False)
            csv_bytes = csv_buffer.getvalue().encode('utf-8')
            output.write(csv_bytes)
            output.seek(0)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'master_rollout_{as_on_date.strftime("%Y%m%d")}_{timestamp}.csv'

            return send_file(
                output,
                as_attachment=True,
                download_name=filename,
                mimetype='text/csv'
            )

        except ValueError:
            flash('Invalid date format', 'error')
        except Exception as e:
            flash(f'Error during rollout: {str(e)}', 'error')
            logging.error('Rollout error: %s', e)

    return render_template('rollout.html')


@app.route('/availed_report', methods=['GET', 'POST'])
def availed_report():
    """Availed Leaves report page (form + generate) similar to deduction_report"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # default params
    as_on_date = date.today()
    year = date.today().year

    if request.method == 'POST':
        as_on_date_str = request.form.get('as_on_date', '').strip()
        year = int(request.form.get('year', date.today().year))
        try:
            as_on_date = parse_any_date(as_on_date_str)
            if as_on_date is None:
                raise ValueError('Invalid date')

            # Generate summary data (lightweight) to show on the page
            employees = MasterData.query.order_by(MasterData.emp_no).all()
            calculator = LeaveCalculator()
            rows = []
            for emp in employees:
                try:
                    res = calculator.calculate_leave_summary(emp.emp_no, as_on_date)
                    if res['success']:
                        data = res['data']
                        rows.append({
                            'emp_no': emp.emp_no,
                            'name': data.get('emp_name', emp.name),
                            'pl_availed': int(data.get('used_balances', {}).get('pl', 0)),
                            'sl_availed': int(data.get('used_balances', {}).get('sl', 0)),
                            'cl_availed': int(data.get('used_balances', {}).get('cl', 0)),
                            'rh_availed': int(data.get('used_balances', {}).get('rh', 0)),
                            'lop_availed': int(data.get('other_details', {}).get('lop_days', 0))
                        })
                    else:
                        rows.append({'emp_no': emp.emp_no, 'name': emp.name, 'pl_availed': 0, 'sl_availed': 0, 'cl_availed': 0, 'rh_availed': 0, 'lop_availed': 0})
                except Exception:
                    rows.append({'emp_no': emp.emp_no, 'name': emp.name, 'pl_availed': 0, 'sl_availed': 0, 'cl_availed': 0, 'rh_availed': 0, 'lop_availed': 0})

            return render_template('availed_report.html', as_on_date=as_on_date, year=year, rows=rows, total=len(rows))

        except ValueError:
            flash('Invalid date format', 'error')
        except Exception as e:
            flash(f'Error generating report: {str(e)}', 'error')
            logging.error('Availed report error: %s', e)

    return render_template('availed_report.html', as_on_date=as_on_date, year=year)


@app.route('/closing_balances_report', methods=['GET', 'POST'])
def closing_balances_report():
    """Closing balances report page (form + generate) similar to deduction_report"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    as_on_date = date.today()
    year = date.today().year

    if request.method == 'POST':
        as_on_date_str = request.form.get('as_on_date', '').strip()
        year = int(request.form.get('year', date.today().year))
        try:
            as_on_date = parse_any_date(as_on_date_str)
            if as_on_date is None:
                raise ValueError('Invalid date')

            employees = MasterData.query.order_by(MasterData.emp_no).all()
            calculator = LeaveCalculator()
            rows = []
            for emp in employees:
                try:
                    res = calculator.calculate_leave_summary(emp.emp_no, as_on_date)
                    if res['success']:
                        data = res['data']
                        rows.append({
                            'emp_no': emp.emp_no,
                            'name': data.get('emp_name', emp.name),
                            'pl_closing': data.get('closing_balances', {}).get('pl', 0),
                            'pl_partial': data.get('closing_balances', {}).get('pl_part', 0),
                            'sl_closing': data.get('closing_balances', {}).get('sl', 0),
                            'cl_closing': data.get('closing_balances', {}).get('cl', 0),
                            'rh_closing': data.get('closing_balances', {}).get('rh', 0)
                        })
                    else:
                        rows.append({'emp_no': emp.emp_no, 'name': emp.name, 'pl_closing': emp.pl, 'pl_partial': getattr(emp, 'partial_pl_days', 0), 'sl_closing': emp.sl, 'cl_closing': emp.cl, 'rh_closing': emp.rh})
                except Exception:
                    rows.append({'emp_no': emp.emp_no, 'name': emp.name, 'pl_closing': emp.pl, 'pl_partial': getattr(emp, 'partial_pl_days', 0), 'sl_closing': emp.sl, 'cl_closing': emp.cl, 'rh_closing': emp.rh})

            return render_template('closing_balances_report.html', as_on_date=as_on_date, year=year, rows=rows, total=len(rows))

        except ValueError:
            flash('Invalid date format', 'error')
        except Exception as e:
            flash(f'Error generating report: {str(e)}', 'error')
            logging.error('Closing balances report error: %s', e)

    return render_template('closing_balances_report.html', as_on_date=as_on_date, year=year)


@app.route('/encashment_report', methods=['GET', 'POST'])
def encashment_report():
    """Encashment report page (form + generate) similar to deduction_report"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    as_on_date = date.today()
    year = date.today().year

    if request.method == 'POST':
        as_on_date_str = request.form.get('as_on_date', '').strip()
        year = int(request.form.get('year', date.today().year))
        try:
            as_on_date = parse_any_date(as_on_date_str)
            if as_on_date is None:
                raise ValueError('Invalid date')

            start_date = date(year, 1, 1)
            # Query encashment entries in entry order
            leaves = LeaveEntry.query.filter(LeaveEntry.lvfrom >= start_date, LeaveEntry.lvfrom <= as_on_date).order_by(LeaveEntry.id.asc()).all()
            rows = []
            for leave in leaves:
                if leave.type and leave.type.upper() == 'E':
                    days = (leave.lvto - leave.lvfrom).days + 1 if leave.lvto and leave.lvfrom else 1
                    rows.append({
                        'emp_no': leave.emp_no,
                        'name': (get_employee_by_number(leave.emp_no).name if get_employee_by_number(leave.emp_no) else ''),
                        'lvfrom': leave.lvfrom.strftime('%Y-%m-%d'),
                        'lvto': leave.lvto.strftime('%Y-%m-%d') if leave.lvto else '',
                        'days': days,
                        'reason': leave.reason or ''
                    })

            return render_template('encashment_report.html', as_on_date=as_on_date, year=year, rows=rows, total=len(rows))

        except ValueError:
            flash('Invalid date format', 'error')
        except Exception as e:
            flash(f'Error generating report: {str(e)}', 'error')
            logging.error('Encashment report error: %s', e)

    return render_template('encashment_report.html', as_on_date=as_on_date, year=year)

@app.route('/attendance/export')
def export_attendance_excel():
    """Export attendance grid to Excel"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Unauthorized', 'error')
        return redirect(url_for('attendance'))
    
    try:
        year = request.args.get('year', datetime.now().year, type=int)
        
        # Get all departments ordered by sort_order and name
        departments = AttendanceDepartment.query.order_by(AttendanceDepartment.sort_order, AttendanceDepartment.name).all()
        
        # Get all attendance indices for the selected year
        indices = AttendanceIndex.query.filter_by(year=year).all()
        
        # Create a dictionary for quick lookup
        index_dict = {}
        for idx in indices:
            key = (idx.department_id, idx.month)
            index_dict[key] = idx.index_value
        
        # Month names
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        
        # Prepare data for Excel
        data = []
        for dept in departments:
            row = {'Department': dept.name}
            for month_num in range(1, 13):
                key = (dept.id, month_num)
                index_value = index_dict.get(key, '')
                row[months[month_num - 1]] = index_value
            data.append(row)
        
        # Create DataFrame
        df = pd.DataFrame(data)
        
        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Attendance', index=False)
        
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'attendance_{year}.xlsx'
        )
    
    except Exception as e:
        logging.error(f"Error exporting attendance Excel: {str(e)}")
        flash(f'Error exporting file: {str(e)}', 'error')
        return redirect(url_for('attendance'))

@app.route('/attendance/departments', methods=['GET', 'POST'])
def manage_departments():
    """Manage departments"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Unauthorized', 'error')
        return redirect(url_for('attendance'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            dept_name = request.form.get('dept_name', '').strip()
            if dept_name:
                # Check if exists
                existing = AttendanceDepartment.query.filter_by(name=dept_name).first()
                if existing:
                    flash(f'Department "{dept_name}" already exists', 'error')
                else:
                    # Get max sort_order
                    max_sort = db.session.query(db.func.max(AttendanceDepartment.sort_order)).scalar() or 0
                    dept = AttendanceDepartment(name=dept_name, sort_order=max_sort + 1)
                    db.session.add(dept)
                    db.session.commit()
                    flash(f'Department "{dept_name}" added successfully', 'success')
            else:
                flash('Department name cannot be empty', 'error')
        
        elif action == 'delete':
            dept_id = request.form.get('dept_id', type=int)
            if dept_id:
                dept = AttendanceDepartment.query.get(dept_id)
                if dept:
                    # Delete all associated indices first
                    AttendanceIndex.query.filter_by(department_id=dept_id).delete()
                    db.session.delete(dept)
                    db.session.commit()
                    flash(f'Department "{dept.name}" deleted successfully', 'success')
        
        return redirect(url_for('manage_departments'))
    
    # GET request - show departments list
    departments = AttendanceDepartment.query.order_by(AttendanceDepartment.sort_order, AttendanceDepartment.name).all()
    return render_template('manage_departments.html', departments=departments)


@app.route('/export_availed_leaves')
def export_availed_leaves():
    """Export availed leaves (PL integer only, SL, CL, RH, LOP) for all employees as on a given date"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        as_on_date_str = request.args.get('as_on_date', '')
        if not as_on_date_str:
            flash('Missing as_on_date', 'error')
            return redirect(url_for('dashboard'))

        as_on_date = parse_any_date(as_on_date_str)
        if as_on_date is None:
            flash('Invalid date', 'error')
            return redirect(url_for('dashboard'))

        employees = MasterData.query.order_by(MasterData.emp_no).all()
        calculator = LeaveCalculator()

        rows = []
        for emp in employees:
            try:
                res = calculator.calculate_leave_summary(emp.emp_no, as_on_date)
                if res['success']:
                    data = res['data']
                    pl_availed = int(data['used_balances'].get('pl', 0)) if data.get('used_balances') else 0
                    # PL availed report wants no fractions, so integer
                    sl_availed = int(data['used_balances'].get('sl', 0)) if data.get('used_balances') else 0
                    cl_availed = int(data['used_balances'].get('cl', 0)) if data.get('used_balances') else 0
                    rh_availed = int(data['used_balances'].get('rh', 0)) if data.get('used_balances') else 0
                    lop_availed = int(data['other_details'].get('lop_days', 0)) if data.get('other_details') else 0
                else:
                    # Fallback to master data when calculator fails
                    pl_availed = 0
                    sl_availed = 0
                    cl_availed = 0
                    rh_availed = 0
                    lop_availed = 0

                rows.append({
                    'emp_no': emp.emp_no,
                    'name': emp.name,
                    'pl_availed': pl_availed,
                    'sl_availed': sl_availed,
                    'cl_availed': cl_availed,
                    'rh_availed': rh_availed,
                    'lop_availed': lop_availed
                })
            except Exception:
                rows.append({
                    'emp_no': emp.emp_no,
                    'name': emp.name,
                    'pl_availed': 0,
                    'sl_availed': 0,
                    'cl_availed': 0,
                    'rh_availed': 0,
                    'lop_availed': 0
                })

        # Create Excel
        output = io.BytesIO()
        df = pd.DataFrame(rows)
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Availed Leaves', index=False)

        output.seek(0)
        filename = f'availed_leaves_{as_on_date.strftime("%Y%m%d")}.xlsx'
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception as e:
        logging.error(f"Error exporting availed leaves: {e}")
        flash(f'Error exporting availed leaves: {str(e)}', 'error')
        return redirect(url_for('dashboard'))


@app.route('/export_closing_balances')
def export_closing_balances():
    """Export closing balances (PL fraction allowed, SL, CL, RH) for all employees as on a given date"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        as_on_date_str = request.args.get('as_on_date', '')
        if not as_on_date_str:
            flash('Missing as_on_date', 'error')
            return redirect(url_for('dashboard'))

        as_on_date = parse_any_date(as_on_date_str)
        if as_on_date is None:
            flash('Invalid date', 'error')
            return redirect(url_for('dashboard'))

        employees = MasterData.query.order_by(MasterData.emp_no).all()
        calculator = LeaveCalculator()

        rows = []
        for emp in employees:
            try:
                res = calculator.calculate_leave_summary(emp.emp_no, as_on_date)
                if res['success']:
                    data = res['data']
                    pl_closing = data['closing_balances'].get('pl', 0)
                    pl_partial = data['closing_balances'].get('pl_part', 0)
                    sl_closing = data['closing_balances'].get('sl', 0)
                    cl_closing = data['closing_balances'].get('cl', 0)
                    rh_closing = data['closing_balances'].get('rh', 0)
                else:
                    pl_closing = emp.pl
                    pl_partial = emp.partial_pl_days if hasattr(emp, 'partial_pl_days') else 0
                    sl_closing = emp.sl
                    cl_closing = emp.cl
                    rh_closing = emp.rh

                rows.append({
                    'emp_no': emp.emp_no,
                    'name': emp.name,
                    'pl_closing': pl_closing,
                    'pl_partial': pl_partial,
                    'sl_closing': sl_closing,
                    'cl_closing': cl_closing,
                    'rh_closing': rh_closing
                })
            except Exception:
                rows.append({
                    'emp_no': emp.emp_no,
                    'name': emp.name,
                    'pl_closing': emp.pl,
                    'pl_partial': emp.partial_pl_days if hasattr(emp, 'partial_pl_days') else 0,
                    'sl_closing': emp.sl,
                    'cl_closing': emp.cl,
                    'rh_closing': emp.rh
                })

        # Create Excel
        output = io.BytesIO()
        df = pd.DataFrame(rows)
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Closing Balances', index=False)

        output.seek(0)
        filename = f'closing_balances_{as_on_date.strftime("%Y%m%d")}.xlsx'
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception as e:
        logging.error(f"Error exporting closing balances: {e}")
        flash(f'Error exporting closing balances: {str(e)}', 'error')
        return redirect(url_for('dashboard'))


@app.route('/export_encashments')
def export_encashments():
    """Export all encashment (E) leave entries up to an as_on_date"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        as_on_date_str = request.args.get('as_on_date', '')
        if not as_on_date_str:
            flash('Missing as_on_date', 'error')
            return redirect(url_for('dashboard'))

        as_on_date = parse_any_date(as_on_date_str)
        if as_on_date is None:
            flash('Invalid date', 'error')
            return redirect(url_for('dashboard'))

        start_date = date(1900, 1, 1)

        # Get all leave entries of type 'E' up to as_on_date in entry order
        leaves = LeaveEntry.query.filter(LeaveEntry.lvfrom >= start_date, LeaveEntry.lvfrom <= as_on_date).order_by(LeaveEntry.id.asc()).all()

        rows = []
        for leave in leaves:
            try:
                if leave.type and leave.type.upper() == 'E':
                    days = (leave.lvto - leave.lvfrom).days + 1 if leave.lvto and leave.lvfrom else 1
                    rows.append({
                        'emp_no': leave.emp_no,
                        'lvfrom': leave.lvfrom.strftime('%Y-%m-%d'),
                        'lvto': leave.lvto.strftime('%Y-%m-%d') if leave.lvto else '',
                        'days': days,
                        'reason': leave.reason or ''
                    })
            except Exception:
                continue

        # Create Excel
        output = io.BytesIO()
        df = pd.DataFrame(rows)
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Encashments', index=False)

        output.seek(0)
        filename = f'encashments_{as_on_date.strftime("%Y%m%d")}.xlsx'
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception as e:
        logging.error(f"Error exporting encashments: {e}")
        flash(f'Error exporting encashments: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/attendance/departments/reorder', methods=['POST'])
def reorder_departments():
    """Update department sort order"""
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        order = request.json.get('order', [])
        
        for idx, dept_id in enumerate(order):
            dept = AttendanceDepartment.query.get(dept_id)
            if dept:
                dept.sort_order = idx
        
        db.session.commit()
        return jsonify({'success': True})
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error reordering departments: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500
