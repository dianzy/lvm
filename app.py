import os
import logging
import sqlite3
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix

# Set up logging
logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Create the app
app = Flask(__name__)

# Session secret configuration with security warning
session_secret = os.environ.get("SESSION_SECRET")
if not session_secret:
    session_secret = "default_secret_key_for_development"
    logging.warning("="*80)
    logging.warning("⚠️  SECURITY WARNING: Using default SESSION_SECRET for development")
    logging.warning("⚠️  This is NOT SECURE for production deployment!")
    logging.warning("⚠️  Set SESSION_SECRET environment variable before deploying")
    logging.warning("="*80)

app.secret_key = session_secret
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure the database
database_url = os.environ.get("DATABASE_URL") or "sqlite:///leave_management.db"
app.config["SQLALCHEMY_DATABASE_URI"] = database_url

# Configure engine options based on database type
if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
    # PostgreSQL configuration
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_timeout": 20,
        "pool_reset_on_return": "commit",
    }
else:
    # SQLite configuration
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_timeout": 20,
        "pool_reset_on_return": "commit",
        "connect_args": {"check_same_thread": False, "timeout": 20}
    }

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# Initialize the app with the extensions
db.init_app(app)

def add_missing_columns():
    """Add missing columns before SQLAlchemy initializes"""
    try:
        # Get database path from config
        db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
        if db_uri.startswith("sqlite:///"):
            db_path = db_uri.replace("sqlite:///", "")

            # Only proceed if database file exists
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()

                # Check if master_data table exists
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='master_data'")
                if cursor.fetchone():
                    # Check if 'l' column exists
                    cursor.execute("PRAGMA table_info(master_data)")
                    columns = [row[1] for row in cursor.fetchall()]

                    if 'l' not in columns:
                        logging.info("Adding missing 'l' column to master_data table...")
                        cursor.execute("ALTER TABLE master_data ADD COLUMN l VARCHAR(1) DEFAULT 'C'")
                        conn.commit()
                        logging.info("✅ Successfully added 'l' column")

                    if 'lop' not in columns:
                        logging.info("Adding missing 'lop' column to master_data table...")
                        cursor.execute("ALTER TABLE master_data ADD COLUMN lop FLOAT DEFAULT 0")
                        conn.commit()
                        logging.info("✅ Successfully added 'lop' column")

                # Check if leave_entry table exists and add is_entered column
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leave_entry'")
                if cursor.fetchone():
                    cursor.execute("PRAGMA table_info(leave_entry)")
                    columns = [row[1] for row in cursor.fetchall()]

                    if 'is_entered' not in columns:
                        logging.info("Adding missing 'is_entered' column to leave_entry table...")
                        cursor.execute("ALTER TABLE leave_entry ADD COLUMN is_entered BOOLEAN DEFAULT 0")
                        conn.commit()
                        logging.info("✅ Successfully added 'is_entered' column")

                conn.close()
        elif db_uri.startswith("postgresql://") or db_uri.startswith("postgres://"):
            # For PostgreSQL, we'll handle migrations after tables are created
            logging.info("PostgreSQL detected - migrations will be handled after table creation")

    except Exception as e:
        logging.error(f"Migration error: {str(e)}")
        # Continue anyway - the app should still work without the L column

# Perform migration before importing routes
with app.app_context():
    add_missing_columns()

# Now we can safely add the L column to our models
from sqlalchemy import text

def update_master_data_model():
    """Dynamically add L column to MasterData model if it exists in database"""
    try:
        from models import MasterData
        
        db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
        
        # Test if L column exists by trying to query it
        if db_uri.startswith("sqlite:///"):
            result = db.session.execute(text("PRAGMA table_info(master_data)"))
            columns = [row[1] for row in result]
        elif db_uri.startswith("postgresql://") or db_uri.startswith("postgres://"):
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'master_data'
            """))
            columns = [row[0] for row in result]
        else:
            columns = []

        if 'l' in columns:
            # Dynamically add the L column to the model
            if not hasattr(MasterData, 'l'):
                MasterData.l = db.Column(db.String(1), default='C')
                logging.info("✅ Added L column to MasterData model")

    except Exception as e:
        logging.warning(f"Could not add L column to model: {e}")

# Update the model after migration
with app.app_context():
    update_master_data_model()

# Create tables and admin user
with app.app_context():
    # Import models here to avoid circular import
    from models import User, MasterData, LeaveEntry
    from sqlalchemy import text, inspect
    
    # Create all tables
    db.create_all()
    logging.info("Database tables created")
    
    # Add missing columns if tables already exist
    inspector = inspect(db.engine)
    
    # Check and add lop column to master_data if missing
    if 'master_data' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('master_data')]
        if 'lop' not in columns:
            try:
                db.session.execute(text("ALTER TABLE master_data ADD COLUMN lop FLOAT DEFAULT 0"))
                db.session.commit()
                logging.info("✅ Added lop column to master_data")
            except Exception as e:
                logging.error(f"Could not add lop column: {e}")
    
    # Check and add is_entered column to leave_entry if missing  
    if 'leave_entry' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('leave_entry')]
        if 'is_entered' not in columns:
            try:
                db.session.execute(text("ALTER TABLE leave_entry ADD COLUMN is_entered BOOLEAN DEFAULT 0"))
                db.session.commit()
                logging.info("✅ Added is_entered column to leave_entry")
            except Exception as e:
                logging.error(f"Could not add is_entered column: {e}")

    # Create admin user if it doesn't exist and check for default password
    admin_user = User.query.filter_by(emp_no='admin').first()
    if not admin_user:
        admin_user = User(
            emp_no='admin',
            name='Administrator',
            is_admin=True
        )
        admin_user.set_password('admin')
        db.session.add(admin_user)
        db.session.commit()
        logging.warning("="*80)
        logging.warning("⚠️  SECURITY WARNING: Admin user created with default credentials")
        logging.warning("⚠️  Username: admin | Password: admin")
        logging.warning("⚠️  CHANGE THIS PASSWORD IMMEDIATELY after first login!")
        logging.warning("="*80)
    elif admin_user.check_password('admin'):
        logging.warning("="*80)
        logging.warning("⚠️  SECURITY WARNING: Admin account still uses DEFAULT PASSWORD")
        logging.warning("⚠️  Username: admin | Password: admin")
        logging.warning("⚠️  CHANGE THIS PASSWORD IMMEDIATELY!")
        logging.warning("="*80)

# Import routes AFTER all initialization is complete
from routes import *
