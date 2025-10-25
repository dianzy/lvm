# Overview

This is a Flask-based Leave Management System designed . The application digitizes an Excel-based leave tracking system, allowing employees to view their leave summaries and administrators to manage user data and upload CSV files. The system calculates leave balances based on complex business rules that mirror the original VBA logic from Excel.

# Recent Changes

## Bug Fixes and UX Improvements (October 16, 2025)
- **Cumulative LOP Display Fix**: Fixed cumulative LOP calculation in leave summary
  - Now correctly shows opening LOP + LOP days from leave entries
  - Previously was only showing opening LOP value from master sheet
  - Displayed on Line 1 of leave summary alongside PL Accrued
- **Half-day CL Limit Warning**: Added validation for half-day CL occasions
  - System now warns when an employee exceeds 6 half-day CL occasions
  - Counts both CL_HALFDAY type and CL with session F/A as half-day occasions
  - Warning appears when creating or editing leave entries
  - Excludes the current leave being edited when counting existing occasions
- **Calendar Month Persistence**: Improved leave entry form usability
  - Calendar now stays on the last entered month instead of resetting to today's date
  - When adding a new leave after saving one, the date picker opens on the previously entered date
  - Reduces clicks and improves data entry speed for consecutive leave entries

## Attendance Tracking System (October 15, 2025)
- **New Feature**: Physical attendance tracking with index numbers
  - Department management with alphabetical ordering
  - Monthly attendance grid (department × month matrix)
  - Index value support for chaotic numbering (1, 2, 170a, etc.)
  - Auto-save functionality with visual feedback
  - Excel import/export for bulk operations
  - Red highlighting for missing submissions
- **Database Models**:
  - AttendanceDepartment: Store department names with sort order
  - AttendanceIndex: Store index values with year/month/department links
- **User Interface**:
  - Real-time auto-save with 500ms debounce
  - Visual feedback (yellow=saving, green=saved, red=missing)
  - Excel upload modal for bulk import
  - Department management page
  - Export to Excel functionality
- **Navigation**: Added to Admin dropdown menu
- **Enhanced Features** (October 15, 2025 - Evening Update):
  - **Global Next Index Number**: Calculates and displays a single next index number at the top of the page, based on the maximum value found across ALL departments and ALL months
  - **Department Progress Report**: Summary table showing which month each department has submitted attendance up to, with color-coded badges (green for submitted, yellow for no submissions)
  - **Smart Index Handling**: Extracts numeric values from alphanumeric indices (e.g., "170a" → suggests 171 as next number)
  - **Sequential Numbering**: Index numbers are sequential across all departments (e.g., if HR submits 200 for June, then HR submits 201 for May)
  - **Visual Display**: Prominent display of global next index number in green alert banner at top of page

## Replit Environment Setup (October 15, 2025)
- **GitHub Import Configuration**: Successfully configured the project to run in Replit environment
  - Python 3.11 with uv package manager for dependency management
  - All dependencies installed via pyproject.toml (Flask, SQLAlchemy, Pandas, Gunicorn, etc.)
  - Flask development server properly configured with host='0.0.0.0' on port 5000
  - ProxyFix middleware already configured for Replit's proxy environment
- **Deployment Configuration**: 
  - Deployment target set to VM (required for SQLite database and local file uploads)
  - Production server: Gunicorn with --reuse-port flag
  - Note: SQLite database and uploads/ directory are persistent on VM deployment
- **Development Workflow**: Configured to run "uv run python main.py" with webview output
- **Security Enhancements**:
  - Added runtime security warnings in app.py that display when using default credentials
  - SESSION_SECRET warning appears at startup if environment variable not set
  - Admin user creation warning displays default credentials when admin user is first created
  - See README.md for comprehensive security documentation
- **Security Requirements for Production**: 
  - **CRITICAL**: Default admin credentials are admin/admin - MUST be changed immediately after first login
  - **REQUIRED**: Set SESSION_SECRET environment variable with a strong random value before deployment
  - **RECOMMENDED**: Use PostgreSQL via DATABASE_URL environment variable instead of SQLite for production
  - **WARNING**: The application auto-creates admin user with default password on first run - this is a development convenience only

## Previous Changes (October 12, 2025)

## Cumulative LOP Tracking and Encashment Logic Fix
- **Cumulative LOP Column**: Added `lop` field to MasterData model to track cumulative leave without pay
  - Column added to database with automatic migration for existing databases
  - Displayed in both individual and bulk leave summaries next to "PL Accrued" on Line 1
  - Editable in master data management (add/edit employee forms)
  - Visible in master data list view
