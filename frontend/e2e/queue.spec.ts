import { test, expect } from "@playwright/test";

test("applicant replaces a passport without losing the ticket or daily claim", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let csrf = "";
  async function auth(role: string) {
    const response = await page.request.post("/api/v1/auth/demo", { data: { role } });
    expect(response.ok()).toBeTruthy();
    csrf = (await response.json()).csrf;
  }
  async function post(path: string, data: any = {}) {
    const response = await page.request.post(`/api/v1${path}`, {
      data, headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }

  await auth("applicant");
  const originalUpload = await post("/documents/demo");
  const document = await post(`/documents/checks/${originalUpload.id}/confirm`, originalUpload.fields);
  const days = await (await page.request.get("/api/v1/days")).json();
  await auth("admin");
  await post(`/admin/days/${days[0].id}`, { closed: false, reason: "Synthetic passport replacement test" });
  await auth("applicant");
  const ticket = await post("/tickets", { document_id: document.id, day_id: days[0].id });

  await page.goto("/");
  await page.getByRole("button", { name: "Мои записи", exact: true }).click();
  await page.getByLabel("Вид запроса по паспорту").selectOption("REPLACE");
  await page.getByLabel("Прежний паспорт").selectOption(document.id);
  await page.getByRole("button", { name: "Синтетический паспорт для запроса" }).click();
  const requestedNumber = await page.locator(".accessform .passportfields dd").nth(1).textContent();
  await page.getByRole("button", { name: "Отправить запрос на проверку" }).click();
  await expect(page.locator(".accessrequest").first()).toContainText("Ожидает проверки сотрудником");

  await auth("admin");
  await page.reload();
  await page.getByRole("button", { name: "Управление", exact: true }).click();
  const review = page.getByLabel("Проверка замены паспорта");
  await expect(review).toContainText(requestedNumber || "");
  for (const label of [
    "Оригинал документа проверен лично",
    "Доступ заявителя к указанному аккаунту проверен",
    "Подтверждено, что документы и аккаунт относятся к одному заявителю",
  ]) await review.getByLabel(label).check();
  await review.getByLabel("Причина решения по паспорту").fill("Synthetic in-person replacement check");
  await review.getByRole("button", { name: "Подтвердить изменение" }).click();
  await expect(review).toHaveCount(0);

  await auth("applicant");
  const mine = await (await page.request.get("/api/v1/tickets/mine")).json();
  expect(mine.find((row: any) => row.id === ticket.id)?.document_revision).toBe(1);
  const documents = await (await page.request.get("/api/v1/documents/mine")).json();
  expect(documents).toHaveLength(1);
  expect(documents[0].id).toBe(document.id);
  expect(documents[0].revision).toBe(2);
  expect(documents[0].fields.number).toBe(requestedNumber);
  const history = await (await page.request.get(`/api/v1/documents/${document.id}/history`)).json();
  expect(history.map((row: any) => row.fields.number)).toEqual([originalUpload.fields.number, requestedNumber]);

  await page.reload();
  await page.getByRole("button", { name: "Мои записи", exact: true }).click();
  await page.getByText(`История паспорта ${requestedNumber}`).click();
  await expect(page.locator(".passporthistory")).toContainText("Прежний паспорт");
  await expect(page.locator(".passporthistory")).toContainText(originalUpload.fields.number);
  await post(`/tickets/${ticket.id}/cancel`);
  expect(errors).toEqual([]);
});

test("admin opens a repeat visit without creating a second ticket or releasing capacity", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let csrf = "";
  async function auth(role: string) {
    const response = await page.request.post("/api/v1/auth/demo", { data: { role } });
    expect(response.ok()).toBeTruthy();
    csrf = (await response.json()).csrf;
  }
  async function post(path: string, data: any = {}) {
    const response = await page.request.post(`/api/v1${path}`, {
      data, headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  const days = await (await page.request.get("/api/v1/days")).json();
  await auth("admin");
  await post(`/admin/days/${days[0].id}`, { closed: false, reason: "Synthetic repeat visit test" });
  await auth("applicant");
  const check = await post("/documents/demo");
  const doc = await post(`/documents/checks/${check.id}/confirm`, check.fields);
  let ticket = await post("/tickets", { document_id: doc.id, day_id: days[0].id });
  await auth("staff");
  ticket = await post("/staff/call-next", { day_id: days[0].id, window: 1 });
  for (const action of ["verify", "start", "complete"])
    ticket = await post(`/staff/tickets/${ticket.id}/${action}`, { version: ticket.version });
  const primaryStart = ticket.started_at, primaryEnd = ticket.completed_at;
  const before = await (await page.request.get(`/api/v1/staff/days/${days[0].id}/summary`)).json();
  await auth("admin");
  await page.goto("/");
  await page.getByRole("button", { name: "Рабочее окно", exact: true }).first().click();
  page.once("dialog", (dialog) => dialog.accept("Synthetic additional document review"));
  await page.getByRole("button", { name: `Повторный приём ${ticket.label}`, exact: true }).click();
  await expect(page.locator(".staffcurrent .repeatnotice")).toContainText("Сверьте оригинал заново");
  await expect(page.getByRole("button", { name: "Начать приём", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Оригинал и личность заявителя совпадают", exact: true }).click();
  await page.getByRole("button", { name: "Начать приём", exact: true }).click();
  await page.getByRole("button", { name: "Завершить приём", exact: true }).click();
  const row = page.locator(".staffrow").filter({ hasText: ticket.label });
  await row.locator(".episodehistory > summary").click();
  await expect(row.locator(".episodeentry")).toContainText("Повторный приём №1: Завершено");
  await expect(row.locator(".episodeentry")).toContainText("Synthetic additional document review");
  const history = await (await page.request.get(`/api/v1/staff/tickets/${ticket.id}/episodes`)).json();
  expect(history.ticket.state).toBe("COMPLETED");
  expect(history.ticket.started_at).toBe(primaryStart);
  expect(history.ticket.completed_at).toBe(primaryEnd);
  expect(history.episodes).toHaveLength(1);
  const after = await (await page.request.get(`/api/v1/staff/days/${days[0].id}/summary`)).json();
  expect(after.consumed).toBe(before.consumed);
  expect(after.free).toBe(before.free);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test("synthetic applicant books and employee completes the visit", async ({
  page,
}) => {
  // Run against a fresh DEMO database; no real documents.
  await page.goto("/");
  await page.getByRole("button", { name: "Записаться", exact: true }).click();
  await page.getByRole("button", { name: "Войти как заявитель" }).click();
  await page
    .getByRole("button", { name: /синтетический тестовый паспорт/ })
    .click();
  await page.getByRole("button", { name: "Подтвердить данные" }).click();
  await page.locator(".dategrid button:not([disabled])").first().click();
  await page.getByRole("button", { name: "Продолжить", exact: true }).click();
  await page
    .getByRole("button", { name: "Подтвердить запись", exact: true })
    .click();
  await expect(page.locator(".ticketcard").first()).toContainText("В очереди");
  const booked = (await (await page.request.get("/api/v1/tickets/mine")).json()).find(
    (ticket: any) => ticket.state === "BOOKED",
  );
  expect(booked).toBeTruthy();
  await page.getByRole("button", { name: /Сменить роль/ }).click();
  await page.getByRole("button", { name: "Сотрудник", exact: true }).click();
  await page.getByRole("button", { name: "Следующий заявитель" }).click();
  await page
    .getByRole("button", { name: "Оригинал и личность заявителя совпадают" })
    .click();
  await page.getByRole("button", { name: "Начать приём" }).click();
  await page.getByRole("button", { name: "Завершить приём" }).click();
  await expect(page.locator(".staffrow").filter({ hasText: booked.label })).toContainText("Завершено");
});

test("employee cannot skip early and can transfer a ticket after closing the day", async ({ page }) => {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  let csrf = "";
  async function auth(role: string) {
    const response = await page.request.post("/api/v1/auth/demo", { data: { role } });
    expect(response.ok()).toBeTruthy();
    csrf = (await response.json()).csrf;
  }
  async function post(path: string, data: any) {
    const response = await page.request.post(`/api/v1${path}`, {
      data, headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  await auth("applicant");
  const upload = await post("/documents/demo", {});
  const document = await post(`/documents/checks/${upload.id}/confirm`, upload.fields);
  const days = await (await page.request.get("/api/v1/days")).json();
  const ticket = await post("/tickets", { document_id: document.id, day_id: days[0].id });
  await auth("staff");
  await page.goto("/");
  await page.getByRole("button", { name: "Рабочее окно", exact: true }).first().click();
  await page.getByRole("button", { name: "Следующий заявитель" }).click();
  await expect(page.locator(".bigcall")).toHaveText(ticket.label);
  await expect(page.getByRole("button", { name: "Не подошёл", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Повторить вызов", exact: true })).toBeDisabled();

  await auth("admin");
  await post(`/admin/days/${days[0].id}`, { closed: true, reason: "Synthetic end of day test" });
  await auth("staff");
  await page.reload();
  await page.getByRole("button", { name: "Рабочее окно", exact: true }).first().click();
  await expect(page.getByLabel("Сводка рабочего дня")).toContainText("Приём закрыт администратором");
  await expect(page.getByRole("button", { name: "Начать приём", exact: true })).toBeDisabled();
  const transfer = page.locator(".stafftransfer").filter({ hasText: `Перенести ${ticket.label}` });
  await transfer.locator("summary").click();
  await page.getByLabel(`Новый день для ${ticket.label}`).selectOption(days[1].id);
  await page.getByLabel(`Причина переноса ${ticket.label}`).fill("Reception ended before applicant was served");
  await transfer.getByRole("button", { name: "Подтвердить перенос" }).click();
  await expect(page.locator(".staffrow").filter({ hasText: ticket.label })).toContainText("Перенесено");

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(failures).toEqual([]);
  await auth("applicant");
  const mine = await (await page.request.get("/api/v1/tickets/mine")).json();
  expect(mine.some((t: any) => t.id === ticket.id && t.state === "TRANSFERRED")).toBeTruthy();
  expect(mine.some((t: any) => t.document_id === document.id && t.date === days[1].date && t.state === "BOOKED")).toBeTruthy();
});
