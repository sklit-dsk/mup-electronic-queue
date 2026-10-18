import { useEffect, useState } from "react";
import { api, post, type Doc, type Fields } from "./api";

type RequestRow = { id: string; kind: "RECOVER" | "REPLACE"; state: string; version: number };
type Act = (action: () => Promise<any>, message?: string) => Promise<any>;
const labels: Record<string, string> = { PENDING: "Ожидает проверки сотрудником", APPROVED: "Подтверждено", REJECTED: "Запрос отклонён — обратитесь к сотруднику", CANCELLED: "Отменён" };
const fieldLabels: Record<string, string> = { country: "Страна", number: "Номер", name: "ФИО", birth_date: "Дата рождения", expiry_date: "Действителен до" };
function PassportFields({ fields }: { fields: Partial<Fields> }) {
  return <dl className="passportfields">{Object.entries(fieldLabels).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{fields[key as keyof Fields] || "—"}</dd></div>)}</dl>;
}

export function DocumentAccessPanel({ documents, health, act, busy, refresh }: { documents: Doc[]; health: any; act: Act; busy: boolean; refresh: unknown }) {
  const [kind, setKind] = useState<"RECOVER" | "REPLACE">("RECOVER");
  const [source, setSource] = useState("");
  const [check, setCheck] = useState<any>(null);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    api<RequestRow[]>("/document-requests/mine").then((r) => { if (active) { setRows(r); setError(false); } }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [refresh]);
  useEffect(() => {
    if (!check || !["PENDING", "PROCESSING"].includes(check.status)) return;
    let active = true;
    const id = setInterval(() => api(`/documents/checks/${check.id}`).then((r) => { if (active) setCheck(r); }).catch(() => {}), 2000);
    return () => { active = false; clearInterval(id); };
  }, [check?.id, check?.status]);
  async function upload(file?: File) {
    if (!file) return;
    const data = new FormData(); data.append("file", file);
    try { setCheck(await act(() => api("/documents/uploads", { method: "POST", body: data }))); } catch {}
  }
  return <section className="card accesspanel" aria-label="Доступ к паспорту и замена">
    <h2>Доступ к паспорту и замена</h2>
    <p>Если сменили телефон или получили новый паспорт, отправьте запрос. Сотрудник проверит оригинал и новый аккаунт лично. Фото само по себе не передаёт доступ.</p>
    <div className="accessform">
      <label>Что нужно сделать<select aria-label="Вид запроса по паспорту" value={kind} onChange={(e) => setKind(e.target.value as "RECOVER" | "REPLACE")}>
        <option value="RECOVER">Восстановить доступ к паспорту</option><option value="REPLACE">Заменить паспорт</option>
      </select></label>
      {kind === "REPLACE" && <label>Прежний паспорт<select aria-label="Прежний паспорт" value={source} onChange={(e) => setSource(e.target.value)}>
        <option value="">Выберите свой паспорт</option>{documents.map((d) => <option key={d.id} value={d.id}>{d.fields.country} · {d.fields.number}</option>)}
      </select></label>}
      <label>Фото действующего паспорта<input aria-label="Фото для восстановления или замены" type="file" accept="image/jpeg,image/png" disabled={busy} onChange={(e) => upload(e.target.files?.[0])} /></label>
      {health?.mode === "demo" && <button className="secondary" disabled={busy} onClick={async () => { try { setCheck(await act(() => post("/documents/demo"))); } catch {} }}>Синтетический паспорт для запроса</button>}
      {check && <div><p>{check.status === "PASSED" ? "Данные распознаны. Проверьте их перед отправкой." : check.status === "REVIEW_REQUIRED" ? "Распознавание требует ручного разбора. Обратитесь к сотруднику." : `Состояние обработки: ${check.status}`}</p>
        {check.fields && <PassportFields fields={check.fields} />}
      </div>}
      <button className="primary" disabled={busy || check?.status !== "PASSED" || (kind === "REPLACE" && !source)} onClick={async () => {
        try { await act(() => post("/document-requests", { kind, upload_id: check.id, document_id: kind === "REPLACE" ? source : undefined }), "Запрос отправлен сотруднику"); setCheck(null); } catch {}
      }}>Отправить запрос на проверку</button>
    </div>
    <h3>Мои запросы</h3>
    {error && <p role="alert">Не удалось загрузить запросы. Обновите страницу.</p>}
    {!rows.length && <p className="muted">Запросов пока нет</p>}
    {rows.map((r) => <div className="accessrequest" key={r.id}>
      <b>{r.kind === "RECOVER" ? "Восстановление доступа" : "Замена паспорта"}</b><p>{labels[r.state] || r.state}</p>
      {r.state === "PENDING" && <button className="secondary" disabled={busy} onClick={() => act(() => post(`/document-requests/${r.id}/cancel`, { version: r.version }), "Запрос отменён").catch(() => {})}>Отменить запрос</button>}
    </div>)}
    {documents.map((d) => <PassportHistory key={`${d.id}:${d.version}`} document={d} />)}
  </section>;
}

