# fantasy-scout

Web personal de analítica para una liga privada de LaLiga Fantasy: plantilla y mercado
propios, enriquecidos con tendencia de valor y probabilidad de jugar.

Ver [`specs/mvp/`](specs/mvp/) para el diseño, requirements y desglose de tareas del MVP.

## Desarrollo local

```bash
uv sync --all-groups
uv run uvicorn fantasy.api.app:app --reload    # http://localhost:8000
uv run pytest
```

Copia `.env.example` a `.env` y rellénalo. En Render las variables viven en el dashboard,
nunca en el repo.

Postgres local vía Docker (puerto **5433**, no 5432 — evita chocar con otros proyectos):

```bash
docker compose up -d
uv run alembic upgrade head
uv run python scripts/crear_usuario.py --email tu@email.com   # alta manual, sin registro público
```

Estado actual: **paso 2 cerrado** (auth propia + token cifrado). Login en `/login`, token
de LaLiga en `/token` tras iniciar sesión; `/plantilla` y `/mercado` siguen con datos mock
del paso 1 hasta el paso 7-8.

```bash
uv run pytest -q                          # suite completa
bash scripts/verificar_paso1.sh           # R1-R5; PUERTO=8010 si el 8000 está ocupado
bash scripts/capturas_maqueta.sh          # capturas a 375px y 1280px
```

