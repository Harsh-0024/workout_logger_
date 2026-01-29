from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import login_required, login_user, logout_user, current_user
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

from config import Config
from models import Session, User, UserRole
from services.auth import AuthService, AuthenticationError
from utils.errors import ValidationError
from utils.logger import logger
from utils.validators import sanitize_text_input, validate_username

from .decorators import dev_only, require_admin

import threading
import time
from collections import deque

_RATE_LIMIT_BUCKETS = {}
_RATE_LIMIT_LOCK = threading.Lock()


def register_auth_routes(app, email_service):
    def _is_safe_redirect_url(target: str) -> bool:
        if not target:
            return False
        ref_url = urlparse(request.host_url)
        test_url = urlparse(urljoin(request.host_url, target))
        return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

    def _get_client_ip() -> str:
        forwarded = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
        if forwarded:
            return forwarded
        return request.remote_addr or 'unknown'

    def _allow_rate_limit(bucket: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        with _RATE_LIMIT_LOCK:
            dq = _RATE_LIMIT_BUCKETS.get(bucket)
            if dq is None:
                dq = deque()
                _RATE_LIMIT_BUCKETS[bucket] = dq

            cutoff = now - float(window_seconds)
            while dq and dq[0] <= cutoff:
                dq.popleft()

            if len(dq) >= int(limit):
                return False

            dq.append(now)
            return True

    def _is_allowed_profile_image(filename: str) -> bool:
        if not filename or '.' not in filename:
            return False
        ext = filename.rsplit('.', 1)[1].lower()
        return ext in {'png', 'jpg', 'jpeg', 'webp'}

    def _save_profile_image(user_id: int, file_storage) -> str:
        uploads_dir = Path(app.static_folder) / 'uploads' / 'avatars'
        uploads_dir.mkdir(parents=True, exist_ok=True)

        filename = secure_filename(file_storage.filename or '')
        if not _is_allowed_profile_image(filename):
            raise AuthenticationError("Unsupported profile image type.")

        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)
        image = image.convert('RGB')

        size = 320
        image = ImageOps.fit(image, (size, size), Image.LANCZOS)

        output_name = f"user_{user_id}.png"
        output_path = uploads_dir / output_name
        image.save(output_path, format='PNG', optimize=True)

        return f"uploads/avatars/{output_name}"

    def _enforce_rate_limit(action: str, identifier: str | None, limit: int, window_seconds: int) -> None:
        if not app.config.get('ENABLE_RATE_LIMITING', False):
            return

        ip = _get_client_ip()
        buckets = [f"{action}:ip:{ip}"]
        if identifier:
            buckets.append(f"{action}:id:{identifier.strip().lower()}")

        for bucket in buckets:
            if not _allow_rate_limit(bucket, limit=limit, window_seconds=window_seconds):
                raise AuthenticationError("Too many attempts. Please wait and try again.")

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

                email_sent = email_service.send_otp_email(
                    email=user.email,
                    username=user.username,
                    otp_code=verification_code,
                    purpose='verify_email',
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

                _enforce_rate_limit('login', username_or_email, limit=10, window_seconds=600)

                user = AuthService.authenticate_user(username_or_email, password)

                if not user:
                    flash("Invalid username/email or password.", "error")
                    return render_template('login.html')

                if user.email and user.email.lower() in Config.ADMIN_EMAIL_ALLOWLIST and not user.is_admin():
                    db_user = Session.query(User).get(user.id)
                    if db_user and not db_user.is_admin():
                        db_user.role = UserRole.ADMIN
                        db_user.updated_at = datetime.now()
                        Session.commit()
                    user.role = UserRole.ADMIN

                login_user(user, remember=remember_me, duration=timedelta(days=Config.REMEMBER_COOKIE_DURATION))

                flash(f"Welcome back, {user.username.title()}!", "success")

                next_page = request.args.get('next')
                if next_page and _is_safe_redirect_url(next_page):
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

    def login_otp_request():
        if current_user.is_authenticated:
            return redirect(url_for('user_dashboard', username=current_user.username))

        identifier = ''
        if request.method == 'POST':
            identifier = request.form.get('username_or_email', '').strip()
            try:
                _enforce_rate_limit('login_otp_request', identifier, limit=5, window_seconds=600)
                otp_payload = AuthService.request_login_otp(identifier)

                email_sent = email_service.send_otp_email(
                    email=otp_payload['email'],
                    username=otp_payload['username'],
                    otp_code=otp_payload['otp_code'],
                    purpose='login_otp',
                )

                if not email_sent:
                    flash("Unable to send login code. Please try again later.", "error")
                    return render_template('request_otp.html', identifier=identifier)

                session['pending_otp_user_id'] = otp_payload['id']
                session['pending_otp_identifier'] = identifier
                session['pending_otp_purpose'] = 'login_otp'

                flash("Login code sent! Check your email.", "success")
                return redirect(url_for('verify_login_otp'))

            except AuthenticationError as e:
                flash(str(e), "error")
            except Exception as e:
                logger.error(f"OTP login request error: {e}", exc_info=True)
                flash("An error occurred. Please try again.", "error")

        if request.method != 'POST':
            identifier = request.args.get('identifier', '').strip() or session.get('pending_otp_identifier', '')

        return render_template('request_otp.html', identifier=identifier)

    def verify_login_otp():
        if current_user.is_authenticated:
            return redirect(url_for('user_dashboard', username=current_user.username))

        user_id = session.get('pending_otp_user_id')
        if not user_id:
            flash("No login code requested. Please request a new code.", "error")
            return redirect(url_for('login_otp_request'))

        user = Session.query(User).get(user_id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for('login'))

        user_email = user.email

        if request.method == 'POST':
            try:
                otp_code = request.form.get('otp_code', '').strip()

                _enforce_rate_limit('login_otp_verify', str(user_id), limit=15, window_seconds=600)

                if AuthService.verify_otp(user_id, otp_code, 'login_otp', mark_verified=True):
                    session.pop('pending_otp_user_id', None)
                    session.pop('pending_otp_identifier', None)
                    session.pop('pending_otp_purpose', None)

                    verified_user = Session.query(User).get(user_id)
                    if verified_user and verified_user.email:
                        if verified_user.email.lower() in Config.ADMIN_EMAIL_ALLOWLIST and not verified_user.is_admin():
                            verified_user.role = UserRole.ADMIN
                            verified_user.updated_at = datetime.now()
                            Session.commit()

                    if verified_user:
                        login_user(
                            verified_user,
                            remember=True,
                            duration=timedelta(days=Config.REMEMBER_COOKIE_DURATION),
                        )
                        session['otp_login_verified'] = True
                        session['otp_login_user_id'] = verified_user.id
                        flash("Signed in with a one-time code. Please set a new password.", "success")
                        return redirect(url_for('user_settings') + '#change-password')

                    flash("Login successful. Please sign in again.", "success")
                    return redirect(url_for('login'))

                flash("Invalid code. Please try again.", "error")
            except AuthenticationError as e:
                flash(str(e), "error")
            except Exception as e:
                logger.error(f"OTP login verification error: {e}", exc_info=True)
                flash("An error occurred. Please try again.", "error")

        return render_template(
            'verify_otp.html',
            email=user_email,
            purpose_label='Login',
            resend_url=url_for('resend_login_otp'),
            action_url=url_for('verify_login_otp'),
        )

    def resend_login_otp():
        identifier = session.get('pending_otp_identifier')
        if not identifier:
            flash("Please request a new login code.", "error")
            return redirect(url_for('login_otp_request'))

        try:
            _enforce_rate_limit('login_otp_resend', identifier, limit=5, window_seconds=600)
            otp_payload = AuthService.request_login_otp(identifier)
            email_sent = email_service.send_otp_email(
                email=otp_payload['email'],
                username=otp_payload['username'],
                otp_code=otp_payload['otp_code'],
                purpose='login_otp',
            )

            if not email_sent:
                flash("Unable to resend code. Please try again.", "error")
                return redirect(url_for('verify_login_otp'))

            session['pending_otp_user_id'] = otp_payload['id']
            session['pending_otp_purpose'] = 'login_otp'
            flash("A new login code has been sent.", "success")
        except AuthenticationError as e:
            flash(str(e), "error")
        except Exception as e:
            logger.error(f"OTP resend error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "error")

        return redirect(url_for('verify_login_otp'))

    def verify_email():
        redirect_username = None
        if current_user.is_authenticated:
            user_id = current_user.id
            redirect_username = current_user.username
        else:
            user_id = session.get('pending_verification_user_id')
            if not user_id:
                flash("No pending verification found.", "error")
                return redirect(url_for('login'))

        user = Session.query(User).get(user_id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for('register'))

        user_email = user.email
        user_username = user.username

        if user.is_verified:
            flash("Email already verified.", "info")
            if current_user.is_authenticated:
                return redirect(url_for('user_dashboard', username=current_user.username))
            return redirect(url_for('login'))

        if request.method == 'POST':
            try:
                verification_code = request.form.get('verification_code', '').strip()

                if AuthService.verify_email(user_id, verification_code):
                    if not current_user.is_authenticated:
                        try:
                            email_service.send_welcome_email(user_email, user_username)
                        except Exception as exc:
                            logger.error(f"Welcome email failed: {exc}", exc_info=True)

                    session.pop('pending_verification_user_id', None)

                    if current_user.is_authenticated:
                        flash("Email verified successfully!", "success")
                        return redirect(url_for('user_dashboard', username=redirect_username))

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
        if current_user.is_authenticated:
            user_id = current_user.id
        else:
            user_id = session.get('pending_verification_user_id')
            if not user_id:
                flash("No pending verification found.", "error")
                return redirect(url_for('login'))

        try:
            user = Session.query(User).get(user_id)
            if not user:
                flash("User not found.", "error")
                return redirect(url_for('register'))

            user_email = user.email
            user_username = user.username

            verification_code = AuthService.resend_verification_code(user_id)

            email_sent = email_service.send_otp_email(
                email=user_email,
                username=user_username,
                otp_code=verification_code,
                purpose='verify_email',
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

    @login_required
    def user_settings():
        user = current_user
        user_id = user.id
        user_email = user.email
        otp_login_verified = (
            session.get('otp_login_verified')
            and session.get('otp_login_user_id') == user.id
        )
        password_change_verified = (
            session.get('password_change_verified')
            and session.get('password_change_user_id') == user.id
        )
        pending_password_change = (
            session.get('pending_password_change')
            and session.get('password_change_user_id') == user.id
        )
        allow_otp_profile = otp_login_verified or password_change_verified

        if request.method == 'POST':
            form_type = request.form.get('form_type') or 'profile'

            try:
                if form_type == 'profile_photo':
                    image_file = request.files.get('profile_image')
                    if not image_file or not image_file.filename:
                        flash("Please choose a profile photo to upload.", "error")
                        return redirect(url_for('user_settings'))

                    if not _is_allowed_profile_image(image_file.filename):
                        flash("Unsupported file type. Use PNG, JPG, or WEBP.", "error")
                        return redirect(url_for('user_settings'))

                    try:
                        user.profile_image = _save_profile_image(user.id, image_file)
                        user.updated_at = datetime.now()
                        Session.commit()
                        flash("Profile photo updated successfully!", "success")
                    except AuthenticationError as e:
                        Session.rollback()
                        flash(str(e), "error")
                    except Exception as e:
                        Session.rollback()
                        logger.error(f"Profile photo update failed: {e}", exc_info=True)
                        flash("Failed to update profile photo. Please try again.", "error")
                    return redirect(url_for('user_settings'))

                if form_type in {'profile', 'profile_otp'}:
                    username = sanitize_text_input(request.form.get('username', ''), max_length=30)
                    email = sanitize_text_input(request.form.get('email', ''), max_length=255)
                    current_password = request.form.get('current_password', '')
                    bodyweight_raw = request.form.get('bodyweight', '').strip()
                    bodyweight = user.bodyweight

                    if bodyweight_raw:
                        try:
                            bodyweight = float(bodyweight_raw)
                        except ValueError:
                            flash("Bodyweight must be a number.", "error")
                            return redirect(url_for('user_settings'))

                        if bodyweight <= 0:
                            flash("Bodyweight must be greater than 0.", "error")
                            return redirect(url_for('user_settings'))

                    if not username or not email:
                        flash("Username and email are required.", "error")
                        return redirect(url_for('user_settings'))

                    if form_type == 'profile':
                        if not current_password and not allow_otp_profile:
                            flash("Please enter your current password to update profile details.", "error")
                            return redirect(url_for('user_settings'))

                        if current_password and not AuthService.verify_password(current_password, user.password_hash):
                            raise AuthenticationError("Current password is incorrect")

                    username = validate_username(username)

                    existing_username = (
                        Session.query(User)
                        .filter(User.username == username, User.id != user_id)
                        .first()
                    )
                    if existing_username:
                        flash("That username is already taken.", "error")
                        return redirect(url_for('user_settings'))

                    existing_email = (
                        Session.query(User)
                        .filter(User.email == email.lower(), User.id != user_id)
                        .first()
                    )
                    if existing_email:
                        flash("That email is already in use.", "error")
                        return redirect(url_for('user_settings'))

                    email = email.lower()
                    email_changed = email != (user_email or '').lower()
                    username_changed = username != user.username

                    if (
                        form_type == 'profile'
                        and not current_password
                        and allow_otp_profile
                        and (username_changed or email_changed)
                    ):
                        flash("Please enter your current password to update your username or email.", "error")
                        return redirect(url_for('user_settings'))

                    if email_changed:
                        session['pending_email_change'] = {
                            'username': username,
                            'email': email,
                            'current_email': user_email,
                            'bodyweight': bodyweight,
                        }
                        session['pending_email_change_user_id'] = user_id

                        otp_payload = AuthService.request_email_change_otps(
                            user_id,
                            current_email=user_email,
                            new_email=email,
                        )
                        old_email_sent = email_service.send_otp_email(
                            email=otp_payload['current_email'],
                            username=otp_payload['username'],
                            otp_code=otp_payload['otp_old'],
                            purpose='change_email_old',
                        )
                        new_email_sent = email_service.send_otp_email(
                            email=otp_payload['new_email'],
                            username=otp_payload['username'],
                            otp_code=otp_payload['otp_new'],
                            purpose='change_email_new',
                        )

                        if old_email_sent and new_email_sent:
                            flash("Two verification codes were sent to your old and new emails.", "info")
                            return redirect(url_for('verify_email_change_otp'))

                        flash("Unable to send email change codes. Please try again.", "error")
                        return redirect(url_for('user_settings'))

                    if form_type == 'profile_otp':
                        session['pending_profile_update'] = {
                            'username': username,
                            'email': email,
                            'bodyweight': bodyweight,
                        }
                        otp_payload = AuthService.request_profile_update_otp(user.id)
                        email_sent = email_service.send_otp_email(
                            email=otp_payload['email'],
                            username=otp_payload['username'],
                            otp_code=otp_payload['otp_code'],
                            purpose='profile_update',
                        )
                        if email_sent:
                            flash("One-time code sent to your email.", "info")
                            return redirect(url_for('verify_profile_update_otp'))
                        flash("Unable to send OTP. Please try again.", "error")
                        return redirect(url_for('user_settings'))

                    user.username = username
                    user.email = email
                    if bodyweight is not None:
                        user.bodyweight = bodyweight
                    user.updated_at = datetime.now()
                    if email in Config.ADMIN_EMAIL_ALLOWLIST and not user.is_admin():
                        user.role = UserRole.ADMIN

                    Session.commit()

                    flash("Profile updated successfully!", "success")
                    return redirect(url_for('user_settings'))

                if form_type == 'password':
                    current_password = request.form.get('current_password', '')
                    new_password = request.form.get('new_password', '')
                    confirm_password = request.form.get('confirm_password', '')

                    if new_password != confirm_password:
                        flash("New password and confirmation do not match.", "error")
                        return redirect(url_for('user_settings'))

                    if otp_login_verified or password_change_verified:
                        AuthService.set_password(user.id, new_password)
                        session.pop('otp_login_verified', None)
                        session.pop('otp_login_user_id', None)
                        session.pop('password_change_verified', None)
                        session.pop('password_change_user_id', None)
                        flash("Password updated successfully!", "success")
                        return redirect(url_for('user_settings'))

                    if not current_password:
                        flash("Please enter your current password or use the OTP option.", "error")
                        return redirect(url_for('user_settings'))

                    if not AuthService.verify_password(current_password, user.password_hash):
                        raise AuthenticationError("Current password is incorrect")

                    AuthService.set_password(user.id, new_password)
                    flash("Password updated successfully!", "success")
                    return redirect(url_for('user_settings'))

                if form_type == 'password_otp_request':
                    otp_payload = AuthService.request_password_change_otp(user.id)
                    email_sent = email_service.send_otp_email(
                        email=otp_payload['email'],
                        username=otp_payload['username'],
                        otp_code=otp_payload['otp_code'],
                        purpose='change_password',
                    )

                    if email_sent:
                        session['pending_password_change'] = True
                        session['password_change_user_id'] = user.id
                        flash("A verification code was sent to your email.", "info")
                        return redirect(url_for('user_settings') + '#change-password')

                    flash("Unable to send a verification code. Please try again.", "error")
                    return redirect(url_for('user_settings'))

                if form_type == 'password_otp':
                    otp_code = request.form.get('otp_code', '').strip()
                    new_password = request.form.get('new_password', '')
                    confirm_password = request.form.get('confirm_password', '')

                    if not pending_password_change:
                        flash("Request a code first to use OTP password change.", "error")
                        return redirect(url_for('user_settings') + '#change-password')

                    if not otp_code:
                        flash("Please enter the one-time code.", "error")
                        return redirect(url_for('user_settings') + '#change-password')

                    if new_password != confirm_password:
                        flash("New password and confirmation do not match.", "error")
                        return redirect(url_for('user_settings') + '#change-password')

                    if AuthService.verify_otp(user.id, otp_code, 'change_password'):
                        AuthService.set_password(user.id, new_password)
                        session.pop('pending_password_change', None)
                        session.pop('password_change_user_id', None)
                        session.pop('otp_login_verified', None)
                        session.pop('otp_login_user_id', None)
                        session.pop('password_change_verified', None)
                        session.pop('password_change_user_id', None)
                        flash("Password updated successfully!", "success")
                        return redirect(url_for('user_settings'))

                    flash("Invalid code. Please try again.", "error")
                    return redirect(url_for('user_settings') + '#change-password')

                if form_type == 'password_otp_cancel':
                    session.pop('pending_password_change', None)
                    session.pop('password_change_user_id', None)
                    flash("OTP password change cancelled.", "info")
                    return redirect(url_for('user_settings') + '#change-password')

                flash("Invalid settings request.", "error")
                return redirect(url_for('user_settings'))
            except (AuthenticationError, ValidationError) as e:
                Session.rollback()
                flash(str(e), "error")
                return redirect(url_for('user_settings'))
            except Exception as e:
                Session.rollback()
                logger.error(f"Settings update error: {e}", exc_info=True)
                flash("Failed to update settings. Please try again.", "error")
                return redirect(url_for('user_settings'))

        profile_image_url = None
        if getattr(user, 'profile_image', None):
            profile_image_url = url_for('static', filename=user.profile_image)

        return render_template(
            'settings.html',
            user=user,
            otp_login_verified=otp_login_verified,
            password_change_verified=password_change_verified,
            pending_password_change=pending_password_change,
            profile_image_url=profile_image_url,
        )

    @login_required
    def verify_profile_update_otp():
        pending_update = session.get('pending_profile_update')
        if not pending_update:
            flash("No pending profile update found.", "error")
            return redirect(url_for('user_settings'))

        user_id = current_user.id
        user = Session.query(User).get(user_id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for('login'))

        user_email = user.email

        if request.method == 'POST':
            try:
                otp_code = request.form.get('otp_code', '').strip()

                if AuthService.verify_otp(user_id, otp_code, 'profile_update'):
                    username = pending_update.get('username')
                    email = pending_update.get('email')
                    bodyweight = pending_update.get('bodyweight')

                    if not username or not email:
                        flash("Missing profile update details.", "error")
                        return redirect(url_for('user_settings'))

                    username = validate_username(username)
                    existing_username = (
                        Session.query(User)
                        .filter(User.username == username, User.id != user_id)
                        .first()
                    )
                    if existing_username:
                        flash("That username is already taken.", "error")
                        return redirect(url_for('user_settings'))

                    existing_email = (
                        Session.query(User)
                        .filter(User.email == email.lower(), User.id != user_id)
                        .first()
                    )
                    if existing_email:
                        flash("That email is already in use.", "error")
                        return redirect(url_for('user_settings'))

                    email = email.lower()
                    email_changed = email != (user_email or '').lower()

                    if email_changed:
                        session['pending_profile_update'] = {
                            'username': username,
                            'email': email,
                            'bodyweight': bodyweight,
                        }
                        session['pending_email_change'] = {
                            'username': username,
                            'email': email,
                            'current_email': user_email,
                            'bodyweight': bodyweight,
                        }
                        session['pending_email_change_user_id'] = user_id

                        otp_payload = AuthService.request_email_change_otps(
                            user_id,
                            current_email=user_email,
                            new_email=email,
                        )
                        old_email_sent = email_service.send_otp_email(
                            email=otp_payload['current_email'],
                            username=otp_payload['username'],
                            otp_code=otp_payload['otp_old'],
                            purpose='change_email_old',
                        )
                        new_email_sent = email_service.send_otp_email(
                            email=otp_payload['new_email'],
                            username=otp_payload['username'],
                            otp_code=otp_payload['otp_new'],
                            purpose='change_email_new',
                        )

                        session.pop('pending_profile_update', None)

                        if old_email_sent and new_email_sent:
                            flash("Two verification codes were sent to your old and new emails.", "info")
                            return redirect(url_for('verify_email_change_otp'))

                        flash("Unable to send email change codes. Please try again.", "error")
                        return redirect(url_for('user_settings'))

                    user = Session.query(User).get(user_id)
                    if not user:
                        flash("User not found.", "error")
                        return redirect(url_for('login'))

                    user.username = username
                    user.email = email
                    if bodyweight is not None:
                        user.bodyweight = bodyweight
                    user.updated_at = datetime.now()
                    if email in Config.ADMIN_EMAIL_ALLOWLIST and not user.is_admin():
                        user.role = UserRole.ADMIN

                    Session.commit()
                    session.pop('pending_profile_update', None)

                    flash("Profile updated successfully!", "success")
                    return redirect(url_for('user_settings'))

                flash("Invalid code. Please try again.", "error")
            except AuthenticationError as e:
                flash(str(e), "error")
            except Exception as e:
                Session.rollback()
                logger.error(f"Profile OTP verification error: {e}", exc_info=True)
                flash("Failed to verify code. Please try again.", "error")

        return render_template(
            'verify_otp.html',
            email=user_email,
            purpose_label='Profile Update',
            resend_url=url_for('resend_profile_update_otp'),
            action_url=url_for('verify_profile_update_otp'),
        )

    @login_required
    def verify_password_change_otp():
        if not session.get('pending_password_change'):
            flash("No password change requested.", "error")
            return redirect(url_for('user_settings'))

        if session.get('password_change_user_id') != current_user.id:
            flash("Password change verification mismatch.", "error")
            return redirect(url_for('user_settings'))

        user = Session.query(User).get(current_user.id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for('login'))

        user_email = user.email

        if request.method == 'POST':
            try:
                otp_code = request.form.get('otp_code', '').strip()

                if AuthService.verify_otp(user.id, otp_code, 'change_password'):
                    session['password_change_verified'] = True
                    session['password_change_user_id'] = user.id
                    session.pop('pending_password_change', None)
                    flash("Code verified. Please enter your new password.", "success")
                    return redirect(url_for('user_settings') + '#change-password')

                flash("Invalid code. Please try again.", "error")
            except AuthenticationError as e:
                flash(str(e), "error")
            except Exception as e:
                logger.error(f"Password change OTP verification error: {e}", exc_info=True)
                flash("Failed to verify code. Please try again.", "error")

        return render_template(
            'verify_otp.html',
            email=user_email,
            purpose_label='Change Password',
            resend_url=url_for('resend_password_change_otp'),
            action_url=url_for('verify_password_change_otp'),
        )

    @login_required
    def resend_password_change_otp():
        try:
            otp_payload = AuthService.request_password_change_otp(current_user.id)
            email_sent = email_service.send_otp_email(
                email=otp_payload['email'],
                username=otp_payload['username'],
                otp_code=otp_payload['otp_code'],
                purpose='change_password',
            )

            if email_sent:
                session['pending_password_change'] = True
                session['password_change_user_id'] = current_user.id
                flash("A new code has been sent to your email.", "success")
            else:
                flash("Unable to resend code. Please try again.", "error")
        except AuthenticationError as e:
            flash(str(e), "error")
        except Exception as e:
            logger.error(f"Password change OTP resend error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "error")

        return redirect(url_for('verify_password_change_otp'))

    @login_required
    def verify_email_change_otp():
        pending_change = session.get('pending_email_change')
        if not pending_change:
            flash("No pending email change found.", "error")
            return redirect(url_for('user_settings'))

        if session.get('pending_email_change_user_id') != current_user.id:
            flash("Email change verification mismatch.", "error")
            return redirect(url_for('user_settings'))

        user = Session.query(User).get(current_user.id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for('login'))

        user_email = user.email
        current_email = pending_change.get('current_email') or user_email
        new_email = pending_change.get('email') or ''

        if request.method == 'POST':
            try:
                otp_old = request.form.get('otp_code_old', '').strip()
                otp_new = request.form.get('otp_code_new', '').strip()

                if not otp_old or not otp_new:
                    flash("Please enter both codes.", "error")
                    return redirect(url_for('verify_email_change_otp'))

                old_valid = AuthService.verify_otp(user.id, otp_old, 'change_email_old')
                new_valid = AuthService.verify_otp(user.id, otp_new, 'change_email_new')

                if old_valid and new_valid:
                    username = pending_change.get('username')
                    email = pending_change.get('email')

                    if not username or not email:
                        flash("Missing email change details.", "error")
                        return redirect(url_for('user_settings'))

                    user = Session.query(User).get(current_user.id)
                    if not user:
                        flash("User not found.", "error")
                        return redirect(url_for('login'))

                    user.username = username
                    user.email = email.lower()
                    bodyweight = pending_change.get('bodyweight')
                    if bodyweight is not None:
                        user.bodyweight = bodyweight
                    user.is_verified = True
                    user.updated_at = datetime.now()
                    if user.email in Config.ADMIN_EMAIL_ALLOWLIST and not user.is_admin():
                        user.role = UserRole.ADMIN

                    Session.commit()

                    session.pop('pending_email_change', None)
                    session.pop('pending_email_change_user_id', None)

                    flash("Email updated successfully!", "success")
                    return redirect(url_for('user_settings'))

                flash("Invalid codes. Please try again.", "error")
            except AuthenticationError as e:
                flash(str(e), "error")
            except Exception as e:
                Session.rollback()
                logger.error(f"Email change OTP verification error: {e}", exc_info=True)
                flash("Failed to verify codes. Please try again.", "error")

        return render_template(
            'verify_email_change_otp.html',
            current_email=current_email,
            new_email=new_email,
            resend_url=url_for('resend_email_change_otp'),
            action_url=url_for('verify_email_change_otp'),
        )

    @login_required
    def resend_email_change_otp():
        pending_change = session.get('pending_email_change')
        if not pending_change:
            flash("No pending email change found.", "error")
            return redirect(url_for('user_settings'))

        try:
            otp_payload = AuthService.request_email_change_otps(
                current_user.id,
                current_email=pending_change.get('current_email'),
                new_email=pending_change.get('email'),
            )

            old_email_sent = email_service.send_otp_email(
                email=otp_payload['current_email'],
                username=otp_payload['username'],
                otp_code=otp_payload['otp_old'],
                purpose='change_email_old',
            )
            new_email_sent = email_service.send_otp_email(
                email=otp_payload['new_email'],
                username=otp_payload['username'],
                otp_code=otp_payload['otp_new'],
                purpose='change_email_new',
            )

            if old_email_sent and new_email_sent:
                flash("New codes have been sent to both email addresses.", "success")
            else:
                flash("Unable to resend codes. Please try again.", "error")
        except AuthenticationError as e:
            flash(str(e), "error")
        except Exception as e:
            logger.error(f"Email change OTP resend error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "error")

        return redirect(url_for('verify_email_change_otp'))

    @login_required
    def resend_profile_update_otp():
        pending_update = session.get('pending_profile_update')
        if not pending_update:
            flash("No pending profile update found.", "error")
            return redirect(url_for('user_settings'))

        try:
            otp_payload = AuthService.request_profile_update_otp(current_user.id)
            email_sent = email_service.send_otp_email(
                email=otp_payload['email'],
                username=otp_payload['username'],
                otp_code=otp_payload['otp_code'],
                purpose='profile_update',
            )

            if email_sent:
                flash("A new code has been sent to your email.", "success")
            else:
                flash("Unable to resend code. Please try again.", "error")
        except AuthenticationError as e:
            flash(str(e), "error")
        except Exception as e:
            logger.error(f"Profile OTP resend error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "error")

        return redirect(url_for('verify_profile_update_otp'))

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
    app.add_url_rule('/login/otp', endpoint='login_otp_request', view_func=login_otp_request, methods=['GET', 'POST'])
    app.add_url_rule('/login/otp/verify', endpoint='verify_login_otp', view_func=verify_login_otp, methods=['GET', 'POST'])
    app.add_url_rule('/login/otp/resend', endpoint='resend_login_otp', view_func=resend_login_otp, methods=['POST'])
    app.add_url_rule('/verify-email', endpoint='verify_email', view_func=verify_email, methods=['GET', 'POST'])
    app.add_url_rule('/resend-verification', endpoint='resend_verification', view_func=resend_verification, methods=['POST'])
    app.add_url_rule('/logout', endpoint='logout', view_func=logout, methods=['GET'])
    app.add_url_rule('/settings', endpoint='user_settings', view_func=user_settings, methods=['GET', 'POST'])
    app.add_url_rule(
        '/settings/verify-otp',
        endpoint='verify_profile_update_otp',
        view_func=verify_profile_update_otp,
        methods=['GET', 'POST'],
    )
    app.add_url_rule(
        '/settings/otp/resend',
        endpoint='resend_profile_update_otp',
        view_func=resend_profile_update_otp,
        methods=['POST'],
    )
    app.add_url_rule(
        '/settings/password/verify-otp',
        endpoint='verify_password_change_otp',
        view_func=verify_password_change_otp,
        methods=['GET', 'POST'],
    )
    app.add_url_rule(
        '/settings/password/resend-otp',
        endpoint='resend_password_change_otp',
        view_func=resend_password_change_otp,
        methods=['POST'],
    )
    app.add_url_rule(
        '/settings/email/verify-otp',
        endpoint='verify_email_change_otp',
        view_func=verify_email_change_otp,
        methods=['GET', 'POST'],
    )
    app.add_url_rule(
        '/settings/email/resend-otp',
        endpoint='resend_email_change_otp',
        view_func=resend_email_change_otp,
        methods=['POST'],
    )
    app.add_url_rule('/internal_db_fix', endpoint='internal_db_fix', view_func=internal_db_fix, methods=['GET'])