function PassportHistory({ document }: { document: Doc }) {
  const [history, setHistory] = useState<any[] | null>(null), [error, setError] = useState(false);
  return <details className="passporthistory" onToggle={(e) => {
    if (e.currentTarget.open && !history) api(`/documents/${document.id}/history`).then(setHistory).catch(() => setError(true));
  }}><summary>История паспорта {document.fields.number}</summary>
    {error ? <p>Не удалось загрузить историю</p> : history?.map((r) => <div key={r.revision}><b>{r.current ? "Действующий паспорт" : "Прежний паспорт"}</b><PassportFields fields={r.fields} /></div>)}
  </details>;
}

export function DocumentRequestReview({ request, act, busy }: { request: RequestRow & { fields: Fields; current_fields: Fields | null; target_phone: string; upload_id: string; expired: boolean; has_image: boolean }; act: Act; busy: boolean }) {
  const [reason, setReason] = useState("");
  const [original, setOriginal] = useState(false), [account, setAccount] = useState(false), [person, setPerson] = useState(false);
  async function decide(decision: string) {
    try { await act(() => post(`/admin/document-requests/${request.id}/${decision}`, { version: request.version, reason,
      original_checked: original, account_checked: account, same_person_checked: person }), "Решение сохранено"); } catch {}
  }
  return <section className="accessreview" aria-label={request.kind === "REPLACE" ? "Проверка замены паспорта" : "Проверка восстановления доступа"}>
    <h3>{request.kind === "REPLACE" ? "Замена паспорта" : "Восстановление доступа"}</h3><p>Аккаунт заявителя: <b>{request.target_phone}</b></p>
    {request.current_fields && <details><summary>Текущие реквизиты в базе</summary><PassportFields fields={request.current_fields} /></details>}
    <h4>Реквизиты из запроса</h4><PassportFields fields={request.fields} />
    {request.has_image && <a href={`/api/v1/documents/uploads/${request.upload_id}/image`} target="_blank" rel="noreferrer">Открыть фото паспорта</a>}
    {request.expired && <p role="alert">Фото или проверка недоступны. Для одобрения нужен новый запрос с действующей загрузкой.</p>}
    <div className="identitychecks">
      <label><input type="checkbox" checked={original} onChange={(e) => setOriginal(e.target.checked)} /> Оригинал документа проверен лично</label>
      <label><input type="checkbox" checked={account} onChange={(e) => setAccount(e.target.checked)} /> Доступ заявителя к указанному аккаунту проверен</label>
      <label><input type="checkbox" checked={person} onChange={(e) => setPerson(e.target.checked)} /> Подтверждено, что документы и аккаунт относятся к одному заявителю</label>
    </div>
    <label>Причина решения<input aria-label="Причина решения по паспорту" maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
    <div className="staffbuttons"><button className="primary" disabled={busy || request.expired || reason.trim().length < 5 || !original || !account || !person} onClick={() => decide("approve")}>Подтвердить изменение</button>
      <button className="secondary" disabled={busy || reason.trim().length < 5} onClick={() => decide("reject")}>Отклонить запрос</button></div>
  </section>;
}
