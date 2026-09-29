from datetime import datetime

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required, current_user

from list_of_exercise import get_workout_days
from models import Plan, RepRange, Session
from services.retrieve import (
    CUSTOM_PICKER_GROUPS,
    CUSTOM_RETRIEVAL_SORT_MODES,
    exercise_groups_for,
    generate_custom_retrieve_output,
    describe_retrieve_output,
    generate_retrieve_output,
    DEFAULT_CUSTOM_WORKOUT_TITLE,
    get_custom_retrieval_exercise_catalog,
    infer_custom_workout_title,
    get_admin_display_name,
    get_effective_plan_text,
    _own_plan_text,
    is_plan_owner,
    record_custom_retrieval,
    set_custom_retrieval_sort_preference,
    set_exercise_group_choice,
)
from list_of_exercise import DEFAULT_PLAN, DEFAULT_REP_RANGES
from services.rep_ranges import canonical_rep_text, merge_rep_entries, parse_rep_entries
from utils.logger import logger
from utils.validators import sanitize_text_input




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
        selected_exercises.append(exercise['name'])

    two_set_keys = [str(key or '').strip() for key in two_set_keys if str(key or '').strip()]
    if len(set(two_set_keys)) != len(two_set_keys) or any(key not in seen_keys for key in two_set_keys):
        raise ValueError('Invalid set selection.')

    return selected_keys, selected_exercises, two_set_keys


