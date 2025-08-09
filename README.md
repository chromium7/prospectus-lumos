# Prospectus Lumos - Financial Tracker

A Django application for tracking income and expenses by parsing Google Sheets containing monthly budget data.

## Project Structure

The project is organized into separate Django apps for better modularity:

- **`accounts`** - User profiles, Google Drive credentials, and document sources
- **`documents`** - Document storage and CSV file management
- **`transactions`** - Individual transaction records
- **`expenses`** - Views, services, and main application logic
- **`libraries/google_cloud`** - Google Drive and Sheets integration

## Features

- 🔐 User authentication and profiles
- 📊 Google Sheets integration for automatic data import
- 📝 CSV export and management
- 📈 Income and expense analysis with filtering
- 📱 Responsive web interface with Bootstrap
- 🎛️ Django admin interface for data management

## Quick Start

### Prerequisites

- Python 3.8+
- Virtual environment (recommended)

### Installation

1. **Clone and setup the project:**
   ```bash
   cd /Users/christofer/private/prospectus_lumos/prospectus_lumos
   source /Users/christofer/private/prospectus_lumos/env/bin/activate
   pip install -r requirements.txt
   ```

2. **Run database migrations:**
   ```bash
   python manage.py migrate
   ```

3. **Create sample data:**
   ```bash
   python manage.py setup_sample_data
   ```

4. **Create a superuser (optional):**
   ```bash
   python manage.py createsuperuser
   ```

5. **Start the development server:**
   ```bash
   python manage.py runserver
   ```

6. **Access the application:**
   - Main app: http://localhost:8000/
   - Admin panel: http://localhost:8000/admin/

### Test Credentials

- **Test user:** `testuser` / `testpass123`
- **Admin user:** `admin` / `admin123`

## Application Structure

### Apps

#### Accounts App (`prospectus_lumos.accounts`)
Manages user-related functionality:
- `UserProfile` - Extended user information
- `GoogleDriveCredentials` - Google service account credentials
- `DocumentSource` - Configuration for document import sources

#### Documents App (`prospectus_lumos.documents`)
Handles document storage:
- `Document` - Processed CSV files with metadata and statistics

#### Transactions App (`prospectus_lumos.transactions`)
Manages transaction data:
- `Transaction` - Individual income/expense entries

#### Expenses App (`prospectus_lumos.expenses`)
Main application logic:
- Views for authentication, dashboard, and analysis
- Services for Google Sheets processing and data analysis

### Key Features

#### Google Sheets Integration
- Automatically discovers "Monthly budget" sheets by name pattern
- Parses "Transactions" sheet with expenses and income tables
- Extracts data with Indonesian Rupiah (Rp) currency format
- Creates CSV files and transaction records

#### Dashboard
- Overview statistics (total income, expenses, net income)
- Recent documents list
- Document source management
- Quick access to analyzers

#### Analysis Tools
- **Income Analyzer**: Total income, averages, category breakdown
- **Expense Analyzer**: Total expenses, averages, category analysis
- Filtering by year and month
- Visual category breakdowns

#### Document Management
- List all processed documents with search and filtering
- Download CSV files
- Pagination for large datasets

## Google Drive Setup

To use Google Drive integration:

1. **Create a Google Cloud Project**
2. **Enable Google Drive and Sheets APIs**
3. **Create a Service Account** and download the JSON credentials
4. **In Django Admin:**
   - Upload the service account JSON file
   - Add the Google Drive folder URL containing budget sheets
   - Create a Document Source linked to the credentials

### Expected Sheet Format

The Google Sheets should be named like:
- "Monthly budget Jan 2025"
- "Monthly budget Feb 2025"

Each sheet should have a "Transactions" tab with:
- **Expenses section** with columns: Date, Amount, Description, Category
- **Income section** with columns: Date, Amount, Description, Category
- Amounts in Indonesian Rupiah format (e.g., "Rp153.700")

## API and Services

### ExpenseSheetService
- `sync_google_drive_documents()` - Import from Google Drive
- `_extract_month_year()` - Parse sheet names
- `_create_csv_content()` - Generate CSV files
- `_create_transaction_records()` - Store individual transactions

### ExpenseAnalyzerService
- `get_income_analysis()` - Income statistics and breakdowns
- `get_expense_analysis()` - Expense statistics and breakdowns

### GoogleDriveBackend
Enhanced with new methods:
- `parse_monthly_budget_sheet()` - Extract expenses and income
- `list_monthly_budget_files()` - Find matching sheets
- `get_sheet_names()` - List all sheets in a workbook

## Development

### Project Layout
```
prospectus_lumos/
├── libraries/google_cloud/     # Google Drive integration
├── prospectus_lumos/
│   ├── accounts/              # User and credentials models
│   ├── documents/             # Document storage models
│   ├── transactions/          # Transaction models
│   ├── expenses/              # Main app logic
│   └── settings.py
├── templates/                 # HTML templates
└── manage.py
```

### Key Management Commands
- `python manage.py setup_sample_data` - Create test data
- `python manage.py migrate` - Apply database changes
- `python manage.py collectstatic` - Collect static files

### Adding New Features
1. Models go in the appropriate app (`accounts`, `documents`, `transactions`)
2. Views and business logic in `expenses` app
3. Templates in `templates/expenses/`
4. Admin configurations in each app's `admin.py`

## Database Schema

### Key Relationships
- User → UserProfile (1:1)
- User → GoogleDriveCredentials (1:1)
- User → DocumentSource (1:N)
- DocumentSource → Document (1:N)
- Document → Transaction (1:N)

### Migration Management
If you encounter migration issues:
```bash
python manage.py migrate <app> zero  # Reset specific app
python manage.py migrate             # Reapply all migrations
```

## Production Deployment

1. **Update settings for production:**
   - Set `DEBUG = False`
   - Configure proper database (PostgreSQL recommended)
   - Set up static file serving
   - Configure `ALLOWED_HOSTS`

2. **Environment variables:**
   - `SECRET_KEY`
   - Database credentials
   - Google service account file path

3. **Security considerations:**
   - Use HTTPS
   - Secure file uploads
   - Regular backup of user data and credentials

## License

This project is for personal/educational use. Please ensure compliance with Google APIs terms of service when using Google Drive integration.
