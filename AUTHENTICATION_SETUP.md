# Authentication System Setup Guide

## Overview

Your Workout Tracker has been upgraded with a comprehensive authentication system featuring:

✅ **User Registration & Login** with email verification  
✅ **Secure Password Storage** using bcrypt hashing  
✅ **Persistent Sessions** with "Remember Me" (30-day cookies)  
✅ **Email Verification** with 6-digit codes (24-hour expiry)  
✅ **Role-Based Access Control** (User & Admin roles)  
✅ **Admin Panel** for user management with email notifications  

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Email Settings

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and configure your email settings:
   ```env
   MAIL_SERVER=smtp.gmail.com
   MAIL_PORT=587
   MAIL_USERNAME=your-email@gmail.com
   MAIL_PASSWORD=your-app-specific-password
   ```

   **For Gmail:**
   - Enable 2-Factor Authentication on your Google Account
   - Generate an "App Password" at: https://myaccount.google.com/apppasswords
   - Use the app password (not your regular password) in `MAIL_PASSWORD`

### 3. Migrate Existing Database

Run the migration script to upgrade your database:

```bash
python migrate_auth.py
```

This will:
- Add authentication columns to your database
- Migrate existing users with default credentials
- Create an admin account

**Default credentials will be displayed after migration.**

### 4. Start the Application

```bash
python app.py
```

Visit `http://localhost:5001` and you'll be redirected to the login page.

---

## Default Credentials (After Migration)

### Admin Account
- **Username:** `admin`
- **Email:** `admin@workouttracker.local`
- **Password:** `Admin123!SecurePassword`

### Existing Users
- **Email:** `{username}@workouttracker.local`
- **Password:** `ChangeMe123!`

⚠️ **IMPORTANT:** Change these passwords immediately after first login!

---

## Architecture

### Security Features

1. **Password Hashing**
   - Bcrypt with automatic salt generation
   - Passwords never stored in plain text
   - Industry-standard security

2. **Email Verification**
   - 6-digit verification codes
   - HMAC-based token generation
   - 24-hour expiration
   - Secure code delivery via email

3. **Session Management**
   - Flask-Login integration
   - Persistent cookies for "Remember Me"
   - 30-day default duration
   - Secure session handling

4. **Role-Based Access Control (RBAC)**
   - Two roles: USER and ADMIN
   - Enum-based role system (clean & type-safe)
   - Admin-only routes protected with `@require_admin` decorator

### Database Schema Changes

New columns added to `users` table:

| Column | Type | Description |
|--------|------|-------------|
| `email` | VARCHAR(255) | User's email address (unique) |
| `password_hash` | VARCHAR(255) | Bcrypt hashed password |
| `role` | ENUM | User role (user/admin) |
| `is_verified` | BOOLEAN | Email verification status |
| `verification_token` | VARCHAR(255) | Current verification code |
| `verification_token_expires` | TIMESTAMP | Token expiration time |

---

## User Flow

### Registration Flow

1. User visits `/register`
2. Fills in: Name, Username, Email, Password
3. System creates account with `is_verified=False`
4. 6-digit verification code sent to email
5. User enters code at `/verify-email`
6. Account activated, welcome email sent
7. User can now log in

### Login Flow

1. User visits `/login`
2. Enters username/email and password
3. System verifies credentials and email verification status
4. Optional "Remember Me" checkbox (30-day cookie)
5. Redirects to personal dashboard

### Admin Flow

1. Admin logs in normally
2. Accesses admin panel at `/admin`
3. Views all users (no access to private workout data)
4. Can delete users with custom reason message
5. Deleted user receives email notification with reason

---

## Admin Capabilities

### User Management

**View Users:**
- See all registered users
- View: username, email, role, verification status, join date
- **Privacy:** Cannot see workout data or passwords

**Delete Users:**
1. Click "Delete" next to any user
2. Enter deletion reason (required)
3. User receives email notification before deletion
4. All user data deleted (cascading delete)

**Restrictions:**
- Cannot delete own account
- Cannot delete other admins
- Must provide deletion reason

### Accessing Admin Panel

Navigate to: `http://localhost:5001/admin`

Only users with `role=ADMIN` can access this page.

---

## API Routes

### Public Routes
- `GET /register` - Registration page
- `POST /register` - Process registration
- `GET /login` - Login page
- `POST /login` - Process login
- `GET /verify-email` - Email verification page
- `POST /verify-email` - Process verification code
- `POST /resend-verification` - Resend verification code

### Protected Routes (Login Required)
- `GET /logout` - Logout user
- `GET /<username>` - User dashboard
- `GET /log` - Log workout page
- `GET /stats` - Statistics page
- `GET /export_csv` - Export workout data
- All other existing workout routes

