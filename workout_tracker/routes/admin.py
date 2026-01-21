import time
from pathlib import Path
from threading import Thread

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from PIL import Image

from services.admin import AdminError, AdminService
from utils.logger import logger

from .decorators import require_admin


def register_admin_routes(app, email_service):
    def _is_allowed_icon(filename):
        if not filename or '.' not in filename:
            return False
        ext = filename.rsplit('.', 1)[1].lower()
        return ext in {'png', 'jpg', 'jpeg', 'webp'}

    def _save_app_icon(file_storage):
        icon_dir = Path(app.static_folder) / 'icons'
        icon_dir.mkdir(parents=True, exist_ok=True)

        image = Image.open(file_storage.stream)
        image = image.convert('RGBA')

        sizes = [
            (512, 'app-icon-512.png'),
            (192, 'app-icon-192.png'),
            (180, 'apple-touch-icon.png'),
        ]

        for size, filename in sizes:
            resized = image.resize((size, size), Image.LANCZOS)
            resized.save(icon_dir / filename, format='PNG', optimize=True)

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

    @require_admin
    def admin_cleanup_duplicates():
        try:
            summary = AdminService.cleanup_duplicate_users(current_user.id)
            if summary['groups'] == 0:
                flash("No duplicate accounts found to clean up.", "info")
            else:
                flash(
                    (
                        "Cleanup complete: "
                        f"{summary['groups']} group(s) processed, "
                        f"{summary['deleted']} duplicate(s) removed, "
                        f"{summary['logs_moved']} log(s) merged."
                    ),
                    "success",
                )
        except AdminError as e:
            flash(str(e), "error")
        except Exception as e:
            logger.error(f"Duplicate cleanup error: {e}", exc_info=True)
            flash("Failed to clean up duplicate accounts.", "error")

        return redirect(url_for('admin_dashboard'))

    @require_admin
    def admin_update_app_icon():
        icon_file = request.files.get('app_icon')

        if not icon_file or not icon_file.filename:
            flash("Please choose an image file to upload.", "error")
            return redirect(url_for('admin_dashboard'))

        if not _is_allowed_icon(icon_file.filename):
            flash("Unsupported file type. Use PNG or JPG.", "error")
            return redirect(url_for('admin_dashboard'))

        try:
            _save_app_icon(icon_file)
            flash(
                "App icon updated. Remove and re-add the app to your Home Screen to refresh the icon.",
                "success",
            )
        except Exception as e:
            logger.error(f"App icon update failed: {e}", exc_info=True)
            flash("Failed to update app icon. Please try again.", "error")

        return redirect(url_for('admin_dashboard'))

    app.add_url_rule('/admin', endpoint='admin_dashboard', view_func=admin_dashboard, methods=['GET'])
    app.add_url_rule(
        '/admin/delete-user',
        endpoint='admin_delete_user',
        view_func=admin_delete_user,
        methods=['POST'],
    )
    app.add_url_rule(
        '/admin/cleanup-duplicates',
        endpoint='admin_cleanup_duplicates',
        view_func=admin_cleanup_duplicates,
        methods=['POST'],
    )
    app.add_url_rule(
        '/admin/app-icon',
        endpoint='admin_update_app_icon',
        view_func=admin_update_app_icon,
        methods=['POST'],
    )
