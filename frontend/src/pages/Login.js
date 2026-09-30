import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Stethoscope, Loader2, Mail, Lock } from "lucide-react";
// Importar o componente de Login do Google (necessita de 'npm install @react-oauth/google')
import { GoogleLogin } from "@react-oauth/google";

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
      const { data } = await api.post("/auth/login", { email, password });
      setUser(data.user);
      toast.success("Bem-vindo(a) de volta!");
      navigate("/dashboard", { replace: true });
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  // Nova função para comunicar com a rota /auth/google do backend
  const handleGoogleSuccess = async (credentialResponse) => {
    setLoading(true);
    try {
      // Envia o token JWT (credential) para a API
      const { data } = await api.post("/auth/google", { 
        credential: credentialResponse.credential 
      });
      setUser(data.user);
      toast.success("Autenticação com Google efetuada com sucesso!");
      navigate("/dashboard", { replace: true });
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Erro na autenticação do Google");
    } finally {
      setLoading(false);
    }
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

          {/* Substitui o botão original pelo componente oficial da Google */}
          <div className="flex w-full justify-center" data-testid="login-google-btn">
            <GoogleLogin
              onSuccess={handleGoogleSuccess}
              onError={() => toast.error("Ocorreu um erro no processo de login com o Google.")}
              shape="rectangular"
              size="large"
              width="384"
            />
          </div>

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