### Admin Routes (Admin Role Required)
- `GET /admin` - Admin dashboard
- `POST /admin/delete-user` - Delete user account

---

## Email Templates

The system includes three email templates:

### 1. Verification Email
- **Subject:** "Verify Your Workout Tracker Account"
- **Content:** 6-digit verification code
- **Design:** Modern HTML with gradient header
- **Expiry:** 24 hours

### 2. Welcome Email
- **Subject:** "Welcome to Workout Tracker!"
- **Content:** Welcome message with feature overview
- **Sent:** After successful email verification

### 3. Account Deletion Email
- **Subject:** "Workout Tracker Account Deleted"
- **Content:** Custom admin message explaining deletion
- **Sent:** Before account deletion

---

## Creating Additional Admins

### Option 1: Promote Existing User (Recommended)

```python
from models import Session, User, UserRole

session = Session()
user = session.query(User).filter_by(username='username_here').first()
user.role = UserRole.ADMIN
session.commit()
session.close()
```

### Option 2: Register New Admin via Code

```python
from services.auth import AuthService
from models import Session, User, UserRole

# Register new admin
user, code = AuthService.register_user(
    username='new_admin',
    email='admin@example.com',
    password='SecurePassword123!',
    is_admin=True  # This creates an admin
)

# Manually verify
session = Session()
user_obj = session.query(User).get(user.id)
user_obj.is_verified = True
session.commit()
session.close()
```

---

## Security Best Practices

### For Production Deployment

1. **Change SECRET_KEY**
   ```env
   SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
   ```

2. **Use Strong Passwords**
   - Minimum 8 characters
   - Mix of letters, numbers, symbols
   - Change all default passwords

3. **Configure HTTPS**
   - Use SSL/TLS certificates
   - Enable secure cookies

4. **Email Security**
   - Use app-specific passwords (not main account password)
   - Enable 2FA on email account
   - Use dedicated email for app notifications

5. **Database Backups**
   - Regular automated backups
   - Test restore procedures

6. **Rate Limiting**
   - Consider adding rate limiting for login attempts
   - Prevent brute force attacks

7. **Monitoring**
   - Monitor failed login attempts
   - Track admin actions
   - Set up error notifications

---

## Troubleshooting

### Email Not Sending

**Check:**
1. Email credentials in `.env` are correct
2. App password (not regular password) for Gmail
3. SMTP server and port are correct
4. TLS/SSL settings match your provider
5. Check application logs for error messages

**Test Email Configuration:**
```python
from flask_mail import Mail, Message
from app import app, mail

with app.app_context():
    msg = Message("Test", recipients=["test@example.com"])
    msg.body = "Test email"
    mail.send(msg)
```

### Migration Issues

**Common issues:**
1. Database locked - Close any connections
2. Column already exists - Migration already run
3. Permission denied - Check file permissions

**Reset migration:**
```bash
# Backup database first!
cp workout.db workout.db.backup

# Then run migration again
python migrate_auth.py
```

### Login Issues

**Symptoms:**
- "Email not verified" error
- "Invalid credentials" error

**Solutions:**
1. Check email verification status in database
2. Verify password hash exists
3. Check user's `is_verified` field
4. Try password reset (to be implemented)

---

## Configuration Reference

### Email Providers

| Provider | SMTP Server | Port | TLS |
|----------|-------------|------|-----|
| Gmail | smtp.gmail.com | 587 | True |
| Outlook | smtp-mail.outlook.com | 587 | True |
| Yahoo | smtp.mail.yahoo.com | 587 | True |
| SendGrid | smtp.sendgrid.net | 587 | True |
| Mailgun | smtp.mailgun.org | 587 | True |

### Config Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | dev-secret-key | Flask secret key |
| `REMEMBER_COOKIE_DURATION` | 30 | Days for remember me |
| `VERIFICATION_TOKEN_EXPIRY` | 24 | Hours until code expires |
| `MAIL_SERVER` | smtp.gmail.com | SMTP server |
| `MAIL_PORT` | 587 | SMTP port |
| `MAIL_USE_TLS` | True | Use TLS encryption |

---

## Support

For issues or questions:
1. Check this documentation
2. Review application logs
3. Check the code comments in service files
4. Verify environment configuration

---

## Future Enhancements

Potential additions to the authentication system:

- [ ] Password reset functionality
- [ ] Two-factor authentication (2FA)
- [ ] OAuth integration (Google, GitHub)
- [ ] Rate limiting on login attempts
- [ ] Account lockout after failed attempts
- [ ] Password strength requirements
- [ ] Email change verification
- [ ] Admin activity audit log
- [ ] Bulk user operations
- [ ] User suspension (temporary disable)

---

**Last Updated:** January 2026  
**Version:** 1.0.0
