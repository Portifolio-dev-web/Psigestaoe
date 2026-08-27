import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api, { formatApiErrorDetail } from "@/lib/api";
import { motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "sonner";
import {
  Plus, Search, MoreVertical, Pencil, Trash2, ShieldOff, FileText, Users, Loader2,
} from "lucide-react";

const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("pt-BR") : "—");
const EMPTY = { full_name: "", cpf: "", birth_date: "", phone: "", email: "", emergency_contact: "", initial_notes: "" };

export default function Patients() {
  const navigate = useNavigate();
  const [patients, setPatients] = useState(null);
  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [confirm, setConfirm] = useState(null); // {type, patient}

  const load = () => api.get("/patients").then(({ data }) => setPatients(data)).catch(() => setPatients([]));
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(EMPTY); setDialogOpen(true); };
  const openEdit = (p) => { setEditing(p); setForm({ ...EMPTY, ...p }); setDialogOpen(true); };
  const upd = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      if (editing) {
        await api.put(`/patients/${editing.id}`, form);
        toast.success("Paciente atualizado.");
      } else {
        await api.post("/patients", form);
        toast.success("Paciente cadastrado.");
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setSaving(false);
    }
  };

  const doAction = async () => {
    if (!confirm) return;
    try {
      if (confirm.type === "delete") {
        await api.delete(`/patients/${confirm.patient.id}`);
        toast.success("Paciente excluído definitivamente.");
      } else {
        await api.post(`/patients/${confirm.patient.id}/anonymize`);
        toast.success("Dados anonimizados (prontuários mantidos p/ guarda legal).");
      }
      setConfirm(null);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  };

  const filtered = (patients || []).filter((p) =>
    p.full_name.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Pacientes</h1>
          <p className="mt-1 text-sm text-slate-500">Gerencie cadastros e acesse prontuários</p>
        </div>
        <Button onClick={openNew} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="new-patient-btn">
          <Plus className="h-4 w-4" /> Novo paciente
        </Button>
      </div>

      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
        <Input placeholder="Buscar por nome..." value={query} onChange={(e) => setQuery(e.target.value)}
          className="pl-9" data-testid="patient-search-input" />
      </div>

      {patients === null ? (
        <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-[#1E3A8A]" /></div>
      ) : filtered.length === 0 ? (
        <Card className="border-slate-200 py-16 text-center shadow-sm">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[#F0F4F8]">
            <Users className="h-6 w-6 text-[#1E3A8A]" />
          </div>
          <p className="mt-4 font-head text-lg font-bold text-[#0F172A]">Nenhum paciente encontrado</p>
          <p className="mt-1 text-sm text-slate-500">Cadastre seu primeiro paciente para começar.</p>
        </Card>
      ) : (
        <>
          {/* Desktop table */}
          <Card className="hidden border-slate-200 shadow-sm md:block" data-testid="patients-table">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Nome</TableHead>
                  <TableHead>Nascimento</TableHead>
                  <TableHead>Última consulta</TableHead>
                  <TableHead className="w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((p) => (
                  <TableRow key={p.id} className="cursor-pointer hover:bg-slate-50"
                    onClick={() => navigate(`/pacientes/${p.id}`)} data-testid={`patient-row-${p.id}`}>
                    <TableCell className="font-medium text-[#0F172A]">
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#1E3A8A] text-xs font-semibold text-white">
                          {(p.full_name || "?")[0]}
                        </div>
                        {p.full_name}
                      </div>
                    </TableCell>
                    <TableCell className="text-slate-600">{fmtDate(p.birth_date)}</TableCell>
                    <TableCell className="text-slate-600">{fmtDate(p.last_consultation_date)}</TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <RowMenu p={p} onEdit={openEdit} onOpen={() => navigate(`/pacientes/${p.id}`)}
                        onDelete={() => setConfirm({ type: "delete", patient: p })}
                        onAnon={() => setConfirm({ type: "anon", patient: p })} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>

          {/* Mobile cards */}
          <div className="space-y-3 md:hidden">
            {filtered.map((p) => (
              <Card key={p.id} className="border-slate-200 p-4 shadow-sm" data-testid={`patient-card-${p.id}`}>
                <div className="flex items-start justify-between">
                  <button className="flex items-center gap-3 text-left" onClick={() => navigate(`/pacientes/${p.id}`)}>
                    <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#1E3A8A] text-sm font-semibold text-white">
                      {(p.full_name || "?")[0]}
                    </div>
                    <div>
                      <p className="font-medium text-[#0F172A]">{p.full_name}</p>
                      <p className="text-xs text-slate-500">Nasc. {fmtDate(p.birth_date)}</p>
                    </div>
                  </button>
                  <RowMenu p={p} onEdit={openEdit} onOpen={() => navigate(`/pacientes/${p.id}`)}
                    onDelete={() => setConfirm({ type: "delete", patient: p })}
                    onAnon={() => setConfirm({ type: "anon", patient: p })} />
                </div>
                <p className="mt-2 text-xs text-slate-500">Última consulta: {fmtDate(p.last_consultation_date)}</p>
              </Card>
            ))}
          </div>
        </>
      )}

      {/* Form dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="font-head">{editing ? "Editar paciente" : "Novo paciente"}</DialogTitle>
            <DialogDescription>O CPF é criptografado (AES-256) antes de ser salvo.</DialogDescription>
          </DialogHeader>
          <form onSubmit={save} className="space-y-4">
            <div>
              <Label>Nome completo *</Label>
              <Input required value={form.full_name} onChange={upd("full_name")} className="mt-1.5" data-testid="patient-name-input" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>CPF</Label>
                <Input value={form.cpf} onChange={upd("cpf")} className="mt-1.5" placeholder="000.000.000-00" data-testid="patient-cpf-input" />
              </div>
              <div>
                <Label>Data de nascimento</Label>
                <Input type="date" value={form.birth_date} onChange={upd("birth_date")} className="mt-1.5" data-testid="patient-birth-input" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Telefone</Label>
                <Input value={form.phone} onChange={upd("phone")} className="mt-1.5" data-testid="patient-phone-input" />
              </div>
              <div>
                <Label>E-mail</Label>
                <Input type="email" value={form.email} onChange={upd("email")} className="mt-1.5" data-testid="patient-email-input" />
              </div>
            </div>
            <div>
              <Label>Contato de emergência</Label>
              <Input value={form.emergency_contact} onChange={upd("emergency_contact")} className="mt-1.5" data-testid="patient-emergency-input" />
            </div>
            <div>
              <Label>Observações iniciais</Label>
              <Textarea value={form.initial_notes} onChange={upd("initial_notes")} className="mt-1.5" rows={3} data-testid="patient-notes-input" />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>Cancelar</Button>
              <Button type="submit" disabled={saving} className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="save-patient-btn">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Salvar"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Confirm */}
      <AlertDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirm?.type === "delete" ? "Excluir paciente?" : "Anonimizar dados?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirm?.type === "delete"
                ? "Esta ação é permanente e remove o paciente e seus prontuários."
                : "Os dados pessoais serão removidos, mas os prontuários serão mantidos pelo período de guarda legal exigido pelo CFP."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={doAction}
              className={confirm?.type === "delete" ? "bg-[#EF4444] hover:bg-red-700" : "bg-[#1E3A8A] hover:bg-[#0F2C59]"}
              data-testid="confirm-action-btn">
              {confirm?.type === "delete" ? "Excluir" : "Anonimizar"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function RowMenu({ p, onEdit, onOpen, onDelete, onAnon }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8" data-testid={`patient-menu-${p.id}`}>
          <MoreVertical className="h-4 w-4 text-slate-500" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={onOpen}><FileText className="mr-2 h-4 w-4" /> Prontuários</DropdownMenuItem>
        <DropdownMenuItem onClick={() => onEdit(p)}><Pencil className="mr-2 h-4 w-4" /> Editar</DropdownMenuItem>
        <DropdownMenuItem onClick={onAnon}><ShieldOff className="mr-2 h-4 w-4" /> Anonimizar</DropdownMenuItem>
        <DropdownMenuItem onClick={onDelete} className="text-[#EF4444]"><Trash2 className="mr-2 h-4 w-4" /> Excluir</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
