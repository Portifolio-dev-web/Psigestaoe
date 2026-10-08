import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { FlaskConical, Loader2 } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useLinkToken } from "@/components/auth/useLinkToken";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PLAN_LABEL, SUBSCRIPTION_PATH, formatBRL } from "@/lib/subscription";

// ---------------------------------------------------------------------------
// PRODUTO (assinatura): "página de pagamento" do pagamento SIMULADO.
//
// Só existe no desenvolvimento: faz o papel da página do meio de pagamento
// real (Asaas, Mercado Pago...), para testar o caminho inteiro sem cobrança.
// O pedido chega num token curto no fragmento da URL (#token=..., lido e
// apagado da barra de endereço por useLinkToken), preso à conta e ao plano.
// "Pagar" vira, no servidor, o mesmo aviso que um meio real mandaria.
// ---------------------------------------------------------------------------

/** Plano do pedido, só para exibir (quem confere o token é o servidor). */
function planFromToken(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload.plan;
  } catch {
    return null;
  }
}

export default function SimulatedCheckout() {
  const token = useLinkToken();
  const navigate = useNavigate();
  const { user, setUser } = useAuth();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const plan = planFromToken(token);
  const price = plan && user?.subscription?.prices?.[plan];

  const send = async (outcome) => {
    setBusy(outcome);
    setError("");
    try {
      const { data } = await api.post("/billing/simulate", { token, outcome });
      setUser((current) => ({ ...current, ...data.user }));
      if (outcome === "recusar") {
        toast.info("Pagamento recusado (simulação). Nada mudou na assinatura.");
        navigate(SUBSCRIPTION_PATH);
      } else {
        navigate(`${SUBSCRIPTION_PATH}&retorno=1`);
      }
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      setBusy("");
    }
  };

  return (
    <div className="mx-auto max-w-md space-y-6">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Pagamento simulado</h1>
        <p className="mt-1 flex items-center gap-1.5 text-sm text-slate-600">
          <FlaskConical className="h-4 w-4" aria-hidden="true" /> Ambiente de desenvolvimento: nenhuma cobrança real.
        </p>
      </div>
      <Card className="border-slate-200 p-6 shadow-sm" data-testid="simulated-checkout">
        {!token || !plan ? (
          <p className="text-sm text-slate-700" role="alert">
            Pedido de pagamento não encontrado ou expirado. <Link to={SUBSCRIPTION_PATH} className="font-medium text-[#1E3A8A] underline">Escolha o plano de novo</Link>.
          </p>
        ) : (
          <>
            <p className="text-sm text-slate-600">Plano {PLAN_LABEL[plan]}</p>
            <p className="mt-1 font-head text-3xl font-extrabold text-[#0F172A]">{price ? formatBRL(price) : "—"}</p>
            <p className="mt-3 text-sm text-slate-600">
              Num meio de pagamento real, aqui a pessoa escolheria Pix, boleto ou cartão. Escolha o resultado:
            </p>
            {error && <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</p>}
            <div className="mt-5 flex flex-col gap-2">
              <Button onClick={() => send("pagar")} disabled={Boolean(busy)} className="bg-[#1E3A8A] hover:bg-[#0F2C59]"
                data-testid="sim-pay">
                {busy === "pagar" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Pagar (simulação)"}
              </Button>
              <Button variant="outline" onClick={() => send("recusar")} disabled={Boolean(busy)} className="border-slate-300"
                data-testid="sim-refuse">
                {busy === "recusar" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Recusar pagamento"}
              </Button>
              <Button variant="ghost" onClick={() => navigate(SUBSCRIPTION_PATH)} disabled={Boolean(busy)} className="text-[#1E3A8A]">
                Voltar sem pagar
              </Button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
