import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Download, FileText, Loader2, LogOut, Search, ShieldAlert, Stethoscope } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { downloadFromApi } from "@/lib/download";
import { formatDateTime } from "@/lib/datetime";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import CloseAccountCard from "@/components/settings/CloseAccountCard";
import ConnectedDevicesCard from "@/components/settings/ConnectedDevicesCard";
import TwoFactorCard from "@/components/settings/TwoFactorCard";

// ===========================================================================
// PRODUTO (análise da administração, AD2): MODO RESTRITO da conta suspensa.
//
// Antes, a conta suspensa não entrava mais: a psicóloga ficava sem os
// prontuários (guarda obrigatória, Resolução CFP nº 001/2009) e sem atender os
// pedidos dos pacientes (LGPD art. 18). Agora ela entra e vê SÓ esta tela, no
// lugar do sistema (ProtectedRoute, App.js), com o que continua liberado:
//   - baixar a ficha de um paciente (pedido do paciente) ou tudo (guarda);
//   - baixar a trilha de auditoria (o que a administração fez e por quê);
//   - cuidar da segurança da conta (duas etapas, aparelhos conectados);
//   - encerrar a conta.
// Quem decide é o servidor (SUSPENDED_ALLOWED_ROUTES em server.py): esta tela
// só oferece o que ele libera; qualquer outra rota responde 403.
// ===========================================================================
const PAGE_SIZE = 50;

