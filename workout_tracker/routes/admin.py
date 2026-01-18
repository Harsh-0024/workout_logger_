import time
from threading import Thread

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from services.admin import AdminError, AdminService
from utils.logger import logger

from .decorators import require_admin


def register_admin_routes(app, email_service):
    def send_deletion_email_async(user_info):
        if not user_info.get('email'):
            return

        def _send():
            try:
                with app.app_context():
                    email_service.send_account_deletion_email(
                        email=user_info['email'],
                        username=user_info['username'],
                        admin_message=user_info['deletion_reason'],
                        admin_username=user_info['admin_username'],
                    )
            except Exception as exc:
                logger.error(f"Failed to send deletion email: {exc}", exc_info=True)

        Thread(target=_send, daemon=True).start()

    @require_admin
    def admin_dashboard():
        try:
            users = AdminService.get_all_users()
            stats = AdminService.get_user_count()

            return render_template(
                'admin_dashboard.html',
                users=users,
                stats=stats,
            )
        except Exception as e:
            logger.error(f"Admin dashboard error: {e}", exc_info=True)
            flash("Error loading admin dashboard.", "error")
            return redirect(url_for('user_dashboard', username=current_user.username))

    @require_admin
    def admin_delete_user():
        try:
            start = time.perf_counter()
            user_id = int(request.form.get('user_id'))
            deletion_reason = request.form.get('deletion_reason', '').strip()

            logger.info(
                f"Admin delete requested: admin_user_id={current_user.id} target_user_id={user_id}"
            )

            if not deletion_reason:
                flash("Deletion reason is required.", "error")
                return redirect(url_for('admin_dashboard'))

            delete_start = time.perf_counter()
            user_info = AdminService.delete_user(
                admin_user_id=current_user.id,
                target_user_id=user_id,
                deletion_reason=deletion_reason,
            )
            logger.info(
                f"Admin delete DB step completed in {time.perf_counter() - delete_start:.3f}s"
            )

            send_deletion_email_async(user_info)

            logger.info(f"Admin delete request finished in {time.perf_counter() - start:.3f}s")

            flash(f"User '{user_info['username']}' has been deleted successfully.", "success")

        except AdminError as e:
            flash(str(e), "error")
        except Exception as e:
            logger.error(f"Delete user error: {e}", exc_info=True)
            flash("An error occurred while deleting the user.", "error")

        return redirect(url_for('admin_dashboard'))

    app.add_url_rule('/admin', endpoint='admin_dashboard', view_func=admin_dashboard, methods=['GET'])
    app.add_url_rule(
        '/admin/delete-user',
        endpoint='admin_delete_user',
        view_func=admin_delete_user,
        methods=['POST'],
    )
