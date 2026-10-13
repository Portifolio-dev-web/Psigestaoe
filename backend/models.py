# models.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import DDL, Column, String, Boolean, DateTime, ForeignKey, Text, Integer, Date, Index, event
from sqlalchemy.orm import relationship
from database import Base

def now_utc():
    return datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Convenção de criptografia (item 13 da auditoria)
# ---------------------------------------------------------------------------
# Colunas marcadas com [CRIPTOGRAFADO] guardam um token Fernet gerado por
# encrypt_field() (server.py), nunca o texto puro. A lista oficial dessas
# colunas fica em rotate_encryption_key.py (ENCRYPTED_COLUMNS), que também
# criptografa dados antigos gravados antes desta regra.
#
# Consequência importante: o banco NÃO consegue buscar nem ordenar por essas
# colunas (cada criptografia gera um valor diferente). A busca de pacientes
# por nome é feita no navegador, depois de descriptografar. Se um dia for
# necessária busca no servidor, a técnica é um "blind index" (HMAC do nome
# normalizado em uma coluna separada), nunca voltar a gravar o nome em claro.

# ---------------------------------------------------------------------------
# Índices (ESCALA)
# ---------------------------------------------------------------------------
# Antes, fora as chaves primárias, só existiam índices em users.email e
# no token do (hoje removido) Google Forms. Toda listagem ("pacientes DESTE profissional,
# mais recentes primeiro") lia a tabela inteira e ordenava em memória, e o
# Postgres NÃO cria índice sozinho para chaves estrangeiras: cada exclusão em
# cascata varria as tabelas filhas.
#
# Cada índice abaixo existe para uma consulta concreta, citada ao lado. A
# regra dos compostos: primeiro a coluna de igualdade (owner_id/patient_id),
# depois a de ordenação, e por último o id (desempate da paginação por
# cursor, ver server.py). O Postgres percorre o índice ao contrário para
# ORDER BY ... DESC, então não é preciso declarar a direção.
# Criados pela migração alembic/versions/20260930_scale_indexes.py.