- **Encashment Calculation Enhancement**: 
  - Changed from hardcoded 15 days to calculating actual days from date range (lvfrom to lvto)
  - Removed date blocking restriction - encashment can now overlap with other leave types
  - Encashment entries now properly calculate days based on provided from/to dates
- **Database Migration**: Robust migration logic that works for both fresh and existing SQLite/PostgreSQL databases
  - Uses SQLAlchemy inspector to check for missing columns post-creation
  - Automatic ALTER TABLE commands to add lop and is_entered columns if missing

## Entry Tracking System for Deduction Reports
- **Excel Export Fix**: Installed xlsxwriter library to enable Excel export functionality for deduction reports
- **Entry Status Tracking**: Added `is_entered` boolean field to LeaveEntry model to track which entries have been processed/entered
- **Mark Entries Feature**: 
  - New route `/mark_entries_entered` allows marking entries as entered up to a specific S.No
  - Visual indicators show entered entries with green background and checkmark icon
  - Form in deduction report header lets users select up to which S.No to mark
  - Marking is cumulative - all entries from 1 to selected S.No are marked
- **UI Enhancements**: 
  - All three tables (All Deductions, LOP, SL HP) now show entry status
  - Clear visual distinction between entered and pending entries
  - JavaScript confirmation dialog before marking entries
- **Database Migration**: Added automatic migration logic for is_entered column in SQLite
- **DATABASE_URL Fix**: Improved handling of empty DATABASE_URL environment variable to prevent startup failures

## Leave Summary Restructuring
- **Individual Leave Summary**: Reorganized layout to show three distinct lines:
  - Line 1: Opening balances (PL, CL, SL) + PL Accrued
  - Line 2: Used balances (PL, CL, SL) + LOP + Maternity + Encashment
  - Line 3: Closing balances (PL, CL, SL, RH) + Employee Status
  
- **Bulk Leave Summary**: Complete redesign to match individual summary format:
  - Input: Comma-separated employee numbers + as-on date
  - Output: Individual-style summaries for each employee, displayed one after another
  - PDF Export: Utilizes browser print with CSS page breaks for one page per employee
  - Removed detailed/simple mode toggle - all summaries now show complete information

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Frontend Architecture
- **Template Engine**: Jinja2 templates with Bootstrap 5 dark theme for responsive UI
- **Static Assets**: CSS styling with Font Awesome icons for enhanced user experience
- **Navigation**: Role-based navigation with different menu options for admins and regular employees
- **Forms**: Server-side form handling for login, password changes, file uploads, and leave calculations

## Backend Architecture
- **Web Framework**: Flask with SQLAlchemy ORM for database operations
- **Session Management**: Flask sessions for user authentication and state management
- **File Upload**: Werkzeug secure filename handling with configurable upload limits (16MB)
- **Password Security**: Werkzeug password hashing for secure credential storage
- **Business Logic**: Dedicated LeaveCalculator class that replicates Excel VBA leave calculation logic

## Database Schema
- **Users Table**: Stores employee credentials with role-based access (admin/employee)
- **MasterData Table**: Contains employee master information including opening leave balances
- **LeaveEntry Table**: Tracks individual leave transactions with various leave types
- **Database Support**: SQLite for development with PostgreSQL compatibility via environment variables

## Authentication and Authorization
- **Role-Based Access**: Two-tier system with admin and employee roles
- **Session-Based Auth**: Server-side session management for user state
- **Password Management**: Self-service password changes and admin password reset capabilities
- **Default Credentials**: Admin account with configurable initial setup

## Leave Calculation Logic
- **Business Rules**: Complex leave calculation engine that processes different leave types (PL, CL, SL, RH, LOP, Maternity)
- **Date Processing**: Handles partial days, session-based leaves, and date range calculations
- **Summary Generation**: Real-time leave balance calculations based on master data and leave entries

# External Dependencies

## Core Framework Dependencies
- **Flask**: Web application framework with SQLAlchemy integration
- **Werkzeug**: WSGI utilities for password hashing and file handling
- **Pandas**: CSV file processing and data manipulation for bulk uploads

## Frontend Dependencies
- **Bootstrap 5**: CSS framework with dark theme support via CDN
- **Font Awesome 6**: Icon library for enhanced UI elements
- **Replit Bootstrap Theme**: Custom dark theme integration

## Database Technology
- **SQLAlchemy**: ORM with DeclarativeBase for database operations
- **SQLite**: Default database for development and testing
- **PostgreSQL**: Production database support via DATABASE_URL environment variable

## File Processing
- **CSV Upload**: Pandas-based CSV parsing for master data and leave entry imports
- **Excel Export**: xlsxwriter library for generating Excel reports from deduction data
- **File Security**: Werkzeug secure filename handling for upload safety
- **Storage**: Local filesystem storage with configurable upload directory