// ---------------------------------------------------------------------------
// Fluxo clínico (C2): aviso de que as evoluções pendentes podem ter mudado.
// O contador do menu (AppLayout) e o bloco do painel (Dashboard) recarregam ao
// ouvir este evento. Disparado ao registrar uma evolução, ao salvar ou remover
// uma sessão e ao marcar uma falta.
// ---------------------------------------------------------------------------
export const PENDING_EVOLUTIONS_EVENT = "psi:pending-evolutions-changed";

export function notifyPendingEvolutionsChanged() {
  window.dispatchEvent(new Event(PENDING_EVOLUTIONS_EVENT));
}
