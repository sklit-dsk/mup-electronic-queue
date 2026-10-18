# База данных и конкурентность

> Ниже исходная целевая модель. Фактическая схема v0.1 упрощена и описана в IMPLEMENTATION.md; код и миграция — в backend/. Не создавать недостающие расширения автоматически.

Это проект модели, не готовые миграции. В T01 Codex превращает его в схему и проверяет на PostgreSQL.

## Сущности

| Сущность | Основные данные и ограничения |
| --- | --- |
| users / sessions / otp_challenges | Контакт, роль, область office/service, сессия и срок; одноразовые OTP с ограничением попыток |
| applicants | Внутренний ID заявителя и связи документов; автоматического слияния по ФИО нет |
| identity_documents | canonical fingerprint UNIQUE, зашифрованные реквизиты, applicant_id, window_verified_at/by |
| document_claim_requests | Связь user/document, статус владения и конфликт; загрузка фото не передаёт чужой аккаунт |
| document_uploads / document_checks | Владелец файла, закрытый object key, качество, OCR/MRZ-результат, версия адаптера, срок удаления |
| offices / services / windows | Рабочие дни, доступные услуги и полномочия окна |
| office_days | UNIQUE office/date, capacity=100, OPEN/CLOSED, версия; родительская строка блокировки дня |
| daily_seats | 100 строк на office_day, UNIQUE day/ordinal; FREE/HELD/ALLOCATED/CONSUMED, owner ticket/offer |
| applicant_day_claims | PRIMARY KEY document_id/business_date; FREE/HELD/ACTIVE/SPENT, owner ticket/offer |
| applicant_service_claims | PRIMARY KEY document_id/service_id; owner для единственной активной будущей записи/предложения, либо FREE |
| queue_counters | UNIQUE office/date/service, next_sequence; выдача номера только в транзакции |
| tickets | UUID, document, office/date/service, sequence, status, companion_count CHECK IN (0,1), version, window |
| waitlist_entries / waitlist_offers | WAITING/OFFERED/ACCEPTED/EXPIRED/DECLINED, FIFO-порядок, expires_at и held seat |
| service_episodes / verification_events | Начало, сверка оригинала, завершение; повторный эпизод с причиной |
| idempotency_records | UNIQUE actor/operation/key, request_hash, итог/ресурс; без сырого паспорта в payload |
| outbox / jobs / audit_events | События, leases, повторы, actor/action/reason, безопасные поля |

## Ограничения схемы

Реализованное дополнение 2а: service_episodes хранит только повторные приёмы; первичный приём остаётся в Ticket. Composite FK (ticket_id, day_id) исключает эпизод другого дня; UNIQUE ticket/number сохраняет порядок истории. Частичные UNIQUE запрещают два активных повтора талона и два повтора у окна. Занятость окна между tickets и service_episodes проверяется под общей блокировкой OfficeDay во всех путях вызова/создания/возврата; эти таблицы не имеют общей межтабличной UNIQUE, поэтому обходить эту блокировку новым путём нельзя. Порядок для повтора: document → day → parent ticket → episode. Номер талона/seat/day claim не создаются заново. Миграция 0003 запрещает downgrade с непустой историей эпизодов, чтобы не удалить её молча.

Реализованное дополнение 2б: фактическая таблица documents — стабильная каноническая запись с owner_id, version и текущей revision. document_revisions хранит уникальный fingerprint и зашифрованные реквизиты каждой версии; ticket.document_revision фиксирует версию при записи. document_requests хранит RECOVER/REPLACE, requester, office scope, upload, base_version, previous_owner, fingerprint, состояние и решение. outbox.document_id позволяет безопасно переназначить только связанные уведомления при recovery; cancelled_at отключает неоднозначное устаревшее событие. Миграция 0004 backfill-ит revision=1 и запрещает downgrade при истории запросов или изменённом документе.

- UNIQUE office/date/service/sequence для номера; полные ссылки должны исключать талон, указывающий на место другого office/date.
- UNIQUE nullable current_ticket_id/current_offer_id в daily_seats: место принадлежит ровно одному текущему владельцу; согласованность owner/state проверяется CHECK.
- Одно занятое окно: частичный UNIQUE window_id для CALLED/IN_SERVICE, window_id обязателен для этих состояний.
- Не хранить несколько несогласованных источников истины «мест осталось». Остаток считается по 100 строкам daily_seats под транзакционной защитой; кэш только для отображения.
- Новый день создаётся один раз вместе со 100 местами; capacity не уменьшается ниже уже занятых/использованных мест.

## Основной алгоритм бронирования

1. Авторизовать пользователя, проверить допустимость document_check, запрос и business_date. Зафиксировать idempotency key; такой же key с иным request_hash даёт 409.
2. В одной транзакции заблокировать canonical identity_documents; upsert claim-строк для даты/услуги и проверить их состояние.
3. Заблокировать office_days и выбрать FREE место; проверить день, лимиты, рабочую дату и время закрытия записи.
4. Увеличить queue_counter, создать BOOKED, занять seat, day claim и service claim, записать audit и outbox.
5. Commit. После commit можно доставлять уведомления. Повтор запроса возвращает тот же результат.

Даже при последнем месте двух параллельных подтверждений быть не может. Простого SELECT COUNT перед INSERT недостаточно.

## Порядок блокировок

Для операций над заявителем: identity_documents (ID по возрастанию), day claims (дата), service claims (ID), office_days (office_id/date по возрастанию), seats (ordinal), queue counters, tickets/offers. Строки claims создаются upsert под блокировкой документа. Все переходы, затрагивающие заявителя, соблюдают этот порядок.

Для call-next: сначала office_day и window, затем конкурентно выбранный BOOKED ticket через FOR UPDATE SKIP LOCKED. Call-next не блокирует identity/claim-строки и не меняет их. Повторный вызов не выделяет ещё одно место. Переходы после вызова сначала читают ссылки без блокировки, затем берут блокировки в основном порядке и повторно проверяют состояние.

Waitlist selector сначала читает кандидата без удержания row lock, затем берёт основные блокировки и повторно проверяет кандидата. Accept/expire/release используют тот же порядок. Добавить ограниченные повторы deadlock/serialization с сохранением идемпотентности, не бесконечный retry.

## Документ и индекс

Fingerprint: HMAC-SHA-256 от однозначно сериализованного нормализованного кортежа country/type/series/number, с отдельным серверным ключом. Не обычный SHA от номера: пространство номеров перебирается. Исходные реквизиты шифровать отдельно; ключи вне БД и репозитория. Ротацию fingerprint key выполнять как миграцию dual-read/dual-index с сохранением уникальности, не менять ключ «на лету».

Нет способа гарантировать «один человек» при двух несвязанных настоящих паспортах: до проверки сотрудником лимит относится к одному документу. Это известное ограничение, не повод объединять по похожему имени.

Текущий 2б связывает замену только с выбранным владельцем document_id после очной проверки. Он не выводит тождество человека из ФИО и не сливает два уже существующих document_id. Все известные ревизии одной записи участвуют в поиске и навсегда резервируют свои fingerprints.
