"""
Admin service for user management and administration capabilities.
"""
from typing import List, Dict, Optional
from datetime import datetime
import time
from models import User, UserRole, Session, Lift, Plan, RepRange, WorkoutLog
from utils.logger import logger


class AdminError(Exception):
    pass


class AdminService:
    
    @staticmethod
    def get_all_users(exclude_admin: bool = False) -> List[Dict]:
        """
        Get list of all users with safe information (no passwords, no workout data).
        
        Args:
            exclude_admin: If True, exclude admin users from the list
        
        Returns:
            List of user dictionaries with safe information
        """
        session = Session()
        try:
            query = session.query(User)
            
            if exclude_admin:
                query = query.filter(User.role == UserRole.USER)
            
            users = query.order_by(User.created_at.desc()).all()
            
            user_list = []
            for user in users:
                user_list.append({
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'role': user.role.value,
                    'is_verified': user.is_verified,
                    'created_at': user.created_at.strftime('%Y-%m-%d %H:%M') if user.created_at else 'N/A',
                    'updated_at': user.updated_at.strftime('%Y-%m-%d %H:%M') if user.updated_at else 'N/A'
                })
            
            return user_list
            
        except Exception as e:
            logger.error(f"Error fetching users: {e}", exc_info=True)
            return []
        finally:
            session.close()
    
    @staticmethod
    def get_user_count() -> Dict[str, int]:
        """
        Get user statistics.
        
        Returns:
            Dictionary with user counts
        """
        session = Session()
        try:
            total = session.query(User).count()
            verified = session.query(User).filter(User.is_verified == True).count()
            unverified = session.query(User).filter(User.is_verified == False).count()
            admins = session.query(User).filter(User.role == UserRole.ADMIN).count()
            regular_users = session.query(User).filter(User.role == UserRole.USER).count()
            
            return {
                'total': total,
                'verified': verified,
                'unverified': unverified,
                'admins': admins,
                'regular_users': regular_users
            }
            
        except Exception as e:
            logger.error(f"Error getting user count: {e}", exc_info=True)
            return {
                'total': 0,
                'verified': 0,
                'unverified': 0,
                'admins': 0,
                'regular_users': 0
            }
        finally:
            session.close()
    
    @staticmethod
    def delete_user(
        admin_user_id: int,
        target_user_id: int,
        deletion_reason: str
    ) -> Dict:
        """
        Delete a user account (admin only).
        
        Args:
            admin_user_id: ID of the admin performing the deletion
            target_user_id: ID of the user to delete
            deletion_reason: Reason for deletion (will be sent to user)
        
        Returns:
            Dictionary with user info (for email notification)
        
        Raises:
            AdminError: If deletion fails or unauthorized
        """
        session = Session()
        try:
            # Verify admin user
            admin = session.query(User).get(admin_user_id)
            if not admin or not admin.is_admin():
                raise AdminError("Unauthorized: Only admins can delete users")
            
            # Get target user
            target_user = session.query(User).get(target_user_id)
            if not target_user:
                raise AdminError("User not found")
            
            # Prevent self-deletion
            if admin_user_id == target_user_id:
                raise AdminError("Cannot delete your own account")
            
            # Prevent deleting other admins (optional security measure)
            if target_user.is_admin():
                raise AdminError("Cannot delete other admin accounts")
            
            # Capture fields before deleting rows (accessing ORM objects after a bulk delete
            # can raise ObjectDeletedError)
            target_username = target_user.username
            target_email = target_user.email
            admin_username = admin.username

            # Store user info for email notification
            user_info = {
                'username': target_username,
                'email': target_email,
                'deletion_reason': deletion_reason,
                'admin_username': admin_username
            }
            
            start = time.perf_counter()

            # Delete user-related data.
            # Avoid ORM relationship cascades (they can load lots of rows into memory
            # and feel like the app "freezes"). Use bulk deletes instead.
            step = time.perf_counter()
            session.query(WorkoutLog).filter(WorkoutLog.user_id == target_user_id).delete(synchronize_session=False)
            logger.info(f"Deleted workout_logs for user_id={target_user_id} in {time.perf_counter() - step:.3f}s")

            step = time.perf_counter()
            session.query(Lift).filter(Lift.user_id == target_user_id).delete(synchronize_session=False)
            logger.info(f"Deleted lifts for user_id={target_user_id} in {time.perf_counter() - step:.3f}s")

            step = time.perf_counter()
            session.query(Plan).filter(Plan.user_id == target_user_id).delete(synchronize_session=False)
            logger.info(f"Deleted plans for user_id={target_user_id} in {time.perf_counter() - step:.3f}s")

            step = time.perf_counter()
            session.query(RepRange).filter(RepRange.user_id == target_user_id).delete(synchronize_session=False)
            logger.info(f"Deleted rep_ranges for user_id={target_user_id} in {time.perf_counter() - step:.3f}s")

            # Delete the user row using bulk delete as well to avoid any ORM cascade work.
            step = time.perf_counter()
            session.query(User).filter(User.id == target_user_id).delete(synchronize_session=False)
            logger.info(f"Deleted users row for user_id={target_user_id} in {time.perf_counter() - step:.3f}s")

            step = time.perf_counter()
            session.commit()
            logger.info(f"Commit completed in {time.perf_counter() - step:.3f}s")
            logger.info(
                f"Deleted user_id={target_user_id} and related data in {time.perf_counter() - start:.3f}s"
            )
            
            logger.info(f"Admin {admin_username} deleted user {target_username}")
            
            return user_info
            
        except AdminError:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Error deleting user: {e}", exc_info=True)
            raise AdminError(f"Failed to delete user: {str(e)}")
        finally:
            session.close()
    
    @staticmethod
    def promote_to_admin(admin_user_id: int, target_user_id: int) -> bool:
        """
        Promote a user to admin role.
        
        Args:
            admin_user_id: ID of the admin performing the promotion
            target_user_id: ID of the user to promote
        
        Returns:
            True if successful
        
        Raises:
            AdminError: If promotion fails or unauthorized
        """
        session = Session()
        try:
            # Verify admin user
            admin = session.query(User).get(admin_user_id)
            if not admin or not admin.is_admin():
                raise AdminError("Unauthorized: Only admins can promote users")
            
            # Get target user
            target_user = session.query(User).get(target_user_id)
            if not target_user:
                raise AdminError("User not found")
            
            if target_user.is_admin():
                raise AdminError("User is already an admin")
            
            # Promote user
            target_user.role = UserRole.ADMIN
            target_user.updated_at = datetime.now()
            session.commit()
            
            logger.info(f"Admin {admin.username} promoted {target_user.username} to admin")
            
            return True
            
        except AdminError:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Error promoting user: {e}", exc_info=True)
            raise AdminError(f"Failed to promote user: {str(e)}")
        finally:
            session.close()
    
    @staticmethod
    def search_users(query: str) -> List[Dict]:
        """
        Search users by username or email.
        
        Args:
            query: Search query string
        
        Returns:
            List of matching users
        """
        session = Session()
        try:
            search_term = f"%{query.lower()}%"
            users = session.query(User).filter(
                (User.username.like(search_term)) | (User.email.like(search_term))
            ).limit(50).all()
            
            user_list = []
            for user in users:
                user_list.append({
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'role': user.role.value,
                    'is_verified': user.is_verified,
                    'created_at': user.created_at.strftime('%Y-%m-%d %H:%M') if user.created_at else 'N/A'
                })
            
            return user_list
            
        except Exception as e:
            logger.error(f"Error searching users: {e}", exc_info=True)
            return []
        finally:
            session.close()