# ---------------------------------------------------------------------------
# Usuários e Sessões
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True, default=lambda: f"user_{uuid.uuid4().hex[:12]}")
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, default="")
    password_hash = Column(String, nullable=True)
    auth_provider = Column(String, default="email")
    picture = Column(String, default="")
    role = Column(String, default="psicologo")
    # Item 6 da auditoria: registro no Conselho Regional de Psicologia (ex.: "06/123456"),
    # usado no termo de consentimento. Migração: alembic/versions/20260930_add_user_crp.py
    crp = Column(String, default="")

    # Item 16 da auditoria: aceite da Política de Privacidade com EVIDÊNCIA.
    # Antes havia só um booleano, e o login com Google o marcava como True sem
    # o usuário aceitar nada. Agora guardamos QUAL versão foi aceita e QUANDO
    # (o IP fica na trilha de auditoria). Se a política mudar de versão, o
    # profissional precisa aceitar de novo (ver legal.py e get_active_user).
    terms_accepted = Column(Boolean, default=False)
    terms_version = Column(String, nullable=True)
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)

    # Termo de consentimento por profissional: corpo do texto personalizado.
    # NULL = usa o modelo padrão (DEFAULT_CONSENT_TERM_BODY em server.py).
    consent_term_body = Column(Text, nullable=True)

    # PRODUTO (cadastro pelo paciente): a integração com o Google Forms (token
    # de webhook permanente, dados do paciente passando pelo Google) foi
    # REMOVIDA. As colunas webhook_token_* saíram na migração
    # 20261009_patient_intake, o que invalidou todos os tokens existentes. O
    # substituto é o link de cadastro temporário (PatientIntake, abaixo).

    # PRODUTO (bloqueio por inatividade): minutos sem uso até a tela travar e
    # exigir a senha de novo. Escolhido pelo profissional em Configurações
    # (5, 10, 15 ou 30). Vale no navegador E no servidor (ver server.py).
    lock_minutes = Column(Integer, default=10)

    # PRODUTO (rascunho automático): chave AES-256 aleatória deste profissional,
    # usada pelo NAVEGADOR para criptografar os rascunhos de prontuário que
    # ficam guardados no próprio computador. Sem login não há chave, então um
    # rascunho copiado do disco é ilegível. Trocar a chave (Configurações)
    # inutiliza os rascunhos de TODOS os aparelhos, ex.: notebook perdido.
    draft_key = Column(Text, nullable=True)  # [CRIPTOGRAFADO]

    # SEGURANÇA (varredura, item 5): vínculo seguro com o Google.
    # Antes, o login com Google entrava em QUALQUER conta com o mesmo e-mail,
    # inclusive numa conta com senha criada por outra pessoa (o cadastro não
    # confirma o e-mail). Ver google_auth em server.py.
    # - google_sub: identificador FIXO da conta Google (claim "sub" do token).
    #   O e-mail de uma conta Google pode mudar ou ser reaproveitado (ex.: um
    #   e-mail corporativo passado a outro funcionário); o sub nunca muda.
    # - email_verified_at: quando a posse do e-mail foi comprovada. Hoje só o
    #   Google comprova. Conta com senha e sem esta data = ninguém provou que
    #   é dono do e-mail.
    # Migração: alembic/versions/20261001_account_security.py
    google_sub = Column(String, unique=True, index=True, nullable=True)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)

    # SEGURANÇA (backlog: MFA): verificação em duas etapas por aplicativo
    # autenticador (TOTP, RFC 6238; ver totp.py). Opcional, ativada pelo
    # profissional em Configurações > Minha conta.
    # - mfa_secret: segredo compartilhado com o aplicativo. Gravado ao INICIAR a
    #   configuração; só vale depois de confirmado com um código
    #   (mfa_enabled_at preenchido).
    # - mfa_last_step: último intervalo de 30 s já usado. Impede reaproveitar
    #   o MESMO código (ex.: alguém que viu o código por cima do ombro).
    # - mfa_recovery_codes: JSON com o HASH dos códigos de recuperação (para
    #   quem perder o celular); cada código vale uma vez.
    mfa_secret = Column(Text, nullable=True)  # [CRIPTOGRAFADO]
    mfa_enabled_at = Column(DateTime(timezone=True), nullable=True)
    mfa_last_step = Column(Integer, nullable=True)
    mfa_recovery_codes = Column(Text, nullable=True)

    # SEGURANÇA (segurança média: encerramento de conta, LGPD art. 18):
    # - closed_at: quando o profissional encerrou a conta. Daí em diante
    #   ninguém entra nela, e o que não está sob guarda já foi apagado.
    # - purge_after: a partir de quando a rotina de manutenção apaga o que
    #   sobrou (prontuários sob guarda do CFP, trilha de auditoria, esta linha).
    closed_at = Column(DateTime(timezone=True), nullable=True)
    purge_after = Column(DateTime(timezone=True), nullable=True)

    # PRODUTO (análise de UI, P7): horário de atendimento. A grade da agenda
    # (Semana/Dia) mostra só estas horas, em vez das 24 h do dia. Sessões fora
    # do horário continuam aparecendo: a grade se estende até elas.
    # NULL = padrão (DEFAULT_AGENDA_HOURS em server.py). Fim exclusivo (21 = até 21:00).
    agenda_start_hour = Column(Integer, nullable=True)
    agenda_end_hour = Column(Integer, nullable=True)

    # PRODUTO (análise de UI, O1): "Primeiros passos" no painel de uma conta nova.
    # - consent_term_reviewed_at: o profissional revisou o termo de consentimento
    #   (salvou o texto, mesmo sem mudar nada). É um dos passos da lista.
    # - onboarding_dismissed_at: a lista foi ocultada ("Ocultar primeiros passos").
    # Migração: alembic/versions/20261006_productivity.py
    consent_term_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    onboarding_dismissed_at = Column(DateTime(timezone=True), nullable=True)

    # PRODUTO (assinatura; regras em billing.py). A situação da conta (teste,
    # assinante, atraso, somente leitura, cortesia) é CALCULADA a partir destas
    # datas, nunca guardada pronta: sem aviso do meio de pagamento, a conta
    # entra sozinha na tolerância e depois no somente leitura.
    # - trial_ends_at: fim do teste grátis (cadastro + TRIAL_DAYS);
    # - courtesy_at / courtesy_reason: cortesia concedida pelo comando do
    #   servidor (manage_billing.py); NULL = sem cortesia;
    # - subscription_plan: "monthly" ou "annual"; subscription_status: "none",
    #   "active", "past_due" ou "canceled" (o que o meio de pagamento informou);
    # - paid_until: até quando o acesso está pago; past_due_since: início do
    #   atraso (conta a tolerância); cancel_at_period_end: cancelada, vale até paid_until;
    # - billing_provider / billing_customer_id / billing_subscription_id: o
    #   meio de pagamento e os identificadores da cliente e da assinatura NELE.
    # Migração: alembic/versions/20261008_billing.py
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    courtesy_at = Column(DateTime(timezone=True), nullable=True)
    courtesy_reason = Column(String, default="")
    subscription_plan = Column(String, nullable=True)
    subscription_status = Column(String, default="none")
    paid_until = Column(DateTime(timezone=True), nullable=True)
    past_due_since = Column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    billing_provider = Column(String, nullable=True)
    billing_customer_id = Column(String, nullable=True, index=True)
    billing_subscription_id = Column(String, nullable=True, index=True)
    billing_updated_at = Column(DateTime(timezone=True), nullable=True)

    # PRODUTO (administração): conta suspensa pela tela de administração
    # (fraude, abuso). Suspensa, ninguém entra nela (todas as sessões são
    # encerradas e o login é recusado) e os links de cadastro do paciente param
    # de funcionar. Os dados continuam guardados, sem alteração: reativar
    # devolve tudo como estava. O motivo vai no e-mail que a pessoa recebe.
    # Migração: alembic/versions/20261010_admin.py
    suspended_at = Column(DateTime(timezone=True), nullable=True)
    suspended_reason = Column(String, default="")

    created_at = Column(DateTime(timezone=True), default=now_utc)

    # Relacionamentos
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    patients = relationship("Patient", back_populates="owner", cascade="all, delete-orphan")


