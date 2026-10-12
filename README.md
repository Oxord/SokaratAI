# SokaratAI
ИИ-тьютор для подготовки к собесам.


## Вход по логину и паролю (опционально)

Чтобы история, динамика и повторение слабых тем считались по каждому пользователю,
включите вход в `.env`:

```
CHAINLIT_AUTH_SECRET=<длинная случайная строка, например `chainlit create-secret`>
ALLOW_SIGNUP=true   # false — запретить саморегистрацию (вход только для существующих)
```

Неизвестный логин при первом входе регистрируется. Пароли хранятся как PBKDF2-хеши
в `data/users.json` (в `.gitignore`). Без `CHAINLIT_AUTH_SECRET` приложение работает
анонимно, история общая. Это решение для пилота; для продакшена нужен OAuth/SSO.

## Озвучка вопросов и аналитика

- **Озвучка вопросов (TTS):** `TTS_ENABLED=true` + `SBER_SALUTE_AUTH_KEY` в `.env` — каждый
  вопрос дополнительно приходит аудио (SaluteSpeech). Без ключа функция выключена.
- **Сводная аналитика** для вузов/HR: `python -m sokrat.analytics [--role "Python Developer"]`
  сохраняет `data/analytics.md` (распределение результатов, типичные пробелы, без ответов
  пользователей).


## LLM-провайдер

По умолчанию используется **DeepSeek**: задайте `DEEPSEEK_API_KEY` в `.env`
(`LLM_PROVIDER=deepseek`). Запасной вариант — OpenRouter (`LLM_PROVIDER=openrouter`
и `OPENROUTER_API_KEY`). Проверить связь можно командой:

```
python -m sokrat.llm_check
```

## hh.ru

Анонимный доступ к `api.hh.ru` закрыт (HTTP 403). Для разбора вакансий по ссылке и
профиля работодателя зарегистрируйте приложение на https://dev.hh.ru и задайте
`HH_API_TOKEN` в `.env`. Без токена работают вставленный текст вакансии и страницы со
структурированной разметкой (Хабр Карьера и др.), а профиль компании строится по тексту вакансии.

## Веб-интерфейс (основной)

Собственный интерфейс Сократа: лендинг, мастер подготовки (вакансия → резюме → настройки),
интервью, отчёт с планом и практикой, личный кабинет.

```
./run-web.sh            # http://127.0.0.1:8000
```

Бэкенд — FastAPI (`web/server.py`) поверх движка `sokrat/engine.py`; фронтенд —
статические файлы в `web/static/` без сборки. Chainlit-версия (`app.py`) сохранена как
запасной интерфейс: `chainlit run app.py`.

Секрет для подписи cookie берётся из `SOKRAT_SECRET` (иначе генерируется в
`data/.secret`). За HTTPS-прокси задайте `SOKRAT_COOKIE_SECURE=true`.

## Деплой

GitHub Pages не подходит (нужен Python-сервер). Подойдёт любой хостинг с Docker:

- **Render** (free): New → Blueprint → выбрать репозиторий; `render.yaml` уже настроен.
  Секрет `DEEPSEEK_API_KEY` вводится в панели Render. Бесплатный сервис «засыпает» после
  простоя (первый запрос ~30–60 сек), файловая система временная: пользователи и история
  теряются при перезапуске — для постоянного хранения нужен платный диск.
- **Hugging Face Spaces** (Docker SDK), **Fly.io**, **Railway** — тот же `Dockerfile`.
- **Быстрый показ с ноутбука:** `./run-web.sh` и `cloudflared tunnel --url http://localhost:8000`
  (или ngrok) — временная публичная HTTPS-ссылка.

Локальная проверка контейнера: `docker build -t sokrat . && docker run -p 8000:8000
-e DEEPSEEK_API_KEY=... sokrat`.

### Обновление на своём сервере (Docker)

```
git archive HEAD | ssh user@server 'rm -rf /opt/sokrat/src && mkdir -p /opt/sokrat/src && tar -x -C /opt/sokrat/src'
ssh user@server 'cd /opt/sokrat/src && docker build -t sokrat:latest . \
  && docker rm -f sokrat && docker run -d --name sokrat --restart unless-stopped \
     -p 8000:8000 --env-file /opt/sokrat/.env -v /opt/sokrat/data:/data sokrat:latest'
```

`/opt/sokrat/.env` содержит `DEEPSEEK_API_KEY`, `SESSION_DIR=/data/sessions`,
`USERS_PATH=/data/users.json`, `SOKRAT_SECRET_PATH=/data/.secret`. Без HTTPS (доступ по IP)
нужен `SOKRAT_COOKIE_SECURE=false`; за доменом с HTTPS — `true`.
