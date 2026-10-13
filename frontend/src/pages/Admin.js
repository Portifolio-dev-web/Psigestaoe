import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Loader2, MoreVertical, Search, ShieldAlert } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { formatDateTime } from "@/lib/datetime";
import { PLAN_LABEL, formatDay } from "@/lib/subscription";
import { countLabel } from "@/lib/utils";
import AdminActionDialog from "@/components/AdminActionDialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

// ===========================================================================
// PRODUTO: ADMINISTRAÇÃO (/administracao).
//
// Controle das contas das psicólogas: situação da assinatura, cortesia,
// prorrogar o teste grátis, encerrar sessões e suspender/reativar.
// A tela só aparece para quem o SERVIDOR diz que é administrador
// (ADMIN_EMAILS); cada rota confere de novo, exige a verificação em duas
// etapas e pede a senha a cada ação (ver server.py, seção "ADMINISTRAÇÃO").
// Nenhum dado de paciente aparece aqui: só o NÚMERO de pacientes de cada conta.
// ===========================================================================

// Situações (as mesmas de billing.access_state + "suspensa"). Texto sempre
// visível: a cor só reforça (WCAG 1.4.1).
const STATUS = {
  trialing: { label: "Teste grátis", cls: "bg-blue-50 text-[#1E3A8A]" },
  active: { label: "Assinante", cls: "bg-emerald-50 text-emerald-800" },
  past_due: { label: "Pagamento em atraso", cls: "bg-amber-50 text-amber-900" },
  read_only: { label: "Somente leitura", cls: "bg-slate-100 text-slate-700" },
  courtesy: { label: "Cortesia", cls: "bg-violet-50 text-violet-800" },
  suspended: { label: "Suspensa", cls: "bg-red-50 text-red-800" },
};
const FILTERS = [
  ["all", "Todas"], ["trialing", "Teste grátis"], ["active", "Assinantes"], ["past_due", "Em atraso"],
  ["read_only", "Somente leitura"], ["courtesy", "Cortesia"], ["suspended", "Suspensas"],
];
const READ_ONLY_DETAIL = { trial_ended: "teste encerrado", payment_overdue: "atraso além da tolerância", canceled: "assinatura encerrada" };

/** Uma frase com o detalhe da situação (até quando, motivo...). */
function statusDetail(account) {
  const sub = account.subscription || {};
  switch (account.status) {
    case "suspended": return account.suspended_reason ? `Motivo: ${account.suspended_reason}` : "";
    case "courtesy": return account.courtesy_reason || "";
    case "trialing": return `até ${formatDay(sub.trial_ends_at)}`;
    case "active": return `${sub.plan ? `plano ${PLAN_LABEL[sub.plan] || sub.plan}, ` : ""}${sub.cancel_at_period_end ? "cancelada, vale " : ""}até ${formatDay(sub.paid_until)}`;
    case "past_due": return `tolerância até ${formatDay(sub.grace_ends_at)}`;
    case "read_only": return READ_ONLY_DETAIL[sub.reason] || "";
    default: return "";
  }
}

/** Ações possíveis para a conta (as mesmas regras do servidor, para não oferecer o que ele recusa). */
function actionsFor(account) {
  const items = [];
  items.push(account.status === "courtesy" ? ["revoke", "Revogar cortesia"] : ["grant", "Conceder cortesia"]);
  // O teste grátis só vale para quem nunca teve período pago e não tem cortesia.
  if (account.status !== "courtesy" && !account.has_paid) items.push(["trial", "Prorrogar teste grátis"]);
  // A própria conta e outras administrações não são desconectadas nem suspensas por aqui.
  if (!account.is_self && !account.is_admin) {
    items.push(["endSessions", "Encerrar sessões"]);
    items.push(account.status === "suspended" ? ["reactivate", "Reativar conta"] : ["suspend", "Suspender conta"]);
  }
  return items;
}

