import { Activity, Bot, Database, FileText, LayoutDashboard, Settings2 } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";
import { useMeta } from "../../api/hooks";
import { useI18n } from "../../lib/i18n";
import { useSettings } from "../../lib/settings";
import { cx } from "../../lib/ui-utils";
import { SourceIcon, SourcePicker } from "../SourcePicker";

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-white">
        <svg viewBox="0 0 24 24" className="h-4.5 w-4.5" fill="none" aria-hidden>
          <path d="M5 18V9m5 9V5m5 13v-6m4 6V8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
      </div>
      <div className="leading-none">
        <div className="font-display text-[15px] font-semibold tracking-tight">BizNext</div>
        <div className="mt-0.5 text-[11px] text-muted">Agent</div>
      </div>
    </div>
  );
}

export function AppShell() {
  const { t, lang, setLang } = useI18n();
  const { devMode, setDevMode, adminToken, setAdminToken } = useSettings();
  const meta = useMeta();
  const [settingsOpen, setSettingsOpen] = useState(false);

  const nav = [
    { to: "/", label: t("nav.dashboard"), icon: LayoutDashboard, end: true },
    { to: "/agent", label: t("nav.agent"), icon: Bot, end: false },
    { to: "/reports", label: t("nav.reports"), icon: FileText, end: false },
    { to: "/data-sources", label: t("nav.sources"), icon: Database, end: false },
  ];

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-paper px-4 py-5 md:flex">
        <Logo />
        <nav className="mt-8 flex flex-col gap-0.5" aria-label="Main">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                  isActive ? "bg-surface font-medium text-ink shadow-[var(--shadow-card)] ring-1 ring-line" : "text-ink-soft hover:bg-sunken hover:text-ink",
                )
              }
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
            </NavLink>
          ))}
          {devMode && (
            <>
              <div className="eyebrow mt-6 mb-1 px-3">{t("nav.developer")}</div>
              <NavLink
                to="/admin/traces"
                className={({ isActive }) =>
                  cx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm", isActive ? "bg-surface font-medium ring-1 ring-line" : "text-ink-soft hover:bg-sunken")
                }
              >
                <Activity className="h-4 w-4" aria-hidden /> {t("nav.traces")}
              </NavLink>
            </>
          )}
        </nav>
        <div className="mt-auto space-y-3">
          {meta.data && (
            <div className="rounded-lg border border-line bg-surface px-3 py-2.5">
              <div className="text-[13px] font-medium text-ink">{meta.data.company.name}</div>
              <div className="text-xs text-muted">{meta.data.company.industry}</div>
              <div className="mt-2 flex items-center gap-1.5 border-t border-line pt-2 text-[11px] text-muted">
                <SourceIcon type={meta.data.source.type} className="h-3 w-3" />
                <span className="truncate">{meta.data.source.name}</span>
              </div>
            </div>
          )}
          <div className="text-[11px] leading-relaxed text-faint">
            {meta.data ? `v${meta.data.version} · ${meta.data.llm.provider} · ${meta.data.llm.model}` : ""}
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-paper/85 px-4 backdrop-blur md:px-8">
          <div className="md:hidden">
            <Logo />
          </div>
          <nav className="flex min-w-0 flex-1 gap-1 overflow-x-auto md:hidden" aria-label="Main mobile">
            {nav.map(({ to, label, end }) => (
              <NavLink key={to} to={to} end={end} className={({ isActive }) => cx("whitespace-nowrap rounded-md px-2 py-1 text-xs", isActive ? "bg-surface font-medium" : "text-muted")}>
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto shrink-0">
            <SourcePicker />
          </div>
          <div
            className="hidden shrink-0 items-center gap-2 rounded-full border border-warning/30 bg-warning-soft px-3 py-1 text-[11px] font-semibold tracking-wide text-warning xl:inline-flex"
            title={t("header.demoHint")}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-warning" />
            {t("header.demo")}
          </div>
          <div className="flex shrink-0 rounded-lg border border-line bg-surface p-0.5 text-xs" role="group" aria-label="Language">
            {(["ru", "en"] as const).map((code) => (
              <button
                key={code}
                type="button"
                onClick={() => setLang(code)}
                className={cx("rounded-md px-2 py-1 font-medium uppercase", lang === code ? "bg-ink text-white" : "text-muted hover:text-ink")}
                aria-pressed={lang === code}
              >
                {code}
              </button>
            ))}
          </div>
          <div className="relative shrink-0">
            <button
              type="button"
              onClick={() => setSettingsOpen((v) => !v)}
              className={cx("grid h-8 w-8 place-items-center rounded-lg border border-line bg-surface text-muted hover:text-ink", devMode && "text-accent")}
              aria-label={t("header.devMode")}
              aria-expanded={settingsOpen}
            >
              <Settings2 className="h-4 w-4" aria-hidden />
            </button>
            {settingsOpen && (
              <div className="absolute right-0 top-10 w-72 rounded-xl border border-line bg-surface p-4 shadow-[var(--shadow-float)]">
                <label className="flex items-center justify-between gap-3 text-sm">
                  <span>{t("header.devMode")}</span>
                  <input type="checkbox" checked={devMode} onChange={(e) => setDevMode(e.target.checked)} className="h-4 w-4 accent-[var(--color-accent)]" />
                </label>
                {devMode && meta.data?.features.admin_token_required && (
                  <label className="mt-3 block text-xs text-muted">
                    {t("header.adminToken")}
                    <input
                      type="password"
                      value={adminToken}
                      onChange={(e) => setAdminToken(e.target.value)}
                      className="mt-1 w-full rounded-md border border-line bg-paper px-2 py-1.5 text-sm text-ink"
                      autoComplete="off"
                    />
                  </label>
                )}
              </div>
            )}
          </div>
        </header>
        <main className="flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
