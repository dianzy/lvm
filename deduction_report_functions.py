# Updated deduction report functions with entry order\n# Replace the deduction_report and export_deduction_excel functions in routes.py with these:\n\n@app.route('/deduction_report', methods=['GET', 'POST'])
def deduction_report():
    """Separate LOP/SL_HP report for salary deductions - Entry Order Preserved"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        as_on_date_str = request.form['as_on_date']
        year = int(request.form.get('year', date.today().year))

        try:
            as_on_date = parse_any_date(as_on_date_str)
            if as_on_date is None:
                raise ValueError("Invalid date")

            # Date range: Jan 1 of selected year to as_on_date
            start_date = date(year, 1, 1)

            # Get all leave entries in the date range - ORDERED BY ID (entry order)
            leaves_query = LeaveEntry.query.filter(
                LeaveEntry.lvfrom >= start_date,
                LeaveEntry.lvfrom <= as_on_date
            ).order_by(LeaveEntry.id.asc()).all()  # Entry order, not date order

            # Debug: Print all leave types found
            leave_types_found = set()
            for leave in leaves_query:
                leave_types_found.add(leave.type)
                if leave.sltype:
                    leave_types_found.add(f"{leave.type}_{leave.sltype}")

            print(f"DEBUG: Leave types found in database: {leave_types_found}")
            print(f"DEBUG: Total leaves in period {start_date} to {as_on_date}: {len(leaves_query)}")

            # Create detailed entries list - preserving entry order
            lop_entries = []
            sl_hp_entries = []
            all_deduction_entries = []

            for leave in leaves_query:
                emp_no = leave.emp_no

                # Enhanced LOP/SL_HP detection logic
                is_lop = False
                is_sl_hp = False

                # Check for LOP (Loss of Pay)
                if leave.type.upper() == 'L':
                    is_lop = True
                    print(f"DEBUG: Found LOP - {emp_no}: {leave.type}")

                # Check for SL Half Pay - Multiple possible formats
                leave_type_upper = leave.type.upper()
                sltype_upper = (leave.sltype or '').upper()

                if (leave_type_upper == 'SL_HP' or 
                    (leave_type_upper == 'S' and sltype_upper == 'H') or
                    (leave_type_upper == 'SL' and sltype_upper == 'H') or
                    (leave_type_upper == 'SL' and sltype_upper == 'HP') or
                    leave_type_upper == 'SLHP'):
                    is_sl_hp = True
                    print(f"DEBUG: Found SL_HP - {emp_no}: {leave.type}/{leave.sltype}")

                if is_lop or is_sl_hp:
                    # Get employee details
                    emp = get_employee_by_number(emp_no)
                    if not emp:
                        print(f"DEBUG: Employee {emp_no} not found in master data")
                        continue
                    else:
                        print(f"DEBUG: Found employee {emp_no} -> {emp.name}")

                    # Calculate days
                    leave_from = leave.lvfrom
                    leave_to = leave.lvto or leave.lvfrom

                    # Adjust if leave extends beyond as_on_date
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
                        'sltype': leave.sltype or ''
                    }

                    # Add to appropriate lists - maintaining entry order
                    if is_lop:
                        lop_entries.append(entry)
                        print(f"DEBUG: Added LOP entry for {emp_no}: {days} days")

                    if is_sl_hp:
                        sl_hp_entries.append(entry)
                        print(f"DEBUG: Added SL_HP entry for {emp_no}: {days} days")

                    # Add to combined list for overall table
                    all_deduction_entries.append(entry)

            print(f"DEBUG: LOP entries: {len(lop_entries)}, SL_HP entries: {len(sl_hp_entries)}")
            print(f"DEBUG: Total deduction entries: {len(all_deduction_entries)}")

            return render_template('deduction_report.html',
                                 lop_entries=lop_entries,
                                 sl_hp_entries=sl_hp_entries,
                                 all_deduction_entries=all_deduction_entries,
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

        # Get deduction data in ENTRY ORDER (same as deduction_report)
        leaves_query = LeaveEntry.query.filter(
            LeaveEntry.lvfrom >= start_date,
            LeaveEntry.lvfrom <= as_on_date
        ).order_by(LeaveEntry.id.asc()).all()  # Entry order

        # Prepare Excel data - maintain entry order
        lop_details = []
        sl_hp_details = []
        all_deduction_details = []

        entry_counter = 0

        for leave in leaves_query:
            emp_no = leave.emp_no

            # Enhanced LOP/SL_HP detection logic (same as deduction_report)
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
                    continue

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
                    'To': leave_to.strftime('%d-%m-%Y') if leave_to != leave_from else leave_from.strftime('%d-%m-%Y'),
                    'Days': days,
                    'Type': leave.type,
                    'SL Type': leave.sltype or '',
                    'Reason': leave.reason or '',
                    'Database ID': leave.id
                }

                # Add to all entries list
                all_deduction_details.append({
                    **entry_data,
                    'Category': 'LOP' if is_lop else 'SL_HP'
                })

                if is_lop:
                    lop_details.append(entry_data)

                if is_sl_hp:
                    sl_hp_details.append(entry_data)

        # Create summary data
        lop_summary = {}
        sl_hp_summary = {}

        for entry in lop_details:
            emp_key = (entry['Emp No'], entry['Name'])
            if emp_key not in lop_summary:
                lop_summary[emp_key] = {'total_days': 0, 'entries': 0}
            lop_summary[emp_key]['total_days'] += entry['Days']
            lop_summary[emp_key]['entries'] += 1

        for entry in sl_hp_details:
            emp_key = (entry['Emp No'], entry['Name'])
            if emp_key not in sl_hp_summary:
                sl_hp_summary[emp_key] = {'total_days': 0, 'entries': 0}
            sl_hp_summary[emp_key]['total_days'] += entry['Days']
            sl_hp_summary[emp_key]['entries'] += 1

        lop_summary_list = [
            {
                'Emp No': emp_no,
                'Name': name,
                'Total LOP Days': data['total_days'],
                'Number of Entries': data['entries']
            }
            for (emp_no, name), data in lop_summary.items()
        ]

        sl_hp_summary_list = [
            {
                'Emp No': emp_no,
                'Name': name,
                'Total SL HP Days': data['total_days'],
                'Number of Entries': data['entries']
            }
            for (emp_no, name), data in sl_hp_summary.items()
        ]

        # Create Excel file with multiple sheets
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            # All Deduction Details - Entry Order
            if all_deduction_details:
                df_all = pd.DataFrame(all_deduction_details)
                df_all.to_excel(writer, sheet_name='All Deduction Details', index=False)
            else:
                pd.DataFrame([{'Message': 'No LOP/SL_HP entries found'}]).to_excel(writer, sheet_name='All Deduction Details', index=False)

            # LOP Summary
            if lop_summary_list:
                pd.DataFrame(lop_summary_list).to_excel(writer, sheet_name='LOP Summary', index=False)
            else:
                pd.DataFrame([{'Message': 'No LOP entries found'}]).to_excel(writer, sheet_name='LOP Summary', index=False)

            # SL HP Summary
            if sl_hp_summary_list:
                pd.DataFrame(sl_hp_summary_list).to_excel(writer, sheet_name='SL HP Summary', index=False)
            else:
                pd.DataFrame([{'Message': 'No SL Half Pay entries found'}]).to_excel(writer, sheet_name='SL HP Summary', index=False)

            # LOP Details - Entry Order
            if lop_details:
                pd.DataFrame(lop_details).to_excel(writer, sheet_name='LOP Details', index=False)
            else:
                pd.DataFrame([{'Message': 'No LOP entries found'}]).to_excel(writer, sheet_name='LOP Details', index=False)

            # SL HP Details - Entry Order
            if sl_hp_details:
                pd.DataFrame(sl_hp_details).to_excel(writer, sheet_name='SL HP Details', index=False)
            else:
                pd.DataFrame([{'Message': 'No SL Half Pay entries found'}]).to_excel(writer, sheet_name='SL HP Details', index=False)

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

