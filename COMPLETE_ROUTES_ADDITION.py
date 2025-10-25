# ==============================================
# ATTENDANCE TRACKING ROUTES
# Add this section to your existing routes.py
# ==============================================

# Add these imports at the top of your routes.py if not already present:
# import calendar
# import logging

@app.route('/departments')
def departments():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('dashboard'))

    departments = Department.query.filter_by(is_active=True).order_by(Department.dept_name.asc()).all()
    return render_template('departments.html', departments=departments)

@app.route('/add_department', methods=['POST'])
def add_department():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('departments'))

    dept_name = request.form.get('dept_name', '').strip()
    if not dept_name:
        flash('Department name is required', 'error')
        return redirect(url_for('departments'))

    try:
        existing = Department.query.filter_by(dept_name=dept_name).first()
        if existing:
            flash(f'Department "{dept_name}" already exists', 'error')
        else:
            new_dept = Department(dept_name=dept_name)
            db.session.add(new_dept)
            db.session.commit()
            flash(f'Department "{dept_name}" added successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding department: {str(e)}', 'error')

    return redirect(url_for('departments'))

@app.route('/upload_departments', methods=['POST'])
def upload_departments():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('departments'))

    if 'departments_file' not in request.files:
        return redirect(url_for('add_department'))

    file = request.files['departments_file']
    if not file or not file.filename:
        flash('No file selected', 'error')
        return redirect(url_for('departments'))

    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file.stream)
        else:
            df = pd.read_excel(file.stream)

        df.columns = df.columns.str.strip()
        dept_col = df.columns[0]  # Use first column

        added_count = 0
        duplicate_count = 0

        for _, row in df.iterrows():
            dept_name = str(row[dept_col]).strip()
            if not dept_name or dept_name.lower() in ['nan', 'none', '', 'null']:
                continue

            existing = Department.query.filter_by(dept_name=dept_name).first()
            if existing:
                duplicate_count += 1
                continue

            new_dept = Department(dept_name=dept_name)
            db.session.add(new_dept)
            added_count += 1

        db.session.commit()
        flash(f'Added {added_count} departments. {duplicate_count} duplicates skipped.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error uploading departments: {str(e)}', 'error')

    return redirect(url_for('departments'))

@app.route('/upload_attendance_data', methods=['POST'])
def upload_attendance_data():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('attendance_tracking'))

    if 'attendance_data_file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('attendance_tracking'))

    file = request.files['attendance_data_file']
    if not file or not file.filename:
        flash('No file selected', 'error')
        return redirect(url_for('attendance_tracking'))

    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file.stream)
        else:
            df = pd.read_excel(file.stream)

        df.columns = df.columns.str.strip()
        dept_col = df.columns[0]
        month_cols = df.columns[1:]

        added_count = 0
        garbage_count = 0
        selected_year = request.form.get('data_year', str(date.today().year))

        for _, row in df.iterrows():
            dept_name = str(row[dept_col]).strip()
            if not dept_name or dept_name.lower() in ['nan', 'none', '', 'null']:
                continue

            dept = Department.query.filter_by(dept_name=dept_name).first()
            if not dept:
                dept = Department(dept_name=dept_name)
                db.session.add(dept)
                db.session.commit()

            for month_col in month_cols:
                cell_value = str(row[month_col]).strip()
                if not cell_value or cell_value.lower() in ['nan', 'none', '', 'null']:
                    continue

                month_num = None
                month_names = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
                              'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

                for i, month_name in enumerate(month_names, 1):
                    if month_name in month_col.lower():
                        month_num = i
                        break

                if month_num is None:
                    import re
                    numbers = re.findall(r'\d+', month_col)
                    if numbers:
                        month_num = int(numbers[0])
                        if month_num < 1 or month_num > 12:
                            continue

                if month_num is None:
                    continue

                month_year = f"{selected_year}-{str(month_num).zfill(2)}"

                existing = AttendanceSubmission.query.filter_by(
                    dept_id=dept.id, month_year=month_year
                ).first()
                if existing:
                    continue

                is_real = True
                index_number = None
                garbage_value = None

                try:
                    index_number = int(cell_value)
                    if index_number > 1000 or index_number < 0:
                        is_real = False
                        garbage_value = cell_value
                        garbage_count += 1
                    else:
                        added_count += 1
                except ValueError:
                    is_real = False
                    garbage_value = cell_value
                    garbage_count += 1

                submission = AttendanceSubmission(
                    dept_id=dept.id,
                    month_year=month_year,
                    index_number=index_number if is_real else None,
                    is_real_submission=is_real,
                    garbage_value=garbage_value,
                    status='Garbage' if not is_real else 'Filed',
                    notes='Imported from Excel'
                )
                db.session.add(submission)

        db.session.commit()
        flash(f'Imported {added_count} real submissions and {garbage_count} placeholder entries.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error uploading data: {str(e)}', 'error')

    return redirect(url_for('attendance_tracking'))