class UserSession(Base):
    __tablename__ = "user_sessions"

    # SEGURANÇA (varredura, itens 7 e 14): o banco guarda só o HASH SHA-256 do
    # ID da sessão (antes: coluna `session_token`, com o ID em texto puro).
    # O ID verdadeiro existe apenas dentro do JWT, no cookie do navegador.
    # Assim, quem ler o banco (um backup, uma consulta indevida) não consegue
    # montar um token válido para as sessões abertas, mesmo conhecendo o
    # JWT_SECRET. Mesmo padrão do token do webhook (webhook_token_hash).
    token_hash = Column(String, primary_key=True)
    # Índice: limpeza das sessões vencidas no login e cascata ao excluir usuário.
    user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    # PRODUTO (bloqueio por inatividade), controlado NO SERVIDOR:
    # - last_activity_at: último uso real (requisições e o "sinal de vida" que
    #   o navegador manda enquanto a pessoa mexe na tela);
    # - locked_at: quando a sessão foi bloqueada. Bloqueada, ela só aceita
    #   desbloqueio e saída: recarregar a página ou chamar a API direto não
    #   adianta, o bloqueio não é só visual;
    # - unlock_failures: senhas erradas na tela de bloqueio. No limite, a
    #   sessão é encerrada (impede força bruta de quem achou a tela travada).
    last_activity_at = Column(DateTime(timezone=True), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    unlock_failures = Column(Integer, default=0)

    # SEGURANÇA (segurança média: sessões com aparelho): antes a tela só dizia
    # QUANTAS sessões estavam abertas, sem como reconhecer um acesso estranho.
    # Agora cada sessão guarda de onde foi aberta ("Chrome no Windows" e o IP)
    # e pode ser encerrada sozinha.
    # - public_id: identificador mostrado ao navegador para encerrar UMA
    #   sessão. Nunca o token_hash: ele não autentica, mas não há motivo para
    #   sair do servidor.
    public_id = Column(String, unique=True, index=True,
                       default=lambda: f"sess_{uuid.uuid4().hex[:16]}")
    device = Column(String, default="")
    ip_address = Column(String, default="")

    user = relationship("User", back_populates="sessions")


class EmailToken(Base):
    """Links de uso único enviados por e-mail (segurança média e backlog).

    - purpose "password_reset": "Esqueci minha senha" (vale 1 hora);
    - purpose "signup": confirmação do cadastro (vale 24 horas). A conta só é
      criada quando a pessoa abre o link e define a senha: assim ninguém cria
      uma conta com o e-mail de outra pessoa, e o cadastro deixa de revelar
      quais e-mails já têm conta (a resposta é sempre "enviamos um link").

    Como os tokens de sessão e do webhook, o banco guarda só o HASH do token:
    o link verdadeiro existe apenas no e-mail.
    """
    __tablename__ = "email_tokens"

    id = Column(String, primary_key=True, default=lambda: f"etk_{uuid.uuid4().hex[:12]}")
    purpose = Column(String, nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, nullable=False)
    # Conta a que o link se refere (recuperação de senha). No cadastro ainda
    # não existe conta: o nome digitado fica em `payload`.
    user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True, index=True)
    payload = Column(Text, default="")  # [CRIPTOGRAFADO] JSON (ex.: nome no cadastro)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        # Invalidar os links anteriores do mesmo e-mail ao pedir um novo.
        Index("ix_email_tokens_email_purpose", "email", "purpose"),
    )

