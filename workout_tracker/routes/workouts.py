from datetime import datetime, timedelta
import html
import re
import threading
import time

from flask import Response, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from itsdangerous import URLSafeSerializer, BadSignature
from sqlalchemy import desc, func

from list_of_exercise import get_workout_days, list_of_exercises
from models import Session, User, WorkoutLog, UserApiKey
from parsers.workout import workout_parser, parse_bw_weight
from services.logging import handle_workout_log
from services.retrieve import generate_retrieve_output, get_effective_plan_text
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

    def _make_shortcut_token(user_id: int):
        payload = {
            "user_id": user_id,
            "scope": "shortcut_recommend",
        }
        return serializer.dumps(payload)

    def _load_shortcut_token(token: str) -> dict:
        payload = serializer.loads(token)
        if not isinstance(payload, dict) or payload.get("scope") != "shortcut_recommend":
            raise BadSignature("Invalid shortcut token")
        return payload

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
        s = html.unescape(str(name)).strip().lower()
        s = s.replace("•", " ")
        s = s.replace("–", "-").replace("—", "-")
        # Normalize hyphens to spaces so "pull-ups" and "pull ups" match.
        s = s.replace("-", " ")
        s = s.replace("’", "'").replace("‘", "'")
        s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
        s = re.sub(r"^\s*\d+\s*[\).:-]\s*", "", s)
        s = re.sub(r"\s+", " ", s)
        s = s.strip()
        # Order-invariant normalization helps match variants like
        # "Standing Calf Raises" vs "Calf Raises Standing".
        try:
            tokens = [t for t in s.split(" ") if t]
            tokens = [t for t in tokens if t not in {"and", "the"}]
            tokens.sort()
            s = " ".join(tokens).strip()
        except Exception:
            s = s.strip()
        return s

    def _norm_title_key(title: str) -> str:
        s = str(title or "").strip().lower()
        s = re.sub(r"\s+", " ", s)
        return s

    def _norm_day_title_key(title: str) -> str:
        s = str(title or "").strip().lower()
        s = s.replace("•", " ")
        s = s.replace("–", "-").replace("—", "-")
        s = s.replace("’", "'").replace("‘", "'")
        s = re.sub(r"\bday\b", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"[^a-z0-9&]+", " ", s)
        return s

    def _clean_workout_title(title: str) -> str:
        cleaned = html.unescape(str(title or "").strip())
        cleaned = cleaned.lstrip('-–—').strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned or "Workout"

    def _contains_phrase(haystack: str, needle: str) -> bool:
        if not haystack or not needle:
            return False
        try:
            pat = r"(^|[^a-z0-9])" + re.escape(str(needle)) + r"($|[^a-z0-9])"
            return re.search(pat, str(haystack)) is not None
        except Exception:
            return False

    def _best_match_plan_day(*, title: str, exercise_list: list[str], plan_days: list[dict], day_lookup: dict):
        workout_ex = {_norm_ex_name(x) for x in (exercise_list or []) if _norm_ex_name(x)}
        if workout_ex:
            workout_len = len(workout_ex)
            best = None
            best_score = None
            for day in plan_days:
                day_ex = {_norm_ex_name(x) for x in (day.get('exercises') or []) if _norm_ex_name(x)}
                if not day_ex:
                    continue
                overlap = len(workout_ex.intersection(day_ex))
                if overlap < 2 or not workout_len:
                    continue
                precision = overlap / float(workout_len)
                recall = overlap / float(max(len(day_ex), 1))
                f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
                score = (f1, overlap, precision)
                if best_score is None or score > best_score:
                    best_score = score
                    best = day

            if best and best_score is not None:
                f1, overlap, _precision = best_score
                if overlap >= 4 or (overlap >= 3 and f1 >= 0.5):
                    return best, "exercises"

        title_key = _norm_day_title_key(title)
        if title_key in day_lookup:
            return day_lookup[title_key], "title"

        for day in plan_days:
            day_name = day.get('name')
            if not isinstance(day_name, str) or not day_name.strip():
                continue
            day_key = _norm_day_title_key(day_name)
            if title_key == day_key or (day_key and title_key.startswith(day_key + " ")) or _contains_phrase(title_key, day_key):
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
                        'title': _clean_workout_title(log.workout_name or "Workout"),
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
                Session.query(func.date(WorkoutLog.date))
                .filter_by(user_id=user.id)
                .distinct()
                .count()
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

            workout_name = _clean_workout_title(logs[0].workout_name or "Workout")
            header_date = workout_date.strftime('%d/%m')
            workout_text = build_exercise_text(logs)
            workout_text = f"{header_date} {workout_name}\n\n{workout_text}".strip()
            exercise_count = len(logs)
            set_count = 0
            missing_bw_exercises = set()
            prev_1rm_by_exercise = {}

            for log in logs:
                prev_log = (
                    Session.query(WorkoutLog)
                    .filter_by(user_id=user.id, exercise=log.exercise)
                    .filter(WorkoutLog.date < start_dt)
                    .order_by(WorkoutLog.date.desc())
                    .first()
                )
                prev_1rm_by_exercise[log.exercise] = (
                    prev_log.estimated_1rm if prev_log and prev_log.estimated_1rm else None
                )

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
                                weight_token = re.sub(r'\s+', '', parts[0]).lower()
                                bw_weight = parse_bw_weight(weight_token, user.bodyweight)
                                if bw_weight is not None:
                                    weight = bw_weight
                                else:
                                    cleaned_weight = re.sub(r'(kg|lbs|lb)', '', parts[0], flags=re.IGNORECASE)
                                    weight = float(cleaned_weight)
                                reps = int(parts[1])
                                total_volume += weight * reps
                        except (ValueError, IndexError):
                            continue
                log.total_volume = total_volume if total_volume > 0 else None
                prev_1rm = prev_1rm_by_exercise.get(log.exercise)
                current_1rm = log.estimated_1rm if log.estimated_1rm else None
                if prev_1rm and current_1rm:
                    delta_pct = ((current_1rm - prev_1rm) / prev_1rm) * 100.0
                    delta_pct = max(-300.0, min(300.0, delta_pct))
                else:
                    delta_pct = None
                log.improvement_pct = delta_pct
            
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

    def _recommend_workout_payload(user, *, use_cache: bool = True, cache_key_prefix: str = "u:"):
        now = time.time()
        cache_key = f"{cache_key_prefix}{user.id}"
        if use_cache:
            with _reco_cache_lock:
                cached = _reco_cache.get(cache_key)
                ttl = cached.get("ttl", 120) if cached else 120
                if cached and now - cached.get("ts", 0) < ttl:
                    return cached["payload"], 200

        try:
            plan_text = get_effective_plan_text(Session, user)
            plan_data = get_workout_days(plan_text or "")
            workout_map = plan_data.get("workout", {}) if isinstance(plan_data, dict) else {}

            categories: list[dict] = []
            plan_days: list[dict] = []
            day_lookup: dict[str, dict] = {}

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
                    day_lookup[_norm_title_key(day_name)] = entry
                    day_lookup[_norm_day_title_key(day_name)] = entry

            if not categories:
                return {"ok": False, "error": "No workout plan found."}, 400

            # Precompute normalized exercise sets per plan day for overlap scoring.
            plan_days_by_cat: dict[str, list[tuple[dict, set[str]]]] = {}
            for d in plan_days:
                cat = d.get("category")
                if not isinstance(cat, str) or not cat.strip():
                    continue
                ex_set = {_norm_ex_name(x) for x in (d.get("exercises") or []) if _norm_ex_name(x)}
                plan_days_by_cat.setdefault(cat.strip().lower(), []).append((d, ex_set))

            # --- Session detection: exercise-first with optional title hint as a *small* boost. ---
            session_days: list[tuple[dict, set[str]]] = plan_days_by_cat.get("session") or []
            session_day_ex_by_id: dict[int, set[str]] = {}
            session_day_name_by_id: dict[int, str] = {}
            for day, day_ex in session_days:
                try:
                    did = int(day.get("day_id") or 0)
                except Exception:
                    continue
                if did <= 0:
                    continue
                session_day_ex_by_id[did] = set(day_ex or set())
                nm = day.get("name")
                if isinstance(nm, str) and nm.strip():
                    session_day_name_by_id[did] = nm

            # Down-weight exercises that appear in many session days (accessories) so they don't dominate matches.
            session_ex_freq: dict[str, int] = {}
            for _did, exs in session_day_ex_by_id.items():
                for ex in exs:
                    session_ex_freq[ex] = session_ex_freq.get(ex, 0) + 1

            session_ex_weight: dict[str, float] = {}
            for ex, freq in session_ex_freq.items():
                try:
                    f = max(int(freq), 1)
                except Exception:
                    f = 1
                # 1/sqrt(freq): unique-ish movements ~1.0, very common ones shrink.
                session_ex_weight[ex] = 1.0 / (float(f) ** 0.5)

            session_anchors: dict[int, set[str]] = {}
            for did, exs in session_day_ex_by_id.items():
                ranked = sorted(exs, key=lambda e: session_ex_weight.get(e, 1.0), reverse=True)
                session_anchors[did] = set(ranked[:2])

            def _session_title_hint_id(title_str: str) -> int | None:
                if not isinstance(title_str, str) or not title_str:
                    return None
                m = re.search(r"\bsession\s*(\d+)\b", title_str, flags=re.IGNORECASE)
                if not m:
                    return None
                try:
                    return int(m.group(1))
                except Exception:
                    return None

            def _score_session_day(*, workout_ex: set[str], day_id: int) -> dict:
                day_ex = session_day_ex_by_id.get(int(day_id)) or set()
                overlap = workout_ex.intersection(day_ex)
                overlap_count = len(overlap)
                overlap_w = sum(session_ex_weight.get(e, 1.0) for e in overlap)
                # Penalize lots of extras slightly (weights for unknown exercises are lower).
                workout_w = sum(session_ex_weight.get(e, 0.7) for e in workout_ex) or 0.0
                day_w = sum(session_ex_weight.get(e, 1.0) for e in day_ex) or 0.0
                wprec = (overlap_w / workout_w) if workout_w else 0.0
                wrec = (overlap_w / day_w) if day_w else 0.0
                wf1 = (2 * wprec * wrec / (wprec + wrec)) if (wprec + wrec) else 0.0
                anchors = session_anchors.get(int(day_id)) or set()
                anchor_hit = bool(anchors.intersection(overlap))
                return {
                    "wf1": float(wf1),
                    "overlap_count": int(overlap_count),
                    "overlap_w": float(overlap_w),
                    "overlap_ex": set(overlap),
                    "anchor_hit": bool(anchor_hit),
                }

            def _best_session_match(*, workout_ex: set[str], title_hint_id: int | None) -> dict:
                """
                Returns:
                  - accepted: match dict (for day_matches) or None
                  - evidence: dict describing accepted/partial evidence (for cycle credit) or None
                """
                if not isinstance(workout_ex, set) or len(workout_ex) < 2:
                    return {"accepted": None, "evidence": None}
                allow_classify = len(workout_ex) >= 3

                best_accept = None
                second_accept = None
                best_partial = None
                for did in session_day_ex_by_id.keys():
                    s = _score_session_day(workout_ex=workout_ex, day_id=did)
                    # Base acceptance: exercise evidence required.
                    oc = s["overlap_count"]
                    wf1 = s["wf1"]
                    is_accept = bool(
                        allow_classify
                        and (
                            oc >= 4
                            or (oc >= 3 and wf1 >= 0.5)
                            or (s["anchor_hit"] and oc >= 3 and wf1 >= 0.55)
                        )
                    )
                    is_partial = bool(s["anchor_hit"] and oc >= 2 and wf1 >= 0.45)

                    if is_accept:
                        bonus = 0.0
                        if title_hint_id is not None and int(title_hint_id) == int(did):
                            # Title is a hint only; add a small controlled bias if the exercise match is already strong.
                            bonus = 0.03
                        score_tup = (min(wf1 + bonus, 1.0), s["overlap_w"], s["overlap_count"])
                        cand = {"day_id": int(did), "score": score_tup, "raw": s}
                        if best_accept is None or cand["score"] > best_accept["score"]:
                            second_accept = best_accept
                            best_accept = cand
                        elif second_accept is None or cand["score"] > second_accept["score"]:
                            second_accept = cand
                        continue

                    if is_partial:
                        # For partial evidence we do NOT apply title bonus; exercises are the foundation.
                        score_tup = (wf1, s["overlap_w"], s["overlap_count"])
                        cand = {"day_id": int(did), "score": score_tup, "raw": s}
                        if best_partial is None or cand["score"] > best_partial["score"]:
                            best_partial = cand

                if not best_accept and not best_partial:
                    return {"accepted": None, "evidence": None}

                    # Ambiguity guard: if top two are very close, don't auto-classify/credit.
                    if best_accept and second_accept:
                        best_primary = float(best_accept["score"][0])  # includes optional title hint bonus
                        second_primary = float(second_accept["score"][0])
                        if (
                            abs(best_primary - second_primary) < 0.04
                            and abs(best_accept["raw"]["overlap_count"] - second_accept["raw"]["overlap_count"]) <= 1
                        ):
                            # If the title hint breaks a near-tie (and exercises are already strong),
                            # allow it as a controlled bias instead of calling it ambiguous.
                            if (
                                title_hint_id is not None
                                and int(title_hint_id) == int(best_accept["day_id"])
                                and (best_primary - second_primary) >= 0.02
                            ):
                                pass
                            else:
                                return {"accepted": None, "evidence": None}

                if best_accept:
                    did = int(best_accept["day_id"])
                    raw = best_accept["raw"]
                    match = {
                        "category": "Session",
                        "day_id": did,
                        "day_name": session_day_name_by_id.get(did) or f"Session {did}",
                        "source": "exercises",
                    }

                    # Cycle credit should be a bit stricter than classification.
                    creditable = bool(
                        raw["overlap_count"] >= 4
                        or (raw["anchor_hit"] and raw["overlap_count"] >= 3 and raw["wf1"] >= 0.55)
                    )
                    evidence = {
                        "day_id": did,
                        "creditable": creditable,
                        "partial": not creditable,
                        "overlap_ex": sorted(list(raw["overlap_ex"])),
                        "overlap_count": raw["overlap_count"],
                        "wf1": raw["wf1"],
                        "anchor_hit": raw["anchor_hit"],
                    }
                    return {"accepted": match, "evidence": evidence}

                did = int(best_partial["day_id"])
                raw = best_partial["raw"]
                evidence = {
                    "day_id": did,
                    "creditable": False,
                    "partial": True,
                    "overlap_ex": sorted(list(raw["overlap_ex"])),
                    "overlap_count": raw["overlap_count"],
                    "wf1": raw["wf1"],
                    "anchor_hit": raw["anchor_hit"],
                }
                return {"accepted": None, "evidence": evidence}

            recent = get_recent_workouts(user, limit=20)
            recent_context: list[dict] = []

            for w in recent:
                dt = w.get("date")
                title = w.get("title") or "Workout"
                title_key = _norm_day_title_key(title)
                workout_ex = {_norm_ex_name(x) for x in (w.get("exercise_list") or []) if _norm_ex_name(x)}
                title_hint_session_id = _session_title_hint_id(title)

                session_evidence = None
                day_matches: list[dict] = []

                # Title-based matches (non-session only). Never allow title-only Session classification.
                for day in plan_days:
                    cat_name = day.get("category")
                    if isinstance(cat_name, str) and cat_name.strip().lower() == "session":
                        continue
                    day_name = day.get("name")
                    if not isinstance(day_name, str) or not day_name.strip():
                        continue
                    day_key = _norm_day_title_key(day_name)
                    if _contains_phrase(title_key, day_key):
                        day_matches.append(
                            {
                                "category": day.get("category"),
                                "day_id": day.get("day_id"),
                                "day_name": day_name,
                                "source": "title",
                            }
                        )

                # Exercise-based best match per category (non-session).
                if workout_ex:
                    workout_len = len(workout_ex)
                    best_by_cat: dict[str, dict] = {}
                    for cat_key, days in plan_days_by_cat.items():
                        if cat_key == "session":
                            continue
                        best = None
                        best_score = None
                        for day, day_ex in days:
                            if not day_ex:
                                continue
                            overlap = len(workout_ex.intersection(day_ex))
                            if overlap < 2 or not workout_len:
                                continue
                            precision = overlap / float(workout_len)
                            recall = overlap / float(max(len(day_ex), 1))
                            f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
                            score = (f1, overlap, precision)
                            if best_score is None or score > best_score:
                                best_score = score
                                best = day
                        if best and best_score is not None:
                            f1, overlap, _precision = best_score
                            if overlap >= 4 or (overlap >= 3 and f1 >= 0.5):
                                best_by_cat[cat_key] = {
                                    "category": best.get("category"),
                                    "day_id": best.get("day_id"),
                                    "day_name": best.get("name"),
                                    "source": "exercises",
                                }
                    day_matches.extend(best_by_cat.values())

                # Session match: exercise-first, title is only a small hint boost.
                if workout_ex and session_day_ex_by_id:
                    sm = _best_session_match(workout_ex=workout_ex, title_hint_id=title_hint_session_id)
                    if isinstance(sm, dict):
                        accepted = sm.get("accepted")
                        if isinstance(accepted, dict):
                            day_matches.append(accepted)
                        ev = sm.get("evidence")
                        if isinstance(ev, dict):
                            session_evidence = ev

                # Deduplicate: one match per category, prefer exercise-based.
                dedup: dict[str, dict] = {}
                for m in day_matches:
                    cat = m.get("category")
                    if not isinstance(cat, str) or not cat.strip():
                        continue
                    cat_key = cat.strip().lower()
                    prev = dedup.get(cat_key)
                    if not prev:
                        dedup[cat_key] = m
                        continue
                    if prev.get("source") != "exercises" and m.get("source") == "exercises":
                        dedup[cat_key] = m
                day_matches = list(dedup.values())

                trained_categories: set[str] = set()
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
                                nm = c.get("name") if isinstance(c, dict) else None
                                if isinstance(nm, str) and nm.strip().lower() == cat_key:
                                    trained_categories.add(nm.strip())
                                    break

                for m in day_matches:
                    cat = m.get("category")
                    if isinstance(cat, str) and cat.strip():
                        trained_categories.add(cat.strip())

                # Primary: prefer exercises for the UI label + per-workout day_id.
                primary = None
                for preferred in ("exercises", "title"):
                    for m in day_matches:
                        if m.get("source") == preferred:
                            primary = m
                            break
                    if primary:
                        break
                if not primary and day_matches:
                    primary = day_matches[0]

                matched_day = None
                matched_source = None
                if primary:
                    for d in plan_days:
                        if (
                            str(d.get("category") or "").strip().lower()
                            == str(primary.get("category") or "").strip().lower()
                            and d.get("day_id") == primary.get("day_id")
                        ):
                            matched_day = d
                            matched_source = primary.get("source")
                            break

                recent_context.append(
                    {
                        "date": dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt),
                        "title": title,
                        "exercises": w.get("exercises") or "",
                        "exercise_list": w.get("exercise_list") or [],
                        "exercise_details": w.get("exercise_details") or "",
                        "session_evidence": session_evidence,
                        "category": matched_day["category"] if matched_day else None,
                        "day_id": matched_day["day_id"] if matched_day else None,
                        "day_name": matched_day["name"] if matched_day else None,
                        "match_source": matched_source,
                        "day_matches": day_matches,
                        "trained_categories": sorted(list(trained_categories)),
                    }
                )

            today = datetime.utcnow().date()
            matched_items: list[tuple[datetime.date, str, int]] = []
            category_trained: dict[str, datetime.date] = {}
            session_evidence_items: list[tuple[datetime.date, dict]] = []

            for item in recent_context:
                date_str = item.get("date")
                try:
                    d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
                except Exception:
                    continue

                trained = item.get("trained_categories")
                if isinstance(trained, list):
                    for cat in trained:
                        if not isinstance(cat, str) or not cat.strip():
                            continue
                        key = cat.strip().lower()
                        prev = category_trained.get(key)
                        if prev is None or d > prev:
                            category_trained[key] = d

                ev = item.get("session_evidence")
                if isinstance(ev, dict) and isinstance(ev.get("day_id"), int):
                    session_evidence_items.append((d, ev))

                matches = item.get("day_matches")
                if isinstance(matches, list):
                    for m in matches:
                        if not isinstance(m, dict):
                            continue
                        cat = m.get("category")
                        day_id = m.get("day_id")
                        src = m.get("source")
                        if not (isinstance(cat, str) and cat.strip()):
                            continue
                        if not isinstance(day_id, int):
                            continue
                        if src not in ("title", "exercises"):
                            continue
                        cat_key = cat.strip().lower()
                        if cat_key == "session":
                            # Session cycle is tracked via session_evidence_items (exercise-first).
                            continue
                        matched_items.append((d, cat_key, int(day_id)))

            matched_items.sort(key=lambda x: x[0])
            session_evidence_items.sort(key=lambda x: x[0])

            # --- Compute cycle done/missing per category (non-session) ---
            cycle_done: dict[str, set[int]] = {}
            num_days_by_cat: dict[str, int] = {}
            for c in categories:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                key = name.strip().lower()
                nd = int(c.get("num_days") or 0)
                if nd > 0:
                    num_days_by_cat[key] = nd
                    cycle_done.setdefault(key, set())

            for d, cat_key, day_id in matched_items:
                nd = num_days_by_cat.get(cat_key)
                if not nd or day_id < 1 or day_id > int(nd):
                    continue
                s = cycle_done.setdefault(cat_key, set())
                s.add(int(day_id))
                if len(s) >= int(nd):
                    s.clear()

            # --- Session cycle progress: credit based on exercise evidence (with partial aggregation). ---
            heading_sessions = plan_data.get("heading_sessions") if isinstance(plan_data, dict) else None
            session_headings = plan_data.get("headings") if isinstance(plan_data, dict) else None
            if not (isinstance(session_headings, list) and session_headings):
                if isinstance(heading_sessions, dict) and heading_sessions:
                    session_headings = sorted([h for h in heading_sessions.keys() if isinstance(h, str)])
                else:
                    session_headings = []

            cycles: list[list[int]] = []
            if isinstance(heading_sessions, dict) and session_headings:
                for h in session_headings:
                    ids = heading_sessions.get(h)
                    if isinstance(ids, list) and ids:
                        cleaned = sorted({int(x) for x in ids if isinstance(x, int)})
                        if cleaned:
                            cycles.append(cleaned)
            if not cycles and session_day_ex_by_id:
                cycles = [sorted(session_day_ex_by_id.keys())]

            day_to_cycle_index: dict[int, int] = {}
            for idx, ids in enumerate(cycles):
                for sid in ids:
                    day_to_cycle_index[int(sid)] = int(idx)

            def _session_union_creditable(day_id: int, covered_ex: set[str]) -> bool:
                day_ex = session_day_ex_by_id.get(int(day_id)) or set()
                if not day_ex:
                    return False
                covered = set(covered_ex or set()).intersection(day_ex)
                oc = len(covered)
                if oc >= 4:
                    return True
                overlap_w = sum(session_ex_weight.get(e, 1.0) for e in covered)
                day_w = sum(session_ex_weight.get(e, 1.0) for e in day_ex) or 0.0
                recall = (overlap_w / day_w) if day_w else 0.0
                anchors = session_anchors.get(int(day_id)) or set()
                anchor_hit = bool(anchors.intersection(covered))
                return bool(anchor_hit and oc >= 3 and recall >= 0.6)

            session_credit_events: list[tuple[datetime.date, int]] = []
            pending: dict[int, dict] = {}
            window_days = 3
            for d, ev in session_evidence_items:
                try:
                    sid = int(ev.get("day_id"))
                except Exception:
                    continue
                if sid <= 0:
                    continue

                overlap_ex = set()
                try:
                    overlap_ex = {str(x) for x in (ev.get("overlap_ex") or []) if isinstance(x, str) and x}
                except Exception:
                    overlap_ex = set()

                if bool(ev.get("creditable")):
                    session_credit_events.append((d, sid))
                    pending.pop(sid, None)
                    continue

                # Partial / non-credit evidence: aggregate within a small window.
                p = pending.get(sid)
                if not p or (d - p.get("start", d)).days > window_days:
                    pending[sid] = {"start": d, "covered": set(overlap_ex)}
                else:
                    p["covered"] = set(p.get("covered") or set()).union(overlap_ex)

                if _session_union_creditable(sid, set(pending[sid].get("covered") or set())):
                    session_credit_events.append((d, sid))
                    pending.pop(sid, None)

            # Simulate cycle progression from credited session events.
            session_done_set: set[int] = set()
            session_missing: list[int] = []
            session_next_day: int = 1
            session_cycle_label = None
            last_session_date = None

            if cycles:
                cur_cycle_idx = 0
                done: set[int] = set()
                cycle_sets = [set(ids) for ids in cycles]
                for d, sid in session_credit_events:
                    idx = day_to_cycle_index.get(int(sid))
                    if idx is None:
                        continue
                    if idx != cur_cycle_idx:
                        cur_cycle_idx = int(idx)
                        done = set()
                    done.add(int(sid))
                    last_session_date = d
                    if done.issuperset(cycle_sets[cur_cycle_idx]):
                        done = set()
                        cur_cycle_idx = (cur_cycle_idx + 1) % len(cycles)

                current_cycle_ids = cycles[cur_cycle_idx]
                session_done_set = set(done)
                session_missing = [i for i in current_cycle_ids if i not in session_done_set]
                session_next_day = int(min(session_missing)) if session_missing else int(min(current_cycle_ids))
                if isinstance(session_headings, list) and len(session_headings) == len(cycles):
                    session_cycle_label = session_headings[cur_cycle_idx]

            if last_session_date:
                prev = category_trained.get("session")
                if prev is None or last_session_date > prev:
                    category_trained["session"] = last_session_date

            for c in categories:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                cat_key = name.strip().lower()
                nd = int(c.get("num_days") or 0)
                done_set = cycle_done.get(cat_key) or set()

                if cat_key == "session" and session_day_ex_by_id:
                    c["cycle_done_day_ids"] = sorted(list(session_done_set))
                    c["cycle_missing_day_ids"] = session_missing or sorted(session_day_ex_by_id.keys())
                    last_date = category_trained.get("session")
                    c["last_done_date"] = last_date.isoformat() if last_date else None
                    c["next_day_id"] = int(session_next_day or 1)
                    if session_cycle_label:
                        c["cycle_label"] = session_cycle_label
                    continue

                missing = [i for i in range(1, nd + 1) if i not in done_set] if nd > 0 else [1]
                next_day = int(min(missing)) if missing else 1
                last_date = category_trained.get(cat_key)
                c["cycle_done_day_ids"] = sorted(done_set)
                c["cycle_missing_day_ids"] = missing
                c["last_done_date"] = last_date.isoformat() if last_date else None
                c["next_day_id"] = next_day

            # --- Choose a category/day (fallback heuristic) ---
            chosen_category = None
            chosen_next_day = 1
            best_score = None

            for c in categories:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                nd = int(c.get("num_days") or 0)
                next_day = int(c.get("next_day_id") or 1)

                last_done_date = c.get("last_done_date")
                last_d = None
                if isinstance(last_done_date, str) and last_done_date:
                    try:
                        last_d = datetime.strptime(last_done_date[:10], "%Y-%m-%d").date()
                    except Exception:
                        last_d = None
                days_since = 999 if not last_d else max((today - last_d).days, 0)

                try:
                    missing_count = len(c.get("cycle_missing_day_ids") or [])
                except Exception:
                    missing_count = 0

                score = (days_since, missing_count, -nd)
                if best_score is None or score > best_score:
                    best_score = score
                    chosen_category = name.strip()
                    chosen_next_day = next_day

            if not chosen_category:
                chosen_category = categories[0]["name"]
                chosen_next_day = int(categories[0].get("next_day_id") or 1)

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

            categories_one = [
                c
                for c in categories
                if isinstance(c, dict)
                and str(c.get("name") or "").strip().lower() == str(chosen_category).strip().lower()
            ]
            plan_days_one = [
                d
                for d in plan_days
                if isinstance(d, dict)
                and str(d.get("category") or "").strip().lower() == str(chosen_category).strip().lower()
            ]

            try:
                reco_ai = GeminiService.recommend_workout(
                    categories=categories_one or categories,
                    recent_workouts=recent_context,
                    plan_days=plan_days_one or plan_days,
                    api_keys=api_key_payloads,
                )
                if isinstance(reco_ai, dict) and isinstance(reco_ai.get("reasons"), list):
                    reco["reasons"] = reco_ai.get("reasons") or []
                reco["model"] = reco_ai.get("model") if isinstance(reco_ai, dict) else reco.get("model")
                key_id = reco_ai.get("key_id") if isinstance(reco_ai, dict) else None
                if key_id:
                    key_row = Session.query(UserApiKey).filter_by(id=key_id, user_id=user.id).first()
                    if key_row:
                        key_row.last_used_at = datetime.utcnow()
                        Session.commit()
                source = "gemini"
            except GeminiServiceError as e:
                logger.warning(f"Gemini unavailable, using fallback: {e}")
                fallback_warning = "Gemini is busy right now. Showing a smart fallback suggestion."

            if not isinstance(reco.get("reasons"), list) or len(reco.get("reasons") or []) < 3:
                try:
                    reco["reasons"] = GeminiService._build_reasons_from_history(
                        category=str(reco.get("category") or ""),
                        day_id=int(reco.get("day_id") or 1),
                        categories=categories,
                        plan_days=plan_days,
                        recent_workouts=recent_context,
                    )
                except Exception:
                    reco["reasons"] = [
                        "Recommended based on your plan and recent workouts.",
                        "Keeps your training split balanced.",
                        "Suggested to stay consistent with your program.",
                    ]

            raw_category = (reco.get("category") or "").strip()
            raw_day_id = int(reco.get("day_id") or 0)

            category_lookup = {c["name"].strip().lower(): c for c in categories}
            chosen = category_lookup.get(raw_category.lower())
            if not chosen:
                chosen = categories[0]
                raw_category = chosen["name"]

            num_days = int(chosen.get("num_days") or 0)
            if raw_day_id < 1 or raw_day_id > num_days:
                raw_day_id = 1

            deep_link = url_for("retrieve_final", category=raw_category, day_id=raw_day_id)

            display_label = ""
            try:
                match_day = next(
                    (
                        d
                        for d in plan_days
                        if isinstance(d, dict)
                        and str(d.get("category") or "").strip().lower() == str(raw_category).strip().lower()
                        and int(d.get("day_id") or 0) == int(raw_day_id)
                    ),
                    None,
                )
                if match_day and isinstance(match_day.get("name"), str):
                    display_label = _format_plan_day_label(match_day.get("name"))
            except Exception:
                display_label = ""

            if str(raw_category).strip().lower() == "session" and (
                not display_label or display_label.strip().lower() == "session"
            ):
                extracted = _extract_session_title(plan_text, raw_day_id)
                if extracted:
                    display_label = extracted

            payload = {
                "ok": True,
                "category": raw_category,
                "day_id": raw_day_id,
                "label": display_label,
                "reasons": reco.get("reasons") or [],
                "url": deep_link,
                "model": reco.get("model"),
                "source": source,
                "warning": fallback_warning,
            }

            if use_cache:
                with _reco_cache_lock:
                    ttl = 60 if source == "fallback" else 120
                    _reco_cache[cache_key] = {"ts": now, "payload": payload, "ttl": ttl}

            return payload, 200
        except Exception as e:
            logger.error(f"Workout recommendation failed: {e}", exc_info=True)
            return {"ok": False, "error": "Unable to generate a recommendation right now."}, 500

    @login_required
    def recommend_workout_api():
        user = current_user
        payload, status = _recommend_workout_payload(user, use_cache=True, cache_key_prefix="u:")
        return jsonify(payload), status

    @login_required
    def history_qa_api():
        user = current_user
        try:
            if not user.is_admin():
                return jsonify({"ok": False, "error": "AI assistant is currently limited to admins."}), 403
            body = request.get_json(silent=True)
            if body is None:
                body = {}
            question = body.get('question')
            context_exercise = body.get('exercise')
            context_metric = body.get('metric')
            context_date = body.get('date')

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

            from services.history_qa import answer_history_question

            exercises = (
                Session.query(WorkoutLog.exercise)
                .filter_by(user_id=user.id)
                .distinct()
                .order_by(WorkoutLog.exercise)
                .all()
            )
            exercise_options = [e[0] for e in exercises if e and e[0]]

            payload = answer_history_question(
                Session,
                user,
                question=str(question or ''),
                context_exercise=str(context_exercise or '').strip() or None,
                context_metric=str(context_metric or '').strip() or None,
                context_date=str(context_date or '').strip() or None,
                api_keys=api_key_payloads,
                exercise_options=exercise_options,
            )

            if not isinstance(payload, dict):
                return jsonify({"ok": False, "error": "Unexpected response."}), 500
            status = 200 if payload.get('ok') else 400
            return jsonify(payload), status
        except Exception as e:
            logger.error(f"History QA failed: {e}", exc_info=True)
            return jsonify({"ok": False, "error": "Unable to answer right now."}), 500

    def _format_reco_label(category: str, day_id: int) -> str:
        cat = str(category or "").strip()
        if not cat:
            cat = "Workout"
        try:
            day = int(day_id)
        except Exception:
            day = 1
        return f"{cat} • Day {day}"

    def _format_plan_day_label(day_name: str) -> str:
        name = str(day_name or '').strip()
        if not name:
            return ''
        name = re.sub(r'\s+(\d+)\s*$', '', name).strip()
        name = name.replace('–', '-').replace('—', '-').replace('•', '-').strip()
        name = re.sub(r'\s*-\s*', ' - ', name)
        name = re.sub(r'\s{2,}', ' ', name)
        return name

    def _extract_session_title(plan_text: str, day_id: int) -> str:
        text = str(plan_text or '')
        if not text.strip():
            return ''
        try:
            did = int(day_id)
        except Exception:
            return ''

        patterns = [
            rf'^\s*Session\s*{did}\s*[–—-]\s*(.+?)\s*$',
            rf'^\s*Session\s*{did}\s*[:]\s*(.+?)\s*$',
            rf'^\s*Session\s*{did}\b\s*(.+?)\s*$',
        ]
        for line in text.splitlines():
            if not line or not line.strip():
                continue
            for pat in patterns:
                m = re.match(pat, line.strip(), flags=re.IGNORECASE)
                if not m:
                    continue
                tail = (m.group(1) or '').strip()
                tail = tail.lstrip('–—-:').strip()
                if tail:
                    tail = tail.replace('–', '-').replace('—', '-').strip()
                    tail = re.sub(r'\s*-\s*', ' - ', tail)
                    tail = re.sub(r'\s{2,}', ' ', tail)
                    return f"Session {did} - {tail}"
                return f"Session {did}"
        return ''

    @login_required
    def shortcut_recommend_url():
        token = _make_shortcut_token(current_user.id)
        shortcut_url = url_for('shortcut_recommend', token=token, _external=True)
        return jsonify({"ok": True, "url": shortcut_url})

    def shortcut_recommend(token):
        try:
            payload = _load_shortcut_token(token)
            user_id = payload.get("user_id")
            if not user_id:
                raise BadSignature("Missing user")
            user = Session.query(User).get(user_id)
            if not user:
                raise BadSignature("Unknown user")
        except BadSignature:
            return Response("Invalid shortcut token.", status=401, mimetype="text/plain")
        except Exception as e:
            logger.error(f"Shortcut token error: {e}", exc_info=True)
            return Response("Invalid shortcut token.", status=401, mimetype="text/plain")

        reco_payload, status = _recommend_workout_payload(user, use_cache=False, cache_key_prefix="shortcut:")
        if status != 200 or not reco_payload.get("ok"):
            msg = reco_payload.get("error") if isinstance(reco_payload, dict) else "Unable to generate a recommendation right now."
            return Response(str(msg), status=status, mimetype="text/plain")

        category = reco_payload.get("category")
        day_id = reco_payload.get("day_id")
        output, _, _ = generate_retrieve_output(Session, user, category, day_id)
        return Response(str(output), mimetype="text/plain")

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
    app.add_url_rule('/shortcut/recommend', endpoint='shortcut_recommend_url', view_func=shortcut_recommend_url, methods=['GET'])
    app.add_url_rule('/shortcut/recommend/<token>', endpoint='shortcut_recommend', view_func=shortcut_recommend, methods=['GET'])
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
    app.add_url_rule('/api/history-qa', endpoint='history_qa_api', view_func=history_qa_api, methods=['POST'])