def register_plan_routes(app):
    def _grid_columns(count):
        """Columns for a group on wide screens: whole rows where the count allows it."""
        if count <= 5:
            return max(count, 1)
        for cols in (4, 3, 5):
            if count % cols == 0:
                return cols
        return 4

    def _lay_out_groups(groups):
        """Merge lone single-session groups, then size each grid so no row ends in a gap."""
        singles = [g for g in groups if len(g['items']) == 1]
        if len(singles) > 1:
            merged = {'title': 'Other' if len(singles) < len(groups) else 'Workouts', 'items': [
                {**g['items'][0], 'number': g['title']} for g in singles
            ]}
            first = groups.index(singles[0])
            groups = [g for g in groups if g not in singles]
            groups.insert(first, merged)
        for group in groups:
            count = len(group['items'])
            wide = _grid_columns(count)
            narrow = 1 if count == 1 else 2
            group['wide'], group['narrow'] = wide, narrow
            # The last cell stretches over whatever the last row leaves empty.
            for item in group['items']:
                item['span_wide'] = item['span_narrow'] = 1
            left_wide, left_narrow = count % wide, count % narrow
            if left_wide:
                group['items'][-1]['span_wide'] = wide - left_wide + 1
            if left_narrow:
                group['items'][-1]['span_narrow'] = narrow - left_narrow + 1
        return groups

    def _retrieve_groups(data):
        """Every session in the plan, grouped the way the plan groups them (cycles, or categories)."""
        workout = data.get('workout') if isinstance(data, dict) else None
        workout = workout if isinstance(workout, dict) else {}
        titles = data.get('session_titles') if isinstance(data, dict) else None
        titles = titles if isinstance(titles, dict) else {}
        headings = data.get('headings') if isinstance(data, dict) else None
        heading_sessions = data.get('heading_sessions') if isinstance(data, dict) else None

        def session_item(sid):
            return {
                'number': f"Session {sid}",
                'name': titles.get(str(sid)) or f"Session {sid}",
                'url': url_for('retrieve_final', category='Session', day_id=sid),
            }

        groups = []
        if isinstance(headings, list) and headings and isinstance(heading_sessions, dict) and heading_sessions:
            for heading in headings:
                ids = sorted(int(x) for x in (heading_sessions.get(heading) or []) if str(x).isdigit())
                if ids:
                    groups.append({'title': heading, 'items': [session_item(sid) for sid in ids]})
            return groups

        for category, days in workout.items():
            day_names = list((days or {}).keys())
            items = []
            for index, day_name in enumerate(day_names, start=1):
                if str(category).strip().lower() == 'session':
                    items.append(session_item(index))
                    continue
                exercises = [
                    str(line).split(' - [', 1)[0].strip()
                    for line in ((days or {}).get(day_name) or [])[:2]
                ]
                items.append({
                    'number': f"Day {index}",
                    'name': ', '.join(e for e in exercises if e) or day_name,
                    'url': url_for('retrieve_final', category=category, day_id=index),
                })
            if items:
                title = 'Sessions' if str(category).strip().lower() == 'session' else category
                groups.append({'title': title, 'items': items})
        return groups

    @login_required
    def retrieve_categories():
        user = current_user

        try:
            raw_text = get_effective_plan_text(Session, user)
            groups = _lay_out_groups(_retrieve_groups(get_workout_days(raw_text or "")))
            if not groups:
                flash("No workout plan found. Please set up your plan first.", "info")
                return redirect(url_for('set_plan'))
            return render_template('retrieve.html', groups=groups)
        except Exception as e:
            logger.error(f"Error in retrieve_categories: {e}", exc_info=True)
            flash("Error loading workout categories.", "error")
            return redirect(url_for('user_dashboard', username=user.username))

    @login_required
    def retrieve_heading_days(heading_id: int):
        # Old two-step links: every session is on one page now.
        return redirect(url_for('retrieve_categories'))

    @login_required
    def retrieve_days(category):
        return redirect(url_for('retrieve_categories'))

    def _session_place(data, category, day_id):
        """("Session 9 · Cycle 3", "Chest & Biceps") for the plan page header."""
        titles = data.get('session_titles') if isinstance(data, dict) else None
        if str(category).strip().lower() == 'session':
            name = (titles or {}).get(str(day_id)) if isinstance(titles, dict) else None
            kicker = f"Session {day_id}"
            heading_sessions = data.get('heading_sessions') if isinstance(data, dict) else None
            for heading, ids in (heading_sessions or {}).items():
                if day_id in [int(x) for x in ids if str(x).isdigit()]:
                    kicker += f" · {heading}"
                    break
            return kicker, name or f"Session {day_id}"
        return f"Day {day_id}", category

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
            kicker, title = _session_place(get_workout_days(get_effective_plan_text(Session, user) or ""), category, day_id)
            return render_template(
                'retrieve_plan.html',
                output=output,
                plan=describe_retrieve_output(output),
                exercise_count=exercise_count,
                set_count=set_count,
                kicker=kicker,
                title=title,
                back_url=url_for('retrieve_categories'),
            )
        except Exception as e:
            logger.error(f"Error in retrieve_final: {e}", exc_info=True)
            flash("Error generating workout plan.", "error")
            return redirect(url_for('retrieve_categories'))

    def _custom_picker_groups(catalog):
        """The picker's muscle groups: your own exercises first, then the rest, each A-Z."""
        groups = {name: [] for name in CUSTOM_PICKER_GROUPS}
        for item in catalog:
            groups.setdefault(item.get('group') or 'Other', []).append(item)
        # Empty groups are kept (hidden) so an exercise can be moved into them.
        return [
            {'name': name, 'items': sorted(items, key=lambda i: (not i.get('yours'), i['name'].casefold()))}
            for name, items in groups.items()
        ]

    def _custom_set_overrides(selected_keys, two_set_keys, set_counts):
        """The sets shown on the page for each exercise, so the plan says exactly what the picker did."""
        overrides = {}
        if set_counts and len(set_counts) == len(selected_keys):
            for key, raw in zip(selected_keys, set_counts):
                try:
                    count = int(raw)
                except (TypeError, ValueError):
                    raise ValueError('Invalid set selection.')
                if not 1 <= count <= 10:
                    raise ValueError('Invalid set selection.')
                overrides[key] = count
            return overrides
        # Older pages sent only the exercises switched to two sets.
        for key in two_set_keys:
            overrides[key] = 2
        return overrides

    @login_required
    def retrieve_custom():
        user = current_user

        try:
            catalog = get_custom_retrieval_exercise_catalog(Session, user, sort_mode='alpha_asc')
            if not catalog:
                flash("No exercises are available to retrieve yet.", "info")
                return redirect(url_for('set_plan'))

            if request.method == 'GET':
                return render_template(
                    'retrieve_custom.html',
                    groups=_custom_picker_groups(catalog),
                    exercises=catalog,
                )

            try:
                selected_keys, selected_exercises, two_set_keys = _resolve_custom_retrieval_selection(
                    catalog,
                    request.form.getlist('exercise'),
                    request.form.getlist('two_set_exercise'),
                )
                set_overrides = _custom_set_overrides(
                    selected_keys, two_set_keys, request.form.getlist('set_count'),
                )
            except ValueError as error:
                flash(str(error), 'error')
                return redirect(url_for('retrieve_custom'))

            try:
                workout_title = infer_custom_workout_title(Session, user, selected_exercises)
            except Exception as e:
                logger.warning(f"Unable to infer custom workout title: {e}", exc_info=True)
                workout_title = None
            workout_title = workout_title or DEFAULT_CUSTOM_WORKOUT_TITLE

            # Give retrieve the full plan line so a plan-only rep range (e.g. "[4, 6-8]")
            # still guides the output; set_overrides carries the sets chosen on the page.
            catalog_by_key = {item['key']: item for item in catalog}
            selected_lines = [
                catalog_by_key[key].get('exercise_line') or catalog_by_key[key]['name']
                for key in selected_keys
            ]
            output, exercise_count, set_count = generate_custom_retrieve_output(
                Session,
                user,
                selected_lines,
                set_overrides=set_overrides,
                title=workout_title,
            )
            try:
                record_custom_retrieval(Session, user, selected_keys)
            except Exception as e:
                Session.rollback()
                logger.warning(f"Unable to record custom retrieval history: {e}", exc_info=True)

            return render_template(
                'retrieve_plan.html',
                output=output,
                plan=describe_retrieve_output(output),
                exercise_count=exercise_count,
                set_count=set_count,
                kicker='Custom workout',
                title=workout_title,
                back_url=url_for('retrieve_custom'),
                custom_retrieval=True,
                custom_workout_title=workout_title,
                default_custom_workout_title=DEFAULT_CUSTOM_WORKOUT_TITLE,
            )
        except Exception as e:
            logger.error(f"Error generating custom workout: {e}", exc_info=True)
            flash("Error generating custom workout.", "error")
            return redirect(url_for('retrieve_custom'))

    @login_required
    def retrieve_custom_review():
        # Picking, sets and order all happen on the one Custom workout page now.
        return redirect(url_for('retrieve_custom'))

    @login_required
    def save_exercise_group():
        """Long-press "Move to…" on the Custom workout page."""
        key = str(request.form.get('exercise') or '').strip()
        group = str(request.form.get('group') or '').strip()
        catalog = get_custom_retrieval_exercise_catalog(Session, current_user, sort_mode='alpha_asc')
        item = next((i for i in catalog if i['key'] == key), None)
        if item is None or group not in CUSTOM_PICKER_GROUPS:
            return jsonify({'ok': False, 'error': 'Unknown exercise or group.'}), 400
        try:
            set_exercise_group_choice(Session, current_user, key, group, item['auto_group'])
        except Exception as e:
            Session.rollback()
            logger.error(f"Error saving exercise group: {e}", exc_info=True)
            return jsonify({'ok': False, 'error': 'Could not save that right now.'}), 500
        return jsonify({'ok': True, 'group': group, 'auto_group': item['auto_group']})

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

            can_follow = not is_plan_owner(Session, user)
            return render_template(
                'set_plan.html',
                # Not following: the editor starts from the built-in plan if they haven't written one.
                current_plan=_own_plan_text(Session, user) or DEFAULT_PLAN.strip(),
                follow_admin_plan=can_follow and getattr(user, 'follow_admin_plan', False),
                admin_display_name=get_admin_display_name(Session),
                can_follow=can_follow,
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

                reps.text_content = canonical_rep_text(request.form.get('rep_text', ''))
                user.follow_admin_exercises = False
                reps.updated_at = datetime.now()
                Session.commit()
                flash("Rep ranges saved.", "success")
                return redirect(url_for('set_exercises'))

            can_follow = not is_plan_owner(Session, user)
            entries = merge_rep_entries(parse_rep_entries(reps.text_content or ""))
            groups = exercise_groups_for(Session, user, [name for name, _ in entries])
            return render_template(
                'set_exercises.html',
                entries=[[name, value, groups[name]] for name, value in entries],
                group_order=CUSTOM_PICKER_GROUPS,
                follow_admin_exercises=can_follow and getattr(user, 'follow_admin_exercises', False),
                admin_display_name=get_admin_display_name(Session),
                can_follow=can_follow,
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
        '/retrieve/custom/group',
        endpoint='save_exercise_group',
        view_func=save_exercise_group,
        methods=['POST'],
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
