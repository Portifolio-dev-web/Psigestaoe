import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { CheckCircle2, Loader2, TriangleAlert } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import AuthShell from "@/components/auth/AuthShell";
import NewPasswordForm from "@/components/auth/NewPasswordForm";
import { useLinkToken } from "@/components/auth/useLinkToken";

// ---------------------------------------------------------------------------
// As duas telas abertas pelos links enviados por e-mail (segurança média):
//   - "signup"          (/criar-senha):     fim do cadastro. Cria a senha e a
//                                           conta nasce, já com a sessão aberta;
//   - "password_reset"  (/redefinir-senha): nova senha. Encerra as sessões de
//                                           todos os aparelhos; a pessoa entra
//                                           de novo pelo login (e pela
//                                           verificação em duas etapas, se ativa).
// A tela confere o link antes de mostrar o formulário: link vencido ou já
// usado mostra o motivo e o caminho para pedir outro.
// ---------------------------------------------------------------------------
const MODES = {
  signup: {
    headline: "Falta pouco para começar.",
    tagline: "Crie a senha da sua conta. O e-mail já foi confirmado pelo link.",
    title: "Criar sua senha",
    submit: "Criar conta",
    retry: { to: "/register", label: "Fazer o cadastro de novo" },
  },
  password_reset: {
    headline: "Recupere o acesso com segurança.",
    tagline: "A nova senha encerra as sessões abertas em todos os aparelhos.",
    title: "Criar nova senha",
    submit: "Salvar nova senha",
    retry: { to: "/esqueci-senha", label: "Pedir um novo link" },
  },
};

export default function EmailLinkPassword({ purpose }) {
  const mode = MODES[purpose];
  const token = useLinkToken();
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [link, setLink] = useState(null); // null = conferindo; false = inválido; {email, name}
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) {
      setLink(false);
      return;
    }
    api.post("/auth/email-link/check", { token, purpose })
      .then(({ data }) => setLink(data))
      .catch(() => setLink(false));
  }, [token, purpose]);

  const submit = async (password) => {
    setSaving(true);
    try {
      if (purpose === "signup") {
        const { data } = await api.post("/auth/register/confirm", { token, password });
        setUser(data.user);
        toast.success("Conta criada! Bem-vindo(a) ao PsiGestão.");
        navigate("/dashboard", { replace: true });
      } else {
        await api.post("/auth/password/reset", { token, new_password: password });
        setDone(true);
      }
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (detail?.code === "invalid_link") setLink(false);
      toast.error(formatApiErrorDetail(detail) || err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <AuthShell headline={mode.headline} tagline={mode.tagline}>
      {link === null ? (
        <div className="flex justify-center py-10" role="status" aria-label="Conferindo o link">
          <Loader2 className="h-6 w-6 animate-spin text-[#1E3A8A]" />
        </div>
      ) : link === false ? (
        <div data-testid="email-link-invalid">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-amber-50 text-amber-700">
            <TriangleAlert className="h-5 w-5" aria-hidden="true" />
          </div>
          <h1 className="mt-4 font-head text-2xl font-bold text-[#0F172A]">Link inválido ou vencido</h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-600">
            Este link já foi usado, venceu ou foi substituído por um mais novo. Por segurança, cada link vale uma vez só.
          </p>
          <Button asChild className="mt-6 w-full bg-[#1E3A8A] hover:bg-[#0F2C59]">
            <Link to={mode.retry.to}>{mode.retry.label}</Link>
          </Button>
        </div>
      ) : done ? (
        <div data-testid="password-reset-done">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
            <CheckCircle2 className="h-5 w-5" aria-hidden="true" />
          </div>
          <h1 className="mt-4 font-head text-2xl font-bold text-[#0F172A]">Senha alterada</h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-600" role="status">
            Pronto. As sessões abertas em outros aparelhos foram encerradas. Entre com a nova senha.
          </p>
          <Button asChild className="mt-6 w-full bg-[#1E3A8A] hover:bg-[#0F2C59]">
            <Link to="/login">Ir para o login</Link>
          </Button>
        </div>
      ) : (
        <>
          <h1 className="font-head text-2xl font-bold text-[#0F172A]">{mode.title}</h1>
          {/* Nome completo: o primeiro "nome" costuma ser um título ("Dra."). */}
          <p className="mt-1 text-sm text-slate-500">
            {link.name ? `${link.name}, a` : "A"} conta é <b className="text-[#0F172A]">{link.email}</b>.
          </p>
          <NewPasswordForm email={link.email} submitLabel={mode.submit} loading={saving} onSubmit={submit} />
        </>
      )}
    </AuthShell>
  );
}
