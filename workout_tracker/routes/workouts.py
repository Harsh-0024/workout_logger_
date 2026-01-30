from datetime import datetime, timedelta
import re
import threading
import time

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from itsdangerous import URLSafeSerializer, BadSignature
from sqlalchemy import desc, func

from list_of_exercise import get_workout_days, list_of_exercises
from models import Session, User, WorkoutLog, UserApiKey
from parsers.workout import workout_parser
from services.logging import handle_workout_log
from services.retrieve import get_effective_plan_text
from utils.errors import ParsingError, ValidationError, UserNotFoundError
from utils.logger import logger
from utils.validators import sanitize_text_input, validate_username


def register_workout_routes(app):
    serializer = URLSafeSerializer(app.config.get('SECRET_KEY', 'workout-share'))

    _reco_cache = {}
    _reco_cache_lock = threading.Lock()

    def _make_share_token(user_id, workout_date):
        payload = {
            "user_id": user_id,
            "date": workout_date.strftime('%Y-%m-%d'),
        }
        return serializer.dumps(payload)

    def _load_share_token(token):
        return serializer.loads(token)

    def _count_sets(sets_json=None, sets_display=None):
        if sets_json and isinstance(sets_json, dict):
            weights = sets_json.get('weights') or []
            reps = sets_json.get('reps') or []
            count = max(len(weights), len(reps))
        elif sets_display:
            matches = re.findall(
                r'(bw(?:/\d+(?:\.\d+)?)?(?:[+-]\d+(?:\.\d+)?)?|-?\d+(?:\.\d+)?)\s*[x×]\s*\d+',
                sets_display,
                flags=re.IGNORECASE,
            )
            count = (len(matches) if matches else 0)
        else:
            count = 0
        return count

    def _log_uses_bw(log):
        haystack = f"{getattr(log, 'exercise_string', '')} {getattr(log, 'sets_display', '')}".lower()
        return 'bw' in haystack

    def build_exercise_text(logs):
        lines = []
        for log in logs:
            if getattr(log, 'exercise_string', None):
                lines.append(log.exercise_string.strip())
                continue

            if getattr(log, 'sets_display', None):
                lines.append(f"{log.exercise} {log.sets_display}")
            elif log.top_weight and log.top_reps:
                lines.append(f"{log.exercise} {log.top_weight} x {log.top_reps}")
            else:
                lines.append(log.exercise)
        return "\n\n".join(lines)

    def _norm_ex_name(name: str) -> str:
        if not name:
            return ""
        s = str(name).strip().lower()
        s = re.sub(r"^\s*\d+\s*[\).:-]\s*", "", s)
        s = re.sub(r"\s+", " ", s)
        return s

    def _norm_title_key(title: str) -> str:
        s = str(title or "").strip().lower()
        s = re.sub(r"\s+", " ", s)
        return s

    def _contains_phrase(haystack: str, needle: str) -> bool:
        if not haystack or not needle:
            return False
        try:
            pat = r"(^|[^a-z0-9])" + re.escape(str(needle)) + r"($|[^a-z0-9])"
            return re.search(pat, str(haystack)) is not None
        except Exception:
            return False

    def _best_match_plan_day(*, title: str, exercise_list: list[str], plan_days: list[dict], day_lookup: dict):
        title_key = _norm_title_key(title)
        if title_key in day_lookup:
            return day_lookup[title_key], "title"

        for day in plan_days:
            day_name = day.get('name')
            if not isinstance(day_name, str) or not day_name.strip():
                continue
            day_key = _norm_title_key(day_name)
            if title_key == day_key or (day_key and title_key.startswith(day_key + " ")) or _contains_phrase(title_key, day_key):
                return day, "title"

        workout_ex = {_norm_ex_name(x) for x in (exercise_list or []) if _norm_ex_name(x)}
        if workout_ex:
            best = None
            best_score = 0.0
            for day in plan_days:
                day_ex = {_norm_ex_name(x) for x in (day.get('exercises') or []) if _norm_ex_name(x)}
                if not day_ex:
                    continue
                overlap = len(workout_ex.intersection(day_ex))
                day_len = len(day_ex)
                required_overlap = max(2, int(day_len * 0.7 + 0.999))
                if overlap < required_overlap:
                    continue
                score = overlap / float(day_len)
                if score > best_score:
                    best_score = score
                    best = day

            if best and best_score >= 0.7:
                return best, "exercises"
            return None, None

        if title_key in day_lookup:
            return day_lookup[title_key], "title"

        for day in plan_days:
            day_name = day.get('name')
            if not isinstance(day_name, str) or not day_name.strip():
                continue
            if _norm_title_key(day_name) and _norm_title_key(day_name) in title_key:
                return day, "title"

        return None, None

    def get_recent_workouts(user, limit=50):
        try:
            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .order_by(desc(WorkoutLog.date), WorkoutLog.id)
                .all()
            )

            workouts = []
            by_date = {}
            for log in logs:
                date_key = log.date.date() if isinstance(log.date, datetime) else log.date

                if date_key not in by_date:
                    by_date[date_key] = {
                        'date': log.date,
                        'title': log.workout_name or "Workout",
                        'exercises': set(),
                        'logs': [],
                    }
                by_date[date_key]['exercises'].add(log.exercise)
                by_date[date_key]['logs'].append(log)

            for date_key in sorted(by_date.keys(), reverse=True):
                item = by_date[date_key]
                exercise_list = sorted(item['exercises'])
                workouts.append({
                    'date': item['date'],
                    'title': item['title'],
                    'exercises': ", ".join(exercise_list),
                    'exercise_list': exercise_list,
                    'exercise_details': build_exercise_text(item['logs']),
                })
                if limit is not None and len(workouts) >= limit:
                    break

            return workouts
        except Exception as e:
            logger.error(f"Error getting recent workouts: {e}", exc_info=True)
            return []

    def get_workout_stats(user):
        try:
            total_workouts = (
                Session.query(WorkoutLog.date).filter_by(user_id=user.id).distinct().count()
            )

            total_exercises = (
                Session.query(WorkoutLog.exercise)
                .filter_by(user_id=user.id)
                .distinct()
                .count()
            )

            latest = (
                Session.query(WorkoutLog.date)
                .filter_by(user_id=user.id)
                .order_by(desc(WorkoutLog.date))
                .first()
            )

            latest_date = latest[0] if latest else None

            return {
                'total_workouts': total_workouts,
                'total_exercises': total_exercises,
                'latest_workout': latest_date,
            }
        except Exception as e:
            logger.error(f"Error getting workout stats: {e}", exc_info=True)
            return {
                'total_workouts': 0,
                'total_exercises': 0,
                'latest_workout': None,
            }

    def index():
        if current_user.is_authenticated:
            return redirect(url_for('user_dashboard', username=current_user.username))
        return redirect(url_for('login'))

    @login_required
    def user_dashboard(username):
        try:
            username = validate_username(username)
            normalized_current = (current_user.username or '').strip().lower()

            if username == normalized_current:
                user = current_user
            else:
                user = Session.query(User).filter(func.lower(User.username) == username).first()

            if not user:
                if username != normalized_current:
                    return redirect(url_for('user_dashboard', username=normalized_current or current_user.username))
                raise UserNotFoundError("User not found")

            if user.id != current_user.id and not current_user.is_admin():
                flash("You can only view your own dashboard.", "error")
                return redirect(url_for('user_dashboard', username=normalized_current or current_user.username))

            recent_workouts = get_recent_workouts(user, limit=250)

            profile_image_url = None
            if getattr(user, 'profile_image', None):
                profile_image_url = url_for('static', filename=user.profile_image)

            display_name = (user.full_name or user.username or '').strip()
            return render_template(
                'index.html',
                display_name=display_name,
                recent_workouts=recent_workouts,
                profile_image_url=profile_image_url,
            )
        except (ValidationError, UserNotFoundError) as e:
            flash(str(e), "error")
            return redirect(url_for('index'))
        except Exception as e:
            logger.error(f"Error in user_dashboard: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "error")
            return redirect(url_for('index'))

    @login_required
    def view_workout(date_str):
        user = current_user
        
        try:
            workout_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_dt = datetime.combine(workout_date, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)
            
            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .filter(WorkoutLog.date >= start_dt)
                .filter(WorkoutLog.date < end_dt)
                .order_by(WorkoutLog.id)
                .all()
            )
            
            if not logs:
                flash("Workout not found.", "error")
                return redirect(url_for('user_dashboard', username=user.username))

            workout_name = logs[0].workout_name or "Workout"
            header_date = workout_date.strftime('%d/%m')
            workout_text = build_exercise_text(logs)
            workout_text = f"{header_date} {workout_name}\n\n{workout_text}".strip()
            exercise_count = len(logs)
            set_count = 0
            missing_bw_exercises = set()

            # Calculate volume for each exercise
            for log in logs:
                set_count += _count_sets(log.sets_json, log.sets_display)
                if user.bodyweight is None and _log_uses_bw(log):
                    missing_bw_exercises.add(log.exercise)
                total_volume = 0
                if log.sets_json and isinstance(log.sets_json, dict):
                    weights = log.sets_json.get('weights') or []
                    reps_list = log.sets_json.get('reps') or []
                    for weight, reps in zip(weights, reps_list):
                        try:
                            total_volume += float(weight) * int(reps)
                        except (TypeError, ValueError):
                            continue
                elif log.sets_display:
                    # Parse sets_display to calculate volume (supports x/×)
                    sets = log.sets_display.split(', ')
                    for s in sets:
                        try:
                            normalized = s.replace('×', 'x')
                            parts = [p.strip() for p in normalized.split('x')]
                            if len(parts) == 2:
                                weight = float(parts[0])
                                reps = int(parts[1])
                                total_volume += weight * reps
                        except (ValueError, IndexError):
                            continue
                log.total_volume = total_volume if total_volume > 0 else None
            
            share_token = _make_share_token(user.id, workout_date)
            share_url = url_for('shared_workout', token=share_token, _external=True)

            return render_template(
                'workout_detail.html',
                date=date_str,
                workout_name=workout_name,
                logs=logs,
                workout_text=workout_text,
                exercise_count=exercise_count,
                set_count=set_count,
                missing_bw_exercises=sorted(missing_bw_exercises),
                share_url=share_url,
            )
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for('user_dashboard', username=user.username))
        except Exception as e:
            logger.error(f"Error viewing workout: {e}", exc_info=True)
            flash("Error loading workout.", "error")
            return redirect(url_for('user_dashboard', username=user.username))

    @login_required
    def workout_history():
        return redirect(url_for('user_dashboard', username=current_user.username))

    @login_required
    def recommend_workout_api():
        user = current_user

        now = time.time()
        cache_key = f"u:{user.id}"
        with _reco_cache_lock:
            cached = _reco_cache.get(cache_key)
            ttl = cached.get('ttl', 120) if cached else 120
            if cached and now - cached.get('ts', 0) < ttl:
                return jsonify(cached['payload'])

        try:
            plan_text = get_effective_plan_text(Session, user)
            plan_data = get_workout_days(plan_text or "")
            workout_map = plan_data.get('workout', {}) if isinstance(plan_data, dict) else {}
            categories = []
            plan_days = []
            day_lookup = {}
            for cat, cat_map in workout_map.items():
                if not isinstance(cat_map, dict):
                    continue
                num_days = len(cat_map)
                if num_days > 0:
                    categories.append({"name": str(cat), "num_days": int(num_days)})
                for day_name, exercises in cat_map.items():
                    if not isinstance(day_name, str):
                        continue
                    match = re.search(r"\s+(\d+)$", day_name)
                    if not match:
                        continue
                    day_id = int(match.group(1))
                    entry = {
                        "category": str(cat),
                        "day_id": day_id,
                        "name": day_name,
                        "exercises": exercises if isinstance(exercises, list) else [],
                    }
                    plan_days.append(entry)
                    day_lookup[day_name.strip().lower()] = entry

            if not categories:
                return jsonify({"ok": False, "error": "No workout plan found."}), 400

            recent = get_recent_workouts(user, limit=20)

            plan_days_by_cat = {}
            plan_days_norm = []
            for d in plan_days:
                if not isinstance(d, dict):
                    continue
                cat = d.get('category')
                if not isinstance(cat, str) or not cat.strip():
                    continue
                ex_set = {_norm_ex_name(x) for x in (d.get('exercises') or []) if _norm_ex_name(x)}
                plan_days_norm.append((d, ex_set))
                plan_days_by_cat.setdefault(cat.strip().lower(), []).append((d, ex_set))

            recent_context = []
            for w in recent:
                dt = w.get('date')
                title = w.get('title') or "Workout"
                title_key = _norm_title_key(title)
                workout_ex = {_norm_ex_name(x) for x in (w.get('exercise_list') or []) if _norm_ex_name(x)}

                day_matches = []
                for day in plan_days:
                    day_name = day.get('name')
                    if not isinstance(day_name, str) or not day_name.strip():
                        continue
                    day_key = _norm_title_key(day_name)
                    if _contains_phrase(title_key, day_key):
                        day_matches.append({
                            'category': day.get('category'),
                            'day_id': day.get('day_id'),
                            'day_name': day_name,
                            'source': 'title',
                        })

                if workout_ex:
                    best_by_cat = {}
                    for cat_key, days in plan_days_by_cat.items():
                        best = None
                        best_score = 0.0
                        for day, day_ex in days:
                            if not day_ex:
                                continue
                            overlap = len(workout_ex.intersection(day_ex))
                            day_len = len(day_ex)
                            required_overlap = max(2, int(day_len * 0.7 + 0.999))
                            if overlap < required_overlap:
                                continue
                            score = overlap / float(day_len)
                            if score > best_score:
                                best_score = score
                                best = day
                        if best and best_score >= 0.7:
                            best_by_cat[cat_key] = (best, best_score)
                    for cat_key, tup in best_by_cat.items():
                        day, _score = tup
                        day_matches.append({
                            'category': day.get('category'),
                            'day_id': day.get('day_id'),
                            'day_name': day.get('name'),
                            'source': 'exercises',
                        })

                dedup = {}
                for m in day_matches:
                    cat = m.get('category')
                    if not isinstance(cat, str) or not cat.strip():
                        continue
                    cat_key = cat.strip().lower()
                    prev = dedup.get(cat_key)
                    if not prev:
                        dedup[cat_key] = m
                        continue
                    if prev.get('source') != 'title' and m.get('source') == 'title':
                        dedup[cat_key] = m
                day_matches = list(dedup.values())

                trained_categories = set()
                for c in categories:
                    nm = c.get('name') if isinstance(c, dict) else None
                    if not isinstance(nm, str) or not nm.strip():
                        continue
                    cat_key = nm.strip().lower()
                    if _contains_phrase(title_key, cat_key):
                        trained_categories.add(nm.strip())

                if workout_ex:
                    for cat_key, days in plan_days_by_cat.items():
                        best_score = 0.0
                        for _day, day_ex in days:
                            if not day_ex:
                                continue
                            overlap = len(workout_ex.intersection(day_ex))
                            if overlap < 2:
                                continue
                            score = overlap / float(max(len(day_ex), 1))
                            if score > best_score:
                                best_score = score
                        if best_score >= 0.35:
                            for c in categories:
                                if not isinstance(c, dict):
                                    continue
                                nm = c.get('name')
                                if isinstance(nm, str) and nm.strip().lower() == cat_key:
                                    trained_categories.add(nm.strip())
                                    break

                for m in day_matches:
                    cat = m.get('category')
                    if isinstance(cat, str) and cat.strip():
                        trained_categories.add(cat.strip())

                primary = None
                for m in day_matches:
                    if m.get('source') == 'title':
                        primary = m
                        break
                if not primary and day_matches:
                    primary = day_matches[0]

                matched_day = None
                matched_source = None
                if primary:
                    for d in plan_days:
                        if str(d.get('category') or '').strip().lower() == str(primary.get('category') or '').strip().lower() and d.get('day_id') == primary.get('day_id'):
                            matched_day = d
                            matched_source = primary.get('source')
                            break
                recent_context.append(
                    {
                        "date": dt.strftime('%Y-%m-%d') if hasattr(dt, 'strftime') else str(dt),
                        "exercises": w.get('exercises') or "",
                        "exercise_list": w.get('exercise_list') or [],
                        "exercise_details": w.get('exercise_details') or "",
                        "category": matched_day['category'] if matched_day else None,
                        "day_id": matched_day['day_id'] if matched_day else None,
                        "day_name": matched_day['name'] if matched_day else None,
                        "match_source": matched_source,
                        "day_matches": day_matches,
                        "trained_categories": sorted(list(trained_categories)),
                    }
                )

            today = datetime.utcnow().date()
            matched_items = []
            category_trained = {}
            for item in recent_context:
                date_str = item.get('date')
                try:
                    d = datetime.strptime(str(date_str)[:10], '%Y-%m-%d').date()
                except Exception:
                    continue

                trained = item.get('trained_categories')
                if isinstance(trained, list):
                    for cat in trained:
                        if not isinstance(cat, str) or not cat.strip():
                            continue
                        key = cat.strip().lower()
                        prev = category_trained.get(key)
                        if prev is None or d > prev:
                            category_trained[key] = d

                matches = item.get('day_matches')
                if isinstance(matches, list):
                    for m in matches:
                        if not isinstance(m, dict):
                            continue
                        cat = m.get('category')
                        day_id = m.get('day_id')
                        src = m.get('source')
                        if not (isinstance(cat, str) and cat.strip()):
                            continue
                        if not isinstance(day_id, int):
                            continue
                        if src not in ('title', 'exercises'):
                            continue
                        matched_items.append((d, cat.strip().lower(), int(day_id)))

            matched_items.sort(key=lambda x: x[0])

            cycle_done: dict[str, set[int]] = {}
            cycle_last_date: dict[str, datetime.date] = {}
            num_days_by_cat: dict[str, int] = {}
            for c in categories:
                if not isinstance(c, dict):
                    continue
                name = c.get('name')
                if not isinstance(name, str) or not name.strip():
                    continue
                key = name.strip().lower()
                nd = int(c.get('num_days') or 0)
                if nd > 0:
                    num_days_by_cat[key] = nd
                    cycle_done.setdefault(key, set())

            for d, cat_key, day_id in matched_items:
                nd = num_days_by_cat.get(cat_key)
                if not nd or day_id < 1 or day_id > int(nd):
                    continue
                s = cycle_done.setdefault(cat_key, set())
                s.add(int(day_id))
                cycle_last_date[cat_key] = d
                if len(s) >= int(nd):
                    s.clear()

            for c in categories:
                if not isinstance(c, dict):
                    continue
                name = c.get('name')
                if not isinstance(name, str) or not name.strip():
                    continue
                cat_key = name.strip().lower()
                nd = int(c.get('num_days') or 0)
                done_set = cycle_done.get(cat_key) or set()
                missing = [i for i in range(1, nd + 1) if i not in done_set] if nd > 0 else [1]
                next_day = int(min(missing)) if missing else 1
                last_date = category_trained.get(cat_key)
                c['cycle_done_day_ids'] = sorted(done_set)
                c['cycle_missing_day_ids'] = missing
                c['last_done_date'] = last_date.isoformat() if last_date else None
                c['next_day_id'] = next_day

            chosen_category = None
            chosen_next_day = 1
            best_score = None
            for c in categories:
                if not isinstance(c, dict):
                    continue
                name = c.get('name')
                if not isinstance(name, str) or not name.strip():
                    continue
                nd = int(c.get('num_days') or 0)
                next_day = int(c.get('next_day_id') or 1)
                last_done_date = c.get('last_done_date')
                last_d = None
                if isinstance(last_done_date, str) and last_done_date:
                    try:
                        last_d = datetime.strptime(last_done_date[:10], '%Y-%m-%d').date()
                    except Exception:
                        last_d = None
                days_since = 999 if not last_d else max((today - last_d).days, 0)
                missing_count = 0
                try:
                    missing_count = len(c.get('cycle_missing_day_ids') or [])
                except Exception:
                    missing_count = 0
                score = (days_since, missing_count, -nd)
                if best_score is None or score > best_score:
                    best_score = score
                    chosen_category = name.strip()
                    chosen_next_day = next_day

            if not chosen_category:
                chosen_category = categories[0]['name']
                chosen_next_day = int(categories[0].get('next_day_id') or 1)

            from services.gemini import GeminiService, GeminiServiceError

            api_keys = (
                Session.query(UserApiKey)
                .filter_by(user_id=user.id, is_active=True)
                .order_by(UserApiKey.created_at.desc())
                .all()
            )
            api_key_payloads = [
                {"id": key.id, "api_key": key.api_key, "account_label": key.account_label}
                for key in api_keys
                if key.api_key
            ]

            fallback_warning = None
            source = "fallback"
            reco = {
                "category": chosen_category,
                "day_id": int(chosen_next_day),
                "reasons": [],
                "model": "heuristic",
            }

            categories_one = [c for c in categories if isinstance(c, dict) and str(c.get('name') or '').strip().lower() == str(chosen_category).strip().lower()]
            plan_days_one = [d for d in plan_days if isinstance(d, dict) and str(d.get('category') or '').strip().lower() == str(chosen_category).strip().lower()]
            try:
                reco_ai = GeminiService.recommend_workout(
                    categories=categories_one or categories,
                    recent_workouts=recent_context,
                    plan_days=plan_days_one or plan_days,
                    api_keys=api_key_payloads,
                )
                if isinstance(reco_ai, dict) and isinstance(reco_ai.get('reasons'), list):
                    reco['reasons'] = reco_ai.get('reasons') or []
                reco['model'] = reco_ai.get('model') if isinstance(reco_ai, dict) else reco.get('model')
                key_id = reco_ai.get("key_id") if isinstance(reco_ai, dict) else None
                if key_id:
                    key_row = (
                        Session.query(UserApiKey)
                        .filter_by(id=key_id, user_id=user.id)
                        .first()
                    )
                    if key_row:
                        key_row.last_used_at = datetime.utcnow()
                        Session.commit()
                source = "gemini"
            except GeminiServiceError as e:
                logger.warning(f"Gemini unavailable, using fallback: {e}")
                fallback_warning = "Gemini is busy right now. Showing a smart fallback suggestion."

            if not isinstance(reco.get('reasons'), list) or len(reco.get('reasons') or []) < 3:
                try:
                    reco['reasons'] = GeminiService._build_reasons_from_history(
                        category=str(reco.get('category') or ''),
                        day_id=int(reco.get('day_id') or 1),
                        categories=categories,
                        plan_days=plan_days,
                        recent_workouts=recent_context,
                    )
                except Exception:
                    reco['reasons'] = [
                        "Recommended based on your plan and recent workouts.",
                        "Keeps your training split balanced.",
                        "Suggested to stay consistent with your program.",
                    ]

            raw_category = (reco.get('category') or '').strip()
            raw_day_id = int(reco.get('day_id') or 0)

            category_lookup = {c['name'].strip().lower(): c for c in categories}
            chosen = category_lookup.get(raw_category.lower())
            if not chosen:
                chosen = categories[0]
                raw_category = chosen['name']

            num_days = int(chosen.get('num_days') or 0)
            if raw_day_id < 1 or raw_day_id > num_days:
                raw_day_id = 1

            deep_link = url_for('retrieve_final', category=raw_category, day_id=raw_day_id)

            payload = {
                "ok": True,
                "category": raw_category,
                "day_id": raw_day_id,
                "reasons": reco.get('reasons') or [],
                "url": deep_link,
                "model": reco.get('model'),
                "source": source,
                "warning": fallback_warning,
            }
            with _reco_cache_lock:
                ttl = 60 if source == "fallback" else 120
                _reco_cache[cache_key] = {"ts": now, "payload": payload, "ttl": ttl}

            return jsonify(payload)
        except Exception as e:
            logger.error(f"Workout recommendation failed: {e}", exc_info=True)
            return jsonify({"ok": False, "error": "Unable to generate a recommendation right now."}), 500

    def shared_workout(token):
        try:
            payload = _load_share_token(token)
            user_id = payload.get('user_id')
            date_str = payload.get('date')
            if not user_id or not date_str:
                raise BadSignature("Missing data")

            share_user = Session.query(User).get(user_id)
            share_username = share_user.username.title() if share_user else "Workout Logger User"

            workout_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_dt = datetime.combine(workout_date, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)

            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user_id)
                .filter(WorkoutLog.date >= start_dt)
                .filter(WorkoutLog.date < end_dt)
                .order_by(WorkoutLog.id)
                .all()
            )

            if not logs:
                return render_template('share_workout.html', missing=True)

            workout_name = logs[0].workout_name or "Workout"
            header_date = workout_date.strftime('%d/%m')
            workout_text = build_exercise_text(logs)
            workout_text = f"{header_date} {workout_name}\n\n{workout_text}".strip()

            return render_template(
                'share_workout.html',
                date=date_str,
                share_username=share_username,
                workout_name=workout_name,
                logs=logs,
                workout_text=workout_text,
                missing=False,
            )
        except BadSignature:
            return render_template('share_workout.html', missing=True)
        except Exception as e:
            logger.error(f"Error loading shared workout: {e}", exc_info=True)
            return render_template('share_workout.html', missing=True)

    @login_required
    def edit_workout(date_str):
        user = current_user

        try:
            workout_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_dt = datetime.combine(workout_date, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)

            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .filter(WorkoutLog.date >= start_dt)
                .filter(WorkoutLog.date < end_dt)
                .order_by(WorkoutLog.id)
                .all()
            )

            if not logs:
                flash("Workout not found.", "error")
                return redirect(url_for('user_dashboard', username=user.username))

            workout_name = logs[0].workout_name or "Workout"
            workout_text = build_exercise_text(logs)

            if request.method == 'POST':
                title = sanitize_text_input(request.form.get('workout_title', ''), max_length=100) or "Workout"
                date_input = request.form.get('workout_date', '').strip()
                exercises_input = request.form.get('workout_text', '').strip()

                if not date_input:
                    flash("Please select a workout date.", "error")
                    return redirect(url_for('edit_workout', date_str=date_str))

                if not exercises_input:
                    flash("Please enter workout exercises.", "error")
                    return redirect(url_for('edit_workout', date_str=date_str))

                try:
                    new_date = datetime.strptime(date_input, '%Y-%m-%d').date()
                except ValueError:
                    flash("Invalid date format.", "error")
                    return redirect(url_for('edit_workout', date_str=date_str))

                new_start_dt = datetime.combine(new_date, datetime.min.time())
                new_end_dt = new_start_dt + timedelta(days=1)

                if new_date != workout_date:
                    conflict = (
                        Session.query(WorkoutLog)
                        .filter_by(user_id=user.id)
                        .filter(WorkoutLog.date >= new_start_dt)
                        .filter(WorkoutLog.date < new_end_dt)
                        .first()
                    )
                    if conflict:
                        flash("A workout already exists on that date. Edit that day instead.", "error")
                        return redirect(url_for('edit_workout', date_str=date_str))

                header_date = new_date.strftime('%d/%m')
                raw_text = f"{header_date} {title}\n{exercises_input}"

                parsed = workout_parser(raw_text, bodyweight=user.bodyweight)
                if not parsed:
                    raise ParsingError("Could not parse workout data. Please check the format.")

                parsed['date'] = new_start_dt
                parsed['workout_name'] = title

                Session.query(WorkoutLog).filter_by(user_id=user.id).filter(
                    WorkoutLog.date >= start_dt,
                    WorkoutLog.date < end_dt,
                ).delete(synchronize_session=False)

                handle_workout_log(Session, user, parsed)
                Session.commit()

                flash("Workout updated successfully!", "success")
                return redirect(url_for('view_workout', date_str=new_date.strftime('%Y-%m-%d')))

            return render_template(
                'workout_edit.html',
                workout_date=workout_date.strftime('%Y-%m-%d'),
                workout_name=workout_name,
                workout_text=workout_text,
                date=date_str,
            )
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for('user_dashboard', username=user.username))
        except ParsingError as e:
            Session.rollback()
            flash(str(e), "error")
            return redirect(url_for('edit_workout', date_str=date_str))
        except Exception as e:
            Session.rollback()
            logger.error(f"Error editing workout: {e}", exc_info=True)
            flash("Error updating workout.", "error")
            return redirect(url_for('edit_workout', date_str=date_str))

    @login_required
    def delete_workout(date_str):
        user = current_user

        try:
            workout_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_dt = datetime.combine(workout_date, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)

            deleted = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .filter(WorkoutLog.date >= start_dt)
                .filter(WorkoutLog.date < end_dt)
                .delete(synchronize_session=False)
            )
            Session.commit()

            if deleted:
                flash("Workout day deleted successfully.", "success")
            else:
                flash("Workout not found.", "error")

            return redirect(url_for('user_dashboard', username=user.username))
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for('user_dashboard', username=user.username))
        except Exception as e:
            Session.rollback()
            logger.error(f"Error deleting workout: {e}", exc_info=True)
            flash("Error deleting workout.", "error")
            return redirect(url_for('edit_workout', date_str=date_str))

    @login_required
    def log_workout():
        user = current_user

        if request.method == 'GET':
            return render_template('log.html', exercise_list=list_of_exercises)

        raw_text = request.form.get('workout_text', '').strip()

        if not raw_text:
            flash("Please enter workout data.", "error")
            return redirect(url_for('log_workout'))

        try:
            parsed = workout_parser(raw_text, bodyweight=user.bodyweight)
            if not parsed:
                raise ParsingError(
                    "Could not parse workout data. Please check the format."
                )
            if user.bodyweight is None and re.search(r'\bbw[+-]?\d*\b', raw_text, re.IGNORECASE):
                flash(
                    "Set your bodyweight in Settings to calculate BW loads accurately.",
                    "info",
                )
        except ParsingError as e:
            flash(str(e), "error")
            return redirect(url_for('log_workout'))
        except Exception as e:
            logger.error(f"Parsing error: {e}", exc_info=True)
            flash("Error parsing workout data. Please check the format.", "error")
            return redirect(url_for('log_workout'))

        try:
            summary = handle_workout_log(Session, user, parsed)
            exercises = parsed.get('exercises') or []
            exercise_count = len(exercises)
            set_count = sum(
                _count_sets({'weights': item.get('weights') or [], 'reps': item.get('reps') or []})
                for item in exercises
            )
            Session.commit()
            logger.info(
                f"Workout logged successfully for user {user.username} on {parsed['date']}"
            )

            return render_template(
                'result.html',
                summary=summary,
                date=parsed['date'].strftime('%Y-%m-%d'),
                exercise_count=exercise_count,
                set_count=set_count,
            )
        except Exception as e:
            Session.rollback()
            logger.error(f"Error saving workout: {e}", exc_info=True)
            flash("Error saving workout. Please try again.", "error")
            return redirect(url_for('log_workout'))

    app.add_url_rule('/', endpoint='index', view_func=index, methods=['GET'])
    app.add_url_rule('/workouts', endpoint='workout_history', view_func=workout_history, methods=['GET'])
    app.add_url_rule('/share/<token>', endpoint='shared_workout', view_func=shared_workout, methods=['GET'])
    app.add_url_rule(
        '/<username>',
        endpoint='user_dashboard',
        view_func=user_dashboard,
        methods=['GET'],
    )
    app.add_url_rule('/workout/<date_str>', endpoint='view_workout', view_func=view_workout, methods=['GET'])
    app.add_url_rule('/workout/<date_str>/edit', endpoint='edit_workout', view_func=edit_workout, methods=['GET', 'POST'])
    app.add_url_rule('/workout/<date_str>/delete', endpoint='delete_workout', view_func=delete_workout, methods=['POST'])
    app.add_url_rule('/log', endpoint='log_workout', view_func=log_workout, methods=['GET', 'POST'])
    app.add_url_rule('/api/recommend-workout', endpoint='recommend_workout_api', view_func=recommend_workout_api, methods=['GET'])
