# Модель данных (черновик)

Предлагаемые сущности, поля не утверждены и требуют data-minimization review.

```mermaid
erDiagram
  SERVICE_DATE ||--o{ BOOKING : contains
  SERVICE_DATE {
    date service_date
    int capacity
  }
  BOOKING {
    uuid id
    date service_date
    string reference
    string status
    datetime created_at
  }
  OPERATOR ||--o{ AUDIT_EVENT : records
  OPERATOR {
    uuid id
    string role
  }
  AUDIT_EVENT {
    uuid id
    string action
    datetime happened_at
  }
```

Никаких паспортных фотографий в начальной схеме. Инвариант: активных броней на дату не более 100. Механизм конкурентного доступа требует отдельного архитектурного решения.