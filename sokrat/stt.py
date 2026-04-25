"""SaluteSpeech (Sber) speech-to-text integration.

Тонкая обёртка над синхронным REST-эндпоинтом SmartSpeech:
1. OAuth (Basic auth ключом из ЛК) → access_token, кешируем до истечения.
2. POST /rest/v1/speech:recognize с сырым PCM 16-bit LE моно.

Лимиты синхронного API: до 1 минуты, до 2 МБ. На 16 kHz это ~1 минута PCM.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Optional

import requests

log = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
RECOGNIZE_URL = "https://smartspeech.sber.ru/rest/v1/speech:recognize"

# Верхняя граница синхронного API SaluteSpeech — 2 МБ payload'а.
_MAX_AUDIO_BYTES = 2 * 1024 * 1024


class SaluteSpeechError(RuntimeError):
    pass


@dataclass
class _Token:
    value: str
    expires_at: float  # epoch seconds


class SaluteSpeechClient:
    def __init__(
        self,
        auth_key: str,
        scope: str = "SALUTE_SPEECH_PERS",
        verify_ssl: bool | str = True,
        timeout: float = 30.0,
    ) -> None:
        if not auth_key:
            raise SaluteSpeechError("SBER_SALUTE_AUTH_KEY is empty")
        self._auth_key = auth_key
        self._scope = scope
        self._verify = verify_ssl
        self._timeout = timeout
        self._token: Optional[_Token] = None
        self._lock = Lock()

    def _fetch_token(self) -> str:
        headers = {
            "Authorization": f"Basic {self._auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        try:
            resp = requests.post(
                OAUTH_URL,
                headers=headers,
                data={"scope": self._scope},
                verify=self._verify,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise SaluteSpeechError(f"OAuth request failed: {exc}") from exc

        if resp.status_code != 200:
            raise SaluteSpeechError(
                f"OAuth failed: HTTP {resp.status_code} {resp.text[:200]}"
            )

        payload = resp.json()
        access_token = payload.get("access_token")
        # API возвращает expires_at в миллисекундах (Unix epoch).
        expires_at_ms = payload.get("expires_at") or 0
        if not access_token:
            raise SaluteSpeechError(
                f"OAuth response missing access_token: {payload!r}"
            )
        self._token = _Token(value=access_token, expires_at=expires_at_ms / 1000.0)
        return access_token

    def _get_token(self) -> str:
        with self._lock:
            now = time.time()
            # Обновляем заранее, чтобы не словить 401 по дороге.
            if self._token and self._token.expires_at - 60 > now:
                return self._token.value
            return self._fetch_token()

    def _invalidate_token(self) -> None:
        with self._lock:
            self._token = None

    def recognize_pcm16(
        self,
        audio: bytes,
        sample_rate: int = 16000,
        language: str = "ru-RU",
    ) -> str:
        if not audio:
            return ""
        if len(audio) > _MAX_AUDIO_BYTES:
            raise SaluteSpeechError(
                f"Audio is too large for sync API: {len(audio)} > {_MAX_AUDIO_BYTES} bytes"
            )

        def _do_request(token: str) -> requests.Response:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": f"audio/x-pcm;bit=16;rate={sample_rate}",
                "Accept": "application/json",
            }
            try:
                return requests.post(
                    RECOGNIZE_URL,
                    headers=headers,
                    params={"language": language},
                    data=audio,
                    verify=self._verify,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise SaluteSpeechError(f"Recognize request failed: {exc}") from exc

        resp = _do_request(self._get_token())
        if resp.status_code == 401:
            # Токен мог истечь между проверкой и запросом — обновим и попробуем ещё раз.
            self._invalidate_token()
            resp = _do_request(self._get_token())

        if resp.status_code != 200:
            raise SaluteSpeechError(
                f"Recognize failed: HTTP {resp.status_code} {resp.text[:200]}"
            )

        data = resp.json()
        result = data.get("result") or []
        if not result:
            return ""
        return str(result[0]).strip()


_client: Optional[SaluteSpeechClient] = None
_client_lock = Lock()


def _resolve_verify() -> bool | str:
    ca_bundle = os.getenv("SBER_SALUTE_CA_BUNDLE", "").strip()
    if ca_bundle:
        return ca_bundle
    flag = os.getenv("SBER_SALUTE_VERIFY_SSL", "true").strip().lower()
    if flag in ("false", "0", "no", "off"):
        log.warning(
            "SaluteSpeech SSL verification disabled — use only for local dev."
        )
        return False
    return True


def get_stt_client() -> Optional[SaluteSpeechClient]:
    """Кешированный клиент, либо None, если ключ не сконфигурирован."""
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        auth_key = os.getenv("SBER_SALUTE_AUTH_KEY", "").strip()
        if not auth_key:
            return None
        scope = (
            os.getenv("SBER_SALUTE_SCOPE", "SALUTE_SPEECH_PERS").strip()
            or "SALUTE_SPEECH_PERS"
        )
        _client = SaluteSpeechClient(
            auth_key=auth_key, scope=scope, verify_ssl=_resolve_verify()
        )
        return _client
