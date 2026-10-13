# billing.py
# ---------------------------------------------------------------------------
# PRODUTO (assinatura): planos, teste grátis, cortesia, tolerância e o modo
# somente leitura. Este módulo guarda só as REGRAS (sem banco, sem rotas):
# server.py grava, audita e expõe as rotas; os testes exercitam as regras aqui.
#
# Situações de uma conta (access_state):
#   courtesy   cortesia concedida pelo comando do servidor: tudo, sem cobrança;
#   active     período pago em vigor (inclusive cancelada, até o fim do período);
#   trialing   teste grátis de TRIAL_DAYS dias desde o cadastro, sem cartão;
#   past_due   o período pago acabou e a renovação não chegou: tudo continua
#              liberado por GRACE_DAYS dias, com aviso;
#   read_only  teste encerrado, atraso além da tolerância, ou assinatura
#              cancelada já vencida. A pessoa VÊ e EXPORTA tudo, mas não cria
#              nem edita pacientes, evoluções ou sessões.
#
# Por que somente leitura, e não bloqueio total: a psicóloga é obrigada a
# guardar os prontuários por 5 anos (Resolução CFP nº 001/2009) e a atender
# os pedidos dos pacientes (LGPD art. 18). Trancar a conta de quem parou de
# pagar a impediria de cumprir essas obrigações.
#
# O estado é calculado pelas DATAS (fim do teste, "pago até", início do
# atraso), não por um campo que alguém precise atualizar: se um aviso do meio
# de pagamento não chegar, a conta entra sozinha na tolerância e depois no
# somente leitura, nunca fica liberada para sempre.
#
# Meio de pagamento: por enquanto só o "simulado" (desenvolvimento e testes).
# Um meio real (Asaas, Mercado Pago, Stripe...) entra como mais uma classe com
# a mesma interface de BillingProvider; nenhuma regra daqui muda.
# ---------------------------------------------------------------------------
import calendar
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger("psigestao.billing")

