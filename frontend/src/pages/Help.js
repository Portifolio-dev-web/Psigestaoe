import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  CalendarDays, CreditCard, FileText, Keyboard, LifeBuoy, Loader2, Mail, Rocket, ShieldCheck, Users,
} from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

// ---------------------------------------------------------------------------
// AJUDA (análise de UI, O2).
//
// Antes não havia "Ajuda" nem contato de suporte em nenhuma tela, e o contato
// do encarregado de dados (LGPD, art. 41) só aparecia dentro da política.
// Esta página reúne:
//   - respostas curtas para as dúvidas do dia a dia (agenda, evoluções,
//     pacientes, segurança), com o caminho exato na tela;
//   - os atalhos de teclado do sistema;
//   - o contato do suporte e do encarregado de dados (GET /api/legal/contact,
//     configurados no servidor: SUPPORT_EMAIL e PRIVACY_DPO_*).
// Abre COM e SEM login (rota /ajuda, ver App.js): quem não consegue entrar é
// quem mais precisa de ajuda. Logada, fica dentro do sistema (menu ao lado) e
// pode trazer de volta os "Primeiros passos" do painel (O1).
// As perguntas usam <details>: nativas, abrem com Enter/Espaço e o leitor de
// tela anuncia "recolhido/expandido" sem código extra.
// ---------------------------------------------------------------------------

const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
const MOD = IS_MAC ? "⌘" : "Ctrl";

const TOPICS = [
  {
    id: "agenda", icon: CalendarDays, title: "Agenda",
    items: [
      ["Como marcar as sessões semanais de uma vez?",
        "Em Agenda > Nova sessão, escolha o paciente e use “Repetir”: toda semana ou a cada 2 semanas, de 4 a 52 sessões. Os horários já ocupados são conferidos antes de salvar."],
      ["Como agendar a partir da ficha do paciente?",
        "Na ficha, o cartão “Agenda” mostra as próximas sessões e o botão “Agendar sessão”, já com o paciente escolhido."],
      ["Como remarcar uma sessão?",
        "Na visão Semana ou Dia, arraste a sessão para o novo horário (o aviso tem “Desfazer”). Pelo teclado, abra a sessão e mude o início."],
      ["O paciente faltou. O que eu faço?",
        "Abra a sessão e escolha o status “Faltou”. O horário fica livre, a sessão deixa de pedir evolução e a falta entra no histórico do paciente."],
      ["Como mudar as horas que aparecem na agenda?",
        "Em Configurações > Agenda, defina o horário de atendimento. Sessões fora dele continuam aparecendo, e a agenda tem “Mostrar 24 horas”."],
    ],
  },
  {
    id: "evolucoes", icon: FileText, title: "Evoluções",
    items: [
      ["Como registrar a evolução de uma sessão?",
        "No Painel (“Hoje” ou “Evoluções pendentes”) ou na sessão da agenda, use “Registrar evolução”: o editor abre com a data e a hora da sessão. Na ficha, use “Nova evolução”."],
      ["Fechei o editor sem salvar. Perdi o texto?",
        "Não. Enquanto você escreve, o texto é guardado criptografado neste aparelho. Ao abrir a evolução de novo, o rascunho volta. Rascunhos vencem em 7 dias."],
      ["Dá para escrever com mais espaço?",
        `Sim: o botão de tela cheia fica no canto do editor, ao lado do “Fechar”. ${MOD}+Enter salva sem tirar as mãos do teclado.`],
      ["Posso corrigir uma evolução já salva?",
        "Sim. A versão anterior não se perde: fica no “Histórico de versões” da evolução."],
    ],
  },
  {
    id: "pacientes", icon: Users, title: "Pacientes",
    items: [
      ["Como acho um paciente pelo telefone?",
        "A busca de Pacientes (e a da agenda) aceita o nome, o telefone (pelo menos 8 números, com ou sem DDD) ou o CPF, inclusive os do responsável."],
      ["Paciente menor de idade: o que muda?",
        "Pela data de nascimento, o cadastro pede os dados do responsável legal, que é quem dá o consentimento."],
      ["Arquivar, anonimizar ou excluir?",
        "Arquivar tira o paciente da lista ativa sem apagar nada. O prontuário tem guarda obrigatória de 5 anos (CFP), por isso nem sempre é possível excluir. Anonimizar apaga os dados de identificação e mantém os prontuários."],
    ],
  },
  {
    id: "seguranca", icon: ShieldCheck, title: "Segurança e privacidade",
    items: [
      ["Por que a tela bloqueou?",
        "Por inatividade (5 a 30 minutos, em Configurações > Minha conta). Um aviso aparece 1 minuto antes. Desbloqueie com a sua senha (ou com o Google): o que estava aberto continua lá."],
      ["Como deixar a conta mais protegida?",
        "Ative a verificação em duas etapas e confira os “Aparelhos conectados”, ambos em Configurações > Minha conta."],
      ["Como baixar os meus dados?",
        "O prontuário de um paciente sai em PDF ou JSON pela ficha (“Exportar prontuário”). Todos os dados da conta: Configurações > Minha conta > “Baixar todos os meus dados (ZIP)”."],
    ],
  },
];

