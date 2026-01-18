"""
Authentication service for user registration, login, and password management.
"""
import bcrypt
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from models import User, UserRole, Session, _seed_user_data
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
            
            # Generate verification code
            verification_code = AuthService.generate_verification_code()
            token_expiry = datetime.now() + timedelta(hours=Config.VERIFICATION_TOKEN_EXPIRY)
            
            # Create user
            user = User(
                username=username.lower(),
                email=email.lower(),
                password_hash=AuthService.hash_password(password),
                role=UserRole.ADMIN if is_admin else UserRole.USER,
                is_verified=False,
                verification_token=verification_code,
                verification_token_expires=token_expiry,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            session.add(user)
            session.flush()
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
            
            # Check if already verified
            if user.is_verified:
                return True
            
            # Check token expiry
            if user.verification_token_expires and user.verification_token_expires < datetime.now():
                raise AuthenticationError("Verification code has expired")
            
            # Verify code
            if user.verification_token == verification_code:
                user.is_verified = True
                user.verification_token = None
                user.verification_token_expires = None
                user.updated_at = datetime.now()
                session.commit()
                return True
            
            return False
            
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
            
            # Generate new code
            verification_code = AuthService.generate_verification_code()
            token_expiry = datetime.now() + timedelta(hours=Config.VERIFICATION_TOKEN_EXPIRY)
            
            user.verification_token = verification_code
            user.verification_token_expires = token_expiry
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
