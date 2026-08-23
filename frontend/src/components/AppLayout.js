import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import {
  LayoutDashboard, Users, CalendarDays, Settings, LogOut, Shield, Menu, X, Stethoscope,
} from "lucide-react";
import { useState } from "react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";

const nav = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard, id: "dashboard" },
  { to: "/pacientes", label: "Pacientes", icon: Users, id: "pacientes" },
  { to: "/agenda", label: "Agenda", icon: CalendarDays, id: "agenda" },
  { to: "/configuracoes", label: "Privacidade & LGPD", icon: Shield, id: "config" },
];

export default function AppLayout() {
  const { user, logout } = useAuth();
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
            <AvatarFallback className="bg-[#0284C7] text-xs text-white">{initials}</AvatarFallback>
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
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen bg-[#F8FAFC]">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-64 bg-[#0F2C59] lg:block">
        <SidebarContent />
      </aside>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-slate-900/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 bg-[#0F2C59]">
            <button
              className="absolute right-3 top-4 text-white/70"
              onClick={() => setOpen(false)}
              data-testid="close-sidebar"
            >
              <X className="h-5 w-5" />
            </button>
            <SidebarContent />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col lg:pl-64">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur lg:px-8">
          <button
            className="lg:hidden text-slate-600"
            onClick={() => setOpen(true)}
            data-testid="open-sidebar"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div className="flex-1" />
          <div className="flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
            <Shield className="h-3.5 w-3.5" /> Dados criptografados (AES-256)
          </div>
        </header>
        <main className="flex-1 px-4 py-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