# ---------------------------------------------------------------------------
# Pacientes
# ---------------------------------------------------------------------------
class Patient(Base):
    __tablename__ = "patients"

    id = Column(String, primary_key=True, default=lambda: f"pat_{uuid.uuid4().hex[:12]}")
    owner_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    full_name = Column(String, nullable=False)  # [CRIPTOGRAFADO] item 13
    cpf = Column(Text, default="")              # [CRIPTOGRAFADO]
    rg = Column(Text, default="")               # [CRIPTOGRAFADO]
    birth_date = Column(Date, nullable=True)
    age = Column(String, default="")
    education = Column(String, default="")
    marital_status = Column(String, default="")
    profession = Column(String, default="")
    phone = Column(String, default="")          # [CRIPTOGRAFADO] item 13
    email = Column(String, default="")          # [CRIPTOGRAFADO] item 13
    address = Column(Text, default="")          # [CRIPTOGRAFADO]
    emergency_contact = Column(String, default="")  # [CRIPTOGRAFADO] item 13
    # PRODUTO (fluxo clínico, C3): responsável legal do paciente MENOR de 18 anos.
    # Obrigatório para menores (é quem consente o atendimento e o contato de
    # referência); opcional para adultos (ex.: curatela). Nome, CPF e telefone
    # identificam uma pessoa: criptografados como os do paciente.
    # Migração: alembic/versions/20261005_clinical_flow.py
    guardian_name = Column(String, default="")          # [CRIPTOGRAFADO]
    guardian_cpf = Column(Text, default="")             # [CRIPTOGRAFADO]
    guardian_relationship = Column(String, default="")  # "Mãe", "Pai", "Tutor(a) legal"...
    guardian_phone = Column(String, default="")         # [CRIPTOGRAFADO]
    initial_notes = Column(Text, default="")    # [CRIPTOGRAFADO] item 13 (conteúdo clínico)
    consent_terms = Column(Boolean, default=False)
    last_consultation_date = Column(String, default="")
    anonymized = Column(Boolean, default=False)

    # Termo de consentimento por profissional: EVIDÊNCIA do consentimento.
    # A LGPD (art. 8º, §2º) põe no controlador o ônus de provar o consentimento.
    # Por isso, quando o consentimento é registrado, guardamos uma cópia exata
    # do texto apresentado e a data. Se depois o profissional mudar o próprio
    # nome, CRP ou o modelo do termo, o registro deste paciente NÃO muda.
    # A revogação (art. 8º, §5º) fica registrada em `consent_revoked_at`.
    consent_given_at = Column(DateTime(timezone=True), nullable=True)
    consent_revoked_at = Column(DateTime(timezone=True), nullable=True)
    consent_term_snapshot = Column(Text, nullable=True)

    # Item 12 da auditoria: arquivamento. Paciente com prontuários dentro do
    # prazo de guarda do CFP não pode ser excluído; ele pode ser ARQUIVADO,
    # o que o tira da lista ativa sem apagar nada.
    archived_at = Column(DateTime(timezone=True), nullable=True)

    # PRODUTO (análise de UI, P5): versão do índice de busca deste paciente
    # (SEARCH_INDEX_VERSION em crypto.py). A versão 2 passou a indexar também
    # telefone e CPF; pacientes com versão menor (ou NULL) são reindexados pelo
    # rebuild_search_index.py, que o prestart roda a cada implantação.
    search_index_version = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    owner = relationship("User", back_populates="patients")
    records = relationship("Record", back_populates="patient", cascade="all, delete-orphan")
    search_tokens = relationship("PatientSearchToken", cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        # Lista de pacientes do profissional, mais recentes primeiro (paginada).
        Index("ix_patients_owner_created", "owner_id", "created_at", "id"),
    )


