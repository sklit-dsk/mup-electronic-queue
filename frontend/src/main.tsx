import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { QRCodeSVG } from "qrcode.react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  Bell,
  CalendarDays,
  Check,
  CheckCheck,
  ChevronDown,
  CircleHelp,
  Clock3,
  FileCheck2,
  Fingerprint,
  LayoutDashboard,
  ListOrdered,
  LoaderCircle,
  LogOut,
  MapPin,
  Monitor,
  MoreHorizontal,
  Plus,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Ticket as TicketIcon,
  UploadCloud,
  Users,
  X,
  ExternalLink,
  ScanLine,
  Globe2,
} from "lucide-react";
import {
  api,
  post,
  setCsrf,
  type Day,
  type Doc,
  type Fields,
  type Ticket,
  type WorkdaySummary,
  type Episode,
} from "./api";
import { dateLabel, weekDay, states, errors, progress, serviceStates } from "./format";
import { getLocale, installTranslator, setLocale, type Locale } from "./i18n";
import { isRoleAllowed, portalFromPath } from "./portal";
import "./style.css";
import { DocumentAccessPanel, DocumentRequestReview } from "./DocumentAccess";

type User = { id: string; name: string; role: string };
type Tab = "overview" | "booking" | "tickets" | "queue" | "staff" | "admin";
const activeStates = ["BOOKED", "CALLED", "SKIPPED", "IN_SERVICE"];
type WorkItem = Ticket & { episode_id?: string; repeat?: boolean };
const Icon = ({ children }: { children: React.ReactNode }) => (
  <span className="iconbox">{children}</span>
);

