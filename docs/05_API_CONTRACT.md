# API — проект контракта v1

> Ниже целевой контракт. Фактическая схема v0.1 доступна через /api/v1/schema, маршруты — backend/app/main.py. Отличия отражены в IMPLEMENTATION.md.

Префикс `/api/v1`. OpenAPI генерируется из реализации; эта таблица описывает требуемый смысл. Имена можно уточнить в рамках задачи, синхронизировав документацию и frontend.

| Метод и путь | Доступ | Результат |
| --- | --- | --- |
| POST /auth/otp/request | Гость, rate limit | challenge_id и masked contact; без кода |
| POST /auth/otp/verify | Гость | HttpOnly session cookie; OTP погашен |
| GET /me; POST /auth/logout | Пользователь | Свой профиль; закрытие сессии |
| POST /documents/uploads | Пользователь | upload_id; проверка типа/размера/декодирования |
| POST /documents/checks | Владелец upload | Асинхронный check_id; идемпотентная постановка задания |
| GET /documents/checks/{id} | Владелец/уполномоченный сотрудник | Статус, извлечённые поля, issues; без чужого файла |
| POST /documents/checks/{id}/confirm | Владелец | Сверка исправлений, документ/claim request или конфликт; не передаёт чужое владение |
| POST /document-requests | Applicant | RECOVER/REPLACE по собственной PASSED-загрузке; идемпотентно, без автоматической передачи доступа |
| GET /document-requests/mine; POST /document-requests/{id}/cancel | Applicant | Только безопасный статус своих запросов; отмена с version |
| GET /documents/{id}/history | Текущий владелец | Ревизии своего паспорта; чужому 404 |
| GET /admin/document-requests | Admin по office scope | Ожидающие запросы, реквизиты и фото для очной проверки |
| POST /admin/document-requests/{id}/{approve,reject} | Admin по office scope | Version, причина и три подтверждения личности для approve |
| GET /offices; GET /services | Публично | Справочники без персональных данных |
| GET /offices/{id}/days | Публично | Даты, статус, приблизительный остаток; подтверждение всё равно транзакционно |
| POST /tickets | Свой документ | BOOKED ticket; Idempotency-Key обязателен |
| GET /tickets/mine; GET /tickets/{id} | Владелец/сотрудник по scope | Свои талоны, разрешённые действия и revision |
| POST /tickets/{id}/cancel | Владелец/сотрудник | Отмена, освобождение места по DOMAIN |
| POST /tickets/{id}/transfer | Владелец/сотрудник | Новый талон, старый TRANSFERRED; один commit |
| POST /waitlist | Свой документ | WAITING без номера талона |
| GET /waitlist/mine | Владелец | Своё ожидание и предложения |
| POST /waitlist/{id}/cancel | Владелец | Отказ от ожидания; активный HELD освобождается |
| POST /waitlist/offers/{id}/accept; /decline | Владелец | Принятие/отказ; TTL проверяет сервер |
| GET /queues/{office}/{date}/{service} | Публично | Только номера окон/талонов и revision; никаких ФИО |
| POST /staff/windows/{id}/call-next | Сотрудник окна | Следующий или пустая очередь; одно назначение |
| POST /staff/tickets/{id}/recall; /skip; /no-show | Сотрудник по scope | Разрешённый переход и аудит |
| POST /staff/tickets/{id}/verify-original | Сотрудник по scope | Результат очной сверки, привязанный к талону |
| POST /staff/tickets/{id}/start; /complete; /reject | Сотрудник по scope | Доменный переход с ожидаемой версией |
| POST /staff/tickets/{id}/release-unused-seat | Уполномоченный supervisor | Только NO_SHOW/REJECTED, причина обязательна |
| POST /staff/tickets/{id}/repeat-episode | Supervisor | Новый эпизод в существующем талоне, без нового места |
| POST /staff/document-reviews/{id}/resolve | Supervisor | Ручной разбор OCR, владельца/документа; причина обязательна |
| GET/POST /admin/calendar; /admin/windows | Администратор | Календарь, OPEN/CLOSED, предел 100 и окна |
| GET /admin/audit | Администратор по scope | Аудит без сырого OCR и паспортных реквизитов |

## Поля создания талона

### Фактические дополнения этапов 1 и 2а

Исходные пути выше — целевой набросок. Действующие пути этого этапа, от `/api/v1`:

