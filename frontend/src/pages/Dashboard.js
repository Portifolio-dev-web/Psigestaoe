import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { addDays, format, startOfDay } from "date-fns";
import { ptBR } from "date-fns/locale";
import api from "@/lib/api";
import { motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Users, CalendarCheck, FileText, Clock, UserPlus, ArrowRight, Activity, CalendarDays,
} from "lucide-react";
import { formatDateOnly } from "@/lib/datetime";
import { STATUS, evolutionPath, formatDateParam, formatTimeRange, isNoShow } from "@/components/agenda/agendaUtils";
import SecurityNudge from "@/components/SecurityNudge";
import PendingEvolutions from "@/components/PendingEvolutions";

// Correção (varredura de UI, defeito 1): antes `new Date(s).toLocaleDateString()`,
// que mostrava o nascimento UM DIA ANTES no Brasil (ver lib/datetime.js).
const fmtDate = formatDateOnly;
const fmtDateTime = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

// Terminologia (UX): "prontuário" é o do paciente; cada registro de sessão é
// uma EVOLUÇÃO. O feed dizia "Prontuário criado" para cada evolução.
const FEED_ACTION = { criado: "Evolução registrada", atualizado: "Evolução editada" };

// UX: um cartão com destino vira link (antes nenhum cartão levava a lugar algum).
function StatCard({ icon: Icon, label, value, tint, testid, to }) {
  const content = (
    <Card className={`h-full border-slate-200 p-5 shadow-sm ${to ? "transition-transform duration-200 hover:-translate-y-[1px] hover:shadow-md" : ""}`}
      data-testid={testid}>
      <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${tint}`}>
        <Icon className="h-5 w-5" strokeWidth={1.7} aria-hidden="true" />
      </div>
      <p className="mt-4 font-head text-3xl font-extrabold text-[#0F172A]">{value}</p>
      <p className="mt-1 text-sm text-slate-500">{label}</p>
    </Card>
  );
  return to ? (
    <Link to={to} className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
      aria-label={`${label}: ${value}`}>
      {content}
    </Link>
  ) : content;
}

// ---------------------------------------------------------------------------
// UX (painel "Hoje"): o que o profissional mais precisa ao abrir o sistema é
// saber quem atende hoje. Antes o painel mostrava só totais.
// Cada sessão do dia mostra horário, paciente e status, e a ação certa:
//   - já aconteceu e não tem evolução -> "Registrar evolução" (editor aberto
//     com a data e a hora da sessão, ligado a ela);
//   - já tem evolução               -> "Ver evolução";
//   - ainda vai acontecer           -> "Abrir ficha".
// "Hoje" é o dia no fuso do navegador (o servidor recebe o intervalo em UTC).
// ---------------------------------------------------------------------------
const STARTED_MARGIN_MS = 10 * 60 * 1000; // 10 min antes do início já vale registrar
const capitalizeFirst = (text) => text.charAt(0).toUpperCase() + text.slice(1);

function TodaySessions() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState(null);
  const today = new Date();

  useEffect(() => {
    const start = startOfDay(new Date());
    const params = new URLSearchParams({ start: start.toISOString(), end: addDays(start, 1).toISOString(), limit: "100" });
    api.get(`/sessions?${params}`)
      .then(({ data }) => setSessions(data))
      .catch(() => setSessions([]));
  }, []);

  const action = (s) => {
    const name = s.patient_name || s.title;
    // Fluxo clínico (C4): falta e cancelada não pedem evolução (só o selo do status).
    if (isNoShow(s.status) || !s.patient_id) return null;
    if (s.record_id) {
      return (
        <Button variant="outline" size="sm" className="border-slate-300" onClick={() => navigate(evolutionPath(s))}
          aria-label={`Ver evolução da sessão de ${name}`}>
          Ver evolução
        </Button>
      );
    }
    if (new Date(s.start).getTime() <= Date.now() + STARTED_MARGIN_MS) {
      return (
        <Button size="sm" className="bg-[#1E3A8A] hover:bg-[#0F2C59]" onClick={() => navigate(evolutionPath(s))}
          aria-label={`Registrar evolução da sessão de ${name}`} data-testid={`today-register-${s.id}`}>
          Registrar evolução
        </Button>
      );
    }
    return (
      <Button variant="ghost" size="sm" className="text-[#1E3A8A]" onClick={() => navigate(`/pacientes/${s.patient_id}`)}
        aria-label={`Abrir a ficha de ${name}`}>
        Abrir ficha
      </Button>
    );
  };

  return (
    <Card className="border-slate-200 p-6 shadow-sm" data-testid="today-sessions">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <CalendarDays className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Hoje</h2>
          {/* Só a primeira letra maiúscula ("Sexta-feira, 2 de outubro"); a classe
              `capitalize` deixava "2 De Outubro". */}
          <span className="text-sm text-slate-500">{capitalizeFirst(format(today, "EEEE, d 'de' MMMM", { locale: ptBR }))}</span>
        </div>
        <Link to={`/agenda?visao=dia&data=${formatDateParam(today)}`} className="text-sm font-medium text-[#1E3A8A] hover:underline">
          Abrir agenda do dia
        </Link>
      </div>

      {sessions === null ? (
        <div className="mt-4 space-y-2">{[0, 1].map((i) => <Skeleton key={i} className="h-12" />)}</div>
      ) : sessions.length === 0 ? (
        <div className="mt-4 flex flex-col items-center gap-2 py-6 text-center">
          <p className="text-sm text-slate-500">Nenhuma sessão marcada para hoje.</p>
          <Button variant="outline" size="sm" className="border-slate-300" onClick={() => navigate("/agenda")}>
            Ir para a agenda
          </Button>
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-slate-100">
          {sessions.map((s) => {
            const cancelled = s.status === "cancelada";
            // Com evolução registrada o selo diz isso (salvo se a sessão foi cancelada depois).
            const withRecord = Boolean(s.record_id) && !cancelled;
            const status = withRecord ? STATUS.realizada : STATUS[s.status] || STATUS.agendada;
            return (
              // Celular: horário e nome numa linha, selo e ação na de baixo (alinhados
              // ao nome: pl-28 = largura do horário + espaço); antes o nome sumia ("C.").
              <li key={s.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:gap-4" data-testid={`today-${s.id}`}>
                <div className="flex min-w-0 flex-1 items-center gap-4">
                  <span className="w-24 shrink-0 text-sm font-medium tabular-nums text-[#0F172A]">{formatTimeRange(s)}</span>
                  <div className="min-w-0 flex-1">
                    <p className={`truncate text-sm font-medium text-[#0F172A] ${cancelled ? "line-through" : ""}`}>
                      {s.patient_name || s.title}
                    </p>
                    {s.patient_name && s.title && <p className="truncate text-xs text-slate-500">{s.title}</p>}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 pl-28 sm:pl-0">
                  <span className={`whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ${status.cls}`}>
                    {withRecord ? "Evolução registrada" : status.label}
                  </span>
                  {action(s)}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    // Sessão bloqueada/expirada é tratada pelo AuthContext (ver lib/api.js).
    api.get("/dashboard/stats").then(({ data }) => setStats(data)).catch(() => {});
  }, []);

  return (
    <motion.div initial="hidden" animate="show"
      variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }}
      className="space-y-6">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">Visão geral da sua prática clínica</p>
      </div>

      {/* UX: a agenda do dia vem primeiro (no celular, antes dos totais). */}
      {/* Segurança (backlog: MFA opcional): convite para ativar a verificação em duas etapas. */}
      <SecurityNudge />
      <TodaySessions />
      {/* Fluxo clínico (C2): sessões de dias anteriores ainda sem evolução. */}
      <PendingEvolutions />

      {!stats ? (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4 md:gap-6">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-32" />)}
        </div>
      ) : (
        <>
          {/* Celular: 2 por linha (antes 1 por linha, 4 cartões ocupavam a tela inteira). */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4 md:gap-6">
            <StatCard icon={Users} label="Pacientes ativos" value={stats.total_patients} tint="bg-blue-50 text-[#1E3A8A]"
              testid="stat-patients" to="/pacientes" />
            <StatCard icon={CalendarCheck} label="Consultas no mês" value={stats.sessions_month} tint="bg-sky-50 text-[#0284C7]"
              testid="stat-sessions" to="/agenda?visao=mes" />
            {/* Terminologia (UX): contava EVOLUÇÕES, mas o rótulo dizia "Prontuários". */}
            <StatCard icon={FileText} label="Evoluções registradas" value={stats.total_records} tint="bg-emerald-50 text-emerald-600"
              testid="stat-records" />
            <StatCard icon={Clock} label="Sessões futuras" value={stats.upcoming_sessions} tint="bg-amber-50 text-amber-600"
              testid="stat-upcoming" to="/agenda?visao=lista" />
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <Card className="border-slate-200 p-6 shadow-sm lg:col-span-2" data-testid="records-feed">
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
                <h2 className="font-head text-lg font-bold text-[#0F172A]">Evoluções recentes</h2>
              </div>
              <div className="mt-4 space-y-1">
                {stats.records_feed.length === 0 && (
                  <p className="py-8 text-center text-sm text-slate-500">Nenhuma evolução registrada ainda.</p>
                )}
                {stats.records_feed.map((r) => (
                  <button key={r.record_id}
                    onClick={() => navigate(`/pacientes/${r.patient_id}?evolucao=${r.record_id}`)}
                    className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left transition-colors hover:bg-slate-50"
                    data-testid={`feed-item-${r.record_id}`}>
                    <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#F0F4F8] text-[#1E3A8A]">
                      <FileText className="h-4 w-4" aria-hidden="true" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-[#0F172A]">{r.patient_name}</p>
                      <p className="text-xs text-slate-500">
                        {FEED_ACTION[r.action] || "Evolução"} · sessão {fmtDateTime(r.session_datetime)}
                        {r.version > 1 && <span className="ml-1 text-amber-700">· v{r.version}</span>}
                      </p>
                    </div>
                    <ArrowRight className="h-4 w-4 text-slate-300" aria-hidden="true" />
                  </button>
                ))}
              </div>
            </Card>

            <Card className="border-slate-200 p-6 shadow-sm" data-testid="recent-patients">
              <div className="flex items-center gap-2">
                <UserPlus className="h-4 w-4 text-[#0369A1]" aria-hidden="true" />
                <h2 className="font-head text-lg font-bold text-[#0F172A]">Novos pacientes</h2>
              </div>
              <div className="mt-4 space-y-1">
                {stats.recent_patients.length === 0 && (
                  <p className="py-8 text-center text-sm text-slate-500">Cadastre seu primeiro paciente.</p>
                )}
                {stats.recent_patients.map((p) => (
                  <button key={p.id} onClick={() => navigate(`/pacientes/${p.id}`)}
                    className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors hover:bg-slate-50">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#1E3A8A] text-xs font-semibold text-white">
                      {(p.full_name || "?")[0]}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-[#0F172A]">{p.full_name}</p>
                      <p className="text-xs text-slate-500">Nasc. {fmtDate(p.birth_date)}</p>
                    </div>
                  </button>
                ))}
              </div>
            </Card>
          </div>
        </>
      )}
    </motion.div>
  );
}
