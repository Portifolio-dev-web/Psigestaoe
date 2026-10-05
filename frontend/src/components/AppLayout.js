import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { useIdleLock } from "@/context/IdleLockContext";
import {
  LayoutDashboard, Users, CalendarDays, Settings, LogOut, Shield, Menu, Stethoscope, Lock, Loader2,
} from "lucide-react";
import { Suspense, useState } from "react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

const nav = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard, id: "dashboard" },
  { to: "/pacientes", label: "Pacientes", icon: Users, id: "pacientes" },
  { to: "/agenda", label: "Agenda", icon: CalendarDays, id: "agenda" },
  // UX (Configurações em abas): a página reúne conta, termo, privacidade e
  // auditoria; o nome antigo ("Privacidade & LGPD") escondia a troca de senha.
  { to: "/configuracoes", label: "Configurações", icon: Settings, id: "config" },
];

export default function AppLayout() {
  const { user, logout } = useAuth();
  const { lockNow } = useIdleLock();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  const initials = (user?.name || user?.email || "P")
    .split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();

  const SidebarContent = () => (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-6 py-6">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/15">
          <Stethoscope className="h-5 w-5 text-white" strokeWidth={1.8} />
        </div>
        <div className="leading-tight">
          <p className="font-head text-lg font-800 font-bold text-white">PsiGestão</p>
          <p className="text-[11px] text-blue-200">Prontuários de Psicologia</p>
        </div>
      </div>
      <nav className="flex-1 space-y-1 px-3 py-2">
        {nav.map((item) => (
          <NavLink
            key={item.id}
            to={item.to}
            onClick={() => setOpen(false)}
            data-testid={`nav-${item.id}`}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors duration-200 ${
                isActive
                  ? "bg-white/15 text-white"
                  : "text-blue-100/80 hover:bg-white/10 hover:text-white"
              }`
            }
          >
            <item.icon className="h-[18px] w-[18px]" strokeWidth={1.6} />
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-white/10 px-3 py-4">
        <div className="flex items-center gap-3 rounded-md px-3 py-2">
          <Avatar className="h-9 w-9 border border-white/20">
            <AvatarImage src={user?.picture} />
            {/* Acessibilidade (varredura de UI): fundo #0284C7 -> #0369A1 (contraste 4,1:1 -> 5,9:1). */}
            <AvatarFallback className="bg-[#0369A1] text-xs text-white">{initials}</AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-white">{user?.name || "Profissional"}</p>
            <p className="truncate text-[11px] text-blue-200">{user?.email}</p>
          </div>
        </div>
        <Button
          data-testid="logout-btn"
          onClick={logout}
          variant="ghost"
          className="mt-1 w-full justify-start gap-3 px-3 text-sm text-blue-100/80 hover:bg-white/10 hover:text-white"
        >
          <LogOut className="h-[18px] w-[18px]" strokeWidth={1.6} /> Sair
        </Button>
        {/* Conformidade: a política fica sempre a um clique (LGPD, art. 9º: acesso facilitado). */}
        <NavLink to="/privacidade" onClick={() => setOpen(false)}
          className="mt-2 block px-3 text-[11px] text-blue-200/70 hover:text-white hover:underline">
          Política de Privacidade
        </NavLink>
      </div>
    </div>
  );

  // Acessibilidade (análise de UI, A3): o menu do celular era uma gaveta feita
  // à mão. O foco ficava no botão de abrir, o Tab ia para a tela ESCONDIDA atrás
  // da gaveta, Esc não fechava e o botão não dizia se o menu estava aberto.
  // Agora é a gaveta do Radix (components/ui/sheet.jsx), que é um diálogo de
  // verdade: o foco entra no menu e fica nele, Esc e o toque fora fecham, o
  // foco volta ao botão ao fechar e o botão informa "expandido/recolhido".
  return (
    <Sheet open={open} onOpenChange={setOpen}>
    <div className="flex min-h-screen bg-[#F8FAFC]">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-64 bg-[#0F2C59] lg:block">
        <SidebarContent />
      </aside>

      {/* Mobile drawer. aria-describedby={undefined}: o menu não precisa de descrição longa. */}
      <SheetContent side="left" closeLabel="Fechar menu" aria-describedby={undefined}
        className="w-64 border-none bg-[#0F2C59] p-0 text-white sm:max-w-none lg:hidden" data-testid="mobile-menu">
        {/* Nome do diálogo para o leitor de tela ("Menu"); não aparece na tela. */}
        <SheetTitle className="sr-only">Menu</SheetTitle>
        <SheetDescription className="sr-only">Navegação do PsiGestão</SheetDescription>
        <SidebarContent />
      </SheetContent>

      <div className="flex min-w-0 flex-1 flex-col lg:pl-64">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur lg:px-8">
          {/* SheetTrigger: o botão anuncia se o menu está aberto (aria-expanded) e
              recebe o foco de volta ao fechar. Área de toque de 40 px (antes 20 px). */}
          <SheetTrigger asChild>
            <button
              className="-ml-2 flex h-10 w-10 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A] lg:hidden"
              aria-label="Abrir menu"
              data-testid="open-sidebar"
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
            </button>
          </SheetTrigger>
          <div className="flex-1" />
          <div className="flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
            {/* Antes: "(AES-256)", algoritmo que o sistema não usa. Ver legal.py, item 7. */}
            <Shield className="h-3.5 w-3.5" /> Dados criptografados
          </div>
          {/* PRODUTO: travar a tela ao sair da sala, sem esperar a inatividade. */}
          <Button variant="outline" size="sm" onClick={() => lockNow("manual")}
            className="gap-1.5 border-slate-300 text-slate-600" title="Bloquear a tela agora" aria-label="Bloquear a tela agora" data-testid="lock-now-btn">
            <Lock className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Bloquear</span>
          </Button>
        </header>
        {/* tabIndex -1 + sem contorno: recebe o foco ao trocar de tela (análise de UI, A1,
            components/RouteAnnouncer.js) sem desenhar uma moldura em volta da tela inteira. */}
        <main tabIndex={-1} className="flex-1 px-4 py-6 focus:outline-none lg:px-8">
          {/* Complementares (desempenho): as telas são carregadas sob demanda
              (App.js). Este Suspense deixa o menu e o topo na tela enquanto só
              o conteúdo carrega, em vez de piscar a página inteira. */}
          <Suspense fallback={(
            <div className="flex justify-center py-16" role="status" aria-label="Carregando">
              <Loader2 className="h-6 w-6 animate-spin text-[#1E3A8A]" />
            </div>
          )}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
    </Sheet>
  );
}
