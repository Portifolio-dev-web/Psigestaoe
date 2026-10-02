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
# users.webhook_token_hash. Toda listagem ("pacientes DESTE profissional,
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

    # Item 15 da auditoria: token do webhook do Google Forms.
    # Antes: coluna `webhook_token` com o token em TEXTO PURO, gerado para todo
    # usuário, sem como trocar e devolvido em toda chamada de /auth/me.
    # Agora: só o hash SHA-256 fica no banco (quem lê o banco não consegue usar
    # o token), o token é mostrado UMA vez ao ser gerado e pode ser trocado ou
    # revogado. `webhook_token_hint` guarda os 4 últimos caracteres, só para
    # o profissional reconhecer qual token está ativo.
    webhook_token_hash = Column(String, unique=True, index=True, nullable=True)
    webhook_token_hint = Column(String, default="")
    webhook_token_created_at = Column(DateTime(timezone=True), nullable=True)

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

    user = relationship("User", back_populates="sessions")

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
    status = Column(String, default="agendada")
    notes = Column(Text, default="")           # [CRIPTOGRAFADO] item 13

    __table_args__ = (
        # Agenda por período (mês visível, próximas sessões) e contagens do painel.
        Index("ix_sessions_owner_start", "owner_id", "start_time"),
    )


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
# Manutenção: expurgar registros antigos (depois do prazo legal) exige que um
# administrador do banco desative o gatilho de forma deliberada e registrada.
_PG_APPEND_ONLY_FUNCTION = DDL(
    "CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION USING MESSAGE = 'audit_logs e somente inclusao (append-only): ' || TG_OP; END; "
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
