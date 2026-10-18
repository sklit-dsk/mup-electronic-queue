# Источники и основания

Сверено 15.10.2026. Здесь только ссылки на первичные материалы. Выбор архитектуры и значения по умолчанию — инженерные решения этого проекта.

- Исходная продуктовая концепция: `MUP_Electronic_Queue_Concept_RU.pdf`, версия 2.0 от 15.10.2026, подготовлена в этом обсуждении. PDF не требуется для работы Codex: требования перенесены в PRD и DOMAIN.
- [Официальная документация Codex: AGENTS.md](https://developers.openai.com/codex/guides/agents-md) — обнаружение и порядок инструкций. Адрес может перенаправлять на ChatGPT Learn.
- [Codex IDE extension](https://developers.openai.com/codex/ide) — работа в редакторе. Не предполагаем конкретную модель или скрытые настройки расширения.
- [FastAPI: SQL databases](https://fastapi.tiangolo.com/tutorial/sql-databases/) — подключение SQL; SQLAlchemy/Alembic выбраны отдельно для проекта.
- [FastAPI: Docker](https://fastapi.tiangolo.com/deployment/docker/) — контейнеризация.
- [Vite: Getting Started](https://vite.dev/guide/) — React/TypeScript scaffold и совместимость Node.js.
- [PostgreSQL 16: Explicit Locking](https://www.postgresql.org/docs/16/explicit-locking.html) — транзакционные блокировки.
- [PostgreSQL: Partial Indexes](https://www.postgresql.org/docs/current/indexes-partial.html) — уникальность для выбранных состояний.
- [NIST SP 800-63A-4](https://pages.nist.gov/800-63-4/sp800-63a.html) — различие проверки документа и владельца, сопоставление MRZ и видимых данных. Это ориентир, не автоматически обязательное право Черногории.
- [PRADO](https://www.consilium.europa.eu/en/documents/prado/) — образцы документов и защитные признаки; не база владельцев паспортов.

При реализации зависимости проверять по официальной документации и фиксировать в lock-файлах. Не копировать команды устаревшего туториала без проверки версии.
