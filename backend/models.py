# models.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Integer,Date
from sqlalchemy.orm import relationship
from database import Base

def now_utc():
    return datetime.now(timezone.utc)

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
    terms_accepted = Column(Boolean, default=False)
    webhook_token = Column(String, default=lambda: f"wh_{uuid.uuid4().hex}")
    created_at = Column(DateTime(timezone=True), default=now_utc)

    # Relacionamentos
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    patients = relationship("Patient", back_populates="owner", cascade="all, delete-orphan")


class UserSession(Base):
    __tablename__ = "user_sessions"

    session_token = Column(String, primary_key=True)
    user_id = user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    user = relationship("User", back_populates="sessions")

# ---------------------------------------------------------------------------
# Pacientes
# ---------------------------------------------------------------------------
class Patient(Base):
    __tablename__ = "patients"

    id = Column(String, primary_key=True, default=lambda: f"pat_{uuid.uuid4().hex[:12]}")
    owner_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    full_name = Column(String, nullable=False)
    cpf = Column(Text, default="") # Text para acomodar a string criptografada com AES-GCM
    rg = Column(Text, default="")  # Text para acomodar criptografia
    birth_date = Column(Date, nullable=True)
    age = Column(String, default="")
    education = Column(String, default="")
    profession = Column(String, default="")
    phone = Column(String, default="")
    email = Column(String, default="")
    address = Column(Text, default="") # Text para acomodar criptografia
    emergency_contact = Column(String, default="")
    initial_notes = Column(Text, default="")
    consent_terms = Column(Boolean, default=False)
    last_consultation_date = Column(String, default="")
    anonymized = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    owner = relationship("User", back_populates="patients")
    records = relationship("Record", back_populates="patient", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Prontuários e Versionamento
# ---------------------------------------------------------------------------
class Record(Base):
    __tablename__ = "records"

    id = Column(String, primary_key=True, default=lambda: f"rec_{uuid.uuid4().hex[:12]}")
    owner_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(String, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    
    session_datetime = Column(DateTime(timezone=True), nullable=True)
    content = Column(Text, default="")   # Text para acomodar criptografia
    diagnosis = Column(Text, default="") # Text para acomodar criptografia
    version = Column(Integer, default=1)
    
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    patient = relationship("Patient", back_populates="records")
    versions = relationship("RecordVersion", back_populates="record", cascade="all, delete-orphan")


class RecordVersion(Base):
    __tablename__ = "record_versions"

    version_id = Column(String, primary_key=True, default=lambda: f"ver_{uuid.uuid4().hex[:12]}")
    record_id = Column(String, ForeignKey("records.id", ondelete="CASCADE"), nullable=False)
    owner_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False)
    
    session_datetime = Column(DateTime(timezone=True), nullable=True)
    content = Column(Text, default="")
    diagnosis = Column(Text, default="")
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
    patient_name = Column(String, default="")
    title = Column(String, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), default=None)       # Renomeado de 'end' para 'end_time'
    status = Column(String, default="agendada")
    notes = Column(Text, default="")


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id = Column(String, primary_key=True, default=lambda: f"log_{uuid.uuid4().hex[:12]}")
    owner_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    user_email = Column(String, nullable=False)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    detail = Column(Text, default="")
    timestamp = Column(DateTime(timezone=True), default=now_utc)