// Produto (assinatura): perguntas sobre teste, planos e somente leitura. Os números
// (dias de teste, tolerância, preços) vêm da conta quando há login; sem login,
// os padrões do servidor (backend/billing.py).
function billingTopic(sub) {
  const trialDays = sub?.trial_days ?? 14;
  const graceDays = sub?.grace_days ?? 7;
  return {
    id: "assinatura", icon: CreditCard, title: "Assinatura",
    items: [
      ["Como funciona o teste grátis?",
        `Ao criar a conta você tem ${trialDays} dias com tudo liberado, sem cartão. Se assinar durante o teste, os dias que sobrarem não se perdem: o período pago começa depois deles.`],
      ["Como assino ou mudo de plano?",
        "Em Configurações > Assinatura, escolha mensal ou anual. O pagamento é feito na página segura do meio de pagamento; o PsiGestão não recebe os dados do seu cartão."],
      ["O que acontece se eu não assinar ou parar de pagar?",
        "Nada é apagado. A conta fica em somente leitura: você continua vendo e exportando tudo (PDF, JSON e o arquivo completo da conta), corrigindo cadastros e atendendo pedidos LGPD dos pacientes, mas não cria pacientes, evoluções nem sessões. Assim você continua cumprindo a guarda de 5 anos dos prontuários exigida pelo CFP."],
      ["E se o pagamento atrasar?",
        `Você continua com tudo liberado por ${graceDays} dias, com um aviso no topo da tela. Regularizando nesse prazo, nada muda.`],
      ["Como cancelo?",
        "Em Configurações > Assinatura > “Cancelar assinatura”. O que já foi pago continua valendo até o fim do período; depois, a conta fica somente leitura. Dá para assinar de novo quando quiser."],
    ],
  };
}

const SHORTCUTS = [
  ["Tab (no início da tela)", "Mostra “Pular para o conteúdo”, que leva direto à tela, sem passar pelo menu"],
  [`${MOD}+Enter`, "Salva a evolução no editor"],
  ["Esc", "Fecha a janela aberta (o texto da evolução fica no rascunho)"],
  ["Enter ou Espaço", "Abre a sessão ou o dia com foco, na agenda"],
  ["Tab até um dia da agenda", "Mostra “+ Nova sessão” para marcar naquele dia"],
];

function ContactCard() {
  const [contact, setContact] = useState(null);

  useEffect(() => {
    api.get("/legal/contact").then(({ data }) => setContact(data)).catch(() => setContact(false));
  }, []);

  const configured = contact && (contact.support_email || contact.dpo_email);
  return (
    <Card className="border-slate-200 p-6 shadow-sm" data-testid="help-contact">
      <div className="flex items-center gap-2">
        <Mail className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Contato</h2>
      </div>
      {contact === null ? (
        <div className="flex justify-center py-4"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" aria-label="Carregando" /></div>
      ) : !configured ? (
        // Fora do <dl>: uma lista de definições só pode ter termos e definições (axe: definition-list).
        <p className="mt-3 text-sm text-slate-600">
          Os contatos ainda não foram configurados nesta instalação. Fale com quem administra o sistema.
        </p>
      ) : (
        <dl className="mt-3 space-y-3 text-sm">
          {contact?.support_email && (
            <div>
              <dt className="font-medium text-slate-700">Suporte (dúvidas de uso e problemas de acesso)</dt>
              <dd><a href={`mailto:${contact.support_email}`} className="text-[#1E3A8A] underline underline-offset-2">{contact.support_email}</a></dd>
            </div>
          )}
          {contact?.dpo_email && (
            <div>
              <dt className="font-medium text-slate-700">Encarregado de dados pessoais (LGPD)</dt>
              <dd className="text-slate-600">
                {contact.dpo_name ? `${contact.dpo_name} · ` : ""}
                <a href={`mailto:${contact.dpo_email}`} className="text-[#1E3A8A] underline underline-offset-2">{contact.dpo_email}</a>
                <span className="block text-xs text-slate-500">Pedidos sobre dados pessoais: acesso, correção, eliminação.</span>
              </dd>
            </div>
          )}
        </dl>
      )}
      <Link to="/privacidade" className="mt-4 inline-block rounded-sm text-sm font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]">
        Ler a Política de Privacidade
      </Link>
    </Card>
  );
}