@app.route('/attendance_tracking')
def attendance_tracking():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    selected_year = request.args.get('year', str(date.today().year))
    selected_month = request.args.get('month', str(date.today().month))

    departments = Department.query.filter_by(is_active=True).order_by(Department.dept_name.asc()).all()

    submissions = AttendanceSubmission.query.join(Department).filter(
        AttendanceSubmission.month_year.like(f'{selected_year}-%')
    ).order_by(AttendanceSubmission.submission_date.desc()).all()

    months = [f'{selected_year}-{str(i).zfill(2)}' for i in range(1, 13)]
    month_names = [calendar.month_name[i] for i in range(1, 13)]

    matrix_data = {}
    for dept in departments:
        matrix_data[dept.id] = {}
        for month in months:
            matrix_data[dept.id][month] = None

    for submission in submissions:
        if submission.dept_id in matrix_data and submission.month_year in matrix_data[submission.dept_id]:
            matrix_data[submission.dept_id][submission.month_year] = submission

    selected_month_year = f"{selected_year}-{str(selected_month).zfill(2)}"
    missing_depts = []
    for dept in departments:
        if selected_month_year not in [s.month_year for s in submissions if s.dept_id == dept.id]:
            missing_depts.append(dept)

    stats = {
        'total_departments': len(departments),
        'total_submissions': len([s for s in submissions if s.is_real_submission]),
        'garbage_entries': len([s for s in submissions if not s.is_real_submission]),
        'missing_this_month': len(missing_depts)
    }

    return render_template('attendance_tracking_enhanced.html', 
                         departments=departments, months=months, month_names=month_names,
                         matrix_data=matrix_data, selected_year=selected_year,
                         selected_month=selected_month, submissions=submissions[:20],
                         missing_depts=missing_depts, stats=stats, calendar=calendar)

@app.route('/mark_attendance_submission', methods=['POST'])
def mark_attendance_submission():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'error')
        return redirect(url_for('attendance_tracking'))

    dept_id = request.form.get('dept_id')
    month_year = request.form.get('month_year')
    submitted_by = request.form.get('submitted_by', '').strip()
    notes = request.form.get('notes', '').strip()
    is_garbage = request.form.get('is_garbage') == 'on'
    garbage_value = request.form.get('garbage_value', '').strip()

    if not dept_id or not month_year:
        flash('Department and month are required', 'error')
        return redirect(url_for('attendance_tracking'))

    try:
        existing = AttendanceSubmission.query.filter_by(dept_id=dept_id, month_year=month_year).first()
        if existing:
            flash('Entry already exists for this department and month', 'error')
            return redirect(url_for('attendance_tracking'))

        if is_garbage:
            new_submission = AttendanceSubmission(
                dept_id=dept_id, month_year=month_year, index_number=None,
                is_real_submission=False, garbage_value=garbage_value or 'PLACEHOLDER',
                submitted_by=submitted_by or None, notes=notes or None, status='Garbage'
            )
            flash_msg = f'Placeholder entry created: {garbage_value or "PLACEHOLDER"}'
        else:
            last_real = AttendanceSubmission.query.filter_by(is_real_submission=True).order_by(
                AttendanceSubmission.index_number.desc()).first()
            next_index = (last_real.index_number + 1) if last_real and last_real.index_number else 1

            new_submission = AttendanceSubmission(
                dept_id=dept_id, month_year=month_year, index_number=next_index,
                is_real_submission=True, submitted_by=submitted_by or None,
                notes=notes or None, status='Filed'
            )
            flash_msg = f'Index Number {next_index} assigned'

        db.session.add(new_submission)
        db.session.commit()

        dept = Department.query.get(dept_id)
        month_obj = datetime.strptime(month_year, '%Y-%m')
        month_name = month_obj.strftime('%B %Y')

        flash(f'{dept.dept_name} - {month_name}: {flash_msg}', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('attendance_tracking'))

