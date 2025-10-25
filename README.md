# Leave Management System

A Flask-based Leave Management System . This application digitizes an Excel-based leave tracking system.

## Quick Start (Development)

1. The application is already running at the preview URL
2. Login with default credentials: `admin` / `admin`
3. **IMPORTANT**: Change the admin password immediately after first login

## Default Login Credentials

- **Employee Number**: admin
- **Password**: admin

## Security Notice ⚠️

### FOR DEVELOPMENT USE ONLY

This application has default credentials for development convenience. Before deploying to production:

1. **CRITICAL - Change Admin Password**: Login and change the default admin/admin password immediately
2. **REQUIRED - Set Session Secret**: Add `SESSION_SECRET` environment variable with a strong random value
3. **RECOMMENDED - Use PostgreSQL**: Set `DATABASE_URL` environment variable to use PostgreSQL instead of SQLite

### Current Security Limitations

- Default admin user is auto-created with password "admin"
- Session secret defaults to "default_secret_key_for_development" if not set via environment variable
- SQLite database is used by default (not recommended for production)

## Deployment

This application is configured for Replit VM deployment to support:
- Persistent SQLite database storage
- Local file uploads in the uploads/ directory

### To Deploy Safely:

1. Set environment variables in Replit Secrets:
   - `SESSION_SECRET`: A strong random string (minimum 32 characters)
   - `DATABASE_URL`: PostgreSQL connection string (recommended for production)

2. Login immediately and change the admin password

3. Click the Deploy button in Replit

## Features

- Role-based access (admin/employee)
- Leave entry and tracking
- Leave balance calculations
- CSV import for master data and leave entries
- Deduction reports with Excel export
- Bulk leave summaries

## Technical Stack

- Flask 3.1.2
- SQLAlchemy 2.0.43
- Pandas 2.3.2
- Gunicorn 23.0.0 (production server)
- Bootstrap 5 (dark theme)

## Documentation

See `replit.md` for detailed system architecture, recent changes, and configuration details.
