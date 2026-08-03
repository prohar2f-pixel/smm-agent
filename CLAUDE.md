# SMM Agent — CLAUDE.md

## 1. О проекте
AI-агент для автоматического ведения соцсетей фрилансера-разработчика (Удача). Учится писать в стиле владельца через онбординг, мониторит конкурентов и RSS, генерирует 5 постов в неделю, публикует в Telegram-канал и ВКонтакте после одобрения кнопкой.  
Полная спецификация: `../spec.md`. Идея: `../idea.md`. Исследование: `../research.md`.

---

## 2. Структура проекта
```
smm-agent/
├── main.py                 # Точка входа, запуск бота + планировщика
├── .env                    # Токены и конфиг (не в git)
├── requirements.txt
├── CLAUDE.md               # Этот файл
├── data/
│   └── agent.db            # SQLite база (создаётся автоматически)
├── bot/
│   ├── handlers.py         # aiogram: все команды и кнопки
│   ├── keyboards.py        # Inline-кнопки согласования
│   └── middlewares.py      # Проверка ADMIN_TELEGRAM_ID
├── agent/
│   ├── style_engine.py     # Онбординг 7 вопросов + brand_voice_prompt
│   ├── content_planner.py  # Генерация плана: сигналы → 5 тем
│   ├── post_generator.py   # Claude: TG-версия + VK-версия поста
│   └── memory.py           # SQLite CRUD (посты, темы, сигналы, настройки)
├── monitoring/
│   ├── rss_fetcher.py      # feedparser → competitor_signals
│   ├── telegram_monitor.py # Telethon → читать каналы конкурентов
│   └── vk_monitor.py       # VK API → читать страницы конкурентов
├── publishing/
│   ├── telegram_pub.py     # Bot API sendMessage → канал
│   └── vk_pub.py           # VK API wall.post → группа
└── scheduler.py            # APScheduler: ежедневный мониторинг + план по пн
```

---

## 3. Tech Stack

| Компонент | Библиотека | Версия |
|---|---|---|
| Язык | Python | 3.11+ |
| Telegram Bot | aiogram | 3.x |
| AI | openai (OpenRouter) | latest |
| LLM | Claude Opus 4.8 | `anthropic/claude-opus-4-8` |
| База данных | SQLite (stdlib) | — |
| Планировщик | APScheduler | 3.x |
| TG-мониторинг | Telethon | latest |
| VK API | vk_api | latest |
| RSS | feedparser | latest |
| Деплой | Windows Server | NSSM-сервис |

---

## 4. Архитектура

```
Пользователь (Telegram)
        ↓ /команды, кнопки
[aiogram handlers] → проверка ADMIN_TELEGRAM_ID
        ↓
[Core Agent]
  StyleEngine      → brand_voice_prompt (SQLite: brand_voice)
  ContentPlanner   → competitor_signals + topics_used → 5 тем
  PostGenerator    → OpenRouter/Claude → content_telegram + content_vk
  ApprovalFlow     → inline-кнопки → статус поста
        ↓
[Publisher]
  TelegramPublisher → Telegram Bot API → канал
  VKPublisher       → VK API wall.post → группа

[APScheduler]
  08:00 ежедневно → RSS + Telethon + VK мониторинг
  09:00 понедельник → генерация плана на неделю
  По расписанию → публикация approved постов

[SQLite: agent.db]
  brand_voice | posts | topics_used | competitor_signals | settings
```

---

## 5. Ключевые решения

| Решение | Почему |
|---|---|
| Python, не Node.js | Лучшая экосистема для AI (LLM-клиенты, feedparser, Telethon) |
| SQLite, не PostgreSQL | Один пользователь — один файл, ноль настройки |
| OpenRouter → Claude Opus 4.8 | Ключ уже есть; лучшее качество русского текста |
| Согласование по одному посту | Быстрее одобрить 1 кнопкой, чем читать 5 сразу |
| Онбординг вместо примеров | Постов нет — стиль учим через 7 вопросов при /start |

---

## 6. Тестирование

**Где тестировать:** локально через `python main.py` с тестовым ботом (@тест-бот из BotFather).

