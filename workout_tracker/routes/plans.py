from datetime import datetime

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import login_required, current_user

from list_of_exercise import get_workout_days
from models import Plan, RepRange, Session
from services.retrieve import (
    CUSTOM_RETRIEVAL_SORT_MODES,
    generate_custom_retrieve_output,
    generate_retrieve_output,
    get_custom_retrieval_exercise_catalog,
    get_custom_retrieval_sort_preference,
    get_admin_display_name,
    get_effective_plan_text,
    _get_admin_plan_text,
    _get_admin_rep_ranges_text,
    record_custom_retrieval,
    set_custom_retrieval_sort_preference,
)
from list_of_exercise import DEFAULT_PLAN, DEFAULT_REP_RANGES
from utils.logger import logger
from utils.validators import sanitize_text_input


CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY = 'custom_retrieval_draft'


def _resolve_custom_retrieval_selection(catalog, selected_keys, two_set_keys):
    """Validate a custom selection against the current catalog and preserve its order."""
    selected_keys = [str(key or '').strip() for key in selected_keys if str(key or '').strip()]
    if not selected_keys:
        raise ValueError('Select at least one exercise.')
    if len(selected_keys) > 30:
        raise ValueError('Select up to 30 exercises at a time.')

    catalog_by_key = {item['key']: item for item in catalog}
    selected_exercises = []
    seen_keys = set()
    for key in selected_keys:
        exercise = catalog_by_key.get(key)
        if not exercise or key in seen_keys:
            raise ValueError('One or more selected exercises are no longer available.')
        seen_keys.add(key)
        selected_exercises.append(exercise['exercise_line'])

    two_set_keys = [str(key or '').strip() for key in two_set_keys if str(key or '').strip()]
    if len(set(two_set_keys)) != len(two_set_keys) or any(key not in seen_keys for key in two_set_keys):
        raise ValueError('Invalid set selection.')

    return selected_keys, selected_exercises, two_set_keys


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
    def retrieve_custom():
        user = current_user

        try:
            sort_mode = get_custom_retrieval_sort_preference(Session, user)
            catalog = get_custom_retrieval_exercise_catalog(Session, user, sort_mode=sort_mode)
            if not catalog:
                flash("No exercises are available to retrieve yet.", "info")
                return redirect(url_for('set_plan'))

            if request.method == 'GET':
                draft = session.get(CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY, {})
                draft_selected_keys = draft.get('selected_keys', []) if isinstance(draft, dict) else []
                draft_two_set_keys = draft.get('two_set_keys', []) if isinstance(draft, dict) else []
                if draft_selected_keys:
                    try:
                        draft_selected_keys, _, draft_two_set_keys = _resolve_custom_retrieval_selection(
                            catalog,
                            draft_selected_keys,
                            draft_two_set_keys,
                        )
                    except ValueError:
                        session.pop(CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY, None)
                        draft_selected_keys = []
                        draft_two_set_keys = []
                return render_template(
                    'retrieve_custom.html',
                    exercises=catalog,
                    sort_mode=sort_mode,
                    draft_selected_keys=draft_selected_keys,
                    draft_two_set_keys=draft_two_set_keys,
                )

            try:
                selected_keys, selected_exercises, two_set_keys = _resolve_custom_retrieval_selection(
                    catalog,
                    request.form.getlist('exercise'),
                    request.form.getlist('two_set_exercise'),
                )
            except ValueError as error:
                flash(str(error), 'error')
                return redirect(url_for('retrieve_custom'))

            if request.form.get('flow') == 'review':
                session[CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY] = {
                    'selected_keys': selected_keys,
                    'two_set_keys': two_set_keys,
                }
                return redirect(url_for('retrieve_custom_review'))

            set_overrides = {key: 2 for key in two_set_keys}

            output, exercise_count, set_count = generate_custom_retrieve_output(
                Session,
                user,
                selected_exercises,
                set_overrides=set_overrides,
            )
            try:
                record_custom_retrieval(Session, user, selected_keys)
            except Exception as e:
                Session.rollback()
                logger.warning(f"Unable to record custom retrieval history: {e}", exc_info=True)
            session.pop(CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY, None)

            return render_template(
                'retrieve_step3.html',
                output=output,
                exercise_count=exercise_count,
                set_count=set_count,
                category_name=None,
                day_id=None,
                back_to_days_url=url_for('retrieve_custom'),
                custom_retrieval=True,
            )
        except Exception as e:
            logger.error(f"Error generating custom workout: {e}", exc_info=True)
            flash("Error generating custom workout.", "error")
            return redirect(url_for('retrieve_custom'))

    @login_required
    def retrieve_custom_review():
        user = current_user

        try:
            sort_mode = get_custom_retrieval_sort_preference(Session, user)
            catalog = get_custom_retrieval_exercise_catalog(Session, user, sort_mode=sort_mode)
            if not catalog:
                flash('No exercises are available to retrieve yet.', 'info')
                return redirect(url_for('set_plan'))

            if request.method == 'POST':
                selected_keys = request.form.getlist('exercise')
                two_set_keys = request.form.getlist('two_set_exercise')
                if not selected_keys and request.form.get('review_action') == 'add_more':
                    session[CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY] = {
                        'selected_keys': [],
                        'two_set_keys': [],
                    }
                    return redirect(url_for('retrieve_custom'))
                try:
                    selected_keys, _, two_set_keys = _resolve_custom_retrieval_selection(
                        catalog,
                        selected_keys,
                        two_set_keys,
                    )
                except ValueError as error:
                    flash(str(error), 'error')
                    return redirect(url_for('retrieve_custom_review'))

                session[CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY] = {
                    'selected_keys': selected_keys,
                    'two_set_keys': two_set_keys,
                }
                return redirect(url_for('retrieve_custom'))

            draft = session.get(CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY, {})
            selected_keys = draft.get('selected_keys', []) if isinstance(draft, dict) else []
            two_set_keys = draft.get('two_set_keys', []) if isinstance(draft, dict) else []
            try:
                selected_keys, _, two_set_keys = _resolve_custom_retrieval_selection(
                    catalog,
                    selected_keys,
                    two_set_keys,
                )
            except ValueError as error:
                session.pop(CUSTOM_RETRIEVAL_DRAFT_SESSION_KEY, None)
                flash(str(error), 'error')
                return redirect(url_for('retrieve_custom'))

            catalog_by_key = {item['key']: item for item in catalog}
            return render_template(
                'retrieve_custom_review.html',
                exercises=[catalog_by_key[key] for key in selected_keys],
                two_set_keys=two_set_keys,
            )
        except Exception as e:
            logger.error(f'Error loading custom workout review: {e}', exc_info=True)
            flash('Error loading your selected exercises.', 'error')
            return redirect(url_for('retrieve_custom'))

    @login_required
    def save_custom_retrieval_sort_preference():
        sort_mode = str(request.form.get('sort_mode') or '').strip()
        if sort_mode not in CUSTOM_RETRIEVAL_SORT_MODES:
            return jsonify({'ok': False, 'error': 'Invalid sort mode.'}), 400

        try:
            saved_mode = set_custom_retrieval_sort_preference(Session, current_user, sort_mode)
            return jsonify({'ok': True, 'sort_mode': saved_mode})
        except Exception as e:
            Session.rollback()
            logger.error(f"Error saving custom retrieval sort preference: {e}", exc_info=True)
            return jsonify({'ok': False, 'error': 'Unable to save sort preference.'}), 500

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
    app.add_url_rule(
        '/retrieve/custom',
        endpoint='retrieve_custom',
        view_func=retrieve_custom,
        methods=['GET', 'POST'],
    )
    app.add_url_rule(
        '/retrieve/custom/review',
        endpoint='retrieve_custom_review',
        view_func=retrieve_custom_review,
        methods=['GET', 'POST'],
    )
    app.add_url_rule(
        '/retrieve/custom/sort-preference',
        endpoint='save_custom_retrieval_sort_preference',
        view_func=save_custom_retrieval_sort_preference,
        methods=['POST'],
    )
    app.add_url_rule('/set_plan', endpoint='set_plan', view_func=set_plan, methods=['GET', 'POST'])
    app.add_url_rule(
        '/set_exercises',
        endpoint='set_exercises',
        view_func=set_exercises,
        methods=['GET', 'POST'],
    )