| Путь | Контракт |
| --- | --- |
| GET /staff/days | Staff/admin своего отделения; будущие даты и прошлые дни с активным остатком |
| GET /staff/days/{day_id}/summary | Сводка: service_status, server_now, hours_enforced, recall_interval_seconds, counts, unresolved, in_service и вместимость |
| POST /admin/days/{day_id} | Admin: closed, reason (3–300 символов), CSRF и Idempotency-Key; причина журналируется |
| POST /tickets/{id}/transfer | day_id, reason (обязателен сотруднику); разрешены BOOKED/CALLED/SKIPPED сотруднику, только BOOKED заявителю; владелец сохраняется |
| POST /staff/tickets/{id}/recall | version; после паузы; в текущем цикле не более двух вызовов |
| POST /staff/tickets/{id}/skip | version; два вызова и истёкшая пауза после второго обязательны |
| POST /staff/tickets/{id}/episodes | Только admin: version родительского талона, window (1–3), reason; создать повторный CALLED без нового талона/места |
| GET /staff/tickets/{id}/episodes | Staff/admin своего отделения; первичный Ticket и повторные эпизоды с причинами/автором/сверкой |
| POST /staff/episodes/{id}/{action} | Staff/admin: version эпизода, reason для ручных исключений, window для return; действия verify/start/complete/recall/skip/return/no-show/reject/cancel |
| POST /tickets/{id}/cancel | Для сотрудника обязательны reason и version; обычный заявитель может отменить BOOKED без тела запроса |

В 2а приватный Ticket содержит started_at, completed_at и episodes с безопасными статусами/датами/окном/версией; внутренние причины и авторы доступны в staff history. Временные поля Ticket/Episode нормализованы к UTC. Повтор отражается в публичном calling тем же номером с repeat=true, без причины/ID/паспортных реквизитов. Счётчик completed считает первичные талоны один раз. Сводка дня дополнена repeat_unresolved, repeat_in_service, repeat_completed. Открытие/изменение эпизодов имеет CSRF, Idempotency-Key, контроль версии и области доступа. Новые коды: REPEAT_NOT_ALLOWED, REPEAT_ALREADY_ACTIVE, REPEAT_SAME_DAY_REQUIRED, WINDOW_REQUIRED.

В 2б Document DTO содержит version/revision, Ticket — document_revision. Staff document response добавляет booked_fields и changed_since_booking. Решение запроса требует reason; approve дополнительно требует original_checked, account_checked и same_person_checked. Новые безопасные коды: DOCUMENT_ACCESS_REVIEW_REQUIRED, DOCUMENT_SUPERSEDED, DOCUMENT_CHANGED, DOCUMENT_REPLACEMENT_CONFLICT, IDENTITY_CHECK_REQUIRED, DOCUMENT_IN_SERVICE, DELIVERY_IN_PROGRESS. Ни один ответ requester не раскрывает прежний аккаунт или внутреннюю причину решения.

Приватный Ticket DTO дополнен call_attempts, last_called_at, call_wait_seconds. Публичный /queues/{day_id} содержит service_status, без реквизитов документов. Статусы дня: OPEN / DEMO_OPEN / BEFORE_OPEN / FINISHED / CLOSED. Дополнительные 409-коды: SERVICE_CLOSED, REPEAT_CALL_REQUIRED, CALL_WAIT_REQUIRED, CALL_LIMIT_REACHED, REASON_REQUIRED.

Остальная часть этого раздела описывает исходный целевой API. Текущий create-ticket по-прежнему принимает document_id/day_id/companion; эталон фактической схемы — OpenAPI.

`document_id`, `office_id`, `service_id`, `business_date`, `companion_count` (0/1). Не принимать от клиента sequence, status, verified, capacity, window_id. Документ и успешный doc-check связываются на сервере с владельцем и неизменной версией реквизитов.

## Ошибки

Единый envelope: `code`, безопасный `message`, `field_errors`, `request_id`. 400/422 — формат/поля; 401 — сессия; 403 — доступ; 404 — ресурс отсутствует или не раскрывается; 409 — состояние, лимит, версия, idempotency conflict; 429 — частота. Коды: DAY_FULL, DOCUMENT_DAY_LIMIT, FUTURE_SERVICE_LIMIT, DOCUMENT_REVIEW_REQUIRED, TICKET_STATE_CONFLICT, OFFER_EXPIRED, VERSION_CONFLICT. Ошибка не раскрывает владельца паспорта и его посещения.

## Идемпотентность и версии

Все изменяющие доменные операции используют Idempotency-Key, уникальный внутри actor/operation. Повтор с тем же payload возвращает исходный ресурс. Для staff-переходов передаётся expected_version; несовпадение даёт 409 с безопасным текущим состоянием. После 409 клиент обновляет данные и не заявляет об успехе.

## Сессии

Cookie HttpOnly, SameSite, Secure на стенде; CSRF-защита для изменений и проверка Origin. Backend разрешает только явные локальные origin. Полномочия проверяются на каждом объекте, не только по роли. Staff login/recovery создаётся отдельным административным процессом, не выбором роли из тела OTP-запроса.
