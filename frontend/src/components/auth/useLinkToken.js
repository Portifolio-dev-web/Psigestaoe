import { useEffect, useState } from "react";

// ---------------------------------------------------------------------------
// Token dos links enviados por e-mail (criar senha, nova senha).
// Segurança média: o servidor coloca o token no FRAGMENTO do link
// (/redefinir-senha#token=...). O navegador nunca envia o fragmento a servidor
// nenhum, então ele não fica em logs de acesso nem no cabeçalho Referer. Ao
// abrir a página, o token é lido e depois TIRADO da barra de endereço
// (replaceState): não fica no histórico nem vai junto se a pessoa copiar o
// endereço. A limpeza fica num efeito (e não no inicializador do estado),
// porque o React em modo estrito roda o inicializador duas vezes.
// ---------------------------------------------------------------------------
const readToken = () => (window.location.hash.match(/token=([A-Za-z0-9_-]+)/) || [])[1] || "";

export function useLinkToken() {
  const [token] = useState(readToken);
  useEffect(() => {
    if (window.location.hash) {
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    }
  }, []);
  return token;
}