class PatientSearchToken(Base):
    """Índice cego da busca por nome (ESCALA). Ver crypto.name_search_tokens.

    Um paciente tem várias linhas: uma por prefixo de cada palavra do nome.
    Desde a análise de UI (P5), também os finais do telefone (do paciente e do
    responsável) e o CPF (crypto.contact_search_tokens), na mesma tabela.
    Nenhuma linha contém o nome; só HMACs que não podem ser revertidos sem a
    SEARCH_INDEX_KEY. É totalmente derivada: pode ser apagada e reconstruída
    com `python rebuild_search_index.py`.
    """
    __tablename__ = "patient_search_tokens"

    patient_id = Column(String, ForeignKey("patients.id", ondelete="CASCADE"), primary_key=True)
    token = Column(String(32), primary_key=True)
    owner_id = Column(String, nullable=False)

    __table_args__ = (
        # A busca: "pacientes DESTE profissional que têm este token".
        Index("ix_patient_search_owner_token", "owner_id", "token"),
    )


# ---------------------------------------------------------------------------
# Cadastro pelo próprio paciente (link temporário)
# ---------------------------------------------------------------------------
class PatientIntake(Base):
    """Link de cadastro que a psicóloga envia ao paciente (substitui o Google Forms).

    Regras (server.py, seção "link de cadastro"):
      - o link vale para COMEÇAR por 30 minutos (expires_at) e para uma pessoa;
      - o cronômetro só começa quando o paciente clica em "Começar" (abrir a
        página não conta: a prévia automática do WhatsApp não consome o link);
      - começou: 10 minutos (deadline_at), prorrogáveis em +10 até 10 vezes
        (acessibilidade, WCAG 2.2.1), e o link fica PRESO àquele navegador
        (fill_secret): se vazar, ninguém continua de outro aparelho;
      - o que o paciente digita é salvo CRIPTOGRAFADO aqui (draft) enquanto ele
        preenche: recarregar a página ou cair a internet não perde nada;
      - enviado: vira paciente (patient_id) e o rascunho é apagado;
      - o tempo acabou com o cadastro pela metade: a psicóloga recebe o que foi
        digitado como "incompleto" e completa com o paciente (nada se perde).

    Como as outras credenciais, o banco guarda só o HASH do link e do segredo
    do navegador: o link verdadeiro existe apenas na mensagem enviada ao paciente.
    """
    __tablename__ = "patient_intakes"

    id = Column(String, primary_key=True, default=lambda: f"cad_{uuid.uuid4().hex[:12]}")
    owner_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    label = Column(Text, default="")            # [CRIPTOGRAFADO] "para quem é" (só a psicóloga vê)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    expires_at = Column(DateTime(timezone=True), nullable=False)    # prazo para COMEÇAR
    started_at = Column(DateTime(timezone=True), nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)    # prazo para TERMINAR
    extensions = Column(Integer, default=0)
    fill_secret_hash = Column(String, nullable=True)
    draft = Column(Text, default="")            # [CRIPTOGRAFADO] JSON do que já foi digitado
    draft_saved_at = Column(DateTime(timezone=True), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    completed_by = Column(String, nullable=True)  # "patient" (enviou) ou "professional" (completou o incompleto)
    patient_id = Column(String, ForeignKey("patients.id", ondelete="SET NULL"), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    # Revisão de segurança (RS2): o cadastro INCOMPLETO tem prazo (60 dias depois
    # que o tempo acabou). Antes ficava guardado para sempre se a psicóloga nunca
    # revisasse. reminded_at: lembrete de 7 dias enviado; discard_warned_at: aviso
    # final enviado (o descarte só acontece 7 dias depois dele). Ver
    # tend_incomplete_intakes em server.py. Migração: 20261013_intake_retention.py
    reminded_at = Column(DateTime(timezone=True), nullable=True)
    discard_warned_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Lista de links da psicóloga, mais recentes primeiro.
        Index("ix_patient_intakes_owner_created", "owner_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Prontuários e Versionamento
# ---------------------------------------------------------------------------
class Record(Base):
    __tablename__ = "records"

    id = Column(String, primary_key=True, default=lambda: f"rec_{uuid.uuid4().hex[:12]}")
    owner_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(String, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)

    session_datetime = Column(DateTime(timezone=True), nullable=True)
    content = Column(Text, default="")   # [CRIPTOGRAFADO]
    diagnosis = Column(Text, default="") # [CRIPTOGRAFADO]
    version = Column(Integer, default=1)

    # PRODUTO (ligação agenda -> evolução): a sessão da agenda a que esta
    # evolução se refere, quando registrada a partir dela. Opcional: evoluções
    # antigas e as registradas direto na ficha não têm sessão. Excluir a sessão
    # da agenda não apaga a evolução (SET NULL): o prontuário é guardado por lei.
    # Migração: alembic/versions/20261002_record_session_link.py
    # Único: no máximo UMA evolução por sessão (o banco garante, mesmo com dois envios simultâneos).
    session_id = Column(String, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True, unique=True)

    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    patient = relationship("Patient", back_populates="records")
    versions = relationship("RecordVersion", back_populates="record", cascade="all, delete-orphan")

    __table_args__ = (
        # Evolução clínica de um paciente, sessão mais recente primeiro (paginada),
        # e o cálculo do prazo de guarda (MAX por paciente).
        Index("ix_records_patient_session", "patient_id", "session_datetime", "id"),
        # Feed "últimas alterações" do painel.
        Index("ix_records_owner_updated", "owner_id", "updated_at"),
    )


class RecordVersion(Base):
    __tablename__ = "record_versions"

    version_id = Column(String, primary_key=True, default=lambda: f"ver_{uuid.uuid4().hex[:12]}")
    # Índices nas chaves estrangeiras: sem eles, excluir um prontuário ou
    # paciente faz o Postgres varrer a tabela inteira de versões.
    record_id = Column(String, ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False, index=True)

    session_datetime = Column(DateTime(timezone=True), nullable=True)
    content = Column(Text, default="")   # [CRIPTOGRAFADO]
    diagnosis = Column(Text, default="") # [CRIPTOGRAFADO]
    version = Column(Integer, nullable=False)
    archived_at = Column(DateTime(timezone=True), default=now_utc)

    record = relationship("Record", back_populates="versions")


# ---------------------------------------------------------------------------
# Agenda (Sessões)
# ---------------------------------------------------------------------------
class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=lambda: f"ses_{uuid.uuid4().hex[:12]}")
    owner_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(String, nullable=True) # Pode ser nulo caso não vincule a um paciente do sistema
    # Item 13: nome do paciente, título e notas da agenda também identificam o
    # paciente (o título costuma ser "Sessão com Fulano"), então são criptografados.
    patient_name = Column(String, default="")  # [CRIPTOGRAFADO] item 13
    title = Column(String, nullable=False)     # [CRIPTOGRAFADO] item 13
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), default=None)       # Renomeado de 'end' para 'end_time'
    # Status: agendada, realizada, faltou (fluxo clínico, C4) ou cancelada.
    status = Column(String, default="agendada")
    notes = Column(Text, default="")           # [CRIPTOGRAFADO] item 13
    # PRODUTO (fluxo clínico, P1): sessões recorrentes. As sessões criadas juntas
    # ("toda semana", "a cada 2 semanas") têm o mesmo series_id e a mesma regra
    # (recurrence: "weekly" ou "biweekly"). Cada uma continua independente
    # (status, evolução, remarcação); a série serve para alterar ou remover
    # "esta e as próximas" de uma vez. NULL = sessão avulsa.
    series_id = Column(String, nullable=True, index=True)
    recurrence = Column(String, nullable=True)

    __table_args__ = (
        # Agenda por período (mês visível, próximas sessões) e contagens do painel.
        Index("ix_sessions_owner_start", "owner_id", "start_time"),
    )


