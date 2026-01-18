from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required, current_user

from list_of_exercise import get_workout_days
from models import Plan, RepRange, Session
from services.retrieve import generate_retrieve_output
from utils.logger import logger
from utils.validators import sanitize_text_input


def register_plan_routes(app):
    @login_required
    def retrieve_categories():
        user = current_user

        try:
            plan = Session.query(Plan).filter_by(user_id=user.id).first()
            raw_text = plan.text_content if plan else ""
            data = get_workout_days(raw_text)

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
    def retrieve_days(category):
        user = current_user

        try:
            category = sanitize_text_input(category, max_length=100)
            plan = Session.query(Plan).filter_by(user_id=user.id).first()

            if not plan:
                flash("No workout plan found.", "error")
                return redirect(url_for('set_plan'))

            data = get_workout_days(plan.text_content)

            if category not in data.get('workout', {}):
                flash("Invalid category.", "error")
                return redirect(url_for('retrieve_categories'))

            num_days = len(data['workout'][category])
            return render_template(
                'retrieve_step2.html',
                category_name=category,
                num_days=num_days,
            )
        except Exception as e:
            logger.error(f"Error in retrieve_days: {e}", exc_info=True)
            flash("Error loading workout days.", "error")
            return redirect(url_for('retrieve_categories'))

    @login_required
    def retrieve_final(category, day_id):
        user = current_user

        try:
            category = sanitize_text_input(category, max_length=100)
            output = generate_retrieve_output(Session, user, category, day_id)
            return render_template('retrieve_step3.html', output=output)
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
                flash("Plan not found. Creating new plan.", "info")
                plan = Plan(user_id=user.id, text_content="")
                Session.add(plan)
                Session.flush()

            if request.method == 'POST':
                plan_text = request.form.get('plan_text', '').strip()
                plan.text_content = plan_text
                plan.updated_at = datetime.now()
                Session.commit()
                flash("Workout plan updated successfully!", "success")
                return redirect(url_for('user_dashboard', username=user.username))

            return render_template('set_plan.html', current_plan=plan.text_content or "")
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
                flash("Rep ranges not found. Creating new entry.", "info")
                reps = RepRange(user_id=user.id, text_content="")
                Session.add(reps)
                Session.flush()

            if request.method == 'POST':
                rep_text = request.form.get('rep_text', '').strip()
                reps.text_content = rep_text
                reps.updated_at = datetime.now()
                Session.commit()
                flash("Rep ranges updated successfully!", "success")
                return redirect(url_for('user_dashboard', username=user.username))

            return render_template('set_exercises.html', current_reps=reps.text_content or "")
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