@app.route('/missing_submissions_report')
def missing_submissions_report():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    selected_year = request.args.get('year', str(date.today().year))

    departments = Department.query.filter_by(is_active=True).order_by(Department.dept_name.asc()).all()
    submissions = AttendanceSubmission.query.filter(
        AttendanceSubmission.month_year.like(f'{selected_year}-%'),
        AttendanceSubmission.is_real_submission == True
    ).all()

    months = [f'{selected_year}-{str(i).zfill(2)}' for i in range(1, 13)]
    month_names = [calendar.month_name[i] for i in range(1, 13)]

    missing_report = {}
    for month, month_name in zip(months, month_names):
        submitted_dept_ids = [s.dept_id for s in submissions if s.month_year == month]
        missing_depts = [d for d in departments if d.id not in submitted_dept_ids]
        missing_report[month] = {
            'month_name': month_name,
            'missing_depts': missing_depts,
            'missing_count': len(missing_depts)
        }

    return render_template('missing_submissions_report.html',
                         missing_report=missing_report, selected_year=selected_year)

@app.route('/search_attendance')
def search_attendance():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'all')
    results = []

    if query:
        try:
            base_query = AttendanceSubmission.query.join(Department)

            if search_type == 'real':
                base_query = base_query.filter(AttendanceSubmission.is_real_submission == True)
            elif search_type == 'garbage':
                base_query = base_query.filter(AttendanceSubmission.is_real_submission == False)

            if query.isdigit():
                index_results = base_query.filter(AttendanceSubmission.index_number == int(query)).all()
                results.extend(index_results)

            dept_results = base_query.filter(Department.dept_name.ilike(f'%{query}%')).all()
            for result in dept_results:
                if result not in results:
                    results.append(result)

            if search_type in ['all', 'garbage']:
                garbage_results = base_query.filter(AttendanceSubmission.garbage_value.ilike(f'%{query}%')).all()
                for result in garbage_results:
                    if result not in results:
                        results.append(result)

        except Exception as e:
            flash(f'Search error: {str(e)}', 'error')

    return render_template('search_attendance_enhanced.html', 
                         results=results, query=query, search_type=search_type)

@app.route('/export_attendance_matrix')
def export_attendance_matrix():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    selected_year = request.args.get('year', str(date.today().year))
    include_garbage = request.args.get('include_garbage', 'true') == 'true'

    try:
        departments = Department.query.filter_by(is_active=True).order_by(Department.dept_name.asc()).all()
        submissions = AttendanceSubmission.query.join(Department).filter(
            AttendanceSubmission.month_year.like(f'{selected_year}-%')
        ).all()

        months = [f'{selected_year}-{str(i).zfill(2)}' for i in range(1, 13)]
        month_names = [calendar.month_name[i] for i in range(1, 13)]

        excel_data = []
        for dept in departments:
            row = {'Department': dept.dept_name}

            for i, month in enumerate(months):
                month_name = month_names[i]
                submission = next((s for s in submissions if s.dept_id == dept.id and s.month_year == month), None)

                if submission:
                    if submission.is_real_submission:
                        row[month_name] = submission.index_number or ''
                    else:
                        if include_garbage:
                            row[month_name] = f"G:{submission.garbage_value}" if submission.garbage_value else 'G:PLACEHOLDER'
                        else:
                            row[month_name] = ''
                else:
                    row[month_name] = ''

            excel_data.append(row)

        output = io.BytesIO()
        df = pd.DataFrame(excel_data)

        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name=f'Attendance_Matrix_{selected_year}', index=False)

        output.seek(0)
        filename = f'attendance_matrix_{selected_year}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'

        return send_file(output, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception as e:
        flash(f'Export error: {str(e)}', 'error')
        return redirect(url_for('attendance_tracking'))