# ---------------------------------------------------------------------------
# Assinatura: avisos já processados do meio de pagamento
# ---------------------------------------------------------------------------
class BillingEventLog(Base):
    """Cada aviso do meio de pagamento é aplicado UMA vez (idempotência).

    Os meios de pagamento reenviam o mesmo aviso até receber resposta, e um
    atacante poderia repetir um aviso antigo ("pagamento confirmado"): a chave
    primária é o identificador do evento NO MEIO DE PAGAMENTO, então a segunda
    cópia é reconhecida e ignorada. Guarda só o necessário (sem valores, sem
    dados do cartão ou da pessoa além do id da conta).
    """
    __tablename__ = "billing_events"

    event_id = Column(String, primary_key=True)        # "<meio>:<id do evento no meio>"
    provider = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    user_id = Column(String, nullable=True, index=True)  # sem FK: o registro sobrevive à exclusão da conta
    received_at = Column(DateTime(timezone=True), default=now_utc)


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id = Column(String, primary_key=True, default=lambda: f"log_{uuid.uuid4().hex[:12]}")
    # Item 14 da auditoria: SEM ForeignKey para users.
    # Antes havia ForeignKey(..., ondelete="CASCADE"): excluir um usuário
    # apagava junto toda a trilha de auditoria dele, exatamente o registro que
    # precisaria sobreviver para investigar um incidente. Agora a trilha
    # guarda os ids como texto e sobrevive a qualquer exclusão.
    owner_id = Column(String, nullable=False)
    user_email = Column(String, nullable=False)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    # Item 13: `detail` NUNCA deve conter nome ou dado do paciente (ele fica
    # em texto puro); a identificação é feita por entity_id.
    detail = Column(Text, default="")
    # Item 14: de onde veio a ação. O Marco Civil da Internet (Lei 12.965/2014,
    # art. 15) exige guardar registros de acesso (IP + data/hora) por 6 meses,
    # e a LGPD (art. 37) exige registro das operações de tratamento.
    ip_address = Column(String, default="")
    user_agent = Column(String, default="")
    timestamp = Column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        # Trilha do profissional, mais recente primeiro (paginada). Substitui o
        # índice simples em owner_id, que não servia para a ordenação.
        Index("ix_audit_logs_owner_timestamp", "owner_id", "timestamp", "log_id"),
    )


