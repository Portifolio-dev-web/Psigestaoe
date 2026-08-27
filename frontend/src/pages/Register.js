import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { Stethoscope, Loader2 } from "lucide-react";

const WAVE = "https://images.unsplash.com/photo-1620121478247-ec786b9be2fa?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NTYxODd8MHwxfHNlYXJjaHw0fHxhYnN0cmFjdCUyMHNvZnQlMjBibHVlJTIwd2F2ZXN8ZW58MHx8fHwxNzg3NDA0NDM4fDA&ixlib=rb-4.1.0&q=85";

export default function Register() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [terms, setTerms] = useState(false);
  const [loading, setLoading] = useState(false);

  const upd = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!terms) {
      toast.warning("É necessário aceitar os Termos de Privacidade (LGPD).");
      return;
    }
    setLoading(true);
    try {
      const { data } = await api.post("/auth/register", { ...form, terms_accepted: terms });
      setUser(data.user);
      toast.success("Conta criada com sucesso!");
      navigate("/dashboard", { replace: true });
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
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
              Comece a organizar sua clínica hoje.
            </h1>
            <p className="mt-4 max-w-md text-blue-100">
              Cadastro rápido e seguro. Seus prontuários protegidos por criptografia
              de ponta e conformidade com o CFP.
            </p>
          </div>
          <p className="text-xs text-blue-200">Criptografia AES-256 · HTTPS/TLS · CFP</p>
        </div>
      </div>

      <div className="flex w-full items-center justify-center px-6 py-10 lg:w-1/2">
        <div className="psi-fade-up w-full max-w-sm">
          <h2 className="font-head text-2xl font-bold text-[#0F172A]">Criar conta</h2>
          <p className="mt-1 text-sm text-slate-500">Cadastro de profissional</p>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <div>
              <Label htmlFor="name" className="text-slate-700">Nome completo</Label>
              <Input id="name" required value={form.name} onChange={upd("name")}
                className="mt-1.5" placeholder="Dra. Maria Silva" data-testid="register-name-input" />
            </div>
            <div>
              <Label htmlFor="email" className="text-slate-700">E-mail</Label>
              <Input id="email" type="email" required value={form.email} onChange={upd("email")}
                className="mt-1.5" placeholder="voce@clinica.com" data-testid="register-email-input" />
            </div>
            <div>
              <Label htmlFor="password" className="text-slate-700">Senha</Label>
              <Input id="password" type="password" required value={form.password} onChange={upd("password")}
                className="mt-1.5" placeholder="Mínimo 6 caracteres" data-testid="register-password-input" />
            </div>
            <div className="flex items-start gap-2.5 rounded-md bg-[#F0F4F8] p-3">
              <Checkbox id="terms" checked={terms} onCheckedChange={setTerms}
                className="mt-0.5" data-testid="register-terms-checkbox" />
              <Label htmlFor="terms" className="text-xs leading-relaxed text-slate-600">
                Li e aceito os <span className="font-medium text-[#1E3A8A]">Termos de Privacidade</span> e
                autorizo o tratamento de dados de saúde conforme a LGPD.
              </Label>
            </div>
            <Button type="submit" disabled={loading}
              className="w-full bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="register-submit-btn">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Criar conta"}
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-slate-500">
            Já tem conta?{" "}
            <Link to="/login" className="font-medium text-[#1E3A8A] hover:underline" data-testid="go-login-link">
              Entrar
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
