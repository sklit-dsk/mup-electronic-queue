import { getIntlLocale } from "./i18n";

export const states: Record<string, string> = {
  BOOKED: "В очереди",
  CALLED: "Подойдите к окну",
  IN_SERVICE: "На приёме",
  COMPLETED: "Завершено",
  CANCELLED: "Отменено",
  TRANSFERRED: "Перенесено",
  SKIPPED: "Вызов пропущен",
  NO_SHOW: "Неявка",
  REJECTED: "Несоответствие",
};
export const errors: Record<string, string> = {
  DAY_FULL: "На этот день мест нет. Выберите другую дату или лист ожидания.",
  DOCUMENT_DAY_LIMIT:
    "По этому паспорту уже занято или использовано место на выбранный день.",
  FUTURE_SERVICE_LIMIT:
    "Сначала завершите или отмените свою действующую запись.",
  DOCUMENT_REVIEW_REQUIRED:
    "Документ требует разбора сотрудником. Это не означает, что паспорт поддельный.",
  DOCUMENT_DATA_MISMATCH:
    "Исправленные данные не совпадают с распознанными. Нужна повторная проверка.",
  AUTH_REQUIRED: "Сессия истекла. Войдите снова.",
  VERSION_CONFLICT: "Запись изменилась. Обновите страницу.",
  WINDOW_BUSY: "У этого окна уже есть вызванный заявитель.",
  ORIGINAL_REQUIRED: "Сначала подтвердите сверку оригинала.",
  OFFER_EXPIRED: "Время подтверждения предложения истекло.",
  DATE_UNAVAILABLE: "Эта дата недоступна.",
  WAITLIST_PRIORITY:
    "Освободившееся место сначала предлагается людям из листа ожидания. Можно выбрать другой день.",
  SMS_NOT_CONFIGURED:
    "SMS ещё не подключены. Для проверки используйте демонстрационный вход.",
  TICKET_STATE_CONFLICT: "Операция недоступна в текущем состоянии.",
  DAY_CLOSED: "Приём на этот день закрыт.",
  SERVICE_CLOSED: "Сейчас новые приёмы недоступны. Часы работы: 08:00–14:00, Подгорица.",
  REPEAT_CALL_REQUIRED: "Сначала повторите вызов и дайте заявителю время подойти.",
  CALL_WAIT_REQUIRED: "Дождитесь окончания паузы после вызова.",
  CALL_LIMIT_REACHED: "Повторный вызов уже выполнен. Дождитесь заявителя или отметьте пропуск.",
  REASON_REQUIRED: "Укажите причину действия.",
  REPEAT_NOT_ALLOWED: "Повторный приём доступен только после завершённого обслуживания.",
  REPEAT_ALREADY_ACTIVE: "По этому талону уже открыт повторный приём.",
  REPEAT_SAME_DAY_REQUIRED: "Повторный приём возможен только в день исходной записи.",
  WINDOW_REQUIRED: "Выберите окно для повторного вызова.",
  DOCUMENT_ACCESS_REVIEW_REQUIRED: "Для доступа к документу требуется разбор сотрудником. Можно отправить запрос на восстановление.",
  DOCUMENT_SUPERSEDED: "Этот паспорт сохранён в истории. Используйте действующий паспорт.",
  DOCUMENT_UPLOAD_EXPIRED: "Срок хранения фото истёк. Загрузите новое фото и отправьте запрос повторно.",
  IDENTITY_CHECK_REQUIRED: "Отметьте очную сверку документа, аккаунта и личности заявителя.",
  DOCUMENT_CHANGED: "Доступ или реквизиты изменились после отправки запроса. Нужен новый запрос.",
  DOCUMENT_IN_SERVICE: "Сначала завершите текущий приём, затем изменяйте доступ или паспорт.",
  DOCUMENT_ALREADY_ACCESSIBLE: "Документ уже доступен в этом аккаунте.",
  DOCUMENT_REPLACEMENT_CONFLICT: "Новый паспорт уже зарегистрирован. Автоматическое объединение записей запрещено; нужен отдельный разбор.",
  DELIVERY_IN_PROGRESS: "Сейчас отправляется уведомление. Повторите решение после завершения отправки.",
  LOGIN_FAILED: "Неверный логин или пароль.",
  FORBIDDEN: "Недостаточно прав для этого действия.",
};
export const serviceStates: Record<string, string> = {
  OPEN: "Приём открыт",
  DEMO_OPEN: "Демонстрационный приём",
  BEFORE_OPEN: "Приём ещё не начался",
  FINISHED: "Приём завершён",
  CLOSED: "Приём закрыт администратором",
};
export function dateLabel(value: string) {
  return new Intl.DateTimeFormat(getIntlLocale(), {
    day: "numeric",
    month: "long",
  }).format(new Date(`${value}T12:00:00`));
}
export function weekDay(value: string) {
  return new Intl.DateTimeFormat(getIntlLocale(), { weekday: "short" }).format(
    new Date(`${value}T12:00:00`),
  );
}
export function progress(used: number, capacity = 100) {
  return Math.max(0, Math.min(100, (used / capacity) * 100));
}