# Item 14 da auditoria: trilha SOMENTE DE INCLUSÃO (append-only), garantida
# pelo próprio banco de dados. Mesmo um bug no código ou uma consulta manual
# feita pela aplicação não consegue alterar ou apagar um registro de auditoria.
#
# Estes gatilhos são criados automaticamente quando a tabela é criada pelo
# create_all() (banco novo). Em banco existente, quem cria é a migração
# alembic/versions/20260930_compliance.py.
#
# Manutenção: a única exclusão permitida é o expurgo de uma conta ENCERRADA
# depois do prazo legal (purge_closed_accounts em server.py), que liga
# `psigestao.audit_purge` só dentro da própria transação. Qualquer outro
# expurgo exige que um administrador do banco desative o gatilho de forma
# deliberada e registrada.
_PG_APPEND_ONLY_FUNCTION = DDL(
    "CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$ "
    "BEGIN "
    # Segurança média (encerramento de conta): única exceção, o expurgo de uma
    # conta encerrada depois do prazo legal (purge_closed_accounts em
    # server.py), que liga esta configuração SÓ na própria transação (SET LOCAL).
    "IF TG_OP = 'DELETE' AND current_setting('psigestao.audit_purge', true) = 'on' THEN RETURN OLD; END IF; "
    "RAISE EXCEPTION USING MESSAGE = 'audit_logs e somente inclusao (append-only): ' || TG_OP; END; "
    "$$ LANGUAGE plpgsql"
)
_PG_APPEND_ONLY_TRIGGER = DDL(
    "CREATE TRIGGER audit_logs_no_update_delete BEFORE UPDATE OR DELETE ON audit_logs "
    "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
)
_PG_NO_TRUNCATE_TRIGGER = DDL(
    "CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_append_only()"
)
# SQLite (testes automatizados e fallback local) tem sintaxe própria.
_SQLITE_NO_UPDATE = DDL(
    "CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs "
    "BEGIN SELECT RAISE(ABORT, 'audit_logs e somente inclusao (append-only)'); END"
)
_SQLITE_NO_DELETE = DDL(
    "CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs "
    "BEGIN SELECT RAISE(ABORT, 'audit_logs e somente inclusao (append-only)'); END"
)

for _ddl in (_PG_APPEND_ONLY_FUNCTION, _PG_APPEND_ONLY_TRIGGER, _PG_NO_TRUNCATE_TRIGGER):
    event.listen(AuditLog.__table__, "after_create", _ddl.execute_if(dialect="postgresql"))
for _ddl in (_SQLITE_NO_UPDATE, _SQLITE_NO_DELETE):
    event.listen(AuditLog.__table__, "after_create", _ddl.execute_if(dialect="sqlite"))
