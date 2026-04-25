Создай коммит с правильным conventional commit message.

## Действия

1. Проверь состояние:
   - `git status` — что изменено
   - `git diff --cached` — что в staging
   - `git diff` — что не в staging
   - Если ничего не в staging — добавь изменённые файлы (НЕ добавляй .env, credentials, секреты)

2. Проанализируй изменения и сгенерируй commit message:
   - Формат: `type: краткое описание` (одна строка, до 72 символов)
   - Типы: `feat`, `fix`, `refactor`, `test`, `chore`, `docs`
   - Описание отвечает на "что и зачем", а не "как"
   - Если изменений много — добавь body через пустую строку

3. Создай коммит с Co-Authored-By:
   ```
   git commit -m "$(cat <<'EOF'
   type: описание

   Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
   EOF
   )"
   ```