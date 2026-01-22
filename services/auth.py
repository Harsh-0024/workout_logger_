"""
Authentication service for user registration, login, and password management.
"""
import bcrypt
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from models import User, UserRole, EmailVerification, Session, _seed_user_data, session_factory
from utils.logger import logger
from config import Config


class AuthenticationError(Exception):
    pass


class AuthService:
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')
    
    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        try:
            return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
        except Exception:
            return False
    
    @staticmethod
    def generate_verification_token() -> str:
        """Generate a secure random verification token."""
        return secrets.token_urlsafe(32)
    
    @staticmethod
    def generate_verification_code() -> str:
        """Generate a 6-digit verification code."""
        return str(secrets.randbelow(900000) + 100000)

    @staticmethod
    def _get_email_verification_expiry(purpose: str) -> datetime:
        if purpose == 'verify_email':
            return datetime.now() + timedelta(hours=Config.VERIFICATION_TOKEN_EXPIRY)
        return datetime.now() + timedelta(minutes=Config.OTP_TOKEN_EXPIRY_MINUTES)

    @staticmethod
    def _create_email_verification(session, user_id: int, email: str, purpose: str) -> str:
        if not email:
            raise AuthenticationError("This account has no email on file")

        session.query(EmailVerification).filter(
            EmailVerification.user_id == user_id,
            EmailVerification.email == email,
            EmailVerification.purpose == purpose,
            EmailVerification.verified_at.is_(None),
        ).delete(synchronize_session=False)

        otp_code = AuthService.generate_verification_code()
        expires_at = AuthService._get_email_verification_expiry(purpose)
        verification = EmailVerification(
            user_id=user_id,
            email=email,
            purpose=purpose,
            code=otp_code,
            expires_at=expires_at,
            created_at=datetime.now(),
        )
        session.add(verification)
        return otp_code

    @staticmethod
    def _get_latest_email_verification(session, user_id: int, purpose: str, email: Optional[str] = None):
        query = session.query(EmailVerification).filter(
            EmailVerification.user_id == user_id,
            EmailVerification.purpose == purpose,
            EmailVerification.verified_at.is_(None),
        )
        if email:
            query = query.filter(EmailVerification.email == email)
        return query.order_by(EmailVerification.created_at.desc()).first()
    
    @staticmethod
    def register_user(
        username: str,
        email: str,
        password: str,
        name: Optional[str] = None,
        is_admin: bool = False
    ) -> Tuple[User, str]:
        """
        Register a new user.
        
        Returns:
            Tuple of (User object, verification_code)
        
        Raises:
            AuthenticationError: If registration fails
        """
        session = Session()
        try:
            # Validate inputs
            if not username or len(username) < 3:
                raise AuthenticationError("Username must be at least 3 characters")
            
            if not email or '@' not in email:
                raise AuthenticationError("Valid email is required")
            
            if not password or len(password) < 8:
                raise AuthenticationError("Password must be at least 8 characters")
            
            # Check if username or email already exists
            existing_user = session.query(User).filter(
                (User.username == username.lower()) | (User.email == email.lower())
            ).first()
            
            if existing_user:
                if existing_user.username == username.lower():
                    raise AuthenticationError("Username already taken")
                else:
                    raise AuthenticationError("Email already registered")
            
            # Create user
            email_lower = email.lower()
            role = UserRole.ADMIN if is_admin or email_lower in Config.ADMIN_EMAIL_ALLOWLIST else UserRole.USER
            user = User(
                username=username.lower(),
                email=email_lower,
                password_hash=AuthService.hash_password(password),
                role=role,
                is_verified=False,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )

            session.add(user)
            session.flush()
            verification_code = AuthService._create_email_verification(
                session,
                user_id=user.id,
                email=user.email,
                purpose='verify_email',
            )
            _seed_user_data(session, user)
            session.commit()
            session.refresh(user)
            
            return user, verification_code
            
        except AuthenticationError:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            raise AuthenticationError(f"Registration failed: {str(e)}")
        finally:
            session.close()

    @staticmethod
    def request_password_change_otp(user_id: int) -> dict:
        """Generate and store a one-time code for password changes."""
        session = Session()
        try:
            user = session.query(User).get(user_id)
            if not user:
                raise AuthenticationError("User not found")

            if not user.email:
                raise AuthenticationError("This account has no email on file")

            otp_code = AuthService._create_email_verification(
                session,
                user_id=user.id,
                email=user.email,
                purpose='change_password',
            )
            session.commit()

            return {
                'id': user.id,
                'email': user.email,
                'username': user.username,
                'otp_code': otp_code,
            }

        except AuthenticationError:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to request password change OTP: {e}", exc_info=True)
            raise AuthenticationError("Unable to send a password change code right now. Please try again.")
        finally:
            session.close()

    @staticmethod
    def request_email_change_otps(user_id: int, current_email: str, new_email: str) -> dict:
        """Generate OTPs for confirming an email change on both old and new addresses."""
        session = Session()
        try:
            user = session.query(User).get(user_id)
            if not user:
                raise AuthenticationError("User not found")

            if not current_email or not new_email:
                raise AuthenticationError("Both current and new email addresses are required")

            otp_old = AuthService._create_email_verification(
                session,
                user_id=user.id,
                email=current_email,
                purpose='change_email_old',
            )
            otp_new = AuthService._create_email_verification(
                session,
                user_id=user.id,
                email=new_email,
                purpose='change_email_new',
            )
            session.commit()

            return {
                'id': user.id,
                'username': user.username,
                'current_email': current_email,
                'new_email': new_email,
                'otp_old': otp_old,
                'otp_new': otp_new,
            }

        except AuthenticationError:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to request email change OTPs: {e}", exc_info=True)
            raise AuthenticationError("Unable to send email change codes right now. Please try again.")
        finally:
            session.close()

    @staticmethod
    def set_password(user_id: int, new_password: str) -> bool:
        """Set a user's password without verifying the current password."""
        session = Session()
        try:
            user = session.query(User).get(user_id)

            if not user:
                return False

            if len(new_password) < 8:
                raise AuthenticationError("New password must be at least 8 characters")

            user.password_hash = AuthService.hash_password(new_password)
            user.updated_at = datetime.now()
            session.commit()
            return True

        except AuthenticationError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    @staticmethod
    def request_login_otp(username_or_email: str) -> dict:
        """Generate and store a one-time code for login."""
        session = Session()
        try:
            identifier = (username_or_email or '').strip().lower()
            if not identifier:
                raise AuthenticationError("Username or email is required")

            user = session.query(User).filter(
                (User.username == identifier) | (User.email == identifier)
            ).first()

            if not user:
                raise AuthenticationError("Account not found for that username or email")

            if not user.email:
                raise AuthenticationError("This account has no email on file")

            otp_code = AuthService._create_email_verification(
                session,
                user_id=user.id,
                email=user.email,
                purpose='login_otp',
            )
            session.commit()

            return {
                'id': user.id,
                'email': user.email,
                'username': user.username,
                'otp_code': otp_code,
            }

        except AuthenticationError:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to request login OTP: {e}", exc_info=True)
            raise AuthenticationError("Unable to send a login code right now. Please try again.")
        finally:
            session.close()

    @staticmethod
    def request_profile_update_otp(user_id: int) -> dict:
        """Generate and store a one-time code for profile updates."""
        session = Session()
        try:
            user = session.query(User).get(user_id)
            if not user:
                raise AuthenticationError("User not found")

            if not user.email:
                raise AuthenticationError("This account has no email on file")

            otp_code = AuthService._create_email_verification(
                session,
                user_id=user.id,
                email=user.email,
                purpose='profile_update',
            )
            session.commit()

            return {
                'id': user.id,
                'email': user.email,
                'username': user.username,
                'otp_code': otp_code,
            }

        except AuthenticationError:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to request profile OTP: {e}", exc_info=True)
            raise AuthenticationError("Unable to send a verification code right now. Please try again.")
        finally:
            session.close()

    @staticmethod
    def verify_otp(user_id: int, otp_code: str, purpose: str, mark_verified: bool = False) -> bool:
        """Verify a one-time code for a given purpose."""
        session = session_factory()
        try:
            user = session.query(User).get(user_id)
            if not user:
                return False

            verification = AuthService._get_latest_email_verification(session, user.id, purpose)
            if not verification:
                return False

            if verification.expires_at and verification.expires_at < datetime.now():
                raise AuthenticationError("One-time code has expired")

            if verification.code != otp_code:
                return False

            verification.verified_at = datetime.now()
            if mark_verified and not user.is_verified:
                user.is_verified = True
            user.updated_at = datetime.now()
            session.commit()
            return True

        except AuthenticationError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()
    
    @staticmethod
    def verify_email(user_id: int, verification_code: str) -> bool:
        """
        Verify a user's email with the verification code.
        
        Returns:
            True if verification successful, False otherwise
        """
        session = Session()
        try:
            user = session.query(User).get(user_id)
            
            if not user:
                return False
            
            if user.is_verified:
                return True

            verification = AuthService._get_latest_email_verification(session, user.id, 'verify_email', user.email)
            if not verification:
                return False

            if verification.expires_at and verification.expires_at < datetime.now():
                raise AuthenticationError("Verification code has expired")

            if verification.code != verification_code:
                return False

            verification.verified_at = datetime.now()
            user.is_verified = True
            user.updated_at = datetime.now()
            session.commit()
            return True
            
        except AuthenticationError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()
    
    @staticmethod
    def resend_verification_code(user_id: int) -> str:
        """
        Generate and return a new verification code for a user.
        
        Returns:
            New verification code
        
        Raises:
            AuthenticationError: If user not found or already verified
        """
        session = Session()
        try:
            user = session.query(User).get(user_id)
            
            if not user:
                raise AuthenticationError("User not found")
            
            if user.is_verified:
                raise AuthenticationError("Email already verified")
            
            verification_code = AuthService._create_email_verification(
                session,
                user_id=user.id,
                email=user.email,
                purpose='verify_email',
            )
            user.updated_at = datetime.now()
            session.commit()
            return verification_code
            
        except AuthenticationError:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            raise AuthenticationError(f"Failed to resend code: {str(e)}")
        finally:
            session.close()
    
    @staticmethod
    def authenticate_user(username_or_email: str, password: str) -> Optional[User]:
        """
        Authenticate a user with username/email and password.
        
        Returns:
            User object if authentication successful, None otherwise
        """
        session = Session()
        try:
            # Try to find user by username or email
            user = session.query(User).filter(
                (User.username == username_or_email.lower()) | 
                (User.email == username_or_email.lower())
            ).first()
            
            if not user:
                return None
            
            # Verify password
            if not AuthService.verify_password(password, user.password_hash):
                return None
            
            # Check if verified
            if not user.is_verified:
                raise AuthenticationError("Please verify your email before logging in")
            
            return user
            
        except AuthenticationError:
            raise
        except Exception:
            return None
        finally:
            session.close()
    
    @staticmethod
    def change_password(user_id: int, old_password: str, new_password: str) -> bool:
        """
        Change a user's password.
        
        Returns:
            True if successful, False otherwise
        """
        session = Session()
        try:
            user = session.query(User).get(user_id)
            
            if not user:
                return False
            
            # Verify old password
            if not AuthService.verify_password(old_password, user.password_hash):
                raise AuthenticationError("Current password is incorrect")
            
            # Validate new password
            if len(new_password) < 8:
                raise AuthenticationError("New password must be at least 8 characters")
            
            # Update password
            user.password_hash = AuthService.hash_password(new_password)
            user.updated_at = datetime.now()
            session.commit()
            
            return True
            
        except AuthenticationError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()