export default function Help() {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const [restoring, setRestoring] = useState(false);

  // O1: traz de volta os "Primeiros passos" ocultados.
  const showFirstSteps = async () => {
    setRestoring(true);
    try {
      const { data } = await api.put("/auth/onboarding", { dismissed: false });
      setUser((current) => ({ ...current, ...data }));
      navigate("/dashboard");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      setRestoring(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-head text-3xl font-extrabold tracking-tight text-[#0F172A]">Ajuda</h1>
        <p className="mt-1 text-sm text-slate-500">Respostas rápidas para o dia a dia. Não achou? Veja o contato no fim da página.</p>
      </div>

      {user?.onboarding_dismissed && (
        <Card className="flex flex-col gap-3 border-slate-200 p-5 shadow-sm sm:flex-row sm:items-center sm:justify-between">
          <p className="flex items-center gap-2 text-sm text-slate-700">
            <Rocket className="h-4 w-4 shrink-0 text-[#1E3A8A]" aria-hidden="true" />
            A lista “Primeiros passos” do painel está oculta.
          </p>
          <Button type="button" variant="outline" size="sm" onClick={showFirstSteps} disabled={restoring}
            className="border-slate-300" data-testid="help-show-first-steps">
            {restoring && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />} Mostrar no painel
          </Button>
        </Card>
      )}

      <section aria-labelledby="help-faq" className="space-y-4">
        <h2 id="help-faq" className="flex items-center gap-2 font-head text-xl font-bold text-[#0F172A]">
          <LifeBuoy className="h-5 w-5 text-[#1E3A8A]" aria-hidden="true" /> Perguntas frequentes
        </h2>
        {[...TOPICS, billingTopic(user?.subscription)].map((topic) => (
          <Card key={topic.id} className="border-slate-200 p-5 shadow-sm" data-testid={`help-topic-${topic.id}`}>
            <h3 className="flex items-center gap-2 font-head text-base font-bold text-[#0F172A]">
              <topic.icon className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" /> {topic.title}
            </h3>
            <div className="mt-2 divide-y divide-slate-100">
              {topic.items.map(([question, answer]) => (
                <details key={question} className="group py-1">
                  <summary className="flex min-h-10 cursor-pointer items-center rounded-sm px-1 text-sm font-medium text-slate-800 hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]">
                    {question}
                  </summary>
                  <p className="px-1 pb-3 text-sm leading-relaxed text-slate-600">{answer}</p>
                </details>
              ))}
            </div>
          </Card>
        ))}
      </section>

      <Card className="border-slate-200 p-6 shadow-sm" data-testid="help-shortcuts">
        <div className="flex items-center gap-2">
          <Keyboard className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Atalhos de teclado</h2>
        </div>
        <table className="mt-3 w-full text-left text-sm">
          <caption className="sr-only">Atalhos de teclado do PsiGestão</caption>
          <thead>
            <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-600">
              <th scope="col" className="py-2 pr-4 font-medium">Tecla</th>
              <th scope="col" className="py-2 font-medium">O que faz</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {SHORTCUTS.map(([keys, what]) => (
              <tr key={keys}>
                {/* Quebra a linha em tela estreita (320 px), em vez de empurrar a página para o lado. */}
                <td className="py-2 pr-4 align-top">
                  <kbd className="inline-block rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-sans text-xs text-slate-700">{keys}</kbd>
                </td>
                <td className="py-2 text-slate-600">{what}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <ContactCard />
    </div>
  );
}
