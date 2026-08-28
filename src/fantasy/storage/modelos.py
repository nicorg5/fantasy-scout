"""Modelos SQLAlchemy 2.0.

`market_snapshot` se añade en el paso 6.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fantasy.storage.base import Base


class Usuario(Base):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    token: Mapped["TokenUsuario | None"] = relationship(
        back_populates="usuario", uselist=False, cascade="all, delete-orphan"
    )


class TokenUsuario(Base):
    """Bearer token de LaLiga Fantasy del usuario, cifrado. Ver R11/R12 en requirements.md."""

    __tablename__ = "user_token"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), unique=True
    )
    token_cifrado: Mapped[bytes] = mapped_column(nullable=False)
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    usuario: Mapped[Usuario] = relationship(back_populates="token")


class OrigenMapeo(enum.Enum):
    """Cómo se estableció un emparejamiento. Auditable a propósito: el matching es el
    punto más frágil del proyecto y hay que poder saber de dónde salió cada enlace."""

    HEURISTICA = "heuristica"
    OVERRIDE_MANUAL = "override_manual"


class Jugador(Base):
    """Identidad canónica del jugador según la API oficial (fuente de verdad)."""

    __tablename__ = "player"

    # El id oficial es una cadena ('3053'), no un entero: ver design.md.
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    apodo: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    equipo_id: Mapped[int] = mapped_column(Integer, nullable=False)
    posicion: Mapped[str] = mapped_column(String(32), nullable=False)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    mapeos: Mapped[list["MapeoJugador"]] = relationship(
        back_populates="jugador", cascade="all, delete-orphan"
    )


class MapeoJugador(Base):
    """Enlace entre un jugador oficial y su identificador en un sitio scrapeado.

    `confianza` y `origen` se guardan siempre para poder auditar y revertir: un mapeo
    heurístico con confianza baja es sospechoso aunque hoy parezca correcto.
    """

    __tablename__ = "player_mapping"
    __table_args__ = (
        # Un jugador oficial tiene como mucho un enlace por sitio scrapeado.
        UniqueConstraint("player_id", "sitio", name="uq_player_mapping_jugador_sitio"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("player.id", ondelete="CASCADE"), nullable=False
    )
    sitio: Mapped[str] = mapped_column(String(64), nullable=False)
    id_externo: Mapped[str] = mapped_column(String(64), nullable=False)
    nombre_externo: Mapped[str] = mapped_column(String(255), nullable=False)
    origen: Mapped[OrigenMapeo] = mapped_column(
        Enum(OrigenMapeo, name="origen_mapeo", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    confianza: Mapped[float] = mapped_column(Float, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    jugador: Mapped[Jugador] = relationship(back_populates="mapeos")


class EstadoAnalitica(enum.Enum):
    """Marca de disponibilidad de la analítica scrapeada, separada del dato oficial."""

    DISPONIBLE = "disponible"
    NO_DISPONIBLE = "unavailable"


class SnapshotMercado(Base):
    """Fotografía diaria de un jugador en el mercado.

    Estructura deliberada (R31): los campos **oficiales** y los **analíticos scrapeados**
    viven en bloques separados, y los analíticos van siempre acompañados de su marca de
    disponibilidad. Nunca se rellena un hueco analítico con un 0: si no hay dato, el
    estado es NO_DISPONIBLE y el motivo lo explica.

    Es una **caché operativa**, no la fuente de verdad del histórico (ver design.md
    §Retención e histórico): perder esta tabla es recuperable.
    """

    __tablename__ = "market_snapshot"
    __table_args__ = (
        # Idempotencia del cron (R30): reejecutar el mismo día no duplica filas.
        # La fecha es la LOCAL de Madrid, no UTC: ver design.md §Cron.
        UniqueConstraint("player_id", "fecha", name="uq_market_snapshot_jugador_fecha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("player.id", ondelete="CASCADE"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)

    # --- bloque OFICIAL (API de LaLiga) ---
    valor_mercado: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_venta: Mapped[int] = mapped_column(Integer, nullable=False)
    estado_jugador: Mapped[str] = mapped_column(String(32), nullable=False)
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- bloque ANALÍTICO (scrapeado, puede faltar sin afectar a lo de arriba) ---
    analitica_estado: Mapped[EstadoAnalitica] = mapped_column(
        Enum(EstadoAnalitica, name="estado_analitica", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    analitica_motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    analitica_origen: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analitica_capturado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tendencia_direccion: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tendencia_variacion_euros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probabilidad_jugar: Mapped[int | None] = mapped_column(Integer, nullable=True)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    jugador: Mapped[Jugador] = relationship()


class CredencialesLaLiga(Base):
    """Credenciales de LaLiga Fantasy del usuario, cifradas.

    **Decisión consciente del usuario (2026-08-28), con los riesgos expuestos**: guardar la
    contraseña es la única forma de que el cron nocturno funcione solo, porque el token de
    LaLiga dura 24 h. Ver design.md §Login automático.

    A diferencia de `Usuario.password_hash`, esto **no puede hashearse**: hay que enviar la
    contraseña a LaLiga, así que el cifrado tiene que ser reversible. Es precisamente por
    eso que una fuga de la base de datos aquí es más grave.

    Guardar credenciales es opcional: sin fila aquí, la app sigue funcionando con el token
    pegado a mano.
    """

    __tablename__ = "laliga_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    email_cifrado: Mapped[bytes] = mapped_column(nullable=False)
    password_cifrado: Mapped[bytes] = mapped_column(nullable=False)
    refresh_token_cifrado: Mapped[bytes | None] = mapped_column(nullable=True)
    ultimo_login_ok: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultimo_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    usuario: Mapped[Usuario] = relationship()


class AnaliticaDiaria(Base):
    """Analítica scrapeada de TODOS los jugadores, un registro por jugador y día.

    Existe para que **la web no tenga que scrapear nunca**: el scraping es serial y
    espaciado por respeto al sitio (~1 minuto para el mercado), lo que hacía inviable
    hacerlo dentro de una petición HTTP. El cron la rellena de noche y la web solo lee.

    Se identifica por el id de **futbolfantasy**, no por el jugador oficial, a propósito:
      - Es el dato tal y como llega de la fuente, sin mezclar identidades.
      - El emparejamiento con la API oficial se hace al servir (es cálculo, no red), así
        que si el matching mejora, estos datos ya guardados se benefician sin migrarlos.
    """

    __tablename__ = "analitica_diaria"
    __table_args__ = (
        UniqueConstraint("fecha", "id_externo", name="uq_analitica_diaria_fecha_jugador"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Identidad en el sitio scrapeado. `id_externo` es cadena porque los entrenadores
    # usan formato 'e119', no numérico.
    id_externo: Mapped[str] = mapped_column(String(32), nullable=False)
    nombre_externo: Mapped[str] = mapped_column(String(255), nullable=False)
    equipo_externo: Mapped[str] = mapped_column(String(16), nullable=False)

    tendencia_direccion: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tendencia_variacion_euros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_diaria_euros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_acumulada_euros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Puede faltar: futbolfantasy solo publica probabilidad del once probable.
    probabilidad_jugar: Mapped[int | None] = mapped_column(Integer, nullable=True)

    origen: Mapped[str] = mapped_column(String(64), nullable=False)
    capturado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