export default function Admin() {
  const { user } = useAuth();
  const [data, setData] = useState(null); // { items, total, page, pages, summary, trial_days_options }
  const [blocked, setBlocked] = useState(null); // null | "mfa" | "denied"
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [action, setAction] = useState(null); // { action, account }
  const requestSeq = useRef(0);

  // Busca no servidor 300 ms depois da última tecla. O termo (nome ou e-mail)
  // vai no CORPO de um POST, nunca na URL (que fica nos logs), e não entra na trilha.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => { setPage(1); }, [debouncedQuery, filter]);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    try {
      const response = await api.post("/admin/users/search", { q: debouncedQuery.trim(), status: filter, page });
      if (seq !== requestSeq.current) return; // chegou uma resposta mais nova
      setBlocked(null);
      setData(response.data);
    } catch (err) {
      if (seq !== requestSeq.current) return;
      const detail = err.response?.data?.detail;
      if (detail?.code === "admin_mfa_required") setBlocked("mfa");
      else if (err.response?.status === 404) setBlocked("denied");
      else toast.error(formatApiErrorDetail(detail) || err.message);
      setData((current) => current || { items: [], total: 0, page: 1, pages: 1, summary: {} });
    }
  }, [page, filter, debouncedQuery]);

  useEffect(() => { load(); }, [load]);

  const done = (updated, message) => {
    toast.success(message);
    setAction(null);
    // A linha muda na hora; a lista recarrega para os totais e o filtro.
    setData((current) => current && { ...current, items: current.items.map((item) => (item.user_id === updated.user_id ? updated : item)) });
    load();
  };

  const summary = data?.summary || {};

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Administração</h1>
        <p className="mt-1 text-sm text-slate-500">
          Contas das psicólogas: assinatura, cortesia e acesso. Nenhum dado de paciente aparece aqui.
        </p>
      </div>

      {blocked === "mfa" && (
        <Card className="flex flex-col gap-3 border-amber-200 bg-amber-50 p-5 sm:flex-row sm:items-center" data-testid="admin-mfa-required">
          <ShieldAlert className="h-6 w-6 shrink-0 text-amber-700" aria-hidden="true" />
          <div className="flex-1 text-sm text-amber-900">
            <p className="font-semibold">Ative a verificação em duas etapas para usar a administração.</p>
            <p className="mt-0.5">Daqui dá para suspender contas e liberar acesso sem cobrança: a sua conta precisa da proteção extra.</p>
          </div>
          <Button asChild className="bg-[#1E3A8A] hover:bg-[#0F2C59]">
            <Link to="/configuracoes?aba=conta">Ativar agora</Link>
          </Button>
        </Card>
      )}

      {blocked === "denied" && (
        <Card className="border-slate-200 p-5 text-sm text-slate-700" data-testid="admin-denied">
          Esta conta não tem acesso à administração.
        </Card>
      )}

      {!blocked && (
        <>
          <div className="flex flex-col gap-3">
            <div className="relative w-full sm:max-w-sm">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} maxLength={120} className="pl-9"
                aria-label="Buscar conta por nome ou e-mail" placeholder="Buscar por nome ou e-mail..."
                data-testid="admin-search" />
            </div>
            {/* Filtro por situação: botões com aria-pressed (o leitor de tela diz qual está ativo). */}
            <div role="group" aria-label="Filtrar por situação" className="flex flex-wrap gap-2">
              {FILTERS.map(([id, label]) => (
                <button key={id} type="button" onClick={() => setFilter(id)} aria-pressed={filter === id}
                  data-testid={`admin-filter-${id}`}
                  className={`min-h-9 rounded-full border px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A] focus-visible:ring-offset-1 ${
                    filter === id ? "border-[#1E3A8A] bg-[#1E3A8A] text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
                  {label} <span className={filter === id ? "text-blue-100" : "text-slate-500"}>({summary[id] ?? 0})</span>
                </button>
              ))}
            </div>
          </div>

          {data && (
            <p className="text-sm text-slate-500" role="status" data-testid="admin-total">
              {countLabel(data.total, "conta", "contas")}
              {debouncedQuery.trim() ? (data.total === 1 ? " encontrada na busca" : " encontradas na busca") : ""}
            </p>
          )}

          {data === null ? (
            <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-[#1E3A8A]" aria-label="Carregando" /></div>
          ) : data.items.length === 0 ? (
            <Card className="border-slate-200 py-12 text-center text-sm text-slate-500">Nenhuma conta encontrada.</Card>
          ) : (
            <>
              <AccountsTable items={data.items} onAction={(name, account) => setAction({ action: name, account })} />
              <AccountsCards items={data.items} onAction={(name, account) => setAction({ action: name, account })} />
            </>
          )}

          {data && data.pages > 1 && (
            <nav aria-label="Páginas da lista de contas" className="flex items-center justify-center gap-3">
              <Button variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Anterior</Button>
              <span className="text-sm text-slate-600">Página {data.page} de {data.pages}</span>
              <Button variant="outline" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>Próxima</Button>
            </nav>
          )}
        </>
      )}

      {action && (
        <AdminActionDialog action={action.action} account={action.account} hasPassword={Boolean(user?.has_password)}
          trialOptions={data?.trial_days_options} onClose={() => setAction(null)} onDone={done} />
      )}
    </div>
  );
}

function StatusBadge({ account }) {
  const status = STATUS[account.status] || STATUS.read_only;
  return <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${status.cls}`}>{status.label}</span>;
}

/** Marcas da conta: você, administração, sem duas etapas. */
function AccountTags({ account }) {
  return (
    <span className="flex flex-wrap gap-1.5">
      {account.is_self && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700">Você</span>}
      {account.is_admin && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700">Administração</span>}
      {!account.mfa_enabled && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-900">Sem duas etapas</span>}
    </span>
  );
}

function ActionsMenu({ account, onAction }) {
  const label = account.name || account.email;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Ações para ${label}`} data-testid={`admin-actions-${account.user_id}`}>
          <MoreVertical className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {actionsFor(account).map(([name, text]) => (
          <DropdownMenuItem key={name} onClick={() => onAction(name, account)} data-testid={`admin-action-${name}`}
            className={["suspend", "endSessions", "revoke"].includes(name) ? "text-red-700 focus:text-red-800" : ""}>
            {text}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function AccountsTable({ items, onAction }) {
  return (
    <Card className="hidden border-slate-200 shadow-sm md:block" data-testid="admin-table">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Conta</TableHead>
            <TableHead>Situação</TableHead>
            <TableHead className="text-right">Pacientes</TableHead>
            <TableHead>Último acesso</TableHead>
            <TableHead>Cadastro</TableHead>
            <TableHead className="w-10"><span className="sr-only">Ações</span></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((account) => (
            <TableRow key={account.user_id} data-testid={`admin-row-${account.user_id}`} data-status={account.status}>
              <TableCell>
                <p className="font-medium text-[#0F172A]">{account.name || "Sem nome"}</p>
                <p className="text-xs text-slate-600">{account.email}</p>
                <div className="mt-1"><AccountTags account={account} /></div>
              </TableCell>
              <TableCell>
                <StatusBadge account={account} />
                <p className="mt-1 max-w-[16rem] text-xs text-slate-600">{statusDetail(account)}</p>
              </TableCell>
              <TableCell className="text-right tabular-nums text-slate-700">{account.patients}</TableCell>
              <TableCell className="text-sm text-slate-600">{account.last_access_at ? formatDateTime(account.last_access_at) : "Nunca"}</TableCell>
              <TableCell className="text-sm text-slate-600">{formatDay(account.created_at)}</TableCell>
              <TableCell><ActionsMenu account={account} onAction={onAction} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function AccountsCards({ items, onAction }) {
  return (
    <ul className="space-y-3 md:hidden" data-testid="admin-cards">
      {items.map((account) => (
        <li key={account.user_id}>
          <Card className="border-slate-200 p-4 shadow-sm">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate font-medium text-[#0F172A]">{account.name || "Sem nome"}</p>
                <p className="truncate text-xs text-slate-600">{account.email}</p>
              </div>
              <ActionsMenu account={account} onAction={onAction} />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2"><StatusBadge account={account} /><AccountTags account={account} /></div>
            {statusDetail(account) && <p className="mt-1 text-xs text-slate-600">{statusDetail(account)}</p>}
            <dl className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-600">
              <div><dt className="text-slate-500">Pacientes</dt><dd className="font-medium text-slate-800">{account.patients}</dd></div>
              <div><dt className="text-slate-500">Último acesso</dt><dd>{account.last_access_at ? formatDay(account.last_access_at) : "Nunca"}</dd></div>
              <div><dt className="text-slate-500">Cadastro</dt><dd>{formatDay(account.created_at)}</dd></div>
            </dl>
          </Card>
        </li>
      ))}
    </ul>
  );
}