PLANS = ("monthly", "annual")
PLAN_MONTHS = {"monthly": 1, "annual": 12}
PLAN_LABELS = {"monthly": "mensal", "annual": "anual"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


# Configuração (variáveis de ambiente; ver backend/.env.example). Preços em
# CENTAVOS, para nunca haver arredondamento de ponto flutuante em dinheiro.
TRIAL_DAYS = _int_env("TRIAL_DAYS", 14)
GRACE_DAYS = _int_env("BILLING_GRACE_DAYS", 7)
PRICES = {
    "monthly": _int_env("PRICE_MONTHLY_CENTS", 5900),
    "annual": _int_env("PRICE_ANNUAL_CENTS", 59000),
}
BILLING_PROVIDER = os.environ.get("BILLING_PROVIDER", "").strip().lower()
KNOWN_PROVIDERS = ("", "simulado")

READ_ONLY_MESSAGES = {
    "trial_ended": "Seu teste grátis terminou. Você continua vendo e exportando tudo; "
                   "para registrar de novo, assine um plano.",
    "payment_overdue": "O pagamento da sua assinatura está em atraso. Você continua vendo e exportando tudo; "
                       "regularize o pagamento para registrar de novo.",
    "canceled": "Sua assinatura foi encerrada. Você continua vendo e exportando tudo; "
                "assine de novo para registrar.",
}


def validate_config(environment: str) -> None:
    """Fail-closed (mesma regra do resto do servidor): configuração inválida
    impede a subida. O pagamento SIMULADO libera a assinatura sem cobrar
    nada, então nunca pode rodar em produção."""
    if BILLING_PROVIDER not in KNOWN_PROVIDERS:
        raise SystemExit(f"BILLING_PROVIDER '{BILLING_PROVIDER}' desconhecido. Use: vazio (sem cobrança) ou simulado.")
    if BILLING_PROVIDER == "simulado" and environment == "production":
        raise SystemExit("SECURITY ALERT: BILLING_PROVIDER=simulado libera assinaturas sem pagamento; "
                         "não pode ser usado em produção.")
    if TRIAL_DAYS < 0 or GRACE_DAYS < 0 or min(PRICES.values()) <= 0:
        raise SystemExit("Configuração de assinatura inválida: TRIAL_DAYS e BILLING_GRACE_DAYS >= 0, preços > 0.")
    if environment == "production" and not BILLING_PROVIDER:
        # Sem meio de pagamento em produção: o sistema funciona (teste grátis
        # e cortesia valem), mas ninguém consegue assinar. Avisa uma vez.
        logger.warning("Nenhum meio de pagamento configurado (BILLING_PROVIDER vazio): "
                       "o botão Assinar mostra 'pagamento indisponível'.")


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------
def _utc(value: Optional[datetime]) -> Optional[datetime]:
    """O SQLite (testes) devolve datas sem fuso; o Postgres, com. Tudo vira UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def add_months(value: datetime, months: int) -> datetime:
    """Soma meses de calendário: 31/01 + 1 mês = 28 ou 29/02 (último dia), como os
    meios de pagamento fazem. Sem dependência extra (python-dateutil)."""
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def trial_end(user) -> datetime:
    """Fim do teste grátis. Contas anteriores ao campo usam o cadastro + TRIAL_DAYS."""
    created = _utc(getattr(user, "created_at", None)) or datetime.now(timezone.utc)
    return _utc(user.trial_ends_at) or created + timedelta(days=TRIAL_DAYS)


def _days_left(until: datetime, now: datetime) -> int:
    """Dias que faltam, arredondando para cima (faltam 2 h = "1 dia")."""
    seconds = (until - now).total_seconds()
    return max(0, int(-(-seconds // 86400)))


# ---------------------------------------------------------------------------
# Situação da conta
# ---------------------------------------------------------------------------
def access_state(user, now: Optional[datetime] = None) -> dict:
    """Situação da assinatura e se a conta pode GRAVAR (criar/editar).

    Ordem das regras: cortesia > período pago > atraso dentro da tolerância >
    teste grátis > somente leitura. Retorna só dados, sem nada pessoal.
    """
    now = now or datetime.now(timezone.utc)
    status = user.subscription_status or "none"
    paid_until = _utc(user.paid_until)
    trial_until = trial_end(user)
    base = {
        "plan": user.subscription_plan or None,
        "status": status,
        "trial_ends_at": trial_until.isoformat(),
        "paid_until": paid_until.isoformat() if paid_until else None,
        "cancel_at_period_end": bool(user.cancel_at_period_end),
        "courtesy": user.courtesy_at is not None,
        "provider": user.billing_provider or None,
        "days_left": None,
        "grace_ends_at": None,
        "reason": None,
    }

    def result(state: str, can_write: bool, **extra) -> dict:
        return {**base, "state": state, "can_write": can_write, **extra}

    if user.courtesy_at is not None:
        return result("courtesy", True)

    # Período pago em vigor. Cancelada continua valendo até o fim do que foi pago.
    if paid_until and now < paid_until:
        return result("active", True, days_left=_days_left(paid_until, now))

    # O período pago acabou sem renovação (aviso de atraso recebido OU aviso de
    # renovação que nunca chegou): tolerância a partir do primeiro dos dois.
    # Cancelada não tem tolerância: a pessoa decidiu encerrar.
    if paid_until and status in ("active", "past_due"):
        overdue_since = min(d for d in (_utc(user.past_due_since), paid_until) if d is not None)
        grace_until = overdue_since + timedelta(days=GRACE_DAYS)
        if now < grace_until:
            return result("past_due", True, grace_ends_at=grace_until.isoformat(),
                          days_left=_days_left(grace_until, now))
        return result("read_only", False, reason="payment_overdue")

    if now < trial_until and not paid_until:
        return result("trialing", True, days_left=_days_left(trial_until, now))

    return result("read_only", False, reason="canceled" if status == "canceled" else "trial_ended")


def public_state(user, now: Optional[datetime] = None) -> dict:
    """O que vai para o navegador (/auth/me): a situação, os preços e se há
    meio de pagamento configurado (sem ele, o botão Assinar avisa)."""
    return {
        **access_state(user, now),
        "prices": dict(PRICES),
        "trial_days": TRIAL_DAYS,
        "grace_days": GRACE_DAYS,
        "checkout_available": bool(BILLING_PROVIDER),
        "simulated": BILLING_PROVIDER == "simulado",
    }


# ---------------------------------------------------------------------------
# Eventos do meio de pagamento (normalizados)
# ---------------------------------------------------------------------------
@dataclass
class BillingEvent:
    """Um aviso do meio de pagamento, já traduzido para o nosso vocabulário.

    `event_id` é o identificador DO MEIO DE PAGAMENTO: o mesmo aviso recebido
    duas vezes (os meios reenviam até receber 200) é aplicado uma vez só.
    `period_end`: fim do período pago, quando o meio informa (o simulado calcula).
    """
    event_id: str
    kind: str  # payment_confirmed | payment_overdue | subscription_canceled | payment_refunded
    user_id: Optional[str] = None
    plan: Optional[str] = None
    period_end: Optional[datetime] = None


def _br_date(value: datetime) -> str:
    return value.astimezone(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y")


def apply_event(user, event: BillingEvent, now: Optional[datetime] = None) -> str:
    """Aplica o evento à conta (só altera os campos de assinatura) e devolve o
    texto para a trilha de auditoria (sem dado pessoal)."""
    now = now or datetime.now(timezone.utc)
    paid_until = _utc(user.paid_until)
    user.billing_updated_at = now

    if event.kind == "payment_confirmed":
        plan = event.plan if event.plan in PLANS else (user.subscription_plan or "monthly")
        if event.period_end:
            new_until = _utc(event.period_end)
        else:
            # Renovação soma a partir do fim do período atual; o primeiro
            # pagamento durante o teste soma a partir do FIM DO TESTE (quem
            # assina cedo não perde os dias de teste que ainda tinha).
            starts = [now] + ([paid_until] if paid_until else [trial_end(user)])
            new_until = add_months(max(starts), PLAN_MONTHS[plan])
        user.subscription_plan = plan
        user.subscription_status = "active"
        user.paid_until = new_until
        user.past_due_since = None
        user.cancel_at_period_end = False
        return f"Pagamento confirmado: plano {PLAN_LABELS[plan]}, acesso até {_br_date(new_until)}"

    if event.kind == "payment_overdue":
        user.subscription_status = "past_due"
        user.past_due_since = _utc(user.past_due_since) or now
        return "Pagamento em atraso: tolerância de %d dias iniciada" % GRACE_DAYS

    if event.kind == "subscription_canceled":
        user.subscription_status = "canceled"
        user.cancel_at_period_end = True
        until = f"acesso até {_br_date(paid_until)}" if paid_until and paid_until > now else "sem período pago em vigor"
        return f"Assinatura cancelada ({until})"

    if event.kind == "payment_refunded":
        # Estorno/contestação: o período pago deixa de valer agora.
        user.subscription_status = "canceled"
        user.paid_until = now
        user.cancel_at_period_end = False
        return "Pagamento estornado: período pago encerrado"

    raise ValueError(f"Evento de pagamento desconhecido: {event.kind}")


# ---------------------------------------------------------------------------
# Meios de pagamento
# ---------------------------------------------------------------------------
class BillingProvider:
    """Interface de um meio de pagamento. Um meio real implementa:
      - start_checkout: cria a cobrança/assinatura no meio e devolve o endereço
        da PÁGINA DE PAGAMENTO dele (nenhum dado de cartão passa pelo nosso servidor);
      - cancel: cancela a renovação (o período pago continua valendo);
      - parse_webhook: confere a autenticidade do aviso (token/assinatura) e o
        traduz em BillingEvent; aviso inválido levanta PermissionError;
      - checkout_hosts: os domínios da página de pagamento do meio (análise da
        assinatura, AS7). O servidor e a tela só levam a pessoa para um desses
        (ou para o próprio sistema); qualquer outro endereço é recusado.
    """
    name = ""
    checkout_hosts: tuple = ()

    async def start_checkout(self, user, plan: str) -> str:
        raise NotImplementedError

    async def cancel(self, user) -> None:
        raise NotImplementedError

    def parse_webhook(self, headers: dict, body: bytes) -> list:
        raise NotImplementedError


class SimulatedProvider(BillingProvider):
    """Pagamento SIMULADO, só para desenvolvimento e testes (validate_config o
    proíbe em produção). A "página de pagamento" é uma tela do próprio app
    (/assinatura/simulacao) com os botões Pagar e Recusar, que chamam a rota de
    simulação; o resultado passa pelo MESMO apply_event de um meio real."""
    name = "simulado"

    def __init__(self, make_checkout_token: Callable[[object, str], str], frontend_link: Callable[..., str]):
        self._make_token = make_checkout_token
        self._link = frontend_link

    async def start_checkout(self, user, plan: str) -> str:
        return self._link("/assinatura/simulacao", self._make_token(user, plan))

    async def cancel(self, user) -> None:
        return None  # nada externo a cancelar

    def parse_webhook(self, headers: dict, body: bytes) -> list:
        raise PermissionError("O pagamento simulado não recebe avisos externos.")
