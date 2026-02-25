from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required, current_user

from list_of_exercise import get_workout_days
from models import Plan, RepRange, Session
from services.retrieve import (
    generate_retrieve_output,
    get_admin_display_name,
    get_effective_plan_text,
    _get_admin_plan_text,
    _get_admin_rep_ranges_text,
)
from list_of_exercise import DEFAULT_PLAN, DEFAULT_REP_RANGES
from utils.logger import logger
from utils.validators import sanitize_text_input


def register_plan_routes(app):
    @login_required
    def retrieve_categories():
        user = current_user

        try:
            raw_text = get_effective_plan_text(Session, user)
            data = get_workout_days(raw_text or "")

            headings = data.get('headings') if isinstance(data, dict) else None
            heading_sessions = data.get('heading_sessions') if isinstance(data, dict) else None
            if isinstance(headings, list) and headings and isinstance(heading_sessions, dict) and heading_sessions:
                return render_template('retrieve_step1.html', headings=headings)

            categories = list(data.get('workout', {}).keys())

            if not categories:
                flash("No workout plan found. Please set up your plan first.", "info")
                return redirect(url_for('set_plan'))

            return render_template('retrieve_step1.html', categories=categories)
        except Exception as e:
            logger.error(f"Error in retrieve_categories: {e}", exc_info=True)
            flash("Error loading workout categories.", "error")
            return redirect(url_for('user_dashboard', username=user.username))

    @login_required
    def retrieve_heading_days(heading_id: int):
        user = current_user

        try:
            raw_text = get_effective_plan_text(Session, user)
            if not raw_text:
                flash("No workout plan found.", "error")
                return redirect(url_for('set_plan'))

            data = get_workout_days(raw_text)
            headings = data.get('headings') if isinstance(data, dict) else None
            heading_sessions = data.get('heading_sessions') if isinstance(data, dict) else None
            if not (isinstance(headings, list) and headings and isinstance(heading_sessions, dict) and heading_sessions):
                flash("Headings not found in plan.", "error")
                return redirect(url_for('retrieve_categories'))

            if not isinstance(heading_id, int) or heading_id < 1 or heading_id > len(headings):
                flash("Invalid heading.", "error")
                return redirect(url_for('retrieve_categories'))

            heading_name = headings[heading_id - 1]
            session_ids = heading_sessions.get(heading_name) or []
            session_ids = [int(x) for x in session_ids if isinstance(x, int) or str(x).isdigit()]
            session_ids.sort()

            session_titles = None
            maybe_titles = data.get("session_titles") if isinstance(data, dict) else None
            if isinstance(maybe_titles, dict):
                session_titles = maybe_titles

            return render_template(
                'retrieve_step2.html',
                category_name='Session',
                num_days=0,
                session_titles=session_titles,
                session_ids=session_ids,
                heading_id=heading_id,
                heading_name=heading_name,
                back_url=url_for('retrieve_categories'),
            )
        except Exception as e:
            logger.error(f"Error in retrieve_heading_days: {e}", exc_info=True)
            flash("Error loading heading days.", "error")
            return redirect(url_for('retrieve_categories'))

    @login_required
    def retrieve_days(category):
        user = current_user

        try:
            # Decode HTML entities first, then sanitize
            import html
            category = html.unescape(category)
            category = sanitize_text_input(category, max_length=100)
            category = html.unescape(category)
            
            raw_text = get_effective_plan_text(Session, user)
            if not raw_text:
                flash("No workout plan found.", "error")
                return redirect(url_for('set_plan'))

            data = get_workout_days(raw_text)

            if category not in data.get('workout', {}):
                flash("Invalid category.", "error")
                return redirect(url_for('retrieve_categories'))

            num_days = len(data['workout'][category])
            session_titles = None
            if str(category).strip().lower() == "session":
                maybe_titles = data.get("session_titles")
                if isinstance(maybe_titles, dict):
                    session_titles = maybe_titles
            return render_template(
                'retrieve_step2.html',
                category_name=category,
                num_days=num_days,
                session_titles=session_titles,
            )
        except Exception as e:
            logger.error(f"Error in retrieve_days: {e}", exc_info=True)
            flash("Error loading workout days.", "error")
            return redirect(url_for('retrieve_categories'))

    @login_required
    def retrieve_final(category, day_id):
        user = current_user

        try:
            # Decode HTML entities first, then sanitize
            import html
            category = html.unescape(category)
            category = sanitize_text_input(category, max_length=100)
            category = html.unescape(category)
            
            output, exercise_count, set_count = generate_retrieve_output(Session, user, category, day_id)

            back_to_days_url = None
            if str(category).strip().lower() == 'session':
                heading_id = request.args.get('heading_id')
                if heading_id and str(heading_id).isdigit():
                    back_to_days_url = url_for('retrieve_heading_days', heading_id=int(heading_id))
            return render_template(
                'retrieve_step3.html',
                output=output,
                exercise_count=exercise_count,
                set_count=set_count,
                category_name=category,
                day_id=day_id,
                back_to_days_url=back_to_days_url,
            )
        except Exception as e:
            logger.error(f"Error in retrieve_final: {e}", exc_info=True)
            flash("Error generating workout plan.", "error")
            return redirect(url_for('retrieve_categories'))

    @login_required
    def set_plan():
        user = current_user

        try:
            plan = Session.query(Plan).filter_by(user_id=user.id).first()

            if not plan:
                plan = Plan(user_id=user.id, text_content="")
                Session.add(plan)
                Session.flush()

            if request.method == 'POST':
                form_type = request.form.get('form_type', 'save_plan')

                if form_type == 'toggle_follow_admin':
                    new_val = request.form.get('follow_admin_plan') == '1'
                    user.follow_admin_plan = new_val
                    user.updated_at = datetime.now()
                    Session.commit()
                    if new_val:
                        flash("Now following admin's plan.", "success")
                    else:
                        flash("Switched to your own plan.", "success")
                    return redirect(url_for('set_plan'))

                plan_text = request.form.get('plan_text', '').strip()
                plan.text_content = plan_text
                user.follow_admin_plan = False
                plan.updated_at = datetime.now()
                Session.commit()
                flash("Workout plan updated successfully!", "success")
                return redirect(url_for('user_dashboard', username=user.username))

            admin_display_name = get_admin_display_name(Session)
            has_admin_plan = False
            if not user.is_admin():
                admin_plan_text = _get_admin_plan_text(Session)
                has_admin_plan = bool(
                    (admin_plan_text and admin_plan_text.strip())
                    or (DEFAULT_PLAN and str(DEFAULT_PLAN).strip())
                )

            display_plan = plan.text_content or ""

            return render_template(
                'set_plan.html',
                current_plan=display_plan,
                follow_admin_plan=getattr(user, 'follow_admin_plan', False),
                has_admin_plan=has_admin_plan,
                admin_display_name=admin_display_name,
                is_admin=user.is_admin(),
            )
        except Exception as e:
            Session.rollback()
            logger.error(f"Error in set_plan: {e}", exc_info=True)
            flash("Error saving workout plan.", "error")
            return redirect(url_for('user_dashboard', username=user.username))

    @login_required
    def set_exercises():
        user = current_user

        try:
            reps = Session.query(RepRange).filter_by(user_id=user.id).first()

            if not reps:
                reps = RepRange(user_id=user.id, text_content="")
                Session.add(reps)
                Session.flush()

            if request.method == 'POST':
                form_type = request.form.get('form_type', 'save_exercises')

                if form_type == 'toggle_follow_admin':
                    new_val = request.form.get('follow_admin_exercises') == '1'
                    user.follow_admin_exercises = new_val
                    user.updated_at = datetime.now()
                    Session.commit()
                    if new_val:
                        flash("Now following admin's rep ranges.", "success")
                    else:
                        flash("Switched to your own rep ranges.", "success")
                    return redirect(url_for('set_exercises'))

                rep_text = request.form.get('rep_text', '').strip()
                reps.text_content = rep_text
                user.follow_admin_exercises = False
                reps.updated_at = datetime.now()
                Session.commit()
                flash("Rep ranges updated successfully!", "success")
                return redirect(url_for('user_dashboard', username=user.username))

            admin_display_name = get_admin_display_name(Session)
            has_admin_exercises = False
            if not user.is_admin():
                admin_rep_text = _get_admin_rep_ranges_text(Session)
                has_admin_exercises = bool(
                    (admin_rep_text and admin_rep_text.strip())
                    or (DEFAULT_REP_RANGES and len(DEFAULT_REP_RANGES) > 0)
                )

            return render_template(
                'set_exercises.html',
                current_reps=reps.text_content or "",
                follow_admin_exercises=getattr(user, 'follow_admin_exercises', False),
                has_admin_exercises=has_admin_exercises,
                admin_display_name=admin_display_name,
                is_admin=user.is_admin(),
            )
        except Exception as e:
            Session.rollback()
            logger.error(f"Error in set_exercises: {e}", exc_info=True)
            flash("Error saving rep ranges.", "error")
            return redirect(url_for('user_dashboard', username=user.username))

    app.add_url_rule(
        '/retrieve/categories',
        endpoint='retrieve_categories',
        view_func=retrieve_categories,
        methods=['GET'],
    )
    app.add_url_rule(
        '/retrieve/heading/<int:heading_id>',
        endpoint='retrieve_heading_days',
        view_func=retrieve_heading_days,
        methods=['GET'],
    )
    app.add_url_rule(
        '/retrieve/days/<category>',
        endpoint='retrieve_days',
        view_func=retrieve_days,
        methods=['GET'],
    )
    app.add_url_rule(
        '/retrieve/final/<category>/<int:day_id>',
        endpoint='retrieve_final',
        view_func=retrieve_final,
        methods=['GET'],
    )
    app.add_url_rule('/set_plan', endpoint='set_plan', view_func=set_plan, methods=['GET', 'POST'])
    app.add_url_rule(
        '/set_exercises',
        endpoint='set_exercises',
        view_func=set_exercises,
        methods=['GET', 'POST'],
    )
