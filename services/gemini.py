import json
from datetime import datetime

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

            models_to_try: list[str] = list(GeminiService.DEFAULT_MODEL_CANDIDATES)
            try:
                available = GeminiService._list_models(api_key)
                if not available:
                    logger.warning("Gemini listModels returned no available models", extra={"key": key_label})
                for model in available:
                    if model not in models_to_try:
                        models_to_try.append(model)
            except requests.RequestException as e:
                last_error = e
                logger.warning(
                    "Gemini listModels failed",
                    extra={"error": type(e).__name__, "key": key_label},
                )

            # Try preferred models first, then any available models for the key.
            cached_model = GeminiService._model_cache.get(cache_key)
            if cached_model:
                if cached_model in models_to_try:
                    models_to_try.remove(cached_model)
                models_to_try.insert(0, cached_model)
            for model in models_to_try:
                try:
                    data = GeminiService._generate_content(api_key=api_key, model=model, payload=payload)
                    text = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    if not text:
                        raise GeminiServiceError("Empty response from Gemini")

                    start = text.find('{')
                    end = text.rfind('}')
                    if start == -1 or end == -1 or end <= start:
                        raise GeminiServiceError("Gemini response was not valid JSON")

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
                    logger.warning(
                        "Gemini model call failed",
                        extra={"status": status, "model": model, "key": key_label},
                    )
                    # 404 means model not available for this API key / region. Try next model.
                    if status == 404:
                        if GeminiService._model_cache.get(cache_key) == model:
                            GeminiService._model_cache.pop(cache_key, None)
                        continue
                    # Auth or quota: try next key.
                    if status in {401, 403, 429}:
                        break
                    raise GeminiServiceError(f"Gemini request failed: HTTP {status}")
                except (requests.RequestException, GeminiServiceError, ValueError, json.JSONDecodeError) as e:
                    last_error = e
                    logger.warning(
                        "Gemini request error",
                        extra={"error": type(e).__name__, "model": model, "key": key_label},
                    )
                    continue

        if last_error:
            raise GeminiServiceError(f"Gemini request failed: {type(last_error).__name__}")

        raise GeminiServiceError("Gemini request failed")

