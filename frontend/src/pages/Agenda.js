import { useEffect, useState, useMemo } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  ChevronLeft, ChevronRight, Plus, CalendarDays, Clock, Trash2, Link2, Loader2, X,
} from "lucide-react";

const WEEKDAYS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];
const MONTHS = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"];
const STATUS = {
  agendada: { label: "Agendada", cls: "bg-blue-100 text-[#1E3A8A]" },
  realizada: { label: "Realizada", cls: "bg-emerald-100 text-emerald-700" },
  cancelada: { label: "Cancelada", cls: "bg-red-100 text-red-600" },
};
const toLocalInput = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 16); };

export default function Agenda() {
  const [cursor, setCursor] = useState(new Date());
  const [sessions, setSessions] = useState([]);
  const [patients, setPatients] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = () => api.get("/sessions").then(({ data }) => setSessions(data)).catch(() => {});
  useEffect(() => {
    load();
    api.get("/patients").then(({ data }) => setPatients(data)).catch(() => {});
  }, []);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const grid = useMemo(() => {
    const first = new Date(year, month, 1);
    const startDay = first.getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const cells = [];
    for (let i = 0; i < startDay; i++) cells.push(null);
    for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d));
    return cells;
  }, [year, month]);

  const sessionsByDay = useMemo(() => {
    const map = {};
    sessions.forEach((s) => {
      if (!s.start) return;
      const d = new Date(s.start);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      (map[key] = map[key] || []).push(s);
    });
    return map;
  }, [sessions]);

  const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const isToday = (d) => dayKey(d) === dayKey(new Date());

  const openNew = (date) => {
    const base = date || new Date();
    base.setHours(9, 0, 0, 0);
    setForm({ id: null, patient_id: "", patient_name: "", title: "Sessão", start: toLocalInput(base), end: "", status: "agendada", notes: "" });
    setOpen(true);
  };
  const openEdit = (s) => {
    setForm({ ...s, start: s.start ? s.start.slice(0, 16) : "", end: s.end ? s.end.slice(0, 16) : "" });
    setOpen(true);
  };

  const save = async () => {
    if (!form.title.trim()) { toast.warning("Informe um título."); return; }
    setSaving(true);
    const patient = patients.find((p) => p.id === form.patient_id);
    const payload = { ...form, patient_name: patient ? patient.full_name : form.patient_name };
    try {
      if (form.id) await api.put(`/sessions/${form.id}`, payload);
      else await api.post("/sessions", payload);
      toast.success("Sessão salva.");
      setOpen(false);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    try {
      await api.delete(`/sessions/${form.id}`);
      toast.success("Sessão removida.");
      setOpen(false);
      load();
    } catch (e) { toast.error("Erro ao remover."); }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Agenda</h1>
          <p className="mt-1 text-sm text-slate-500">Gerencie horários e sessões</p>
        </div>
        <Button onClick={() => openNew()} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="new-session-btn">
          <Plus className="h-4 w-4" /> Nova sessão
        </Button>
      </div>

      {/* Integration placeholders */}
      <div className="flex flex-wrap gap-2">
        <button className="flex items-center gap-2 rounded-full border border-dashed border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-500 transition-colors hover:border-[#0284C7] hover:text-[#0284C7]"
          onClick={() => toast.info("Integração Google Calendar: configure o webhook em Privacidade & LGPD (em breve).")} data-testid="gcal-hook">
          <Link2 className="h-3.5 w-3.5" /> Conectar Google Calendar
        </button>
        <button className="flex items-center gap-2 rounded-full border border-dashed border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-500 transition-colors hover:border-[#0284C7] hover:text-[#0284C7]"
          onClick={() => toast.info("Integração Calendly: gancho de API preparado (em breve).")} data-testid="calendly-hook">
          <Link2 className="h-3.5 w-3.5" /> Conectar Calendly
        </button>
      </div>

      <Card className="border-slate-200 p-4 shadow-sm sm:p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-head text-lg font-bold text-[#0F172A]">{MONTHS[month]} {year}</h2>
          <div className="flex items-center gap-1">
            <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => setCursor(new Date(year, month - 1, 1))} data-testid="prev-month"><ChevronLeft className="h-4 w-4" /></Button>
            <Button variant="ghost" size="sm" className="text-xs" onClick={() => setCursor(new Date())}>Hoje</Button>
            <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => setCursor(new Date(year, month + 1, 1))} data-testid="next-month"><ChevronRight className="h-4 w-4" /></Button>
          </div>
        </div>

        <div className="grid grid-cols-7 gap-1 text-center">
          {WEEKDAYS.map((w) => <div key={w} className="py-2 text-xs font-medium uppercase tracking-wide text-slate-400">{w}</div>)}
          {grid.map((d, i) => {
            if (!d) return <div key={i} />;
            const list = sessionsByDay[dayKey(d)] || [];
            return (
              <button key={i} onClick={() => openNew(new Date(d))}
                className={`min-h-[84px] rounded-md border p-1.5 text-left transition-colors hover:bg-slate-50 ${isToday(d) ? "border-[#1E3A8A] bg-blue-50/40" : "border-slate-100"}`}
                data-testid={`day-${d.getDate()}`}>
                <span className={`text-xs font-medium ${isToday(d) ? "text-[#1E3A8A]" : "text-slate-500"}`}>{d.getDate()}</span>
                <div className="mt-1 space-y-1">
                  {list.slice(0, 2).map((s) => (
                    <div key={s.id} onClick={(e) => { e.stopPropagation(); openEdit(s); }}
                      className={`truncate rounded px-1 py-0.5 text-[10px] ${STATUS[s.status]?.cls || STATUS.agendada.cls}`}>
                      {new Date(s.start).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })} {s.title}
                    </div>
                  ))}
                  {list.length > 2 && <div className="text-[10px] text-slate-400">+{list.length - 2}</div>}
                </div>
              </button>
            );
          })}
        </div>
      </Card>

      {/* Upcoming list */}
      <Card className="border-slate-200 p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <CalendarDays className="h-4 w-4 text-[#1E3A8A]" />
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Próximas sessões</h2>
        </div>
        <div className="mt-4 space-y-2">
          {sessions.filter((s) => new Date(s.start) >= new Date()).slice(0, 8).length === 0 && (
            <p className="py-6 text-center text-sm text-slate-400">Nenhuma sessão agendada.</p>
          )}
          {sessions.filter((s) => new Date(s.start) >= new Date()).slice(0, 8).map((s) => (
            <button key={s.id} onClick={() => openEdit(s)} className="flex w-full items-center gap-3 rounded-md border border-slate-100 p-3 text-left transition-colors hover:bg-slate-50">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#F0F4F8] text-[#1E3A8A]"><Clock className="h-4 w-4" /></div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-[#0F172A]">{s.title}{s.patient_name ? ` · ${s.patient_name}` : ""}</p>
                <p className="text-xs text-slate-500">{new Date(s.start).toLocaleString("pt-BR", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</p>
              </div>
              <Badge className={`${STATUS[s.status]?.cls} hover:opacity-100`}>{STATUS[s.status]?.label}</Badge>
            </button>
          ))}
        </div>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="font-head">{form?.id ? "Editar sessão" : "Nova sessão"}</DialogTitle>
            <DialogDescription>Agende ou edite uma sessão clínica.</DialogDescription>
          </DialogHeader>
          {form && (
            <div className="space-y-4">
              <div>
                <Label>Título</Label>
                <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="mt-1.5" data-testid="session-title-input" />
              </div>
              <div>
                <Label>Paciente</Label>
                <Select value={form.patient_id || "none"} onValueChange={(v) => setForm({ ...form, patient_id: v === "none" ? "" : v })}>
                  <SelectTrigger className="mt-1.5" data-testid="session-patient-select"><SelectValue placeholder="Selecione" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Sem paciente</SelectItem>
                    {patients.map((p) => <SelectItem key={p.id} value={p.id}>{p.full_name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Início</Label>
                  <Input type="datetime-local" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} className="mt-1.5" data-testid="session-start-input" />
                </div>
                <div>
                  <Label>Fim</Label>
                  <Input type="datetime-local" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} className="mt-1.5" />
                </div>
              </div>
              <div>
                <Label>Status</Label>
                <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                  <SelectTrigger className="mt-1.5" data-testid="session-status-select"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="agendada">Agendada</SelectItem>
                    <SelectItem value="realizada">Realizada</SelectItem>
                    <SelectItem value="cancelada">Cancelada</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Notas</Label>
                <Textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className="mt-1.5" />
              </div>
            </div>
          )}
          <DialogFooter className="flex-row justify-between sm:justify-between">
            {form?.id ? (
              <Button variant="ghost" onClick={remove} className="gap-2 text-red-500 hover:bg-red-50 hover:text-red-600" data-testid="delete-session-btn"><Trash2 className="h-4 w-4" /> Remover</Button>
            ) : <span />}
            <Button onClick={save} disabled={saving} className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="save-session-btn">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Salvar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