export default function SuspendedAccount() {
  const { user, logout } = useAuth();
  const [query, setQuery] = useState("");
  const [patients, setPatients] = useState(null);
  const [support, setSupport] = useState("");
  const inFlight = useSingleFlight();
  const requestSeq = useRef(0);

  // Título da aba: a tela é sempre esta, qualquer que seja o endereço.
  useEffect(() => { document.title = "Conta suspensa · PsiGestão"; }, []);

  // Contato do suporte, para contestar (rota pública; a página Ajuda não abre no modo restrito).
  useEffect(() => {
    api.get("/legal/contact").then(({ data }) => setSupport(data.support_email || data.dpo_email || "")).catch(() => {});
  }, []);

  // Lista para achar o paciente: os primeiros por nome, ou a busca (nome, telefone
  // ou CPF; mínimo 2 caracteres) no servidor, com o termo no CORPO (fora da URL).
  useEffect(() => {
    const term = query.trim();
    const timer = setTimeout(async () => {
      const seq = ++requestSeq.current;
      try {
        const { data } = term.length >= 2
          ? await api.post("/patients/search", { q: term, archived: false, limit: PAGE_SIZE, sort: "name" })
          : await api.get(`/patients?limit=${PAGE_SIZE}&sort=name`);
        if (seq === requestSeq.current) setPatients(data.items);
      } catch (err) {
        if (seq === requestSeq.current) setPatients([]);
      }
    }, term ? 300 : 0);
    return () => clearTimeout(timer);
  }, [query]);

  const download = (key, path, name) => inFlight.run(key, async () => {
    try {
      await downloadFromApi(path, name);
    } catch (err) {
      toast.error(err.message || formatApiErrorDetail(null));
    }
  });

  return (
    <main tabIndex={-1} className="min-h-screen bg-[#F8FAFC] px-4 py-8 focus:outline-none" data-testid="suspended-account">
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#1E3A8A]">
              <Stethoscope className="h-5 w-5 text-white" aria-hidden="true" />
            </div>
            <span className="font-head text-lg font-bold text-[#0F172A]">PsiGestão</span>
          </div>
          <Button variant="ghost" onClick={logout} className="gap-2 text-slate-600" data-testid="suspended-logout">
            <LogOut className="h-4 w-4" aria-hidden="true" /> Sair
          </Button>
        </div>

        <Card className="border-amber-200 bg-amber-50 p-6 shadow-sm">
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-1 h-6 w-6 shrink-0 text-amber-700" aria-hidden="true" />
            <div className="space-y-2 text-sm leading-relaxed text-amber-950">
              <h1 className="font-head text-2xl font-bold text-[#0F172A]">Conta suspensa</h1>
              <p>
                Esta conta foi suspensa pela administração do PsiGestão
                {user?.suspended?.at ? ` em ${formatDateTime(user.suspended.at)}` : ""}.
              </p>
              {user?.suspended?.reason && <p data-testid="suspended-reason"><b>Motivo:</b> {user.suspended.reason}</p>}
              <p>
                Os seus dados e prontuários estão guardados, sem nenhuma alteração. Enquanto a conta estiver suspensa,
                não é possível registrar, agendar nem enviar links de cadastro. Você continua podendo exportar os dados
                (para a guarda do prontuário e os pedidos dos pacientes), reforçar a segurança da conta e encerrá-la.
              </p>
              <p>
                Para contestar a suspensão,{" "}
                {support ? (
                  <>escreva para <a href={`mailto:${support}`} className="font-medium underline underline-offset-2">{support}</a>.</>
                ) : "fale com o suporte do PsiGestão."}{" "}
                As regras estão nos{" "}
                <a href="/termos" target="_blank" rel="noopener noreferrer" className="font-medium underline underline-offset-2">
                  Termos de Uso (abre em nova aba)
                </a>.
              </p>
            </div>
          </div>
        </Card>

        <Card className="border-slate-200 p-6 shadow-sm">
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Pedidos de pacientes</h2>
          <p className="mt-1 text-sm text-slate-600">
            Um paciente pediu a cópia dos dados ou do prontuário? Baixe a ficha dele em PDF. Para guardar tudo, use
            "Exportar todos os dados", mais abaixo.
          </p>
          <div className="relative mt-4 sm:max-w-sm">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} maxLength={120} className="pl-9"
              aria-label="Buscar paciente por nome, telefone ou CPF" placeholder="Buscar por nome, telefone ou CPF..."
              data-testid="suspended-search" />
          </div>
          {patients === null ? (
            <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" aria-label="Carregando" /></div>
          ) : patients.length === 0 ? (
            <p className="mt-4 text-sm text-slate-500" role="status">Nenhum paciente encontrado.</p>
          ) : (
            <ul className="mt-4 divide-y divide-slate-100 rounded-md border border-slate-200" data-testid="suspended-patients">
              {patients.map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-3 p-3">
                  <span className="min-w-0 truncate text-sm font-medium text-[#0F172A]">{p.full_name}</span>
                  <Button type="button" size="sm" variant="outline" className="shrink-0 gap-2 border-slate-300"
                    disabled={inFlight.isBusy(`pdf:${p.id}`)} aria-label={`Baixar a ficha de ${p.full_name} em PDF`}
                    onClick={() => download(`pdf:${p.id}`, `/patients/${p.id}/export?format=pdf`, "ficha_paciente.pdf")}>
                    {inFlight.isBusy(`pdf:${p.id}`) ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" aria-hidden="true" />}
                    Baixar PDF
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="border-slate-200 p-6 shadow-sm">
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Trilha de auditoria</h2>
          <p className="mt-1 text-sm text-slate-600">
            Os acessos e as ações feitas na conta, inclusive as da administração, com data e endereço IP.
          </p>
          <Button type="button" variant="outline" className="mt-4 gap-2 border-slate-300" disabled={inFlight.isBusy("audit")}
            onClick={() => download("audit", "/audit/export", "trilha_auditoria.csv")} data-testid="suspended-audit-export">
            {inFlight.isBusy("audit") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" aria-hidden="true" />}
            Baixar a trilha (CSV)
          </Button>
        </Card>

        {/* RS1: aqui só se REFORÇA a proteção (o servidor não deixa desativar). */}
        <TwoFactorCard allowDisable={false} />
        <ConnectedDevicesCard />
        {/* Exportar todos os dados (ZIP) e encerrar a conta: o mesmo cartão das Configurações. */}
        <CloseAccountCard />
      </div>
    </main>
  );
}
