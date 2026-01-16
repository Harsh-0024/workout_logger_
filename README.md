# Workout Tracker

A comprehensive Flask web application for logging gym workouts, tracking personal records, analyzing progress, and generating personalized workout plans.

## Features

### Core Functionality
- **Workout Logging**: Parse and log workout sessions from raw text input
- **Personal Records Tracking**: Automatically track and update PRs using strength metrics (estimated 1RM)
- **Workout Plan Generation**: Retrieve personalized workout plans based on your best performances
- **Progress Analytics**: Visualize your strength progress with interactive charts
- **Data Export**: Export workout history as CSV or JSON

### Advanced Features
- **Multi-User Support**: Separate profiles for different users
- **Exercise History**: Track all exercises with date-based history
- **Smart Parsing**: Intelligent workout text parser supporting multiple formats
- **Statistics Dashboard**: View current 1RM, max 1RM, improvement metrics, and percentage changes
- **Recent Workouts**: Quick access to your latest workout sessions
- **Responsive Design**: Beautiful dark theme UI optimized for mobile and desktop

## Tech Stack

- **Backend**: Flask (Python)
- **Database**: SQLAlchemy ORM with PostgreSQL/SQLite support
- **Frontend**: Bootstrap 5, Chart.js
- **Deployment**: Railway-ready (Procfile included)

## Installation

### Prerequisites
- Python 3.8+
- pip

### Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd Workout_plan
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables** (optional)
   ```bash
   export SECRET_KEY="your-secret-key-here"
   export DATABASE_URL="postgresql://user:pass@host/db"  # Optional, defaults to SQLite
   export FLASK_DEBUG=True  # For development
   export PORT=5001  # Optional, defaults to 5001
   ```

4. **Initialize the database**
   ```bash
   python app.py
   ```
   The database will be automatically initialized on first run.

5. **Import existing data** (optional)
   ```bash
   python3 import_lifts.py
   ```
   This will import best lifts, workout history, plans, and rep ranges from CSV files.
   See `IMPORT_DATA.md` for more details.

## Usage

### Starting the Application

```bash
python app.py
```

The application will start on `http://localhost:5001` (or the port specified in your environment).

### Logging a Workout

1. Navigate to the "Log" page
2. Paste your workout data in the format:
   ```
   15/01 Chest & Triceps 1
   Flat Barbell Press - 80x5, 85x5, 90x3
   Triceps Rod Pushdown - 40x10, 45x8, 50x6
   ...
   ```
3. Click "Analyze & Save"

### Retrieving a Workout Plan

1. Go to "Retrieve" from the navigation
2. Select a workout category (e.g., "Chest & Triceps")
3. Choose a day number
4. View your personalized plan with your best performances

### Viewing Statistics

1. Navigate to "Stats"
2. Select an exercise from the dropdown
3. View your progress chart and statistics:
   - Current 1RM
   - Maximum 1RM achieved
   - Improvement metrics
   - Percentage change

### Exporting Data

- **CSV Export**: Click "Export CSV" on the stats page
- **JSON Export**: Click "Export JSON" on the stats page

## Project Structure

```
Workout_plan/
├── app.py                 # Main Flask application
├── config.py             # Configuration management
├── models.py             # Database models
├── requirements.txt      # Python dependencies
├── Procfile              # Railway deployment config
├── parsers/
│   └── workout.py        # Workout text parser
├── services/
│   ├── logging.py       # Workout logging service
│   ├── retrieve.py     # Workout plan retrieval
│   ├── stats.py        # Statistics and exports
│   └── helpers.py      # Helper functions
├── utils/
│   ├── errors.py       # Custom exceptions
│   ├── validators.py   # Input validation
│   └── logger.py      # Logging configuration
└── templates/
    ├── base.html       # Base template
    ├── index.html     # Dashboard
    ├── log.html       # Workout logging form
    ├── stats.html     # Statistics page
    └── ...            # Other templates
```

## Configuration

The application uses a configuration system (`config.py`) that supports:

- **Development Mode**: Debug logging, detailed error messages
- **Production Mode**: Optimized settings, error handling
- **Database**: Automatic PostgreSQL/SQLite detection
- **Security**: Configurable secret keys, CSRF protection

## Database Schema

- **Users**: User accounts
- **Lifts**: Personal records for each exercise
- **Plans**: Workout plan templates
- **RepRanges**: Exercise-specific rep range preferences
- **WorkoutLogs**: Historical workout data with dates, weights, reps, and estimated 1RM

## API Endpoints

- `GET /` - Home page / User selection
- `GET /<username>` - User dashboard
- `GET /log` - Workout logging form
- `POST /log` - Submit workout
- `GET /stats` - Statistics dashboard
- `GET /stats/data/<exercise>` - Chart data (JSON)
- `GET /export_csv` - Export as CSV
- `GET /export_json` - Export as JSON
- `GET /retrieve/categories` - Workout categories
- `GET /retrieve/days/<category>` - Days for category
- `GET /retrieve/final/<category>/<day_id>` - Generated plan

## Development

### Running in Development Mode

```bash
export FLASK_DEBUG=True
python app.py
```

### Database Migrations

If you need to migrate from an old database schema, use:
```
GET /internal_db_fix
```

## Deployment

### Railway

The application is configured for Railway deployment with a `Procfile`:

```
web: python app.py
```

Set environment variables in Railway:
- `SECRET_KEY`: A secure random key
- `DATABASE_URL`: PostgreSQL connection string (auto-detected)

### Other Platforms

The application can be deployed to any platform supporting Flask:
- Heroku
- AWS Elastic Beanstalk
- Google Cloud Run
- DigitalOcean App Platform

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the MIT License.

## Support

For issues, questions, or feature requests, please open an issue on the repository.

---

**Built with ❤️ for fitness enthusiasts**
