import { useEffect, useState } from "react";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Shield, Lock, FileCheck, History, KeyRound, ScrollText, Loader2, CheckCircle2,
} from "lucide-react";

const ACTION_LABEL = {
  criar: "Criação", editar: "Edição", visualizar: "Visualização",
  excluir: "Exclusão", anonimizar: "Anonimização", exportar: "Exportação",
};
const ENTITY_LABEL = { paciente: "Paciente", prontuario: "Prontuário", agenda: "Agenda", usuario: "Usuário" };

export default function Settings() {
  const { user } = useAuth();
  const [logs, setLogs] = useState(null);

  useEffect(() => {
    api.get("/audit").then(({ data }) => setLogs(data)).catch(() => setLogs([]));
  }, []);

  const items = [
    { icon: Lock, title: "Criptografia em repouso", desc: "CPF, anotações clínicas e diagnósticos são cifrados com AES-256-GCM antes de gravar no banco.", ok: true },
    { icon: Shield, title: "Criptografia em trânsito", desc: "Toda comunicação ocorre via HTTPS/TLS.", ok: true },
    { icon: History, title: "Imutabilidade & versionamento", desc: "Prontuários não são sobrescritos: cada edição gera uma nova versão arquivada em auditoria.", ok: true },
    { icon: KeyRound, title: "Multi-tenancy seguro", desc: "Cada profissional acessa apenas os próprios dados, com sessão isolada.", ok: true },
    { icon: FileCheck, title: "Direitos do titular (LGPD)", desc: "Exportação segura (PDF/JSON) e anonimização respeitando o período de guarda do CFP.", ok: true },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Privacidade & LGPD</h1>
        <p className="mt-1 text-sm text-slate-500">Conformidade, criptografia e trilha de auditoria</p>
      </div>

      <Card className="border-slate-200 p-6 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600"><CheckCircle2 className="h-5 w-5" /></div>
          <div>
            <p className="font-head font-bold text-[#0F172A]">Conta em conformidade</p>
            <p className="text-sm text-slate-500">{user?.name} · {user?.email} · Termos aceitos</p>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {items.map((it) => (
          <Card key={it.title} className="border-slate-200 p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#F0F4F8] text-[#1E3A8A]"><it.icon className="h-4 w-4" /></div>
              <div>
                <div className="flex items-center gap-2">
                  <p className="font-medium text-[#0F172A]">{it.title}</p>
                  <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100">Ativo</Badge>
                </div>
                <p className="mt-1 text-sm leading-relaxed text-slate-500">{it.desc}</p>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <Card className="border-slate-200 p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <ScrollText className="h-4 w-4 text-[#1E3A8A]" />
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Trilha de auditoria</h2>
          <span className="text-xs text-slate-400">(imutável)</span>
        </div>
        <Separator className="my-4" />
        {logs === null ? (
          <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" /></div>
        ) : logs.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">Nenhum registro de auditoria ainda.</p>
        ) : (
          <div className="max-h-[420px] space-y-1 overflow-y-auto psi-scroll" data-testid="audit-log">
            {logs.map((l) => (
              <div key={l.log_id} className="flex items-center gap-3 rounded-md px-2 py-2 text-sm hover:bg-slate-50">
                <Badge variant="secondary" className="w-28 shrink-0 justify-center text-[11px]">{ACTION_LABEL[l.action] || l.action}</Badge>
                <span className="text-slate-600">{ENTITY_LABEL[l.entity_type] || l.entity_type}</span>
                <span className="truncate text-slate-400">{l.detail}</span>
                <span className="ml-auto shrink-0 text-xs text-slate-400">
                  {new Date(l.timestamp).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
