import json
from datetime import datetime
import time
import re

import requests

from config import Config
from utils.logger import logger


class GeminiServiceError(Exception):
    pass


class GeminiService:
    DEFAULT_MODEL_CANDIDATES = (
        "gemini-2.5-flash",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    )
    _model_cache: dict[str, str] = {}
    _key_cooldown_until: dict[str, float] = {}
    _key_bad_models: dict[str, set[str]] = {}
    _available_models_cache: dict[str, dict] = {}
    _available_models_ttl_s: int = 600

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    @staticmethod
    def _headers(api_key: str) -> dict:
        return {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _list_models(api_key: str) -> list[str]:
        url = f"{GeminiService.BASE_URL}/models"
        resp = requests.get(url, headers=GeminiService._headers(api_key), timeout=20)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return []

        out: list[str] = []
        for m in models:
            if not isinstance(m, dict):
                continue
            name = m.get("name")
            supported = m.get("supportedGenerationMethods")
            if not isinstance(name, str):
                continue
            if not (isinstance(supported, list) and "generateContent" in supported):
                continue
            # API returns names like "models/gemini-1.5-flash"; normalize.
            out.append(name.replace("models/", "", 1))
        return out

    @staticmethod
    def _generate_content(*, api_key: str, model: str, payload: dict) -> dict:
        url = f"{GeminiService.BASE_URL}/models/{model}:generateContent"
        resp = requests.post(url, headers=GeminiService._headers(api_key), json=payload, timeout=20)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    @staticmethod
    def recommend_workout(
        *,
        categories: list[dict],
        recent_workouts: list[dict],
        plan_days: list[dict],
        api_keys: list[dict] | None = None,
    ) -> dict:
        env_key = getattr(Config, "GEMINI_API_KEY", None)
        keys_to_try: list[dict] = []
        if api_keys:
            keys_to_try.extend([k for k in api_keys if k.get("api_key")])
        if env_key:
            keys_to_try.append({"id": None, "api_key": env_key, "source": "env"})
        if not keys_to_try:
            raise GeminiServiceError("GEMINI_API_KEY is not configured")

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "You are a strength training coach inside a workout logging app. "
                                "Given a user's workout plan categories/days and their recent workouts, "
                                "recommend which plan day they should do today.\n\n"
                                "Return ONLY valid JSON with this exact schema:\n"
                                "{\"category\": string, \"day_id\": integer, \"reasons\": [string, ...]}\n\n"
                                "Constraints:\n"
                                "- category must be one of the provided categories.\n"
                                "- day_id must be within 1..num_days for that category.\n"
                                "- Prefer the least-recently-trained day across the whole plan.\n"
                                "- Use recent workouts (title, exercises, details) to match plan days.\n"
                                "- reasons must be 3-6 short bullet-style strings.\n\n"
                                f"Plan categories: {json.dumps(categories, ensure_ascii=False)}\n"
                                f"Plan days (name, category, day_id, exercises): {json.dumps(plan_days, ensure_ascii=False)}\n"
                                f"Recent workouts (most recent first): {json.dumps(recent_workouts, ensure_ascii=False)}\n"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 256,
            },
        }

        last_error: Exception | None = None

        for key_entry in keys_to_try:
            api_key = key_entry.get("api_key")
            if not api_key:
                continue

            key_label = key_entry.get("account_label") or key_entry.get("source") or f"key_id={key_entry.get('id')}"
            cache_key = (
                str(key_entry.get("id"))
                if key_entry.get("id")
                else key_entry.get("account_label")
                or key_entry.get("source")
                or f"suffix_{api_key[-6:]}"
            )

            now_ts = time.time()
            cooldown_until = GeminiService._key_cooldown_until.get(cache_key)
            if cooldown_until and cooldown_until > now_ts:
                continue

            models_to_try: list[str] = []
            try:
                available = None
                cached_models_entry = GeminiService._available_models_cache.get(cache_key)
                now_models_ts = time.time()
                if isinstance(cached_models_entry, dict):
                    cached_ts = cached_models_entry.get("ts")
                    cached_models = cached_models_entry.get("models")
                    if isinstance(cached_ts, (int, float)) and isinstance(cached_models, list):
                        if now_models_ts - float(cached_ts) < float(GeminiService._available_models_ttl_s):
                            available = cached_models

                if available is None:
                    available = GeminiService._list_models(api_key)
                    GeminiService._available_models_cache[cache_key] = {"ts": now_models_ts, "models": available}
                if available:
                    preferred = [m for m in GeminiService.DEFAULT_MODEL_CANDIDATES if m in available]
                    remaining = [m for m in available if m not in preferred]
                    models_to_try = preferred + remaining
                else:
                    logger.warning("Gemini listModels returned no available models", extra={"key": key_label})
            except requests.RequestException as e:
                last_error = e
                logger.warning(
                    "Gemini listModels failed",
                    extra={"error": type(e).__name__, "key": key_label},
                )
                models_to_try = list(GeminiService.DEFAULT_MODEL_CANDIDATES)

            if not models_to_try:
                models_to_try = list(GeminiService.DEFAULT_MODEL_CANDIDATES)
            # Try preferred models first, then any available models for the key.
            cached_model = GeminiService._model_cache.get(cache_key)
            if cached_model:
                if cached_model in models_to_try:
                    models_to_try.remove(cached_model)
                models_to_try.insert(0, cached_model)
            for model in models_to_try:
                bad_models = GeminiService._key_bad_models.get(cache_key)
                if bad_models and model in bad_models:
                    continue
                try:
                    data = GeminiService._generate_content(api_key=api_key, model=model, payload=payload)
                    candidate0 = None
                    try:
                        candidates = data.get("candidates") if isinstance(data, dict) else None
                        if isinstance(candidates, list) and candidates:
                            candidate0 = candidates[0] if isinstance(candidates[0], dict) else None
                    except Exception:
                        candidate0 = None

                    text = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    if not text:
                        finish_reason = None
                        try:
                            finish_reason = candidate0.get("finishReason") if candidate0 else None
                        except Exception:
                            finish_reason = None

                        block_reason = None
                        try:
                            prompt_feedback = data.get("promptFeedback") if isinstance(data, dict) else None
                            if isinstance(prompt_feedback, dict):
                                block_reason = prompt_feedback.get("blockReason")
                        except Exception:
                            block_reason = None

                        raise GeminiServiceError(
                            f"Empty response from Gemini (finish_reason={finish_reason}, block_reason={block_reason})"
                        )

                    start = text.find('{')
                    end = text.rfind('}')
                    if start == -1 or end == -1 or end <= start:
                        excerpt = text.strip().replace("\n", " ")
                        if len(excerpt) > 240:
                            excerpt = excerpt[:240] + "…"
                        raise GeminiServiceError(f"Gemini response was not valid JSON (excerpt={excerpt})")

                    obj = json.loads(text[start : end + 1])
                    if not isinstance(obj, dict):
                        raise GeminiServiceError("Gemini response JSON was not an object")

                    if not isinstance(obj.get("category"), str) or not obj["category"].strip():
                        raise GeminiServiceError("Missing category")
                    if not isinstance(obj.get("day_id"), int):
                        raise GeminiServiceError("Missing day_id")
                    if not isinstance(obj.get("reasons"), list) or not all(isinstance(x, str) for x in obj["reasons"]):
                        raise GeminiServiceError("Missing reasons")

                    obj["category"] = obj["category"].strip()
                    obj["reasons"] = [r.strip() for r in obj["reasons"] if r and r.strip()]

                    GeminiService._model_cache[cache_key] = model

                    return {
                        "category": obj["category"],
                        "day_id": int(obj["day_id"]),
                        "reasons": obj["reasons"],
                        "model": model,
                        "generated_at": datetime.utcnow().isoformat() + "Z",
                        "key_id": key_entry.get("id"),
                    }
                except requests.HTTPError as e:
                    last_error = e
                    status = getattr(e.response, "status_code", None)
                    body = ""
                    try:
                        body = (e.response.text or "") if getattr(e, "response", None) is not None else ""
                    except Exception:
                        body = ""
                    if body and len(body) > 800:
                        body = body[:800] + "…"

                    logger.warning(
                        f"Gemini model call failed (key={key_label}, model={model}, status={status}, body={body})"
                    )
                    # 404 means model not available for this API key / region. Try next model.
                    if status == 404:
                        if GeminiService._model_cache.get(cache_key) == model:
                            GeminiService._model_cache.pop(cache_key, None)
                        continue
                    # Auth or quota: try next key.
                    if status in {401, 403, 429}:
                        if status == 429:
                            retry_after_s = None
                            try:
                                retry_after_header = None
                                if getattr(e, "response", None) is not None:
                                    retry_after_header = e.response.headers.get("retry-after")
                                if retry_after_header:
                                    retry_after_s = float(retry_after_header)
                            except Exception:
                                retry_after_s = None

                            if retry_after_s is None:
                                try:
                                    m = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", body, flags=re.IGNORECASE)
                                    if m:
                                        retry_after_s = float(m.group(1))
                                except Exception:
                                    retry_after_s = None

                            # If quota is literally 0 for this model, don't try it again for this key.
                            if body and "limit: 0" in body:
                                GeminiService._key_bad_models.setdefault(cache_key, set()).add(model)

                            if retry_after_s and retry_after_s > 0:
                                # Cool down slightly longer than the suggested backoff.
                                GeminiService._key_cooldown_until[cache_key] = time.time() + float(retry_after_s) + 0.5
                        break
                    raise GeminiServiceError(f"Gemini request failed: HTTP {status}")
                except (requests.RequestException, GeminiServiceError, ValueError, json.JSONDecodeError) as e:
                    last_error = e
                    msg = str(e) if e else ""
                    msg = msg.replace("\n", " ")
                    if msg and len(msg) > 400:
                        msg = msg[:400] + "…"
                    logger.warning(
                        f"Gemini request error (key={key_label}, model={model}, error={type(e).__name__}, message={msg})"
                    )
                    continue

        if last_error:
            raise GeminiServiceError(f"Gemini request failed: {type(last_error).__name__}")

        raise GeminiServiceError("Gemini request failed")

