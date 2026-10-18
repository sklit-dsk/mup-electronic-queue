# Архитектура первой версии

## Решение

Модульный монолит: FastAPI + PostgreSQL. React обращается к API. Отдельный worker из той же кодовой базы обрабатывает OCR, outbox, сроки предложений и удаление файлов. Не нужны отдельный брокер и Redis для корректности очереди; при 100 посетителях в день важнее транзакции и понятный код.

| Часть | Выбор |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, psycopg, Alembic |
| База | Отдельный PostgreSQL 16 для проекта; не использовать чужую homelab-БД |
| Frontend | React + TypeScript + Vite; npm и package-lock.json |
| Python зависимости | pyproject.toml + uv.lock; команды через uv |
| Тесты | pytest/HTTPX, PostgreSQL integration, Vitest, Playwright |
| Файлы | Закрытый локальный Docker volume; адаптер FileStore |
| Очередь UI | Polling 5 секунд с revision; SSE можно добавить позднее |
| Среда | Docker Compose; локальные адреса привязаны к 127.0.0.1 |

Это инженерный выбор для проекта, не требование MUP. Точные версии pin/lock создаются в T00. Node.js — ветка 22, не ниже 22.12, либо совместимая поддерживаемая версия, проверенная по документации Vite. Не использовать тег latest в готовых deployment-файлах.

## План расположения кода

- `backend/app/api/`: HTTP, схемы ответов, авторизация.
- `backend/app/domain/`: перечисления, переходы, правила.
- `backend/app/services/`: booking, transfer, queue, waitlist, documents.
- `backend/app/db/` и `backend/alembic/`: модели, репозитории, миграции.
- `backend/app/adapters/`: SMS, OCR, FileStore, Clock.
- `backend/app/worker/`: задания и outbox.
- `backend/tests/`: unit/integration, независимые соединения для гонок.
- `frontend/src/`: features/auth, documents, booking, ticket, staff, admin, display.
- `frontend/e2e/`: Playwright.
- `infra/`: Compose/proxy, только когда соответствующая задача их создаст.

В backend/frontend уже есть код. Фактическое покрытие отражено в IMPLEMENTATION.md.

## Границы

Backend — единственный источник бизнес-правил. Номер, остаток мест и доступность операции не вычисляются доверенно в браузере. Фото не проходит через публичный frontend static server. Worker не обращается к внешнему OCR/SMS, пока адаптер не подключён явно.

После транзакции уведомления отправляются через outbox. Ошибка SMS не откатывает уже выданный талон. Задания имеют lease, retries, dedupe и безопасное повторное выполнение. Блокировки БД не держатся во время сетевого вызова или OCR.

Polling получает минимальные DTO. Никаких websocket-инфраструктуры, CDN для паспортов, n8n и Kubernetes в первой версии. Интерфейс провайдеров нужен только там, где действительно меняется внешняя зависимость.
