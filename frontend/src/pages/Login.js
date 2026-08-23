import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Stethoscope, Loader2, Mail, Lock } from "lucide-react";

const WAVE = "https://images.unsplash.com/photo-1620121478247-ec786b9be2fa?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NTYxODd8MHwxfHNlYXJjaHw0fHxhYnN0cmFjdCUyMHNvZnQlMjBibHVlJTIwd2F2ZXN8ZW58MHx8fHwxNzg3NDA0NDM4fDA&ixlib=rb-4.1.0&q=85";

export default function Login() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleEmailLogin = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      console.log("[login] POST start");
      const { data } = await api.post("/auth/login", { email, password });
      console.log("[login] POST ok, navigating");
      setUser(data.user);
      toast.success("Bem-vindo(a) de volta!");
      navigate("/dashboard", { replace: true });
    } catch (err) {
      console.log("[login] POST error", err?.response?.status, err?.message);
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = () => {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    const redirectUrl = window.location.origin + "/dashboard";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  return (
    <div className="flex min-h-screen">
      <div className="relative hidden w-1/2 lg:block">
        <img src={WAVE} alt="" className="absolute inset-0 h-full w-full object-cover" />
        <div className="absolute inset-0 bg-[#0F2C59]/75" />
        <div className="relative flex h-full flex-col justify-between p-12 text-white">
          <div className="flex items-center gap-2.5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-white/15">
              <Stethoscope className="h-5 w-5" />
            </div>
            <span className="font-head text-xl font-bold">PsiGestão</span>
          </div>
          <div>
            <h1 className="font-head text-4xl font-extrabold leading-tight">
              Cuidado clínico com segurança de dados.
            </h1>
            <p className="mt-4 max-w-md text-blue-100">
              Prontuários criptografados, trilha de auditoria e conformidade LGPD para
              profissionais de psicologia.
            </p>
          </div>
          <p className="text-xs text-blue-200">Criptografia AES-256 · HTTPS/TLS · CFP</p>
        </div>
      </div>

      <div className="flex w-full items-center justify-center px-6 lg:w-1/2">
        <div className="psi-fade-up w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-2.5">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[#1E3A8A]">
                <Stethoscope className="h-5 w-5 text-white" />
              </div>
              <span className="font-head text-xl font-bold text-[#0F172A]">PsiGestão</span>
            </div>
          </div>
          <h2 className="font-head text-2xl font-bold text-[#0F172A]">Entrar</h2>
          <p className="mt-1 text-sm text-slate-500">Acesse o painel do profissional</p>

          <form onSubmit={handleEmailLogin} className="mt-6 space-y-4">
            <div>
              <Label htmlFor="email" className="text-slate-700">E-mail</Label>
              <div className="relative mt-1.5">
                <Mail className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                <Input
                  id="email" type="email" required value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="voce@clinica.com" className="pl-9"
                  data-testid="login-email-input"
                />
              </div>
            </div>
            <div>
              <Label htmlFor="password" className="text-slate-700">Senha</Label>
              <div className="relative mt-1.5">
                <Lock className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                <Input
                  id="password" type="password" required value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••" className="pl-9"
                  data-testid="login-password-input"
                />
              </div>
            </div>
            <Button
              type="submit" disabled={loading}
              className="w-full bg-[#1E3A8A] hover:bg-[#0F2C59]"
              data-testid="login-submit-btn"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Entrar"}
            </Button>
          </form>

          <div className="my-5 flex items-center gap-3">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-xs text-slate-400">ou</span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>

          <Button
            variant="outline" onClick={handleGoogle}
            className="w-full gap-2 border-slate-300"
            data-testid="login-google-btn"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z" />
            </svg>
            Continuar com Google
          </Button>

          <p className="mt-6 text-center text-sm text-slate-500">
            Não tem conta?{" "}
            <Link to="/register" className="font-medium text-[#1E3A8A] hover:underline" data-testid="go-register-link">
              Cadastre-se
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
