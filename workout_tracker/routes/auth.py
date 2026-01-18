from datetime import timedelta

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import login_required, login_user, logout_user, current_user

from config import Config
from models import Session, User
from services.auth import AuthService, AuthenticationError
from utils.logger import logger

from .decorators import dev_only, require_admin


def register_auth_routes(app, email_service):
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('user_dashboard', username=current_user.username))

        if request.method == 'POST':
            try:
                name = request.form.get('name', '').strip()
                username = request.form.get('username', '').strip()
                email = request.form.get('email', '').strip()
                password = request.form.get('password', '')

                user, verification_code = AuthService.register_user(
                    username=username,
                    email=email,
                    password=password,
                    name=name,
                )

                email_sent = email_service.send_verification_email(
                    email=user.email,
                    username=user.username,
                    verification_code=verification_code,
                )

                if not email_sent:
                    logger.warning(f"Failed to send verification email to {email}")
                    flash(
                        "Account created but verification email could not be sent. Please contact support.",
                        "warning",
                    )
                else:
                    flash("Account created! Please check your email for the verification code.", "success")

                session['pending_verification_user_id'] = user.id
                return redirect(url_for('verify_email'))

            except AuthenticationError as e:
                flash(str(e), "error")
                return render_template('register.html')
            except Exception as e:
                logger.error(f"Registration error: {e}", exc_info=True)
                flash("An error occurred during registration. Please try again.", "error")
                return render_template('register.html')

        return render_template('register.html')

    def login():
        if current_user.is_authenticated:
            return redirect(url_for('user_dashboard', username=current_user.username))

        if request.method == 'POST':
            try:
                username_or_email = request.form.get('username_or_email', '').strip()
                password = request.form.get('password', '')
                remember_me = request.form.get('remember_me') == 'on'

                user = AuthService.authenticate_user(username_or_email, password)

                if not user:
                    flash("Invalid username/email or password.", "error")
                    return render_template('login.html')

                login_user(user, remember=remember_me, duration=timedelta(days=Config.REMEMBER_COOKIE_DURATION))

                flash(f"Welcome back, {user.username.title()}!", "success")

                next_page = request.args.get('next')
                if next_page:
                    return redirect(next_page)
                return redirect(url_for('user_dashboard', username=user.username))

            except AuthenticationError as e:
                flash(str(e), "error")
                return render_template('login.html')
            except Exception as e:
                logger.error(f"Login error: {e}", exc_info=True)
                flash("An error occurred during login. Please try again.", "error")
                return render_template('login.html')

        return render_template('login.html')

    def verify_email():
        if current_user.is_authenticated:
            return redirect(url_for('user_dashboard', username=current_user.username))

        user_id = session.get('pending_verification_user_id')
        if not user_id:
            flash("No pending verification found. Please register first.", "error")
            return redirect(url_for('register'))

        user = Session.query(User).get(user_id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for('register'))

        user_email = user.email
        user_username = user.username

        if user.is_verified:
            flash("Email already verified. Please log in.", "info")
            return redirect(url_for('login'))

        if request.method == 'POST':
            try:
                verification_code = request.form.get('verification_code', '').strip()

                if AuthService.verify_email(user_id, verification_code):
                    email_service.send_welcome_email(user_email, user_username)

                    session.pop('pending_verification_user_id', None)

                    Session.remove()
                    verified_user = Session.query(User).get(user_id)
                    if verified_user:
                        login_user(
                            verified_user,
                            remember=True,
                            duration=timedelta(days=Config.REMEMBER_COOKIE_DURATION),
                        )
                        flash("Email verified successfully! You're now signed in.", "success")
                        return redirect(url_for('user_dashboard', username=verified_user.username))

                    flash("Email verified successfully! Please log in.", "success")
                    return redirect(url_for('login'))

                flash("Invalid verification code. Please try again.", "error")

            except AuthenticationError as e:
                flash(str(e), "error")
            except Exception as e:
                logger.error(f"Verification error: {e}", exc_info=True)
                flash("An error occurred. Please try again.", "error")

        return render_template('verify_email.html', email=user_email)

    def resend_verification():
        user_id = session.get('pending_verification_user_id')
        if not user_id:
            flash("No pending verification found.", "error")
            return redirect(url_for('register'))

        try:
            user = Session.query(User).get(user_id)
            if not user:
                flash("User not found.", "error")
                return redirect(url_for('register'))

            user_email = user.email
            user_username = user.username

            verification_code = AuthService.resend_verification_code(user_id)

            email_sent = email_service.send_verification_email(
                email=user_email,
                username=user_username,
                verification_code=verification_code,
            )

            if email_sent:
                flash("Verification code resent! Please check your email.", "success")
            else:
                flash("Failed to send verification email. Please try again later.", "error")

        except AuthenticationError as e:
            flash(str(e), "error")
        except Exception as e:
            logger.error(f"Resend verification error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "error")

        return redirect(url_for('verify_email'))

    @login_required
    def logout():
        logout_user()
        flash("Logged out successfully.", "info")
        return redirect(url_for('login'))

    @dev_only
    @require_admin
    def internal_db_fix():
        return (
            "<pre>This endpoint is deprecated. Database schema migrations are now managed by Alembic.\n"
            "Run: alembic upgrade head\n"
            "For an existing database created before Alembic, you may need: alembic stamp head</pre>",
            410,
        )

    app.add_url_rule('/register', endpoint='register', view_func=register, methods=['GET', 'POST'])
    app.add_url_rule('/login', endpoint='login', view_func=login, methods=['GET', 'POST'])
    app.add_url_rule('/verify-email', endpoint='verify_email', view_func=verify_email, methods=['GET', 'POST'])
    app.add_url_rule('/resend-verification', endpoint='resend_verification', view_func=resend_verification, methods=['POST'])
    app.add_url_rule('/logout', endpoint='logout', view_func=logout, methods=['GET'])
    app.add_url_rule('/internal_db_fix', endpoint='internal_db_fix', view_func=internal_db_fix, methods=['GET'])
