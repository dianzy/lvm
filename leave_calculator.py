from models import MasterData, LeaveEntry
from datetime import datetime, date, timedelta
import logging

class LeaveCalculator:
    def calculate_leave_summary(self, emp_no, as_on_date):
        """
        Calculate leave summary with VB logic - ALLOW NEGATIVE BALANCES for proper validation
        """
        try:
            # Get Master Data
            master_data = MasterData.query.filter_by(emp_no=emp_no).first()
            if not master_data:
                return {'success': False, 'error': 'Employee not found in master sheet.'}

            # Get Leave Data - handle both string and numeric emp_no formats
            leave_entries_str = LeaveEntry.query.filter_by(emp_no=emp_no).all()
            leave_entries_float = LeaveEntry.query.filter_by(emp_no=f"{emp_no}.0").all()
            all_leave_entries = leave_entries_str + leave_entries_float

            # Initialize variables from master data
            emp_name = master_data.name
            doj = master_data.doj

            # Get employee status using the graceful method
            emp_flag = master_data.get_emp_status() if hasattr(master_data, 'get_emp_status') else 'C'

            # Opening balances
            opening_pl = master_data.pl
            opening_pl_part = master_data.partial_pl_days
            opening_sl = master_data.sl
            opening_rh = master_data.rh
            opening_lop = master_data.lop if hasattr(master_data, 'lop') else 0

            # Calculate CL based on employee flag (P/C/R)
            if emp_flag == 'P':
                # Probationer: CL accrued based on service days (1 CL per 30 days)
                probation_service_days = (as_on_date - doj).days
                cl_opening = int(probation_service_days / 30)
                emp_status = "Probationer"
            elif emp_flag == 'C':
                # Confirmed: Fixed 12 CL per year
                cl_opening = 12
                emp_status = "Permanent"
            else:
                # Default fallback
                cl_opening = master_data.cl if hasattr(master_data, 'cl') else 0
                emp_status = "Unknown"

            # Initialize counters
            pl_used = cl_used = sl_used = rh_used = 0
            lop_days = mat_leave = encashments = 0

            # Maps for overlap check and retroactive simulation
            taken_dates = set()
            inelig_dates = set()
            pl_use_on_date = {}

            # Filter and sort leave entries that are on or before as_on_date
            valid_leave_entries = []
            for leave in all_leave_entries:
                if leave.lvfrom <= as_on_date:
                    valid_leave_entries.append(leave)

            # Sort by date first (ascending order - earliest first)
            valid_leave_entries.sort(key=lambda x: x.lvfrom)

            # Process leave entries and create details list
            leave_details = []
            serial_no = 1

            for leave in valid_leave_entries:
                leave_from = leave.lvfrom
                leave_to = leave.lvto or leave.lvfrom

                # Adjust leave_to if it extends beyond as_on_date
                if leave_to > as_on_date:
                    leave_to = as_on_date

                # Calculate leave days
                session_val = leave.session or ""
                leave_type = leave.type.upper()
                
                # Normalize "CL HALF DAY" or similar variations to "CL_HALFDAY"
                if 'CL' in leave_type and 'HALF' in leave_type:
                    leave_type = 'CL_HALFDAY'

                if leave_type == "CL_HALFDAY":
                    days = 0.5
                else:
                    days = (leave_to - leave_from).days + 1
                    if session_val in ['F', 'A']:
                        days = 0.5

                # Overlap check (skip for encashment as it can overlap with other leaves)
                if leave_type != 'E':
                    current_date = leave_from
                    while current_date <= leave_to:
                        date_key = current_date.strftime('%Y-%m-%d')
                        if days == 0.5:
                            date_key += " (0.5)"
                        if date_key in taken_dates:
                            return {'success': False, 'error': f'Overlapping leave on {date_key}'}
                        taken_dates.add(date_key)
                        current_date += timedelta(days=1)

                # Process different leave types for calculations (leave_type is already normalized above)
                if leave_type == 'CL' or leave_type == 'CL_HALFDAY':
                    cl_used += days
                elif leave_type == 'PL':
                    pl_used += days
                    # Track PL usage for retroactive calculation
                    current_date = leave_from
                    while current_date <= leave_to:
                        date_key = current_date.strftime('%Y-%m-%d')
                        if date_key not in pl_use_on_date:
                            pl_use_on_date[date_key] = 0
                        if leave_from == leave_to and session_val in ['F', 'A']:
                            pl_use_on_date[date_key] += 0.5
                        else:
                            pl_use_on_date[date_key] += 1
                        inelig_dates.add(date_key)
                        current_date += timedelta(days=1)
                elif leave_type == 'SL_FP':
                    sl_used += 2 * days
                    current_date = leave_from
                    while current_date <= leave_to:
                        inelig_dates.add(current_date.strftime('%Y-%m-%d'))
                        current_date += timedelta(days=1)
                elif leave_type == 'SL_HP':
                    sl_used += 1 * days
                    current_date = leave_from
                    while current_date <= leave_to:
                        inelig_dates.add(current_date.strftime('%Y-%m-%d'))
                        current_date += timedelta(days=1)
                elif leave_type == 'S':
                    sl_type_val = getattr(leave, 'sltype', '') or ""
                    if sl_type_val == 'F':
                        sl_used += 2 * days
                    elif sl_type_val == 'H':
                        sl_used += 1 * days
                    current_date = leave_from
                    while current_date <= leave_to:
                        inelig_dates.add(current_date.strftime('%Y-%m-%d'))
                        current_date += timedelta(days=1)
                elif leave_type == 'L':
                    lop_days += days
                    current_date = leave_from
                    while current_date <= leave_to:
                        inelig_dates.add(current_date.strftime('%Y-%m-%d'))
                        current_date += timedelta(days=1)
                elif leave_type == 'M':
                    mat_leave += days
                    current_date = leave_from
                    while current_date <= leave_to:
                        inelig_dates.add(current_date.strftime('%Y-%m-%d'))
                        current_date += timedelta(days=1)
                elif leave_type == 'E':
                    encashments += 1
                    pl_used += days
                    # Encashment can overlap with other leaves, so we don't add to inelig_dates
                    # Track PL usage on each date for retroactive accrual
                    current_date = leave_from
                    while current_date <= leave_to:
                        date_key = current_date.strftime('%Y-%m-%d')
                        if date_key not in pl_use_on_date:
                            pl_use_on_date[date_key] = 0
                        # Distribute days proportionally
                        if session_val in ['F', 'A']:
                            pl_use_on_date[date_key] += 0.5
                        else:
                            pl_use_on_date[date_key] += 1
                        current_date += timedelta(days=1)
                elif leave_type == 'RH':
                    rh_used += days

                # Add to leave details with sequential serial number
                leave_details.append({
                    'sl_no': serial_no,
                    'lv_from': leave_from.strftime('%d-%m-%Y'),
                    'lv_to': leave_to.strftime('%d-%m-%Y') if leave_to != leave_from else '',
                    'session': session_val or '',
                    'type': leave.type,
                    'sl_type': getattr(leave, 'sltype', '') or '',
                    'days': days,
                    'reason': getattr(leave, 'reason', '') or ''
                })

                # Increment serial number for next entry
                serial_no += 1

            # Retroactive PL accrual simulation (as per VB logic)
            balance_full = int(opening_pl)
            eligible_count = int(opening_pl_part)

            # Cap at 270 days maximum for accrual, but allow negative for usage
            if balance_full >= 270:
                balance_full = 270
                eligible_count = 0

            # Simulate day by day from DOJ to as_on_date
            current_date = doj
            while current_date <= as_on_date:
                date_key = current_date.strftime('%Y-%m-%d')

                # Apply PL usage on this date
                if date_key in pl_use_on_date:
                    use_amount = pl_use_on_date[date_key]
                    balance_full -= int(use_amount)

                    # Handle fractional usage (0.5 day = 5/11)
                    if (use_amount - int(use_amount)) >= 0.5:
                        # ALLOW NEGATIVE BALANCE - REMOVED: if balance_full < 0: balance_full = 0
                        eligible_count -= 5
                        if eligible_count < -10:
                            eligible_count = -10

                    # ALLOW NEGATIVE BALANCE - REMOVED: if balance_full < 0: balance_full = 0

                # Reset eligible count if balance hits 270 (but only if positive)
                if balance_full >= 270:
                    eligible_count = 0

                # Accrue PL if eligible (not on ineligible dates) - ALLOW ACCRUAL EVEN WHEN NEGATIVE
                if not date_key in inelig_dates:
                    if balance_full < 270:
                        eligible_count += 1
                        if eligible_count >= 11:
                            balance_full += 1
                            eligible_count = 0
                            if balance_full >= 270:
                                balance_full = 270
                                eligible_count = 0

                current_date += timedelta(days=1)

            # Final adjustments - ALLOW NEGATIVE
            pl_closing_full = balance_full  # Can be negative now
            pl_closing_part = max(0, eligible_count) if pl_closing_full >= 0 else 0

            # Calculate accrued PL for display
            closing_decimal = pl_closing_full + (pl_closing_part / 11.0)
            opening_decimal = opening_pl + (opening_pl_part / 11.0)
            accrued_decimal = closing_decimal - opening_decimal + pl_used
            if accrued_decimal < 0:
                accrued_decimal = 0

            accrued_full = int(accrued_decimal)
            accrued_part = round((accrued_decimal - accrued_full) * 11, 0)

            # Calculate closing balances for other leave types - ALLOW NEGATIVE
            closing_cl = cl_opening - cl_used  # Can be negative
            closing_sl = opening_sl - sl_used  # Can be negative  
            closing_rh = opening_rh - rh_used  # Can be negative

            # Calculate cumulative LOP (opening + LOP days from entries)
            cumulative_lop = float(opening_lop) + lop_days

            # Prepare summary data - leave_details is already sorted by date with proper serial numbers
            summary_data = {
                'emp_no': emp_no,
                'emp_name': emp_name,
                'doj': doj.strftime('%d-%m-%Y'),
                'as_on_date': as_on_date.strftime('%d-%m-%Y'),
                'emp_status': emp_status,
                'opening_balances': {
                    'pl': int(opening_pl),
                    'pl_part': int(opening_pl_part),
                    'cl': int(cl_opening),
                    'cl_label': 'Accrued CL' if emp_flag == 'P' else 'Opening CL',
                    'sl': int(opening_sl),
                    'rh': int(opening_rh),
                    'lop': float(opening_lop),
                    'cumulative_lop': cumulative_lop
                },
                'closing_balances': {
                    'pl': pl_closing_full,  # Can be negative
                    'pl_part': pl_closing_part,
                    'cl': int(closing_cl),  # Can be negative
                    'sl': int(closing_sl),  # Can be negative
                    'rh': int(closing_rh)  # Can be negative
                },
                'used_balances': {
                    'pl': pl_used,
                    'cl': cl_used,
                    'sl': sl_used,
                    'rh': rh_used
                },
                'other_details': {
                    'lop_days': lop_days,
                    'mat_leave': mat_leave,
                    'encashments': encashments,
                    'accrued_pl_full': accrued_full,
                    'accrued_pl_part': int(accrued_part)
                },
                'leave_details': leave_details  # Already in date ascending order with proper serial numbers
            }

            return {'success': True, 'data': summary_data}

        except Exception as e:
            logging.error(f"Error in calculate_leave_summary: {str(e)}")
            return {'success': False, 'error': f'Calculation error: {str(e)}'}