function App() {
  const portal = portalFromPath(window.location.pathname);
  const [user, setUser] = useState<User | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [tab, setTab] = useState<Tab>(portal === "admin" ? "admin" : portal === "staff" ? "staff" : "overview");
  const [days, setDays] = useState<Day[]>([]);
  const [dayId, setDayId] = useState("");
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [queue, setQueue] = useState<any>(null);
  const [staffTickets, setStaffTickets] = useState<Ticket[]>([]);
  const [waiting, setWaiting] = useState<any[]>([]);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const [online, setOnline] = useState(true);
  const [showNotices, setShowNotices] = useState(false);
  const [loading, setLoading] = useState(true);
  const [roleMenu, setRoleMenu] = useState(false);
  const [showBoard, setShowBoard] = useState(false);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const sessionActor = useRef<string | undefined>(undefined);
  sessionActor.current = user?.id;

  const tell = useCallback((text: string, error = false) => {
    setToast({ text, error });
    window.setTimeout(() => setToast(null), 6000);
  }, []);
  const load = useCallback(async () => {
    const actorId = user?.id;
    const fresh = () => sessionActor.current === actorId;
    try {
      const d = await api<Day[]>(user && user.role !== "applicant" ? "/staff/days" : "/days");
      if (!fresh()) return;
      setDays(d);
      const chosen = d.some((day) => day.id === dayId) ? dayId : d[0]?.id;
      if (chosen && chosen !== dayId) setDayId(chosen);
      if (chosen) { const q = await api(`/queues/${encodeURIComponent(chosen)}`); if (!fresh()) return; setQueue(q); }
      if (user) {
        const [docs, tks, wait, notes] = await Promise.all([
          api("/documents/mine"),
          api("/tickets/mine"),
          api("/waitlist/mine"),
          api("/notifications"),
        ]);
        if (!fresh()) return;
        setDocuments(docs);
        setTickets(tks);
        setWaiting(wait);
        setNotifications(notes);
        if (user.role !== "applicant" && chosen) {
          const staffRows = await api<Ticket[]>(`/staff/tickets?day_id=${encodeURIComponent(chosen)}`);
          if (!fresh()) return;
          setStaffTickets(staffRows);
        }
      }
      setOnline(true);
      setLastSync(new Date());
    } catch (e: any) {
      if (!fresh()) return;
      setOnline(false);
      if (e.message === "AUTH_REQUIRED") {
        setUser(null);
        setCsrf("");
        setDocuments([]); setTickets([]); setStaffTickets([]); setWaiting([]); setNotifications([]);
      }
    } finally {
      if (fresh()) setLoading(false);
    }
  }, [user, dayId]);
  useEffect(() => {
    api("/health")
      .then(setHealth)
      .catch(() => setOnline(false));
    api("/me")
      .then((r) => {
        setUser(r.user);
        setCsrf(r.csrf);
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  if (portal === "demo") return <DemoDirectory />;

  async function act(fn: () => Promise<any>, message?: string) {
    setBusy(true);
    try {
      const r = await fn();
      if (message) tell(message);
      await load();
      return r;
    } catch (e: any) {
      tell(errors[e.message] || e.message, true);
      if (["VERSION_CONFLICT", "STATE_CONFLICT", "TICKET_STATE_CONFLICT", "DAY_CLOSED", "SERVICE_CLOSED"].includes(e.message))
        await load();
      throw e;
    } finally {
      setBusy(false);
    }
  }
  async function demoLogin(role: string) {
    try {
      const r = await post("/auth/demo", { role });
      setUser(r.user);
      setCsrf(r.csrf);
      setRoleMenu(false);
      setTab(
        role === "applicant"
          ? "overview"
          : role === "staff"
            ? "staff"
            : "admin",
      );
      tell("Демонстрационный профиль открыт");
    } catch (e: any) {
      tell(errors[e.message] || e.message, true);
    }
  }
  const day = days.find((d) => d.id === dayId);
  const current = tickets.find((t) => t.episodes.some((e) => ["CALLED", "IN_SERVICE", "SKIPPED"].includes(e.state))) ||
    tickets.find((t) => activeStates.includes(t.state));
  const counts = {
    waiting: queue?.waiting?.length || 0,
    calling: queue?.calling?.length || 0,
    completed: queue?.completed || 0,
  };
  const applicantNav = [
    {
      id: "overview" as Tab,
      label: "Обзор",
      icon: <LayoutDashboard size={19} />,
    },
    {
      id: "booking" as Tab,
      label: "Записаться",
      icon: <CalendarDays size={19} />,
    },
    {
      id: "tickets" as Tab,
      label: "Мои записи",
      icon: <TicketIcon size={19} />,
    },
    {
      id: "queue" as Tab,
      label: "Живая очередь",
      icon: <ListOrdered size={19} />,
    },
  ];
  const staffNav = [
    { id: "queue" as Tab, label: "Живая очередь", icon: <ListOrdered size={19} /> },
    { id: "staff" as Tab, label: "Рабочее окно", icon: <Monitor size={19} /> },
  ];
  const adminNav = [
    ...staffNav,
    { id: "admin" as Tab, label: "Управление", icon: <SlidersHorizontal size={19} /> },
  ];
  const nav = portal === "admin" ? adminNav : portal === "staff" ? staffNav : applicantNav;

  const roleAllowed = !user || isRoleAllowed(portal, user.role);

  if ((portal === "staff" || portal === "admin") && (!user || !roleAllowed)) {
    return (
      <PortalLogin
        portal={portal}
        health={health}
        user={user}
        onAuthenticated={(result: any) => {
          setCsrf(result.csrf);
          setUser(result.user);
          setTab(portal);
        }}
        demoLogin={() => demoLogin(portal)}
        tell={tell}
      />
    );
  }

  if (portal === "applicant" && user && !roleAllowed) {
    return <PortalMismatch user={user} />;
  }

  if (showBoard)
    return (
      <div className="fullboard">
        <div className="boardbrand">
          <Logo />
          <button className="secondary" onClick={() => setShowBoard(false)}>
            <X size={18} /> Закрыть табло
          </button>
        </div>
        <div className="eyebrow">PODGORICA · ШАЛТЕР ЗА ИНОСТРАНЦЕ</div>
        <h1>Приглашаем к окну</h1>
        <p>{serviceStates[queue?.service_status]}</p>
        <div className="boardcalls">
          {queue?.calling?.length ? (
            queue.calling.map((t: any) => (
              <div key={t.number}>
                <strong>{t.label}</strong>
                <span>ОКНО {t.window}{t.repeat ? " · ПОВТОРНО" : ""}</span>
              </div>
            ))
          ) : (
            <p>Ожидайте следующего вызова</p>
          )}
        </div>
        <div className="boardnext">
          Следующие в очереди{" "}
          <b>
            {queue?.waiting
              ?.slice(0, 6)
              .map((t: any) => t.label)
              .join(" · ") || "—"}
          </b>
        </div>
        <p className="muted">
          {online
            ? "Обновляется автоматически"
            : "Нет соединения · данные могут быть устаревшими"}
        </p>
      </div>
    );

  return (
    <div className="shell">
      <aside className="sidebar">
        <Logo />
        <div className="workspace">
          <div className="workspaceflag">ME</div>
          <div>
            <b>Podgorica</b>
            <span>Очередь для иностранцев</span>
          </div>
          <ChevronDown size={15} />
        </div>
        <div className="navlabel">ЛИЧНЫЙ КАБИНЕТ</div>
        <nav>
          {nav.map((n) => (
            <button
              key={n.id}
              className={tab === n.id ? "active" : ""}
              onClick={() => setTab(n.id)}
            >
              {n.icon}
              {n.label}
              {n.id === "tickets" && current && (
                <span className="navcount">1</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebarbottom">
          <div className="helpbox">
            <CircleHelp size={23} />
            <b>Как работает очередь?</b>
            <p>Запишитесь на день и следите за своим номером онлайн.</p>
            <button
              onClick={() => {
                setTab("overview");
                tell(
                  "Оригинал паспорта проверяют только у окна. Сопровождающий не получает отдельного обслуживания.",
                );
              }}
            >
              Подробнее <ArrowRight size={15} />
            </button>
          </div>
          <div className="sidebarfoot">
            <ShieldCheck size={16} /> Именная запись · защищённые данные
          </div>
        </div>
      </aside>
      <div className="mainwrap">
        <header className="topbar">
          <div className="breadcrumb">
            Личный кабинет <span>/</span>{" "}
            <b>{nav.find((n) => n.id === tab)?.label}</b>
          </div>
          <div className="topactions">
            <label className="lang" aria-label="Язык интерфейса">
              <Globe2 size={15} />
              <select
                value={getLocale()}
                onChange={(event) => setLocale(event.target.value as Locale)}
                aria-label="Язык интерфейса"
              >
                <option value="ru">RU</option>
                <option value="me">CG</option>
                <option value="en">EN</option>
              </select>
            </label>
            <button
              className="noticebtn"
              aria-label="Уведомления"
              onClick={() => setShowNotices(!showNotices)}
            >
              <Bell size={19} />
              {notifications.length > 0 && <i />}
            </button>
            <div className="divider" />
            <button className="profile" onClick={() => setRoleMenu(!roleMenu)}>
              <span className="avatar">
                {user
                  ? user.name
                      .split(" ")
                      .map((x) => x[0])
                      .slice(0, 2)
                      .join("")
                  : "Г"}
              </span>
              <span>
                <b>{user?.name || "Гость"}</b>
                <small>
                  {user?.role === "admin"
                    ? "Администратор"
                    : user?.role === "staff"
                      ? "Сотрудник"
                      : "Заявитель"}
                </small>
              </span>
              <ChevronDown size={14} />
            </button>
            {roleMenu && (
              <div className="dropdown">
                <b>Ваш профиль</b>
                {user && (
                  <button
                    onClick={() => {
                      post("/auth/logout").then(() => {
                        setUser(null);
                        setDocuments([]);
                        setTickets([]);
                        setStaffTickets([]);
                        setWaiting([]);
                        setNotifications([]);
                        setCsrf("");
                        setRoleMenu(false);
                        setTab("overview");
                      });
                    }}
                  >
                    <LogOut size={16} /> Выйти
                  </button>
                )}
              </div>
            )}
          </div>
        </header>
        <main>
          {health?.mode === "demo" && (
            <div className="demobar">
              <span>
                <i /> Тестовый стенд
              </span>{" "}
              Только синтетические документы. Запись не является официальной
              записью в MUP.
              <a href="/demo">Демонстрационные порталы <ArrowRight size={13} /></a>
            </div>
          )}
          {!online && (
            <div className="offline">
              Нет соединения с сервером. Показываем последние полученные данные.{" "}
              <button onClick={load}>Повторить</button>
            </div>
          )}
          {tab === "overview" && (
            <>
              <div className="pagehead">
                <div>
                  <div className="eyebrow">ВАШЕ ВРЕМЯ ИМЕЕТ ЗНАЧЕНИЕ</div>
                  <h1>
                    {user?.role === "applicant"
                      ? "Добро пожаловать, " + user.name.split(" ")[0]
                      : "Добро пожаловать"}
                    <span className="wave">✦</span>
                  </h1>
                  <p>Документы по порядку. Без утренней очереди.</p>
                </div>
                <button className="primary" onClick={() => setTab("booking")}>
                  <Plus size={18} /> Новая запись
                </button>
              </div>
              <div className="overviewgrid">
                <section className="hero">
                  <div className="herobadge">
                    <span /> ЭЛЕКТРОННАЯ ОЧЕРЕДЬ
                  </div>
                  <h2>
                    Меньше ожидания.
                    <br />
                    Больше времени
                    <br />
                    <span>для жизни.</span>
                  </h2>
                  <p>
                    Запишитесь на удобный день.
                    <br />
                    Следите за очередью — приходите к своему номеру.
                  </p>
                  <button
                    className="whitebtn"
                    onClick={() => setTab("booking")}
                  >
                    Записаться на приём <ArrowRight size={17} />
                  </button>
                  <div className="herolines">
                    <div />
                    <div />
                    <div />
                  </div>
                  <div className="heroticket">
                    <span>VAŠ BROJ / ВАШ НОМЕР</span>
                    <b>{current?.label || "A—001"}</b>
                    <div>
                      <span>Podgorica</span>
                      <Check size={17} />
                    </div>
                  </div>
                  <div className="herobottom">
                    <ShieldCheck size={15} /> Один паспорт. Одна запись. Честная
                    очередь.
                  </div>
                </section>
                <section className="card officecard">
                  <div className="cardtitle">
                    <h3>Отделение</h3>
                    <span className="pill green">
                      <span />{" "}
                      {serviceStates[queue?.service_status] || "Расписание открыто"}
                    </span>
                  </div>
                  <div className="officevisual">
                    <Building />
                  </div>
                  <h3>Подгорица</h3>
                  <p className="muted">
                    MUP · Отделение по работе с иностранцами
                  </p>
                  <div className="officedetail">
                    <MapPin size={17} />
                    <span>Podgorica, Crna Gora</span>
                  </div>
                  <div className="officedetail">
                    <Clock3 size={17} />
                    <span>
                      Приём документов <b>08:00 — 14:00</b>
                    </span>
                  </div>
                  <div className="officebottom">
                    <span>По номерам, без записи на время</span>
                    <ArrowRight size={16} />
                  </div>
                </section>
              </div>
              <div className="sectionhead">
                <h2>Очередь · {day ? dateLabel(day.date) : "сегодня"}</h2>
                <button onClick={() => setTab("queue")}>
                  Открыть очередь <ArrowRight size={15} />
                </button>
              </div>
              <div className="stats">
                <Stat
                  icon={<Users size={20} />}
                  label="Ожидают приёма"
                  value={counts.waiting}
                  note="в электронной очереди"
                />
                <Stat
                  icon={<Monitor size={20} />}
                  label="Сейчас на приёме"
                  value={counts.calling}
                  note="до 3 рабочих окон"
                />
                <Stat
                  icon={<CheckCheck size={20} />}
                  label="Уже обслужено"
                  value={counts.completed}
                  note="завершённых обращений"
                />
                <Stat
                  icon={<CalendarDays size={20} />}
                  label="Свободных мест"
                  value={day?.free ?? "—"}
                  note="из 100 на выбранный день"
                />
              </div>
              <div className="bottomgrid">
                <section className="card myrecord">
                  <div className="cardtitle">
                    <h3>Моя ближайшая запись</h3>
                    {current && (
                      <span className="pill green">
                        {current.episodes.some((e) => ["CALLED", "IN_SERVICE", "SKIPPED"].includes(e.state)) ? "Повторный приём" : states[current.state]}
                      </span>
                    )}
                  </div>
                  {current ? (
                    <TicketCard
                      ticket={current}
                      compact
                      onView={() => setTab("tickets")}
                    />
                  ) : (
                    <div className="emptyrecord">
                      <Icon>
                        <TicketIcon size={23} />
                      </Icon>
                      <div>
                        <b>Всё начинается с первой записи</b>
                        <p>Выберите дату и получите персональный номер.</p>
                      </div>
                      <button
                        className="secondary"
                        onClick={() => setTab("booking")}
                      >
                        Выбрать день <ArrowRight size={15} />
                      </button>
                    </div>
                  )}
                </section>
                <section className="card rulescard">
                  <div className="smalllabel">ПРОСТЫЕ ПРАВИЛА</div>
                  <h3>
                    Очередь, в которой
                    <br />
                    все на своём месте.
                  </h3>
                  <p>
                    <Check size={16} /> Один паспорт — одна запись на день
                  </p>
                  <p>
                    <Check size={16} /> Один сопровождающий без приёма
                  </p>
                  <p>
                    <Check size={16} /> Оригинал паспорта — у окна
                  </p>
                </section>
              </div>
            </>
          )}
          {tab === "booking" && (
            <BookingPage
              key={user?.id || "guest"}
              user={user}
              days={days}
              documents={documents}
              busy={busy}
              onLogin={() =>
                demoLogin("applicant").then(() => setTab("booking"))
              }
              act={act}
              tell={tell}
              onDone={() => setTab("tickets")}
              health={health}
            />
          )}
          {tab === "tickets" && (
            <>
              <PageHead
                eyebrow="ЛИЧНЫЙ КАБИНЕТ"
                title="Мои записи"
                description="Ваши талоны, история посещений и предложения."
              />
              {user?.role === "applicant" && <DocumentAccessPanel key={user.id} documents={documents} health={health} act={act} busy={busy} refresh={tickets} />}
              <div className="ticketlist">
                {!tickets.length && (
                  <Empty
                    icon={<TicketIcon />}
                    text="У вас пока нет записей"
                    action={() => setTab("booking")}
                    label="Записаться"
                  />
                )}
                {tickets.map((t) => (
                  <div key={t.id} className="card ticketdetail">
                    <TicketCard ticket={t} />
                    {t.state === "BOOKED" && (
                      <div className="ticketbuttons">
                        <select
                          aria-label="Новая дата"
                          defaultValue=""
                          onChange={(e) => {
                            if (
                              e.target.value &&
                              confirm("Перенести запись на выбранную дату?")
                            )
                              act(
                                () =>
                                  post(`/tickets/${t.id}/transfer`, {
                                    day_id: e.target.value,
                                  }),
                                "Запись перенесена",
                              ).catch(() => {});
                          }}
                        >
                          <option value="">Перенести на другой день</option>
                          {days
                            .filter((d) => d.date !== t.date && !d.closed)
                            .map((d) => (
                              <option key={d.id} value={d.id}>
                                {dateLabel(d.date)} · {d.free} мест
                              </option>
                            ))}
                        </select>
                        <button
                          className="textdanger"
                          onClick={() => {
                            if (confirm("Отменить запись и освободить место?"))
                              act(
                                () => post(`/tickets/${t.id}/cancel`),
                                "Запись отменена",
                              ).catch(() => {});
                          }}
                        >
                          Отменить запись
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              {waiting.length > 0 && (
                <>
                  <h2 className="subheading">Лист ожидания</h2>
                  {waiting.map((w) => (
                    <div className="card waitrow" key={w.id}>
                      <div>
                        <b>
                          {dateLabel(w.day_id.split(":").slice(1).join(":"))}
                        </b>
                        <p>
                          {w.state === "OFFERED"
                            ? "Для вас появилось место. Подтвердите до " +
                              new Date(w.expires_at).toLocaleTimeString("ru", {
                                hour: "2-digit",
                                minute: "2-digit",
                              })
                            : w.state === "WAITING"
                              ? "Ожидаем свободное место"
                              : w.state}
                        </p>
                      </div>
                      {w.state === "OFFERED" && (
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() =>
                            act(
                              () => post(`/waitlist/${w.id}/accept`),
                              "Место подтверждено",
                            ).catch(() => {})
                          }
                        >
                          Подтвердить
                        </button>
                      )}
                      {["WAITING", "OFFERED"].includes(w.state) && (
                        <button
                          className="secondary"
                          onClick={() =>
                            act(
                              () => post(`/waitlist/${w.id}/cancel`),
                              "Ожидание отменено",
                            ).catch(() => {})
                          }
                        >
                          Отказаться
                        </button>
                      )}
                    </div>
                  ))}
                </>
              )}
            </>
          )}
          {tab === "queue" && (
            <>
              <PageHead
                eyebrow="ОБНОВЛЯЕТСЯ КАЖДЫЕ 5 СЕКУНД"
                title="Живая очередь"
                description="Следите за вызовами. Номер талона закреплён за заявителем."
              />
              <div className="queuecontrols">
                <select
                  aria-label="День очереди"
                  value={dayId}
                  onChange={(e) => setDayId(e.target.value)}
                >
                  {days.map((d) => (
                    <option value={d.id} key={d.id}>
                      {dateLabel(d.date)}
                    </option>
                  ))}
                </select>
                <button
                  className="secondary"
                  onClick={() => setShowBoard(true)}
                >
                  <Monitor size={17} /> Полноэкранное табло
                </button>
              </div>
              <p className="muted">{serviceStates[queue?.service_status]}</p>
              <div className="windowgrid">
                {[1, 2, 3].map((w) => {
                  const t = queue?.calling?.find((t: any) => t.window === w);
                  return (
                    <div
                      className={"card windowcard " + (t ? "isactive" : "")}
                      key={w}
                    >
                      <span>ОКНО {w}</span>
                      <strong>{t?.label || "—"}</strong>
                      <p>
                        {t
                          ? t.state === "CALLED"
                            ? "Приглашаем к окну"
                            : "Идёт приём"
                          : "Ожидает заявителя"}
                      </p>
                    </div>
                  );
                })}
              </div>
              <section className="card">
                <div className="cardtitle">
                  <h3>Следующие номера</h3>
                  <span className="muted">
                    {queue?.waiting?.length || 0} ожидают
                  </span>
                </div>
                <div className="queuenumbers">
                  {queue?.waiting?.length ? (
                    queue.waiting.map((t: any) => (
                      <span key={t.number}>{t.label}</span>
                    ))
                  ) : (
                    <p className="muted">Сейчас никто не ожидает вызова</p>
                  )}
                </div>
              </section>
            </>
          )}
          {tab === "staff" && (
            <StaffPage
              dayId={dayId}
              setDayId={setDayId}
              days={days}
              tickets={staffTickets}
              busy={busy}
              act={act}
              tell={tell}
              role={user?.role || ""}
            />
          )}
          {tab === "admin" && (
            <AdminPage days={days} act={act} busy={busy} tell={tell} />
          )}
          <footer>
            <span>
              Red <b>·</b> Электронная очередь
            </span>
            <span className={"sync " + (online ? "" : "bad")}>
              <i />
              {online ? "Данные обновлены" : "Нет соединения"}
              {lastSync &&
                " · " +
                  lastSync.toLocaleTimeString("ru", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
            </span>
            <span>Podgorica, Crna Gora</span>
          </footer>
        </main>
      </div>
      {showNotices && (
        <div className="noticepanel">
          <div className="cardtitle">
            <h3>Уведомления</h3>
            <button
              className="iconbutton"
              onClick={() => setShowNotices(false)}
            >
              <X size={18} />
            </button>
          </div>
          {notifications.length ? (
            notifications.slice(0, 10).map((n) => (
              <div className="noticeitem" key={n.id}>
                <Bell size={17} />
                <div>
                  <b>
                    {(
                      {
                        BOOKED: "Запись подтверждена",
                        CALLED: "Подойдите к окну",
                        CANCELLED: "Запись отменена",
                        WAITLIST_OFFER: "Появилось свободное место",
                      } as any
                    )[n.kind] || n.kind}
                  </b>
                  <p>
                    {n.payload.label || n.payload.date}
                    {n.payload.window && " · Окно " + n.payload.window}
                  </p>
                </div>
              </div>
            ))
          ) : (
            <p className="muted">Новых уведомлений нет</p>
          )}
        </div>
      )}
      {toast && (
        <div role="status" className={"toast " + (toast.error ? "error" : "")}>
          <span>
            {toast.error ? <CircleHelp size={20} /> : <Check size={20} />}
          </span>
          {toast.text}
          <button onClick={() => setToast(null)}>
            <X size={17} />
          </button>
        </div>
      )}
    </div>
  );
}

function Logo() {
  return (
    <div className="logo">
      <span className="logomark">
        <i />
        <i />
        <i />
      </span>
      red<span className="logodot">.</span>
      <small>ЭЛЕКТРОННАЯ ОЧЕРЕДЬ</small>
    </div>
  );
}
function Building() {
  return (
    <svg viewBox="0 0 320 130" fill="none" aria-hidden="true">
      <path d="M0 111h320" stroke="#cbd8d4" />
      <path
        d="M73 110V51l87-28 87 28v59"
        fill="#f5f7f3"
        stroke="#a7bdb5"
        strokeWidth="1.3"
      />
      <path d="M62 52l98-34 98 34H62Z" fill="#e6eee7" stroke="#9eb7aa" />
      <path d="M91 61h138" stroke="#b5c8be" />
      <path
        d="M94 66h11v44H94zm34 0h11v44h-11zm53 0h11v44h-11zm34 0h11v44h-11z"
        fill="#d5e2d7"
        stroke="#a7bdb5"
      />
      <path d="M150 78h20v32h-20z" fill="#a6bfb0" />
      <path d="M84 111h153l9 7H76l8-7Z" fill="#d5e2d7" />
      <circle cx="160" cy="44" r="8" fill="#c0d3c2" />
      <path d="M160 20V3m0 0h22v11h-22" stroke="#a5b5a8" />
      <path d="M160 3h22v11h-22" fill="#d9c1aa" />
      <path
        d="M39 111V78m0 15c-26-6-19-36-3-40 21-5 32 26 3 40Z"
        fill="#c9d9b8"
        stroke="#adc29b"
      />
      <path
        d="M283 111V84m0 13c-23-4-20-30-4-36 19-6 32 23 4 36Z"
        fill="#c9d9b8"
        stroke="#adc29b"
      />
    </svg>
  );
}
function PageHead({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div className="pagehead">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
    </div>
  );
}
function Stat({ icon, label, value, note }: any) {
  return (
    <section className="card stat">
      <div className="stathead">
        <Icon>{icon}</Icon>
        <span>{label}</span>
      </div>
      <b>{value}</b>
      <p>{note}</p>
    </section>
  );
}
function Empty({ icon, text, action, label }: any) {
  return (
    <div className="card empty">
      <Icon>{icon}</Icon>
      <h3>{text}</h3>
      <button className="primary" onClick={action}>
        {label}
        <ArrowRight size={16} />
      </button>
    </div>
  );
}
function TicketCard({
  ticket: t,
  compact,
  onView,
}: {
  ticket: Ticket;
  compact?: boolean;
  onView?: () => void;
}) {
  return (
    <div className={"ticketcard " + (compact ? "compact" : "")}>
      <div className="ticketnumber">
        <span>ВАШ НОМЕР</span>
        <strong>{t.label}</strong>
      </div>
      <div className="ticketinfo">
        <span className="pill green">{states[t.state] || t.state}</span>
        <h3>Подача документов на ВНЖ</h3>
        <p>
          <CalendarDays size={15} /> {dateLabel(t.date)} <span>·</span>{" "}
          Подгорица
        </p>
        <small>
          {t.companion ? "С одним сопровождающим" : "Без сопровождающего"}
          {t.window && ` · Окно ${t.window}`}
        </small>
        {t.episodes?.some((e) => ["CALLED", "IN_SERVICE", "SKIPPED"].includes(e.state)) && (
          <p className="repeatnotice">Повторный приём: {t.episodes.filter((e) => ["CALLED", "IN_SERVICE", "SKIPPED"].includes(e.state)).map((e) =>
            `${states[e.state]}${e.window ? ` · Окно ${e.window}` : ""}`
          ).join("; ")}</p>
        )}
      </div>
      {!compact && (
        <div className="ticketqr">
          <QRCodeSVG value={"red:ticket:" + t.id} size={74} level="M" />
          <small>Для поиска у окна</small>
        </div>
      )}
      {compact && (
        <button
          className="iconbutton"
          aria-label="Открыть запись"
          onClick={onView}
        >
          <ArrowRight size={20} />
        </button>
      )}
    </div>
  );
}

function BookingPage({
  user,
  days,
  documents,
  busy,
  onLogin,
  act,
  tell,
  onDone,
  health,
}: any) {
  const [step, setStep] = useState(1),
    [check, setCheck] = useState<any>(null),
    [fields, setFields] = useState<Fields | null>(null);
  const [docId, setDocId] = useState(""),
    [dateId, setDateId] = useState(""),
    [companion, setCompanion] = useState(false);
  const [accessConflict, setAccessConflict] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (check && ["PENDING", "PROCESSING"].includes(check.status)) {
      const t = setInterval(
        () =>
          api(`/documents/checks/${check.id}`)
            .then((r) => {
              setCheck(r);
              if (r.fields) setFields(r.fields);
            })
            .catch(() => {}),
        2000,
      );
      return () => clearInterval(t);
    }
  }, [check?.id, check?.status]);
  async function demo() {
    try {
      const r = await act(() => post("/documents/demo"));
      setCheck(r);
      setFields(r.fields);
    } catch {}
  }
  async function upload(file?: File) {
    if (!file) return;
    const data = new FormData();
    data.append("file", file);
    try {
      const r = await act(() =>
        api("/documents/uploads", { method: "POST", body: data }),
      );
      setCheck(r);
      setFields(null);
    } catch {}
  }
  async function confirmFields() {
    if (!fields) return;
    try {
      const r = await act(() =>
        post(`/documents/checks/${check.id}/confirm`, fields),
      );
      setDocId(r.id);
      setAccessConflict(false);
      setStep(2);
    } catch (error: any) { setAccessConflict(error.message === "DOCUMENT_ACCESS_REVIEW_REQUIRED"); }
  }
  async function book(wait = false) {
    try {
      await act(
        () =>
          post(wait ? "/waitlist" : "/tickets", {
            document_id: docId,
            day_id: dateId,
            companion: companion ? 1 : 0,
          }),
        wait ? "Вы добавлены в лист ожидания" : "Запись подтверждена",
      );
      onDone();
    } catch {}
  }
  if (!user)
    return (
      <>
        <PageHead
          eyebrow="НАЧНЁМ С ПРОСТОГО"
          title="Запись на приём"
          description="Войдите, чтобы сохранить документ и получить именной талон."
        />
        <div className="card loginbox">
          <Icon>
            <Fingerprint size={28} />
          </Icon>
          <h2>Ваше место — только ваше</h2>
          <p>Паспортные данные используются для проверки повторных записей.</p>
          {health?.mode === "demo" ? (
            <>
              <button className="primary" onClick={onLogin}>
                Войти как заявитель <ArrowRight size={17} />
              </button>
              <small>Тестовый вход. Реальные SMS не подключены.</small>
            </>
          ) : (
            <PhoneLogin onSuccess={() => location.reload()} tell={tell} />
          )}
        </div>
      </>
    );
  const selected = days.find((d: Day) => d.id === dateId);
  return (
    <>
      <PageHead
        eyebrow="ИМЕННАЯ ЗАПИСЬ"
        title="Записаться на приём"
        description="Один документ. Несколько простых шагов. Ваш номер в очереди."
      />
      <div className="steps">
        {["Паспорт", "День посещения", "Подтверждение"].map((x, i) => (
          <div
            key={x}
            className={step === i + 1 ? "current" : step > i + 1 ? "done" : ""}
          >
            <span>{step > i + 1 ? <Check size={15} /> : i + 1}</span>
            {x}
            {i < 2 && <i />}
          </div>
        ))}
      </div>
      <div className="bookinglayout">
        <section className="card bookingform">
          {step === 1 && (
            <>
              <div className="formtitle">
                <Icon>
                  <ScanLine size={22} />
                </Icon>
                <div>
                  <h2>Подтвердите данные паспорта</h2>
                  <p>Фото помогает избежать ошибок и повторных записей.</p>
                </div>
              </div>
              {documents.length > 0 && (
                <>
                  <label>Сохранённый документ</label>
                  <div className="saveddocs">
                    {documents.map((d: Doc) => (
                      <button
                        key={d.id}
                        onClick={() => {
                          setDocId(d.id);
                          setStep(2);
                        }}
                      >
                        <FileCheck2 size={22} />
                        <span>
                          <b>{d.fields.name}</b>
                          <small>
                            {d.fields.country} · {d.fields.number}
                          </small>
                        </span>
                        <ArrowRight size={17} />
                      </button>
                    ))}
                  </div>
                  <div className="or">
                    <span>или загрузите другой документ</span>
                  </div>
                </>
              )}
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png"
                hidden
                onChange={(e) => upload(e.target.files?.[0])}
              />
              <button
                className="dropzone"
                disabled={busy}
                onClick={() => fileRef.current?.click()}
              >
                <UploadCloud size={32} />
                <b>Загрузите страницу с фотографией</b>
                <span>
                  JPG или PNG, до 10 МБ. Страница целиком, без бликов.
                </span>
                <em>Выбрать фотографию</em>
              </button>
              {health?.mode === "demo" && (
                <button className="samplebutton" disabled={busy} onClick={demo}>
                  <FileCheck2 size={17} /> Использовать синтетический тестовый
                  паспорт <ArrowRight size={15} />
                </button>
              )}
              {check && (
                <div className="checkresult">
                  <div className="checkstatus">
                    {["PENDING", "PROCESSING"].includes(check.status) ? (
                      <LoaderCircle className="spin" size={20} />
                    ) : (
                      <FileCheck2 size={20} />
                    )}
                    <b>
                      {(
                        {
                          PENDING: "В очереди на распознавание",
                          PROCESSING: "Читаем документ…",
                          PASSED: "Данные распознаны",
                          REVIEW_REQUIRED: "Нужен разбор сотрудником",
                          RETAKE: "Пожалуйста, переснимите документ",
                          FAILED: "Ошибка обработки",
                        } as any
                      )[check.status] || check.status}
                    </b>
                    {check.demo && <span className="pill">TEST</span>}
                  </div>
                  {check.issues?.length > 0 && (
                    <p className="muted">
                      {check.issues.join(" · ")}. Подлинность по фотографии не
                      подтверждается.
                    </p>
                  )}
                  {fields && check.status === "PASSED" && (
                    <>
                      <div className="fieldgrid">
                        {(
                          [
                            ["name", "ФИО латиницей"],
                            ["number", "Номер паспорта"],
                            ["country", "Страна выдачи · 3 буквы"],
                            ["birth_date", "Дата рождения"],
                            ["expiry_date", "Действителен до"],
                          ] as const
                        ).map(([key, label]) => (
                          <label
                            key={key}
                            className={key === "name" ? "full" : ""}
                          >
                            {label}
                            <input
                              type={key.includes("date") ? "date" : "text"}
                              value={fields[key]}
                              onChange={(e) =>
                                setFields({ ...fields, [key]: e.target.value })
                              }
                            />
                          </label>
                        ))}
                      </div>
                      <p className="privacyline">
                        <ShieldCheck size={15} /> Автопроверка сравнивает
                        данные. Оригинал проверят у окна.
                      </p>
                      <button
                        className="primary"
                        disabled={busy}
                        onClick={confirmFields}
                      >
                        Подтвердить данные <ArrowRight size={17} />
                      </button>
                      {accessConflict && <button className="secondary" disabled={busy} onClick={async () => {
                        try { await act(() => post("/document-requests", { kind: "RECOVER", upload_id: check.id }), "Запрос на восстановление отправлен"); onDone(); } catch {}
                      }}>Запросить восстановление доступа</button>}
                      <button className="secondary" disabled={busy} onClick={onDone}>Заменить паспорт или восстановить доступ</button>
                    </>
                  )}
                </div>
              )}
            </>
          )}
          {step === 2 && (
            <>
              <div className="formtitle">
                <Icon>
                  <CalendarDays size={22} />
                </Icon>
                <div>
                  <h2>Выберите день посещения</h2>
                  <p>Запись по номеру очереди, без назначенного часа.</p>
                </div>
              </div>
              <label>Услуга</label>
              <div className="servicechoice">
                <FileCheck2 size={21} />
                <div>
                  <b>Подача документов на ВНЖ</b>
                  <span>Podgorica · Отделение для иностранцев</span>
                </div>
                <Check size={18} />
              </div>
              <label>Доступные даты</label>
              <div className="dategrid">
                {days.map((d: Day) => (
                  <button
                    className={dateId === d.id ? "selected" : ""}
                    key={d.id}
                    disabled={d.closed}
                    onClick={() => setDateId(d.id)}
                  >
                    <small>{weekDay(d.date)}</small>
                    <b>{new Date(d.date + "T12:00").getDate()}</b>
                    <span>
                      {d.closed
                        ? "Закрыто"
                        : d.free
                          ? d.free + " мест"
                          : "Мест нет"}
                    </span>
                  </button>
                ))}
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={companion}
                  onChange={(e) => setCompanion(e.target.checked)}
                />
                <span>
                  <b>Со мной будет один сопровождающий</b>
                  <small>
                    Без подачи собственных документов и отдельного обслуживания.
                  </small>
                </span>
              </label>
              <div className="formactions">
                <button className="secondary" onClick={() => setStep(1)}>
                  <ArrowLeft size={16} /> Назад
                </button>
                <button
                  className="primary"
                  disabled={!dateId}
                  onClick={() => setStep(3)}
                >
                  Продолжить <ArrowRight size={17} />
                </button>
              </div>
            </>
          )}
          {step === 3 && (
            <>
              <div className="formtitle">
                <Icon>
                  <CheckCheck size={22} />
                </Icon>
                <div>
                  <h2>Всё готово к записи</h2>
                  <p>Проверьте детали перед подтверждением.</p>
                </div>
              </div>
              <div className="summaryrows">
                <div>
                  <span>Отделение</span>
                  <b>Podgorica</b>
                </div>
                <div>
                  <span>Услуга</span>
                  <b>Подача документов на ВНЖ</b>
                </div>
                <div>
                  <span>День</span>
                  <b>{selected && dateLabel(selected.date)}</b>
                </div>
                <div>
                  <span>Порядок приёма</span>
                  <b>По номеру, 08:00–14:00</b>
                </div>
                <div>
                  <span>Сопровождение</span>
                  <b>
                    {companion
                      ? "Один человек без обслуживания"
                      : "Без сопровождающего"}
                  </b>
                </div>
              </div>
              <div className="infobox">
                <ShieldCheck size={20} />
                <p>
                  Возьмите оригинал паспорта. Передать запись другому человеку
                  нельзя. Следите за вызовами в личном кабинете.
                </p>
              </div>
              {selected?.free === 0 && (
                <div className="infobox amber">
                  <Clock3 size={20} />
                  <p>
                    Все 100 мест уже заняты. Можно встать в лист ожидания — это
                    пока не подтверждённая запись.
                  </p>
                </div>
              )}
              <div className="formactions">
                <button className="secondary" onClick={() => setStep(2)}>
                  <ArrowLeft size={16} /> Назад
                </button>
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => book(selected?.free === 0)}
                >
                  {busy ? (
                    <LoaderCircle className="spin" size={17} />
                  ) : (
                    <Check size={17} />
                  )}{" "}
                  {selected?.free === 0
                    ? "Встать в лист ожидания"
                    : "Подтвердить запись"}
                </button>
              </div>
            </>
          )}
        </section>
        <aside className="bookingaside">
          <div className="asideicon">
            <ShieldCheck size={28} />
          </div>
          <h3>
            Ваше место
            <br />
            защищено правилами
          </h3>
          <p>Система проверяет повторные записи по реквизитам паспорта.</p>
          <ul>
            <li>
              <Check size={16} /> Один паспорт — одна запись на день
            </li>
            <li>
              <Check size={16} /> Не более 100 заявителей в день
            </li>
            <li>
              <Check size={16} /> Проверка оригинала только у окна
            </li>
          </ul>
          <div className="asidefoot">
            <Clock3 size={18} />
            <span>
              Часы приёма
              <br />
              <b>08:00 — 14:00</b>
            </span>
          </div>
        </aside>
      </div>
    </>
  );
}

function DemoDirectory() {
  return (
    <div className="portalpage">
      <section className="card portalcard">
        <Logo />
        <div className="eyebrow">ТЕСТОВЫЙ СТЕНД</div>
        <h1>Выберите отдельный портал</h1>
        <p>Роли разделены по адресам. Демонстрационные данные не являются официальной записью в MUP.</p>
        <div className="portallinks">
          <a className="primary" href="/">Портал заявителя</a>
          <a className="secondary" href="/staff">Портал сотрудника</a>
          <a className="secondary" href="/admin">Портал администратора</a>
        </div>
      </section>
    </div>
  );
}

function PortalLogin({ portal, health, user, onAuthenticated, demoLogin, tell }: any) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const isAdmin = portal === "admin";
  const expected = isAdmin ? "администратора" : "сотрудника";

  if (user) {
    return (
      <div className="portalpage">
        <section className="card portalcard">
          <Logo />
          <ShieldCheck size={34} />
          <h1>Нет доступа к этому порталу</h1>
          <p>Текущая учётная запись не имеет роли {expected}. Выйдите и войдите с подходящей учётной записью.</p>
          <button className="primary" onClick={() => post("/auth/logout").then(() => location.reload())}>
            <LogOut size={16} /> Выйти
          </button>
          <a href="/">Вернуться на портал заявителя</a>
        </section>
      </div>
    );
  }

  const login = () => post("/auth/staff", { username, password })
    .then((result: any) => {
      if (isAdmin && result.user.role !== "admin") {
        setCsrf(result.csrf);
        return post("/auth/logout").then(() => { throw new Error("FORBIDDEN"); });
      }
      onAuthenticated(result);
    })
    .catch((error: any) => tell(errors[error.message] || error.message, true));

  return (
    <div className="portalpage">
      <section className="card portalcard">
        <Logo />
        <div className="eyebrow">СЛУЖЕБНЫЙ ДОСТУП</div>
        <h1>{isAdmin ? "Портал администратора" : "Портал сотрудника"}</h1>
        <p>{isAdmin ? "Управление очередью, календарём и журналом действий." : "Рабочее место для вызова и обслуживания заявителей."}</p>
        <div className="phonelogin">
          <label>Логин<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
          <label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} onKeyDown={(event) => event.key === "Enter" && login()} /></label>
          <button className="primary" disabled={!username || !password} onClick={login}>Войти</button>
        </div>
        {health?.mode === "demo" && (
          <div className="demoportalentry">
            <span>Только для демонстрации</span>
            <button className="secondary" onClick={demoLogin}>Открыть демонстрационный профиль</button>
          </div>
        )}
        <a href="/">Вернуться на портал заявителя</a>
      </section>
    </div>
  );
}

function PortalMismatch({ user }: { user: User }) {
  return (
    <div className="portalpage">
      <section className="card portalcard">
        <Logo />
        <h1>Открыт служебный профиль</h1>
        <p>{user.name}, эта учётная запись предназначена для служебного портала.</p>
        <a className="primary" href={user.role === "admin" ? "/admin" : "/staff"}>Перейти в служебный портал</a>
        <button className="secondary" onClick={() => post("/auth/logout").then(() => location.reload())}>Выйти</button>
      </section>
    </div>
  );
}

function PhoneLogin({ onSuccess, tell }: any) {
  const [phone, setPhone] = useState(""),
    [challenge, setChallenge] = useState(""),
    [code, setCode] = useState("");
  return (
    <div className="phonelogin">
      <label>
        Телефон
        <input
          placeholder="+382…"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
      </label>
      {challenge && (
        <label>
          Код из SMS
          <input value={code} onChange={(e) => setCode(e.target.value)} />
        </label>
      )}
      <button
        className="primary"
        onClick={() => {
          (challenge
            ? post("/auth/otp/verify", { challenge_id: challenge, code })
            : post("/auth/otp/request", { phone })
          )
            .then((r) => {
              if (r.user) {
                setCsrf(r.csrf);
                onSuccess();
              } else setChallenge(r.challenge_id);
            })
            .catch((e: any) => tell(errors[e.message] || e.message, true));
        }}
      >
        {challenge ? "Войти" : "Получить код"}
      </button>
    </div>
  );
}

function StaffPage({
  dayId,
  setDayId,
  days,
  tickets,
  busy,
  act,
  tell,
  role,
}: any) {
  const [windowId, setWindowId] = useState(1),
    [doc, setDoc] = useState<any>(null),
    [reason, setReason] = useState("");
  const [summary, setSummary] = useState<WorkdaySummary | null>(null);
  const [summaryError, setSummaryError] = useState(false);
  const [receivedAt, setReceivedAt] = useState(0);
  const [clock, setClock] = useState(Date.now());
  const workItems: WorkItem[] = tickets.flatMap((t: Ticket) => [
    ...(["CALLED", "IN_SERVICE"].includes(t.state) ? [t] : []),
    ...t.episodes.filter((e) => ["CALLED", "IN_SERVICE"].includes(e.state)).map((e) => ({
      ...t, ...e, id: t.id, number: t.number, episode_id: e.id, repeat: true,
    })),
  ]);
  const current = workItems.find((t) => t.window === windowId);
  const selectedDate = days.find((d: Day) => d.id === dayId)?.date;
  useEffect(() => { setDoc(null); setReason(""); }, [current?.id, current?.episode_id]);
  useEffect(() => {
    let active = true;
    api<WorkdaySummary>(`/staff/days/${encodeURIComponent(dayId)}/summary`).then((value) => {
      if (active) { setSummary(value); setReceivedAt(Date.now()); setSummaryError(false); }
    }).catch(() => { if (active) setSummaryError(true); });
    return () => { active = false; };
  }, [dayId, tickets]);
  useEffect(() => {
    const id = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const report = summary?.id === dayId && !summaryError ? summary : null;
  const serviceOpen = !!report && ["OPEN", "DEMO_OPEN"].includes(report.service_status);
  const waitSeconds = current?.last_called_at && report ? Math.max(0, Math.ceil((
    Date.parse(current.last_called_at) + report.recall_interval_seconds * 1000 -
    (Date.parse(report.server_now) + Math.max(0, clock - receivedAt))
  ) / 1000)) : 0;
  async function action(t: WorkItem, a: string) {
    try {
      await act(
        () =>
          post(t.episode_id ? `/staff/episodes/${t.episode_id}/${a}` : `/staff/tickets/${t.id}/${a}`,
            { version: t.version, reason }),
        "Состояние обновлено",
      );
      setReason("");
    } catch {}
  }
  return (
    <>
      <PageHead
        eyebrow="РАБОЧЕЕ МЕСТО"
        title="Приём заявителей"
        description="Проверяйте оригинал у окна. Каждый талон принадлежит одному человеку."
      />
      <div className="queuecontrols">
        <select
          aria-label="День приёма"
          value={dayId}
          onChange={(e) => setDayId(e.target.value)}
        >
          {days.map((d: Day) => (
            <option key={d.id} value={d.id}>
              {dateLabel(d.date)}
            </option>
          ))}
        </select>
        <div className="windowtabs">
          {[1, 2, 3].map((w) => (
            <button
              className={w === windowId ? "active" : ""}
              onClick={() => setWindowId(w)}
              key={w}
            >
              Окно {w}
            </button>
          ))}
        </div>
      </div>
      <section className="card workdayreport" aria-label="Сводка рабочего дня">
        <h3>{report ? serviceStates[report.service_status] : "Сводка дня недоступна"}</h3>
        {report ? <>
          <p>Не обслужено: <b>{report.unresolved.length}</b> · На приёме: <b>{report.in_service.length}</b> · Завершено: <b>{report.counts.COMPLETED}</b></p>
          <p>Повторные приёмы: ожидают решения <b>{report.repeat_unresolved.length}</b> · На приёме <b>{report.repeat_in_service.length}</b> · Завершено <b>{report.repeat_completed}</b></p>
          {!report.hours_enforced && <p className="muted">Деморежим: ограничение часов 08:00–14:00 отключено. Прошедшие дни закрыты для новых вызовов.</p>}
          {!serviceOpen && <p>Новые приёмы остановлены. Уже начатый приём можно завершить. Оставшиеся талоны сохраняются для разбора и переноса.</p>}
        </> : <p>Не удалось получить актуальное состояние. Новые вызовы временно недоступны.</p>}
      </section>
      <div className="staffgrid">
        <section className="card staffcurrent">
          <div className="cardtitle">
            <h3>Окно {windowId}</h3>
            <span className="pill green">
              {current ? states[current.state] : serviceOpen ? "Готово к работе" : "Вызовы остановлены"}
            </span>
          </div>
          {current ? (
            <>
              <div className="bigcall">{current.label}</div>
              <p className="callstate">{states[current.state]}</p>
              {current.repeat && <p className="repeatnotice">Повторный приём по исходному талону. Сверьте оригинал заново.</p>}
              <div className="staffbuttons">
                <button
                  className="secondary"
                  onClick={() =>
                    api(`/staff/tickets/${current.id}/document`)
                      .then(setDoc)
                      .catch((e: any) => tell(e.message, true))
                  }
                >
                  <FileCheck2 size={17} /> Данные паспорта
                </button>
                {current.state === "CALLED" && (
                  <button
                    className="secondary"
                    disabled={busy || !serviceOpen || current.call_attempts >= 2 || waitSeconds > 0}
                    onClick={() => action(current, "recall")}
                  >
                    <Bell size={17} /> Повторить вызов
                  </button>
                )}
              </div>
              {current.state === "CALLED" && <p className="muted">
                Вызов {current.call_attempts} из 2.
                {waitSeconds > 0 ? ` Пауза: ${waitSeconds} сек.` : " Пауза завершена."}
              </p>}
              {doc && (
                <div className="documentdetails">
                  {Object.entries(doc.fields).map(([k, v]) => (
                    <div key={k}>
                      <small>
                        {
                          (
                            {
                              name: "ФИО",
                              number: "Номер",
                              country: "Страна",
                              birth_date: "Дата рождения",
                              expiry_date: "Действителен до",
                            } as any
                          )[k]
                        }
                      </small>
                      <b>{String(v)}</b>
                    </div>
                  ))}
                </div>
              )}
              {doc?.changed_since_booking && <p className="repeatnotice">Паспорт заменён после записи. Проверьте действующий оригинал по указанным выше реквизитам.</p>}
              {current.state === "CALLED" && (
                <>
                  <button
                    className={
                      "originalcheck " +
                      (current.original_match ? "checked" : "")
                    }
                    disabled={busy || !serviceOpen || current.original_match}
                    onClick={() => action(current, "verify")}
                  >
                    <span>
                      {current.original_match ? <Check size={18} /> : null}
                    </span>
                    Оригинал и личность заявителя совпадают
                  </button>
                  <div className="staffbuttons">
                    <button
                      className="primary"
                      disabled={busy || !serviceOpen || !current.original_match}
                      onClick={() => action(current, "start")}
                    >
                      Начать приём <ArrowRight size={17} />
                    </button>
                    <button
                      className="secondary"
                      disabled={busy || current.call_attempts < 2 || waitSeconds > 0}
                      onClick={() => action(current, "skip")}
                    >
                      Не подошёл
                    </button>
                  </div>
                  <input
                    placeholder={current.repeat ? "Причина отмены или несоответствия" : "Причина несоответствия (для отказа)"}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                  <button
                    className="textdanger"
                    disabled={!reason || busy}
                    onClick={() => action(current, "reject")}
                  >
                    Документ не совпадает с записью
                  </button>
                  {current.repeat && <button className="secondary" disabled={busy || !reason.trim()}
                    onClick={() => action(current, "cancel")}>Отменить повторный приём</button>}
                </>
              )}
              {current.state === "IN_SERVICE" && (
                <button
                  className="primary wide"
                  disabled={busy}
                  onClick={() => action(current, "complete")}
                >
                  <CheckCheck size={18} /> Завершить приём
                </button>
              )}
            </>
          ) : (
            <div className="emptywindow">
              <Icon>
                <Monitor size={30} />
              </Icon>
              <h2>Можно пригласить следующего</h2>
              <p>Система выберет первый ожидающий талон.</p>
              <button
                className="primary"
                disabled={busy || !serviceOpen}
                onClick={() =>
                  act(() =>
                    post("/staff/call-next", {
                      day_id: dayId,
                      window: windowId,
                    }),
                  )
                    .then((r: any) => {
                      if (r?.empty) tell("Ожидающих заявителей нет");
                    })
                    .catch(() => {})
                }
              >
                Следующий заявитель <ArrowRight size={18} />
              </button>
            </div>
          )}
        </section>
        <section className="card stafflist">
          <div className="cardtitle">
            <h3>Очередь · {selectedDate && dateLabel(selectedDate)}</h3>
            <span>{tickets.length} записей</span>
          </div>
          {!tickets.length && (
            <p className="muted">Талонов на эту дату пока нет</p>
          )}
          {tickets.map((t: Ticket) => (
            <div className="staffrow" key={t.id}>
              <b>{t.label}</b>
              <span className={"statusdot s-" + t.state} />
              <span>{states[t.state]}</span>
              {t.state === "COMPLETED" && <>
                {role === "admin" && <button disabled={busy || !serviceOpen || t.episodes.some((e: Episode) => ["CALLED", "IN_SERVICE", "SKIPPED"].includes(e.state))}
                  onClick={() => {
                    const reason = prompt(`Причина повторного приёма ${t.label} у окна ${windowId}`);
                    if (reason?.trim()) act(() => post(`/staff/tickets/${t.id}/episodes`, {
                      version: t.version, window: windowId, reason,
                    }), "Повторный приём открыт").catch(() => {});
                  }}>Повторный приём {t.label}</button>}
                <EpisodeHistory ticket={t} act={act} busy={busy} serviceOpen={serviceOpen} windowId={windowId} />
              </>}
              {t.state === "SKIPPED" && (
                <div>
                  <input
                    placeholder="Причина"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                  <button
                    disabled={!reason.trim() || busy || !serviceOpen}
                    onClick={() => action(t, "return")}
                  >
                    Вернуть
                  </button>
                  <button
                    disabled={!reason || busy}
                    onClick={() => action(t, "no-show")}
                  >
                    Неявка
                  </button>
                </div>
              )}
              {["BOOKED", "CALLED", "SKIPPED"].includes(t.state) && (
                <StaffTransfer key={`${t.id}:${dayId}`} ticket={t} days={days} act={act} busy={busy} />
              )}
              {role === "admin" &&
                ["NO_SHOW", "REJECTED"].includes(t.state) && (
                  <button
                    onClick={() => {
                      const r = prompt(
                        "Причина освобождения неиспользованного места",
                      );
                      if (r)
                        act(
                          () =>
                            post(`/staff/tickets/${t.id}/release`, {
                              version: t.version,
                              reason: r,
                            }),
                          "Место освобождено",
                        ).catch(() => {});
                    }}
                  >
                    Освободить место
                  </button>
                )}
            </div>
          ))}
        </section>
      </div>
    </>
  );
}

function EpisodeHistory({ ticket, act, busy, serviceOpen, windowId }: {
  ticket: Ticket; act: any; busy: boolean; serviceOpen: boolean; windowId: number;
}) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<any>(null);
  const [error, setError] = useState(false);
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (!open) return;
    let active = true;
    api(`/staff/tickets/${ticket.id}/episodes`).then((r) => { if (active) { setHistory(r); setError(false); } })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [ticket.id, ticket.version, open]);
  const when = (value: string | null) => value ? new Date(value).toLocaleString("ru", { timeZone: "Europe/Podgorica" }) : "—";
  async function actOn(e: Episode, action: string) {
    try { await act(() => post(`/staff/episodes/${e.id}/${action}`, { version: e.version, reason, window: windowId }), "Повторный приём обновлён"); setReason(""); } catch {}
  }
  return <details className="episodehistory" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>История приёмов {ticket.label} · повторных: {ticket.episodes.length}</summary>
    <p>Первичный приём: {states[ticket.state]} · {when(ticket.started_at)} — {when(ticket.completed_at)}</p>
    {error ? <p role="alert">Не удалось получить историю. Закройте и откройте её снова.</p> : !history ? <p>Загрузка истории…</p> : history.episodes.map((e: Episode & { reason: string }) => <section key={e.id} className="episodeentry">
      <b>Повторный приём №{e.number}: {states[e.state]}</b>
      <p>Причина: {e.reason}</p>
      <p>Открыт: {when(e.created_at)} · Начат: {when(e.started_at)} · Завершён: {when(e.completed_at)}</p>
      {e.state === "SKIPPED" && <div>
        <input aria-label={`Причина решения по повторному приёму ${ticket.label}`} placeholder="Причина решения" maxLength={300} value={reason} onChange={(event) => setReason(event.target.value)} />
        <button disabled={busy || !reason.trim() || !serviceOpen} onClick={() => actOn(e, "return")}>Вернуть к окну {windowId}</button>
        <button disabled={busy || !reason.trim()} onClick={() => actOn(e, "no-show")}>Неявка повторно</button>
      </div>}
      {["CALLED", "SKIPPED"].includes(e.state) && <div>
        <input aria-label={`Причина отмены повторного приёма ${ticket.label}`} placeholder="Причина отмены" maxLength={300} value={reason} onChange={(event) => setReason(event.target.value)} />
        <button disabled={busy || !reason.trim()} onClick={() => actOn(e, "cancel")}>Отменить повтор</button>
      </div>}
    </section>)}
  </details>;
}

function StaffTransfer({ ticket, days, act, busy }: { ticket: Ticket; days: Day[]; act: any; busy: boolean }) {
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const available = days.filter((d) => d.date > ticket.date && !d.closed);
  return <details className="stafftransfer">
    <summary>Перенести {ticket.label}</summary>
    <label>Новый день
      <select aria-label={`Новый день для ${ticket.label}`} value={target} onChange={(e) => setTarget(e.target.value)}>
        <option value="">Выберите дату</option>
        {available.map((d) => <option key={d.id} value={d.id}>{dateLabel(d.date)} · свободно {d.free}</option>)}
      </select>
    </label>
    <label>Причина переноса
      <input aria-label={`Причина переноса ${ticket.label}`} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
    </label>
    <p className="muted">При отсутствии места исходный талон сохранится. Новый номер назначается в конце очереди выбранного дня.</p>
    <button disabled={busy || !target || !reason.trim()} onClick={() => act(
      () => post(`/tickets/${ticket.id}/transfer`, { day_id: target, reason }), "Талон перенесён",
    ).catch(() => {})}>Подтвердить перенос</button>
  </details>;
}

function AdminPage({ days, act, busy, tell }: any) {
  const [audit, setAudit] = useState<any[]>([]),
    [reviews, setReviews] = useState<any[]>([]);
  const [accessRequests, setAccessRequests] = useState<any[]>([]);
  useEffect(() => {
    api("/admin/audit").then(setAudit);
    api("/admin/reviews").then(setReviews);
    api("/admin/document-requests").then(setAccessRequests).catch(() => {});
  }, [days]);
  return (
    <>
      <PageHead
        eyebrow="АДМИНИСТРИРОВАНИЕ"
        title="Управление очередью"
        description="Общая вместимость отделения — 100 мест. Все исключения сохраняются в журнале."
      />
      <div className="adminstats">
        <Stat
          icon={<ShieldCheck size={20} />}
          label="Лимит отделения"
          value="100"
          note="на все три окна вместе"
        />
        <Stat
          icon={<Clock3 size={20} />}
          label="Часы приёма"
          value="08—14"
          note="Europe/Podgorica"
        />
        <Stat
          icon={<FileCheck2 size={20} />}
          label="На разборе"
          value={reviews.length}
          note="автоматические проверки"
        />
      </div>
      <section className="card">
        <div className="cardtitle">
          <h3>Календарь приёма</h3>
          <span className="muted">{days.length} дней</span>
        </div>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Дата</th>
                <th>Свободно</th>
                <th>В очереди</th>
                <th>Удержано</th>
                <th>Использовано</th>
                <th>Приём</th>
              </tr>
            </thead>
            <tbody>
              {days.map((d: Day) => (
                <tr key={d.id}>
                  <td>
                    <b>{dateLabel(d.date)}</b>
                  </td>
                  <td>
                    <span className="freecount">{d.free}</span>
                  </td>
                  <td>{d.allocated}</td>
                  <td>{d.held}</td>
                  <td>{d.consumed}</td>
                  <td>
                    <button
                      disabled={busy}
                      className={"pill " + (d.closed ? "" : "green")}
                      onClick={() => {
                        const reason = prompt("Причина изменения режима приёма");
                        if (!reason || reason.trim().length < 3) return;
                        act(
                          () =>
                            post(`/admin/days/${encodeURIComponent(d.id)}`, {
                              closed: !d.closed,
                              reason,
                            }),
                          d.closed ? "День открыт" : "Приём закрыт",
                        ).catch(() => {});
                      }}
                    >
                      {d.closed ? "Открыть" : "Закрыть"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {reviews.length > 0 && (
        <section className="card reviewsection">
          <div className="cardtitle">
            <h3>Разбор документов</h3>
          </div>
          {reviews.map((r) => (
            <ReviewForm key={r.id} review={r} act={act} />
          ))}
        </section>
      )}
      {accessRequests.length > 0 && <section className="card accesspanel"><h2>Доступ и смена паспорта</h2>
        {accessRequests.map((request) => <DocumentRequestReview key={request.id} request={request} act={act} busy={busy} />)}
      </section>}
      <section className="card auditcard">
        <div className="cardtitle">
          <h3>Журнал действий</h3>
          <span className="muted">Последние 100 событий</span>
        </div>
        {audit.length ? (
          <div className="auditrows">
            {audit.map((a) => (
              <div key={a.id}>
                <span className="auditicon">
                  <ShieldCheck size={15} />
                </span>
                <div>
                  <b>{a.action}</b>
                  <small>
                    {a.target.slice(0, 12)}
                    {a.reason && " · " + a.reason}
                  </small>
                </div>
                <time>
                  {new Date(a.at).toLocaleTimeString("ru", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </time>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">
            Здесь появятся действия сотрудников и изменения записей
          </p>
        )}
      </section>
    </>
  );
}
function ReviewForm({ review, act }: any) {
  const [f, setF] = useState<Fields>(
      review.fields?.number
        ? review.fields
        : {
            country: "",
            number: "",
            name: "",
            birth_date: "",
            expiry_date: "",
          },
    ),
    [reason, setReason] = useState("");
  return (
    <div className="reviewform">
      <b>Документ {review.id.slice(0, 8)}</b>
      <p className="muted">
        Сверьте фото и заполните поля. Это ручной разбор, не автоматическое
        подтверждение подлинности.
      </p>
      <a
        href={`/api/v1/documents/uploads/${review.id}/image`}
        target="_blank"
        rel="noreferrer"
      >
        Открыть фото в отдельной вкладке <ExternalLink size={13} />
      </a>
      <div className="fieldgrid">
        {Object.entries(f).map(([k, v]) => (
          <label key={k}>
            {k}
            <input
              type={k.includes("date") ? "date" : "text"}
              value={v}
              onChange={(e) => setF({ ...f, [k]: e.target.value })}
            />
          </label>
        ))}
      </div>
      <input
        placeholder="Причина ручного решения"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <button
        className="secondary"
        disabled={reason.length < 5}
        onClick={() =>
          act(
            () => post(`/admin/reviews/${review.id}`, { fields: f, reason }),
            "Результат проверки сохранён",
          ).catch(() => {})
        }
      >
        Подтвердить результат разбора
      </button>
    </div>
  );
}

installTranslator();
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
