# StreamForge

[![CI](https://github.com/Arenx3/streamforge/actions/workflows/ci.yml/badge.svg)](https://github.com/Arenx3/streamforge/actions/workflows/ci.yml)

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
                    │Redis │  │ RustFS (S3)│
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
| `beat`       | `backend/Dockerfile.worker` | Tareas periódicas (limpieza de subidas abandonadas)       |
| `accounts-worker` | `backend/Dockerfile.worker` | Correos y limpieza de cuentas, en su propia cola |
| `mailpit`    | `axllent/mailpit`           | Bandeja local: atrapa los correos de desarrollo           |
| `postgres`   | `postgres:16-alpine`        | Base de datos                                             |
| `redis`      | `redis:7-alpine`            | Broker y result backend de Celery                         |
| `storage`    | `rustfs/rustfs`             | Almacenamiento S3 local (Cloudflare R2 en producción)     |
| `storage-init` | `amazon/aws-cli`          | Crea el bucket al arrancar (idempotente) y termina        |

## Stack

- **Backend:** Python 3.12, Django 5.1, Django REST Framework, Celery 5.4
- **Multimedia:** FFmpeg (HLS `.m3u8` + `.ts`, thumbnails)
- **Datos:** PostgreSQL 16, Redis 7
- **Almacenamiento:** S3 vía `boto3` — RustFS en local, Cloudflare R2 en producción
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
| http://localhost/api/auth/    | Autenticación (JWT)       |
| http://localhost/admin/       | Django admin              |
| http://localhost:9001         | Consola de RustFS (S3)    |
| http://localhost:8025         | Correos locales (Mailpit) |

Crear un superusuario:

```bash
docker compose exec backend python manage.py createsuperuser
```

## Autenticación

La API usa JWT. Todo endpoint nuevo es privado por defecto (`IsAuthenticated`).

| Método | Ruta                       | Qué hace                                                  |
|--------|----------------------------|-----------------------------------------------------------|
| POST   | `/api/auth/register/`      | Crea una cuenta sin verificar y envía el correo           |
| POST   | `/api/auth/token/`         | Login con email: `access` (15 min) y `refresh` (7 días)   |
| POST   | `/api/auth/token/refresh/` | Devuelve un par nuevo e invalida el `refresh` usado       |
| GET    | `/api/auth/me/`            | Usuario actual (`Authorization: Bearer <access>`)         |
| POST   | `/api/auth/email/verify/`  | Confirma el correo con `token` (requiere sesión)          |
| POST   | `/api/auth/email/resend/`  | Reenvía el correo de verificación                         |
| POST   | `/api/auth/password/reset/` | Envía el enlace de recuperación a `email`                |
| POST   | `/api/auth/password/reset/confirm/` | Cambia la contraseña con `token` y `password`    |

Las cuentas se identifican por email, sin distinguir mayúsculas. Login y registro tienen rate limit por IP (`THROTTLE_RATE_LOGIN`, `THROTTLE_RATE_REGISTER`).

### Verificación y recuperación

- **Sin verificar** se puede iniciar sesión, pero no subir videos (`403`, `code: email_not_verified`). Si nadie confirma en 3 días (`ACCOUNT_UNVERIFIED_TTL_HOURS`), la cuenta se borra y el correo queda libre.
- **Confirmar exige sesión y enlace.** Así, quien registre un correo ajeno no puede lograr que su dueño le active la cuenta.
- **Recuperar contraseña** usa un enlace de un solo uso válido por 1 hora. También verifica el correo y cierra todas las sesiones.
- **Privacidad:** registro y recuperación responden igual exista o no el correo.
- **En local** los correos no salen a internet: se ven en http://localhost:8025 (Mailpit). Los enlaces apuntan a `FRONTEND_URL`.
- **Aviso de seguridad:** cada cambio de contraseña se notifica por correo.
- **Límites:** un correo por tipo y dirección cada 2 minutos, y `AUTH_EMAIL_DAILY_LIMIT` al día. El 20 % se reserva para recuperar cuentas, así que una avalancha de registros no bloquea a nadie.
- **Alertas:** si un correo agota sus reintentos o se alcanza el límite, se registra en logs y se avisa al webhook de Slack o Discord en `EMAIL_ALERT_WEBHOOK_URL`, si existe.
- **SMTP real (opcional):** configura las variables `EMAIL_*` (`.env.example` trae el ejemplo para Resend) y levanta el stack con `docker compose up -d --scale mailpit=0`.

## Subida de videos

Los archivos van directo del navegador a S3; Django solo coordina.

1. `POST /api/videos/` con `title`, `content_type` (`video/*`) y `size_bytes`. Devuelve el video y una URL prefirmada por parte.
2. El cliente corta el archivo según `upload.parts[].size` y hace `PUT` de cada parte a su URL. El tamaño va firmado: si no coincide, S3 la rechaza.
3. `POST /api/videos/{id}/complete/` ensambla el archivo, lo verifica y pasa el video a `UPLOADED`. Se puede reintentar sin riesgo.

Las subidas sin terminar se cancelan solas después de `VIDEO_UPLOAD_ABANDON_HOURS` (tarea periódica en `beat`).

## Transcodificación

Al completar la subida, el worker convierte el original a HLS:

- **Calidades:** 360p, 720p y 1080p, sin escalar por encima del original. Si el original mide entre dos escalones, su tamaño real se agrega como calidad máxima (un 480p genera 360p y 480p).
- **Formato:** H.264 + AAC en segmentos de 6 s, con keyframes alineados para cambiar de calidad sin cortes.
- **Salida en S3:** `videos/<id>/hls/master.m3u8` y una carpeta por calidad.
- **Thumbnail:** un frame representativo cerca del 10 % del video, en WebP de hasta 1280 px, en `videos/<id>/thumbnail.webp`. Si no se puede generar, el video queda listo igual.
- **Seguridad:** solo se aceptan contenedores de video reales. FFmpeg no puede leer archivos locales ni la red aunque el archivo lo intente, y se eliminan los metadatos del original (como la ubicación GPS).
- **Progreso:** `GET /api/videos/{id}/status/` devuelve `status`, `progress` (0 a 100) y `error_message`. El avance en vivo vive en Redis y PostgreSQL solo se actualiza cada 5 %.
- **Límites:** duración máxima con `VIDEO_MAX_DURATION_SECONDS` y tiempo de proceso con `VIDEO_TRANSCODE_MAX_SECONDS`.

## API de videos

Documentación interactiva en http://localhost/api/docs/ (se desactiva con `API_DOCS_ENABLED=False`).

| Método | Ruta                          | Qué hace                                               |
|--------|-------------------------------|--------------------------------------------------------|
| GET    | `/api/videos/`                | Lista tus videos, paginada, con filtro `?status=`      |
| POST   | `/api/videos/`                | Inicia una subida                                      |
| GET    | `/api/videos/{id}/`           | Metadatos, thumbnail y URL de reproducción             |
| PATCH  | `/api/videos/{id}/`           | Edita título o descripción                             |
| DELETE | `/api/videos/{id}/`           | Borra el video y todos sus archivos                    |
| POST   | `/api/videos/{id}/complete/`  | Termina la subida y empieza el procesamiento           |
| GET    | `/api/videos/{id}/status/`    | Estado y progreso en vivo                              |

**Reproducción privada:** `playback_url` lleva un token firmado que vence a las 6 horas (`VIDEO_PLAYBACK_URL_EXPIRES`) y sirve para un solo video. El bucket nunca es público: cada segmento se entrega con su propia URL prefirmada.

## Manejo de fallos

- **Archivo inválido o error de FFmpeg:** el video queda `FAILED` con un mensaje legible y el worker sigue con la cola.
- **Errores temporales de S3:** cada operación se reintenta hasta 3 veces con espera exponencial.
- **Limpieza:** nunca quedan temporales en `/tmp/streamforge`. Si un video falla se borran sus archivos HLS parciales y se conserva el original.
- **Caída del worker:** cada ejecución envía un latido. Si deja de latir por `VIDEO_STUCK_AFTER_SECONDS` (3 min), `beat` la reinicia; tras 3 intentos el video queda `FAILED`.

## Variables de entorno

Todas las credenciales y configuración viven en `.env` (nunca se versiona). `.env.example` documenta cada variable. Puntos clave:

- `DJANGO_SECRET_KEY`: genera uno con `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Firma los tokens JWT, así que la app no arranca con el valor `change-me`.
- `TRUSTED_PROXY_COUNT`: proxies delante de Django (1 = nginx, 2 = CDN + nginx). Si es incorrecto, el rate limit se puede evadir o termina bloqueando a todos a la vez.
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`: en local también son las credenciales del servidor RustFS (consola en el puerto `STORAGE_CONSOLE_PORT`).
- `NGINX_PORT`: cámbialo si el puerto 80 está ocupado.
- `AWS_S3_PUBLIC_ENDPOINT_URL`: host que usa el navegador para subir. En local es `http://localhost:9000`.
- Para producción con R2 basta con cambiar `AWS_S3_ENDPOINT_URL`, `AWS_S3_PUBLIC_ENDPOINT_URL` y las credenciales, y configurar CORS en el bucket.

## Estructura del repositorio

```
.
├── backend/
│   ├── accounts/          # registro, login JWT y perfil
│   ├── config/            # settings, urls, celery app
│   ├── core/              # health check, cliente S3, tareas base
│   ├── videos/            # modelo Video, subida a S3 y HLS
│   ├── Dockerfile         # imagen API (gunicorn)
│   ├── Dockerfile.worker  # imagen worker (Celery + FFmpeg)
│   └── entrypoint.sh      # migrate + collectstatic
├── frontend/              # React + Vite (fase 3)
├── storage/init-bucket.sh # bootstrap del bucket
├── nginx/default.conf     # reverse proxy
└── docker-compose.yml
```

## Reglas del proyecto

1. **Cero bloqueo HTTP:** ningún trabajo de FFmpeg ni I/O pesado corre en el request de Django; todo va a Celery.
2. **Fallos de FFmpeg:** limpiar temporales en `/tmp/streamforge`, marcar el video como `FAILED` y registrar el error sin tumbar el worker.
3. **Configuración por entorno:** nada de credenciales en el código.
4. **UI:** acabado de producto SaaS (referencias: Vercel, Linear, Raycast). Modo oscuro por defecto, skeletons en estados de carga, transiciones sutiles, cero plantillas genéricas.

## Flujo de trabajo

GitHub Flow: cada cambio nace de un issue, se desarrolla en su rama y entra a `main` por Pull Request con CI en verde (squash merge). Commits con [Conventional Commits](https://www.conventionalcommits.org/).

Detalles, setup de desarrollo y qué valida el CI en [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

- [x] **Fase 1 — Infraestructura:** Docker Compose, PostgreSQL, Redis, RustFS (S3), Django, Celery + FFmpeg, Nginx
- [x] **Fase 2 — Pipeline de video:** modelos, subida, transcodificación HLS multi-calidad, thumbnails, estados
- [ ] **Fase 3 — Frontend:** dashboard, subida con progreso en vivo, reproductor HLS
- [ ] **Fase 4 — Compartir:** enlaces públicos, tests E2E con Playwright, despliegue
