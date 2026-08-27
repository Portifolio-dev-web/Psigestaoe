import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import { motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Users, CalendarCheck, FileText, Clock, UserPlus, ArrowRight, Activity } from "lucide-react";

const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("pt-BR") : "—");
const fmtDateTime = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

function StatCard({ icon: Icon, label, value, tint, testid }) {
  return (
    <Card className="border-slate-200 p-5 shadow-sm transition-transform duration-200 hover:-translate-y-[1px] hover:shadow-md" data-testid={testid}>
      <div className="flex items-center justify-between">
        <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${tint}`}>
          <Icon className="h-5 w-5" strokeWidth={1.7} />
        </div>
      </div>
      <p className="mt-4 font-head text-3xl font-extrabold text-[#0F172A]">{value}</p>
      <p className="mt-1 text-sm text-slate-500">{label}</p>
    </Card>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    console.log("[dash] mount, fetching stats");
    api.get("/dashboard/stats").then(({ data }) => { console.log("[dash] stats ok"); setStats(data); }).catch((e) => { console.log("[dash] stats err", e?.message); });
  }, []);

  if (!stats) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-56" />
        <div className="grid grid-cols-1 gap-6 md:grid-cols-4">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-32" />)}
        </div>
      </div>
    );
  }

  return (
    <motion.div initial="hidden" animate="show"
      variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }}
      className="space-y-6">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">Visão geral da sua prática clínica</p>
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-4">
        <StatCard icon={Users} label="Pacientes ativos" value={stats.total_patients} tint="bg-blue-50 text-[#1E3A8A]" testid="stat-patients" />
        <StatCard icon={CalendarCheck} label="Consultas no mês" value={stats.sessions_month} tint="bg-sky-50 text-[#0284C7]" testid="stat-sessions" />
        <StatCard icon={FileText} label="Prontuários" value={stats.total_records} tint="bg-emerald-50 text-emerald-600" testid="stat-records" />
        <StatCard icon={Clock} label="Sessões futuras" value={stats.upcoming_sessions} tint="bg-amber-50 text-amber-600" testid="stat-upcoming" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="border-slate-200 p-6 shadow-sm lg:col-span-2" data-testid="records-feed">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-[#1E3A8A]" />
            <h2 className="font-head text-lg font-bold text-[#0F172A]">Evoluções recentes</h2>
          </div>
          <div className="mt-4 space-y-1">
            {stats.records_feed.length === 0 && (
              <p className="py-8 text-center text-sm text-slate-400">Nenhum prontuário registrado ainda.</p>
            )}
            {stats.records_feed.map((r) => (
              <button key={r.record_id}
                onClick={() => navigate(`/pacientes/${r.patient_id}`)}
                className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left transition-colors hover:bg-slate-50"
                data-testid={`feed-item-${r.record_id}`}>
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#F0F4F8] text-[#1E3A8A]">
                  <FileText className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-[#0F172A]">{r.patient_name}</p>
                  <p className="text-xs text-slate-500">
                    Prontuário {r.action} · sessão {fmtDateTime(r.session_datetime)}
                    {r.version > 1 && <span className="ml-1 text-amber-600">· v{r.version}</span>}
                  </p>
                </div>
                <ArrowRight className="h-4 w-4 text-slate-300" />
              </button>
            ))}
          </div>
        </Card>

        <Card className="border-slate-200 p-6 shadow-sm" data-testid="recent-patients">
          <div className="flex items-center gap-2">
            <UserPlus className="h-4 w-4 text-[#0284C7]" />
            <h2 className="font-head text-lg font-bold text-[#0F172A]">Novos pacientes</h2>
          </div>
          <div className="mt-4 space-y-1">
            {stats.recent_patients.length === 0 && (
              <p className="py-8 text-center text-sm text-slate-400">Cadastre seu primeiro paciente.</p>
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
    </motion.div>
  );
}
