# StreamForge

Plataforma full-stack de procesamiento y streaming adaptativo de video (HLS). Los usuarios suben videos, un worker los transcodifica de forma asíncrona con FFmpeg, el progreso se monitorea en tiempo real y cada video puede compartirse mediante un enlace público con reproducción adaptativa.

> **Estado:** Fase 1 — infraestructura base. Aún no hay modelos de dominio ni frontend.

## Arquitectura

```
                    ┌──────────────┐
  navegador ──────► │    nginx     │  :80
                    └──────┬───────┘
             /api, /admin  │  /static
                    ┌──────▼───────┐        ┌────────────┐
                    │   backend    │◄──────►│ PostgreSQL │
                    │ Django + DRF │        └────────────┘
                    └──┬────────┬──┘
              encola   │        │ boto3
                    ┌──▼───┐  ┌─▼──────────┐
                    │Redis │  │ MinIO (S3) │
                    └──┬───┘  └─▲──────────┘
                       │        │ sube HLS / thumbnails
                    ┌──▼────────┴──┐
                    │    worker    │
                    │Celery + FFmpeg│
                    └──────────────┘
```

| Servicio     | Imagen / build              | Rol                                                      |
|--------------|-----------------------------|----------------------------------------------------------|
| `nginx`      | `nginx:1.27-alpine`         | Reverse proxy, estáticos, subidas grandes sin buffering   |
| `backend`    | `backend/Dockerfile`        | API Django REST (gunicorn). Corre migraciones al iniciar  |
| `worker`     | `backend/Dockerfile.worker` | Celery con FFmpeg para transcodificación HLS              |
| `postgres`   | `postgres:16-alpine`        | Base de datos                                             |
| `redis`      | `redis:7-alpine`            | Broker y result backend de Celery                         |
| `minio`      | `minio/minio`               | Almacenamiento S3 local (Cloudflare R2 en producción)     |
| `minio-init` | `minio/mc`                  | Crea el bucket al arrancar (idempotente) y termina        |

## Stack

- **Backend:** Python 3.12, Django 5.1, Django REST Framework, Celery 5.4
- **Multimedia:** FFmpeg (HLS `.m3u8` + `.ts`, thumbnails)
- **Datos:** PostgreSQL 16, Redis 7
- **Almacenamiento:** MinIO / Cloudflare R2 vía `boto3`
- **Frontend (próximamente):** React + TypeScript + Vite + Tailwind

## Requisitos

- Docker Desktop (o Docker Engine + Compose v2)
- En Windows: WSL 2 habilitado

## Puesta en marcha

```bash
git clone https://github.com/Arenx3/streamforge.git
cd streamforge
cp .env.example .env        # completa los valores marcados como change-me
docker compose up --build
```

Verifica que todo esté sano:

```bash
curl http://localhost/api/health/
# {"status": "ok", "checks": {"database": true, "redis": true, "storage": true}}

docker compose exec worker celery -A config call core.ping
```

| URL                           | Qué es                    |
|-------------------------------|---------------------------|
| http://localhost/api/health/  | Health check del stack    |
| http://localhost/admin/       | Django admin              |
| http://localhost:9001         | Consola de MinIO          |

Crear un superusuario:

```bash
docker compose exec backend python manage.py createsuperuser
```

## Variables de entorno

Todas las credenciales y configuración viven en `.env` (nunca se versiona). `.env.example` documenta cada variable. Puntos clave:

- `DJANGO_SECRET_KEY`: genera uno con `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
- `MINIO_ROOT_PASSWORD` y `AWS_SECRET_ACCESS_KEY` deben coincidir en local (mínimo 8 caracteres).
- `NGINX_PORT`: cámbialo si el puerto 80 está ocupado.
- Para producción con R2 basta con cambiar `AWS_S3_ENDPOINT_URL` y las credenciales.

## Estructura del repositorio

```
.
├── backend/
│   ├── config/            # settings, urls, celery app
│   ├── core/              # health check, cliente S3, tareas base
│   ├── Dockerfile         # imagen API (gunicorn)
│   ├── Dockerfile.worker  # imagen worker (Celery + FFmpeg)
│   └── entrypoint.sh      # migrate + collectstatic
├── frontend/              # React + Vite (fase 3)
├── minio/init-bucket.sh   # bootstrap del bucket
├── nginx/default.conf     # reverse proxy
└── docker-compose.yml
```

## Reglas del proyecto

1. **Cero bloqueo HTTP:** ningún trabajo de FFmpeg ni I/O pesado corre en el request de Django; todo va a Celery.
2. **Fallos de FFmpeg:** limpiar temporales en `/tmp/streamforge`, marcar el video como `FAILED` y registrar el error sin tumbar el worker.
3. **Configuración por entorno:** nada de credenciales en el código.
4. **UI:** acabado de producto SaaS (referencias: Vercel, Linear, Raycast). Modo oscuro por defecto, skeletons en estados de carga, transiciones sutiles, cero plantillas genéricas.

## Flujo de trabajo

- `main` siempre estable. Cada cambio entra por una rama y Pull Request.
- Nombres de rama: `feature/<tema>`, `fix/<tema>`, `docs/<tema>`, `chore/<tema>`.
- Commits con [Conventional Commits](https://www.conventionalcommits.org/): `feat(backend): ...`, `fix(worker): ...`, `build: ...`, `docs: ...`.
- Antes de abrir un PR: `docker compose up --build` levanta limpio y `/api/health/` responde `ok`.

## Roadmap

- [x] **Fase 1 — Infraestructura:** Docker Compose, PostgreSQL, Redis, MinIO, Django, Celery + FFmpeg, Nginx
- [ ] **Fase 2 — Pipeline de video:** modelos, subida, transcodificación HLS multi-calidad, thumbnails, estados
- [ ] **Fase 3 — Frontend:** dashboard, subida con progreso en vivo, reproductor HLS
- [ ] **Fase 4 — Compartir:** enlaces públicos, tests E2E con Playwright, despliegue
