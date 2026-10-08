// ---------------------------------------------------------------------------
// PRODUTO (assinatura): textos e formatos compartilhados pelas telas.
// A situação da conta vem pronta do servidor (user.subscription, calculada em
// backend/billing.py); aqui só se traduz para a tela. Os motivos do somente
// leitura repetem as mensagens do servidor (READ_ONLY_MESSAGES).
// ---------------------------------------------------------------------------

export const PLAN_LABEL = { monthly: "mensal", annual: "anual" };

/** Centavos -> "R$ 59,00" (dinheiro sempre em centavos: sem erro de arredondamento). */
export const formatBRL = (cents) =>
  (Number(cents || 0) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

/** ISO -> "08/11/2026" no fuso do navegador. */
export const formatDay = (iso) => (iso ? new Date(iso).toLocaleDateString("pt-BR") : "");

/** "1 dia" / "3 dias". */
export const daysLabel = (n) => `${n} ${n === 1 ? "dia" : "dias"}`;

// Por que a conta está no somente leitura (frase curta, para a faixa de aviso).
export const READ_ONLY_REASON = {
  trial_ended: "seu teste grátis terminou",
  payment_overdue: "o pagamento da assinatura está em atraso",
  canceled: "sua assinatura foi encerrada",
};

// O que continua liberado no somente leitura (mesma regra do servidor: get_writer).
export const READ_ONLY_ALLOWED =
  "Você continua vendo e exportando tudo, corrigindo cadastros e atendendo pedidos LGPD dos pacientes.";

export const SUBSCRIPTION_PATH = "/configuracoes?aba=assinatura";