**Перед каждым деплоем проверить:**
- [ ] `/start` → онбординг проходит все 7 шагов
- [ ] `/plan` → план генерируется без дублей из topics_used
- [ ] Кнопка `✅ Опубликовать` → пост появляется в канале И на VK-стене
- [ ] Кнопка `✏️ Переделать` → бот спрашивает правку, перегенерирует
- [ ] Кнопка `📅 Перенести` → пост уходит в scheduled, не публикуется сейчас
- [ ] Таймаут 48ч → статус меняется на `postponed`
- [ ] `/voice` → профиль стиля отображается корректно
- [ ] Мониторинг: `competitor_signals` пополняется ежедневно
- [ ] VK-токен: `wall.post` не возвращает ошибку авторизации

---

## 7. Документация

| Файл | Содержит | Когда обновлять |
|---|---|---|
| `CLAUDE.md` | Ориентир по проекту для AI | При изменении архитектуры или стека |
| `docs/content-strategy/README.md` | Точка входа в систему продюсирования AiProhar | При изменении статуса направления или правил работы |
| `docs/content-strategy/competitors.md` | Карта конкурентов и источников | После существенного исследования рынка |
| `docs/content-strategy/aiprohar-playbook.md` | Позиционирование, серии, производство и метрики | После подтверждённых результатов собственным контентом |
| `docs/content-strategy/idea-backlog.md` | Банк мастер-тем без дат и обязательств | При появлении реального проекта, вопроса или сигнала спроса |
| `../spec.md` | Полная спецификация | При добавлении новых функций |
| `.env.example` | Шаблон переменных без значений | При добавлении новой env-переменной |
| `data/agent.db` | Вся память агента | Обновляется автоматически |
| `agent/style_engine.py` | Логика онбординга и brand_voice | При изменении вопросов онбординга |

---

## 8. Commands

```bash
# Установка зависимостей
pip install -r requirements.txt

# Первый запуск (создаёт agent.db, запускает онбординг)
python main.py

# Деплой на Windows Server как сервис через NSSM
nssm install smm-agent "C:\Python311\python.exe" "C:\Projects\smm-agent\main.py"
nssm start smm-agent

# Проверить логи
nssm status smm-agent
type logs\agent.log | tail -50

# Перезапустить после изменений
nssm restart smm-agent
```

---

## 9. Самообновление

Агент обновляет свои файлы-состояния автоматически:

| Файл/таблица | Кто обновляет | Когда |
|---|---|---|
| `brand_voice` (SQLite) | `style_engine.py` | После онбординга + после /voice + после оценки поста |
| `posts` (SQLite) | `post_generator.py`, `publishing/` | При генерации, одобрении, публикации |
| `topics_used` (SQLite) | `content_planner.py` | После одобрения плана |
| `competitor_signals` (SQLite) | `monitoring/` | Ежедневно в 08:00 |
| `settings` (SQLite) | `bot/handlers.py` | При изменении через /settings, /competitors |

**Механизм:** APScheduler запускает `scheduler.py` → вызывает модули мониторинга → записывают в SQLite → ContentPlanner читает перед генерацией плана → PostGenerator читает brand_voice перед каждым постом.

**В новой сессии Claude:** прочитай `CLAUDE.md` (этот файл) → `../spec.md` → `agent/memory.py` (схема БД) → `agent/style_engine.py` (онбординг). Этого достаточно чтобы понять весь проект.

---

## 10. Секреты

- Настоящие ключи/токены/пароли — только в `.env`, и `.env` обязательно в `.gitignore`.
- В `.env.example` — только заглушки (`YOUR_TOKEN_HERE`), никогда реальные значения.
- Ключ, попавший хоть раз в коммит, считается скомпрометированным — меняй его, а не просто удаляй строку.

---

## 11. Контент-стратегия AiProhar

При любом запросе о продюсировании, социальных сетях, органическом трафике, контент-стратегии, конкурентах, рубриках, постах, видео, Stories, Reels, Shorts, VK Клипах, лид-магнитах или переупаковке контента **сначала прочитай `docs/content-strategy/README.md` и указанный там профильный документ**.

Текущий статус направления — **активен первый текстовый пилот NERÉIDE**. Разрешено готовить материалы только в рамках утверждённой спецификации `docs/content-strategy/nereide-pilot.md`. Не создавай календарь, не публикуй контент и не запускай автоматический постинг без отдельной прямой команды владельца. Остальные подтверждённые идеи и исследования можно сохранять в документацию без назначения дат и обязательной частоты.
